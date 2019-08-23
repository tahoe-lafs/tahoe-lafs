
"""
I contain the client-side code which speaks to storage servers, in particular
the foolscap-based server implemented in src/allmydata/storage/*.py .
"""

# roadmap:
#
# 1: implement StorageFarmBroker (i.e. "storage broker"), change Client to
# create it, change uploader/servermap to get rrefs from it. ServerFarm calls
# IntroducerClient.subscribe_to . ServerFarm hides descriptors, passes rrefs
# to clients. webapi status pages call broker.get_info_about_serverid.
#
# 2: move get_info methods to the descriptor, webapi status pages call
# broker.get_descriptor_for_serverid().get_info
#
# 3?later?: store descriptors in UploadResults/etc instead of serverids,
# webapi status pages call descriptor.get_info and don't use storage_broker
# or Client
#
# 4: enable static config: tahoe.cfg can add descriptors. Make the introducer
# optional. This closes #467
#
# 5: implement NativeStorageClient, pass it to Tahoe2PeerSelector and other
# clients. Clients stop doing callRemote(), use NativeStorageClient methods
# instead (which might do something else, i.e. http or whatever). The
# introducer and tahoe.cfg only create NativeStorageClients for now.
#
# 6: implement other sorts of IStorageClient classes: S3, etc


import re, time, hashlib
from ConfigParser import (
    NoSectionError,
)
import attr
from zope.interface import (
    Attribute,
    Interface,
    implementer,
)
from twisted.internet import defer
from twisted.application import service
from twisted.plugin import (
    getPlugins,
)
from eliot import (
    log_call,
)
from foolscap.api import eventually
from foolscap.reconnector import (
    ReconnectionInfo,
)
from allmydata.interfaces import (
    IStorageBroker,
    IDisplayableServer,
    IServer,
    IStorageServer,
    IFoolscapStoragePlugin,
)
from allmydata.util import log, base32, connection_status
from allmydata.util.assertutil import precondition
from allmydata.util.observer import ObserverList
from allmydata.util.rrefutil import add_version_to_remote_reference
from allmydata.util.hashutil import permute_server_hash

# who is responsible for de-duplication?
#  both?
#  IC remembers the unpacked announcements it receives, to provide for late
#  subscribers and to remove duplicates

# if a client subscribes after startup, will they receive old announcements?
#  yes

# who will be responsible for signature checking?
#  make it be IntroducerClient, so they can push the filter outwards and
#  reduce inbound network traffic

# what should the interface between StorageFarmBroker and IntroducerClient
# look like?
#  don't pass signatures: only pass validated blessed-objects

@attr.s
class StorageClientConfig(object):
    """
    Configuration for a node acting as a storage client.

    :ivar preferred_peers: An iterable of the server-ids (``bytes``) of the
        storage servers where share placement is preferred, in order of
        decreasing preference.  See the *[client]peers.preferred*
        documentation for details.

    :ivar dict[unicode, dict[bytes, bytes]] storage_plugins: A mapping from
        names of ``IFoolscapStoragePlugin`` configured in *tahoe.cfg* to the
        respective configuration.
    """
    preferred_peers = attr.ib(default=())
    storage_plugins = attr.ib(default=attr.Factory(dict))

    @classmethod
    def from_node_config(cls, config):
        """
        Create a ``StorageClientConfig`` from a complete Tahoe-LAFS node
        configuration.

        :param _Config config: The loaded Tahoe-LAFS node configuration.
        """
        ps = config.get_config("client", "peers.preferred", b"").split(b",")
        preferred_peers = tuple([p.strip() for p in ps if p != b""])

        enabled_storage_plugins = (
            name.strip()
            for name
            in config.get_config(
                b"client",
                b"storage.plugins",
                b"",
            ).decode("utf-8").split(u",")
            if name.strip()
        )

        storage_plugins = {}
        for plugin_name in enabled_storage_plugins:
            try:
                plugin_config = config.items(b"storageclient.plugins." + plugin_name)
            except NoSectionError:
                plugin_config = []
            storage_plugins[plugin_name] = dict(plugin_config)

        return cls(
            preferred_peers,
            storage_plugins,
        )


@implementer(IStorageBroker)
class StorageFarmBroker(service.MultiService):
    """I live on the client, and know about storage servers. For each server
    that is participating in a grid, I either maintain a connection to it or
    remember enough information to establish a connection to it on demand.
    I'm also responsible for subscribing to the IntroducerClient to find out
    about new servers as they are announced by the Introducer.

    :ivar StorageClientConfig storage_client_config: Values from the node
        configuration file relating to storage behavior.
    """

    @property
    def preferred_peers(self):
        return self.storage_client_config.preferred_peers

    def __init__(
            self,
            permute_peers,
            tub_maker,
            node_config,
            storage_client_config=None,
    ):
        service.MultiService.__init__(self)
        assert permute_peers # False not implemented yet
        self.permute_peers = permute_peers
        self._tub_maker = tub_maker

        self.node_config = node_config

        if storage_client_config is None:
            storage_client_config = StorageClientConfig()
        self.storage_client_config = storage_client_config

        # self.servers maps serverid -> IServer, and keeps track of all the
        # storage servers that we've heard about. Each descriptor manages its
        # own Reconnector, and will give us a RemoteReference when we ask
        # them for it.
        self.servers = {}
        self._static_server_ids = set() # ignore announcements for these
        self.introducer_client = None
        self._threshold_listeners = [] # tuples of (threshold, Deferred)
        self._connected_high_water_mark = 0

    @log_call(action_type=u"storage-client:broker:set-static-servers")
    def set_static_servers(self, servers):
        # Sorting the items gives us a deterministic processing order.  This
        # doesn't really matter but it makes the logging behavior more
        # predictable and easier to test (and at least one test does depend on
        # this sorted order).
        for (server_id, server) in sorted(servers.items()):
            try:
                storage_server = self._make_storage_server(server_id, server)
            except Exception:
                # TODO: The _make_storage_server failure is logged but maybe
                # we should write a traceback here.  Notably, tests don't
                # automatically fail just because we hit this case.  Well
                # written tests will still fail if a surprising exception
                # arrives here but they might be harder to debug without this
                # information.
                pass
            else:
                self._static_server_ids.add(server_id)
                self.servers[server_id] = storage_server
                storage_server.setServiceParent(self)
                storage_server.start_connecting(self._trigger_connections)

    def get_client_storage_plugin_web_resources(self, node_config):
        """
        Get all of the client-side ``IResource`` implementations provided by
        enabled storage plugins.

        :param allmydata.node._Config node_config: The complete node
            configuration for the node from which these web resources will be
            served.

        :return dict[unicode, IResource]: Resources for all of the plugins.
        """
        plugins = {
            plugin.name: plugin
            for plugin
            in getPlugins(IFoolscapStoragePlugin)
        }
        return {
            name: plugins[name].get_client_resource(node_config)
            for (name, config)
            in self.storage_client_config.storage_plugins.items()
        }

    @log_call(
        action_type=u"storage-client:broker:make-storage-server",
        include_args=["server_id"],
        include_result=False,
    )
    def _make_storage_server(self, server_id, server):
        assert isinstance(server_id, unicode) # from YAML
        server_id = server_id.encode("ascii")
        handler_overrides = server.get("connections", {})
        s = NativeStorageServer(
            server_id,
            server["ann"],
            self._tub_maker,
            handler_overrides,
            self.node_config,
            self.storage_client_config,
        )
        s.on_status_changed(lambda _: self._got_connection())
        return s

    def when_connected_enough(self, threshold):
        """
        :returns: a Deferred that fires if/when our high water mark for
        number of connected servers becomes (or ever was) above
        "threshold".
        """
        d = defer.Deferred()
        self._threshold_listeners.append( (threshold, d) )
        self._check_connected_high_water_mark()
        return d

    # these two are used in unit tests
    def test_add_rref(self, serverid, rref, ann):
        s = self._make_storage_server(
            serverid.decode("ascii"),
            {"ann": ann.copy()},
        )
        s._rref = rref
        s._is_connected = True
        self.servers[serverid] = s

    def test_add_server(self, server_id, s):
        s.on_status_changed(lambda _: self._got_connection())
        self.servers[server_id] = s

    def use_introducer(self, introducer_client):
        self.introducer_client = ic = introducer_client
        ic.subscribe_to("storage", self._got_announcement)

    def _got_connection(self):
        # this is called by NativeStorageServer when it is connected
        self._check_connected_high_water_mark()

    def _check_connected_high_water_mark(self):
        current = len(self.get_connected_servers())
        if current > self._connected_high_water_mark:
            self._connected_high_water_mark = current

        remaining = []
        for threshold, d in self._threshold_listeners:
            if self._connected_high_water_mark >= threshold:
                eventually(d.callback, None)
            else:
                remaining.append( (threshold, d) )
        self._threshold_listeners = remaining

    def _got_announcement(self, key_s, ann):
        precondition(isinstance(key_s, str), key_s)
        precondition(key_s.startswith("v0-"), key_s)
        precondition(ann["service-name"] == "storage", ann["service-name"])
        server_id = key_s
        if server_id in self._static_server_ids:
            log.msg(format="ignoring announcement for static server '%(id)s'",
                    id=server_id,
                    facility="tahoe.storage_broker", umid="AlxzqA",
                    level=log.UNUSUAL)
            return
        s = self._make_storage_server(
            server_id.decode("utf-8"),
            {u"ann": ann},
        )
        server_id = s.get_serverid()
        old = self.servers.get(server_id)
        if old:
            if old.get_announcement() == ann:
                return # duplicate
            # replacement
            del self.servers[server_id]
            old.stop_connecting()
            old.disownServiceParent()
            # NOTE: this disownServiceParent() returns a Deferred that
            # doesn't fire until Tub.stopService fires, which will wait for
            # any existing connections to be shut down. This doesn't
            # generally matter for normal runtime, but unit tests can run
            # into DirtyReactorErrors if they don't block on these. If a test
            # replaces one server with a newer version, then terminates
            # before the old one has been shut down, it might get
            # DirtyReactorErrors. The fix would be to gather these Deferreds
            # into a structure that will block StorageFarmBroker.stopService
            # until they have fired (but hopefully don't keep reference
            # cycles around when they fire earlier than that, which will
            # almost always be the case for normal runtime).
        # now we forget about them and start using the new one
        s.setServiceParent(self)
        self.servers[server_id] = s
        s.start_connecting(self._trigger_connections)
        # the descriptor will manage their own Reconnector, and each time we
        # need servers, we'll ask them if they're connected or not.

    def _trigger_connections(self):
        # when one connection is established, reset the timers on all others,
        # to trigger a reconnection attempt in one second. This is intended
        # to accelerate server connections when we've been offline for a
        # while. The goal is to avoid hanging out for a long time with
        # connections to only a subset of the servers, which would increase
        # the chances that we'll put shares in weird places (and not update
        # existing shares of mutable files). See #374 for more details.
        for dsc in self.servers.values():
            dsc.try_to_connect()

    def get_servers_for_psi(self, peer_selection_index):
        # return a list of server objects (IServers)
        assert self.permute_peers == True
        connected_servers = self.get_connected_servers()
        preferred_servers = frozenset(s for s in connected_servers if s.get_longname() in self.preferred_peers)
        def _permuted(server):
            seed = server.get_permutation_seed()
            is_unpreferred = server not in preferred_servers
            return (is_unpreferred,
                    permute_server_hash(peer_selection_index, seed))
        return sorted(connected_servers, key=_permuted)

    def get_all_serverids(self):
        return frozenset(self.servers.keys())

    def get_connected_servers(self):
        return frozenset([s for s in self.servers.values() if s.is_connected()])

    def get_known_servers(self):
        return frozenset(self.servers.values())

    def get_nickname_for_serverid(self, serverid):
        if serverid in self.servers:
            return self.servers[serverid].get_nickname()
        return None

    def get_stub_server(self, serverid):
        if serverid in self.servers:
            return self.servers[serverid]
        # some time before 1.12, we changed "serverid" to be "key_s" (the
        # printable verifying key, used in V2 announcements), instead of the
        # tubid. When the immutable uploader delegates work to a Helper,
        # get_stub_server() is used to map the returning server identifiers
        # to IDisplayableServer instances (to get a name, for display on the
        # Upload Results web page). If the Helper is running 1.12 or newer,
        # it will send pubkeys, but if it's still running 1.11, it will send
        # tubids. This clause maps the old tubids to our existing servers.
        for s in self.servers.values():
            if isinstance(s, NativeStorageServer):
                if serverid == s.get_tubid():
                    return s
        return StubServer(serverid)

@implementer(IDisplayableServer)
class StubServer(object):
    def __init__(self, serverid):
        self.serverid = serverid # binary tubid
    def get_serverid(self):
        return self.serverid
    def get_name(self):
        return base32.b2a(self.serverid)[:8]
    def get_longname(self):
        return base32.b2a(self.serverid)
    def get_nickname(self):
        return "?"


class IFoolscapStorageServer(Interface):
    """
    An internal interface that mediates between ``NativeStorageServer`` and
    Foolscap-based ``IStorageServer`` implementations.
    """
    nickname = Attribute("""
    A name for this server for presentation to users.
    """)
    permutation_seed = Attribute("""
    A stable value associated with this server which a client can use as an
    input to the server selection permutation ordering.
    """)
    tubid = Attribute("""
    The identifier for the Tub in which the server is run.
    """)
    storage_server = Attribute("""
    An IStorageServer provide which implements a concrete Foolscap-based
    protocol for communicating with the server.
    """)
    name = Attribute("""
    Another name for this server for presentation to users.
    """)
    longname = Attribute("""
    *Another* name for this server for presentation to users.
    """)
    lease_seed = Attribute("""
    A stable value associated with this server which a client can use as an
    input to a lease secret generation function.
    """)

    def connect_to(tub, got_connection):
        """
        Attempt to establish and maintain a connection to the server.

        :param Tub tub: A Foolscap Tub from which the connection is to
            originate.

        :param got_connection: A one-argument callable which is called with a
            Foolscap ``RemoteReference`` when a connection is established.
            This may be called multiple times if the connection is lost and
            then re-established.

        :return foolscap.reconnector.Reconnector: An object which manages the
            connection and reconnection attempts.
        """


@implementer(IFoolscapStorageServer)
@attr.s(frozen=True)
class _FoolscapStorage(object):
    """
    Abstraction for connecting to a storage server exposed via Foolscap.
    """
    nickname = attr.ib()
    permutation_seed = attr.ib()
    tubid = attr.ib()

    storage_server = attr.ib(validator=attr.validators.provides(IStorageServer))

    _furl = attr.ib()
    _short_description = attr.ib()
    _long_description = attr.ib()


    @property
    def name(self):
        return self._short_description

    @property
    def longname(self):
        return self._long_description

    @property
    def lease_seed(self):
        return self.tubid

    @classmethod
    def from_announcement(cls, server_id, furl, ann, storage_server):
        """
        Create an instance from a fURL and an announcement like::

            {"permutation-seed-base32": "...",
             "nickname": "...",
            }

        *nickname* is optional.
        """
        m = re.match(r'pb://(\w+)@', furl)
        assert m, furl
        tubid_s = m.group(1).lower()
        tubid = base32.a2b(tubid_s)
        if "permutation-seed-base32" in ann:
            ps = base32.a2b(str(ann["permutation-seed-base32"]))
        elif re.search(r'^v0-[0-9a-zA-Z]{52}$', server_id):
            ps = base32.a2b(server_id[3:])
        else:
            log.msg("unable to parse serverid '%(server_id)s as pubkey, "
                    "hashing it to get permutation-seed, "
                    "may not converge with other clients",
                    server_id=server_id,
                    facility="tahoe.storage_broker",
                    level=log.UNUSUAL, umid="qu86tw")
            ps = hashlib.sha256(server_id).digest()
        permutation_seed = ps

        assert server_id
        long_description = server_id
        if server_id.startswith("v0-"):
            # remove v0- prefix from abbreviated name
            short_description = server_id[3:3+8]
        else:
            short_description = server_id[:8]
        nickname = ann.get("nickname", "")

        return cls(
            nickname=nickname,
            permutation_seed=permutation_seed,
            tubid=tubid,
            storage_server=storage_server,
            furl=furl,
            short_description=short_description,
            long_description=long_description,
        )

    def connect_to(self, tub, got_connection):
        return tub.connectTo(self._furl, got_connection)


@implementer(IFoolscapStorageServer)
class _NullStorage(object):
    """
    Abstraction for *not* communicating with a storage server of a type with
    which we can't communicate.
    """
    nickname = ""
    permutation_seed = hashlib.sha256("").digest()
    tubid = hashlib.sha256("").digest()
    storage_server = None

    lease_seed = hashlib.sha256("").digest()

    name = "<unsupported>"
    longname = "<storage with unsupported protocol>"

    def connect_to(self, tub, got_connection):
        return NonReconnector()


class NonReconnector(object):
    """
    A ``foolscap.reconnector.Reconnector``-alike that doesn't do anything.
    """
    def stopConnecting(self):
        pass

    def reset(self):
        pass

    def getReconnectionInfo(self):
        return ReconnectionInfo()

_null_storage = _NullStorage()


class AnnouncementNotMatched(Exception):
    """
    A storage server announcement wasn't matched by any of the locally enabled
    plugins.
    """


def _storage_from_foolscap_plugin(node_config, config, announcement, get_rref):
    """
    Construct an ``IStorageServer`` from the most locally-preferred plugin
    that is offered in the given announcement.

    :param allmydata.node._Config node_config: The node configuration to
        pass to the plugin.
    """
    plugins = {
        plugin.name: plugin
        for plugin
        in getPlugins(IFoolscapStoragePlugin)
    }
    storage_options = announcement.get(u"storage-options", [])
    for plugin_name, plugin_config in config.storage_plugins.items():
        try:
            plugin = plugins[plugin_name]
        except KeyError:
            raise ValueError("{} not installed".format(plugin_name))
        for option in storage_options:
            if plugin_name == option[u"name"]:
                furl = option[u"storage-server-FURL"]
                return furl, plugin.get_storage_client(
                    node_config,
                    option,
                    get_rref,
                )
    raise AnnouncementNotMatched()


@implementer(IServer)
class NativeStorageServer(service.MultiService):
    """I hold information about a storage server that we want to connect to.
    If we are connected, I hold the RemoteReference, their host address, and
    the their version information. I remember information about when we were
    last connected too, even if we aren't currently connected.

    @ivar last_connect_time: when we last established a connection
    @ivar last_loss_time: when we last lost a connection

    @ivar version: the server's versiondict, from the most recent announcement
    @ivar nickname: the server's self-reported nickname (unicode), same

    @ivar rref: the RemoteReference, if connected, otherwise None
    @ivar remote_host: the IAddress, if connected, otherwise None
    """

    VERSION_DEFAULTS = {
        "http://allmydata.org/tahoe/protocols/storage/v1" :
        { "maximum-immutable-share-size": 2**32 - 1,
          "maximum-mutable-share-size": 2*1000*1000*1000, # maximum prior to v1.9.2
          "tolerates-immutable-read-overrun": False,
          "delete-mutable-shares-with-zero-length-writev": False,
          "available-space": None,
          },
        "application-version": "unknown: no get_version()",
        }

    def __init__(self, server_id, ann, tub_maker, handler_overrides, node_config, config=StorageClientConfig()):
        service.MultiService.__init__(self)
        assert isinstance(server_id, str)
        self._server_id = server_id
        self.announcement = ann
        self._tub_maker = tub_maker
        self._handler_overrides = handler_overrides

        self._storage = self._make_storage_system(node_config, config, ann)

        self.last_connect_time = None
        self.last_loss_time = None
        self.remote_host = None
        self._rref = None
        self._is_connected = False
        self._reconnector = None
        self._trigger_cb = None
        self._on_status_changed = ObserverList()

    def _make_storage_system(self, node_config, config, ann):
        """
        :param allmydata.node._Config node_config: The node configuration to pass
            to any configured storage plugins.

        :param StorageClientConfig config: Configuration specifying desired
            storage client behavior.

        :param dict ann: The storage announcement from the storage server we
            are meant to communicate with.

        :return IFoolscapStorageServer: An object enabling communication via
            Foolscap with the server which generated the announcement.
        """
        # Try to match the announcement against a plugin.
        try:
            furl, storage_server = _storage_from_foolscap_plugin(
                node_config,
                config,
                ann,
                # Pass in an accessor for our _rref attribute.  The value of
                # the attribute may change over time as connections are lost
                # and re-established.  The _StorageServer should always be
                # able to get the most up-to-date value.
                self.get_rref,
            )
        except AnnouncementNotMatched:
            # Nope.
            pass
        else:
            return _FoolscapStorage.from_announcement(
                self._server_id,
                furl.encode("utf-8"),
                ann,
                storage_server,
            )

        # Try to match the announcement against the anonymous access scheme.
        try:
            furl = ann[u"anonymous-storage-FURL"]
        except KeyError:
            # Nope
            pass
        else:
            # See comment above for the _storage_from_foolscap_plugin case
            # about passing in get_rref.
            storage_server = _StorageServer(get_rref=self.get_rref)
            return _FoolscapStorage.from_announcement(
                self._server_id,
                furl.encode("utf-8"),
                ann,
                storage_server,
            )

        # Nothing matched so we can't talk to this server.
        return _null_storage

    def get_permutation_seed(self):
        return self._storage.permutation_seed
    def get_name(self): # keep methodname short
        # TODO: decide who adds [] in the short description. It should
        # probably be the output side, not here.
        return self._storage.name
    def get_longname(self):
        return self._storage.longname
    def get_tubid(self):
        return self._storage.tubid
    def get_lease_seed(self):
        return self._storage.lease_seed
    def get_foolscap_write_enabler_seed(self):
        return self._storage.tubid
    def get_nickname(self):
        return self._storage.nickname

    def on_status_changed(self, status_changed):
        """
        :param status_changed: a callable taking a single arg (the
            NativeStorageServer) that is notified when we become connected
        """
        return self._on_status_changed.subscribe(status_changed)

    # Special methods used by copy.copy() and copy.deepcopy(). When those are
    # used in allmydata.immutable.filenode to copy CheckResults during
    # repair, we want it to treat the IServer instances as singletons, and
    # not attempt to duplicate them..
    def __copy__(self):
        return self
    def __deepcopy__(self, memodict):
        return self

    def __repr__(self):
        return "<NativeStorageServer for %s>" % self.get_name()
    def get_serverid(self):
        return self._server_id
    def get_version(self):
        if self._rref:
            return self._rref.version
        return None
    def get_announcement(self):
        return self.announcement
    def get_remote_host(self):
        return self.remote_host

    def get_connection_status(self):
        last_received = None
        if self._rref:
            last_received = self._rref.getDataLastReceivedAt()
        return connection_status.from_foolscap_reconnector(self._reconnector,
                                                           last_received)

    def is_connected(self):
        return self._is_connected

    def get_available_space(self):
        version = self.get_version()
        if version is None:
            return None
        protocol_v1_version = version.get('http://allmydata.org/tahoe/protocols/storage/v1', {})
        available_space = protocol_v1_version.get('available-space')
        if available_space is None:
            available_space = protocol_v1_version.get('maximum-immutable-share-size', None)
        return available_space

    def start_connecting(self, trigger_cb):
        self._tub = self._tub_maker(self._handler_overrides)
        self._tub.setServiceParent(self)
        self._trigger_cb = trigger_cb
        self._reconnector = self._storage.connect_to(self._tub, self._got_connection)

    def _got_connection(self, rref):
        lp = log.msg(format="got connection to %(name)s, getting versions",
                     name=self.get_name(),
                     facility="tahoe.storage_broker", umid="coUECQ")
        if self._trigger_cb:
            eventually(self._trigger_cb)
        default = self.VERSION_DEFAULTS
        d = add_version_to_remote_reference(rref, default)
        d.addCallback(self._got_versioned_service, lp)
        d.addCallback(lambda ign: self._on_status_changed.notify(self))
        d.addErrback(log.err, format="storageclient._got_connection",
                     name=self.get_name(), umid="Sdq3pg")

    def _got_versioned_service(self, rref, lp):
        log.msg(format="%(name)s provided version info %(version)s",
                name=self.get_name(), version=rref.version,
                facility="tahoe.storage_broker", umid="SWmJYg",
                level=log.NOISY, parent=lp)

        self.last_connect_time = time.time()
        self.remote_host = rref.getLocationHints()
        self._rref = rref
        self._is_connected = True
        rref.notifyOnDisconnect(self._lost)

    def get_rref(self):
        return self._rref

    def get_storage_server(self):
        """
        See ``IServer.get_storage_server``.
        """
        if self._rref is None:
            return None
        return self._storage.storage_server

    def _lost(self):
        log.msg(format="lost connection to %(name)s", name=self.get_name(),
                facility="tahoe.storage_broker", umid="zbRllw")
        self.last_loss_time = time.time()
        # self._rref is now stale: all callRemote()s will get a
        # DeadReferenceError. We leave the stale reference in place so that
        # uploader/downloader code (which received this IServer through
        # get_connected_servers() or get_servers_for_psi()) can continue to
        # use s.get_rref().callRemote() and not worry about it being None.
        self._is_connected = False
        self.remote_host = None

    def stop_connecting(self):
        # used when this descriptor has been superceded by another
        self._reconnector.stopConnecting()

    def try_to_connect(self):
        # used when the broker wants us to hurry up
        self._reconnector.reset()

class UnknownServerTypeError(Exception):
    pass


@implementer(IStorageServer)
@attr.s
class _StorageServer(object):
    """
    ``_StorageServer`` is a direct pass-through to an ``RIStorageServer`` via
    a ``RemoteReference``.
    """
    _get_rref = attr.ib()

    @property
    def _rref(self):
        return self._get_rref()

    def get_version(self):
        return self._rref.callRemote(
            "get_version",
        )

    def allocate_buckets(
            self,
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums,
            allocated_size,
            canary,
    ):
        return self._rref.callRemote(
            "allocate_buckets",
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums,
            allocated_size,
            canary,
        )

    def add_lease(
            self,
            storage_index,
            renew_secret,
            cancel_secret,
    ):
        return self._rref.callRemote(
            "add_lease",
            storage_index,
            renew_secret,
            cancel_secret,
        )

    def renew_lease(
            self,
            storage_index,
            renew_secret,
    ):
        return self._rref.callRemote(
            "renew_lease",
            storage_index,
            renew_secret,
        )

    def get_buckets(
            self,
            storage_index,
    ):
        return self._rref.callRemote(
            "get_buckets",
            storage_index,
        )

    def slot_readv(
            self,
            storage_index,
            shares,
            readv,
    ):
        return self._rref.callRemote(
            "slot_readv",
            storage_index,
            shares,
            readv,
        )

    def slot_testv_and_readv_and_writev(
            self,
            storage_index,
            secrets,
            tw_vectors,
            r_vector,
    ):
        return self._rref.callRemote(
            "slot_testv_and_readv_and_writev",
            storage_index,
            secrets,
            tw_vectors,
            r_vector,
        )

    def advise_corrupt_share(
            self,
            share_type,
            storage_index,
            shnum,
            reason,
    ):
        return self._rref.callRemoteOnly(
            "advise_corrupt_share",
            share_type,
            storage_index,
            shnum,
            reason,
        )
