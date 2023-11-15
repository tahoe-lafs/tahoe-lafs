
"""
I contain the client-side code which speaks to storage servers, in particular
the foolscap-based server implemented in src/allmydata/storage/*.py .

Ported to Python 3.
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

from __future__ import annotations

from typing import Union, Callable, Any, Optional, cast, Dict
from os import urandom
import re
import time
import hashlib
from io import StringIO
from configparser import NoSectionError
import json

import attr
from attr import define
from hyperlink import DecodedURL
from twisted.web.client import HTTPConnectionPool
from zope.interface import (
    Attribute,
    Interface,
    implementer,
)
from twisted.python.failure import Failure
from twisted.web import http
from twisted.internet.task import LoopingCall
from twisted.internet import defer, reactor
from twisted.internet.interfaces import IReactorTime
from twisted.application import service
from twisted.logger import Logger
from twisted.plugin import (
    getPlugins,
)
from eliot import (
    log_call,
)
from foolscap.ipb import IRemoteReference
from foolscap.api import eventually, RemoteException
from foolscap.reconnector import (
    ReconnectionInfo,
)
from allmydata.interfaces import (
    IStorageBroker,
    IDisplayableServer,
    IServer,
    IStorageServer,
    IFoolscapStoragePlugin,
    VersionMessage
)
from allmydata.grid_manager import (
    create_grid_manager_verifier, SignedCertificate
)
from allmydata.crypto import (
    ed25519,
)
from allmydata.util.tor_provider import _Provider as TorProvider
from allmydata.util import log, base32, connection_status
from allmydata.util.assertutil import precondition
from allmydata.util.observer import ObserverList
from allmydata.util.rrefutil import add_version_to_remote_reference
from allmydata.util.hashutil import permute_server_hash
from allmydata.util.dictutil import BytesKeyDict, UnicodeKeyDict
from allmydata.util.deferredutil import async_to_deferred, race
from allmydata.util.attrs_provides import provides
from allmydata.storage.http_client import (
    StorageClient, StorageClientImmutables, StorageClientGeneral,
    ClientException as HTTPClientException, StorageClientMutables,
    ReadVector, TestWriteVectors, WriteVector, TestVector, ClientException,
    StorageClientFactory
)
from .node import _Config

_log = Logger()

ANONYMOUS_STORAGE_NURLS = "anonymous-storage-NURLs"


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
        decreasing preference.  See the *[client]peers.preferred* documentation
        for details.

    :ivar dict[unicode, dict[unicode, unicode]] storage_plugins: A mapping from
        names of ``IFoolscapStoragePlugin`` configured in *tahoe.cfg* to the
        respective configuration.

    :ivar list[ed25519.VerifyKey] grid_manager_keys: with no keys in
        this list, we'll upload to any storage server. Otherwise, we will
        only upload to a storage-server that has a valid certificate
        signed by at least one of these keys.
    """
    preferred_peers = attr.ib(default=())
    storage_plugins = attr.ib(default=attr.Factory(dict))
    grid_manager_keys = attr.ib(default=attr.Factory(list))

    @classmethod
    def from_node_config(cls, config):
        """
        Create a ``StorageClientConfig`` from a complete Tahoe-LAFS node
        configuration.

        :param _Config config: The loaded Tahoe-LAFS node configuration.
        """
        ps = config.get_config("client", "peers.preferred", "").split(",")
        preferred_peers = tuple([p.strip() for p in ps if p != ""])

        enabled_storage_plugins = (
            name.strip()
            for name
            in config.get_config(
                "client",
                "storage.plugins",
                "",
            ).split(u",")
            if name.strip()
        )

        storage_plugins = {}
        for plugin_name in enabled_storage_plugins:
            try:
                plugin_config = config.items("storageclient.plugins." + plugin_name)
            except NoSectionError:
                plugin_config = []
            storage_plugins[plugin_name] = dict(plugin_config)

        grid_manager_keys = []
        for name, gm_key in config.enumerate_section('grid_managers').items():
            grid_manager_keys.append(
                ed25519.verifying_key_from_string(gm_key.encode("ascii"))
            )


        return cls(
            preferred_peers,
            storage_plugins,
            grid_manager_keys,
        )

    def get_configured_storage_plugins(self) -> dict[str, IFoolscapStoragePlugin]:
        """
        :returns: a mapping from names to instances for all available
            plugins

        :raises MissingPlugin: if the configuration asks for a plugin
            for which there is no corresponding instance (e.g. it is
            not installed).
        """
        plugins = {
            plugin.name: plugin
            for plugin
            in getPlugins(IFoolscapStoragePlugin)
        }

        # mypy doesn't like "str" in place of Any ...
        configured: Dict[Any, IFoolscapStoragePlugin] = dict()
        for plugin_name in self.storage_plugins:
            try:
                plugin = plugins[plugin_name]
            except KeyError:
                raise MissingPlugin(plugin_name)
            configured[plugin_name] = plugin
        return configured


@implementer(IStorageBroker)
class StorageFarmBroker(service.MultiService):
    """I live on the client, and know about storage servers. For each server
    that is participating in a grid, I either maintain a connection to it or
    remember enough information to establish a connection to it on demand.
    I'm also responsible for subscribing to the IntroducerClient to find out
    about new servers as they are announced by the Introducer.

    :ivar _tub_maker: A one-argument callable which accepts a dictionary of
        "handler overrides" and returns a ``foolscap.api.Tub``.

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
            node_config: _Config,
            storage_client_config=None,
            default_connection_handlers=None,
            tor_provider: Optional[TorProvider]=None,
    ):
        service.MultiService.__init__(self)
        if default_connection_handlers is None:
            default_connection_handlers = {"tcp": "tcp"}

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
        self.servers = BytesKeyDict()
        self._static_server_ids : set[bytes] = set() # ignore announcements for these
        self.introducer_client = None
        self._threshold_listeners : list[tuple[float,defer.Deferred[Any]]]= [] # tuples of (threshold, Deferred)
        self._connected_high_water_mark = 0
        self._tor_provider = tor_provider
        self._default_connection_handlers = default_connection_handlers

    @log_call(action_type=u"storage-client:broker:set-static-servers")
    def set_static_servers(self, servers):
        # Sorting the items gives us a deterministic processing order.  This
        # doesn't really matter but it makes the logging behavior more
        # predictable and easier to test (and at least one test does depend on
        # this sorted order).
        for (server_id, server) in sorted(servers.items()):
            try:
                storage_server = self._make_storage_server(
                    server_id.encode("utf-8"),
                    server,
                )
            except Exception:
                # TODO: The _make_storage_server failure is logged but maybe
                # we should write a traceback here.  Notably, tests don't
                # automatically fail just because we hit this case.  Well
                # written tests will still fail if a surprising exception
                # arrives here but they might be harder to debug without this
                # information.
                pass
            else:
                if isinstance(server_id, str):
                    server_id = server_id.encode("utf-8")
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
        return UnicodeKeyDict({
            name: plugins[name].get_client_resource(node_config)
            for (name, config)
            in self.storage_client_config.storage_plugins.items()
        })

    @staticmethod
    def _should_we_use_http(node_config: _Config, announcement: dict) -> bool:
        """
        Given an announcement dictionary and config, return whether we should
        connect to storage server over HTTP.
        """
        return not node_config.get_config(
            "client", "force_foolscap", default=False, boolean=True,
        ) and len(announcement.get(ANONYMOUS_STORAGE_NURLS, [])) > 0

    @log_call(
        action_type=u"storage-client:broker:make-storage-server",
        include_args=["server_id"],
        include_result=False,
    )
    def _make_storage_server(self, server_id, server):
        """
        Create a new ``IServer`` for the given storage server announcement.

        :param bytes server_id: The unique identifier for the server.

        :param dict server: The server announcement.  See ``Static Server
            Definitions`` in the configuration documentation for details about
            the structure and contents.

        :return IServer: The object-y representation of the server described
            by the given announcement.
        """
        assert isinstance(server_id, bytes)
        gm_verifier = create_grid_manager_verifier(
            self.storage_client_config.grid_manager_keys,
            [SignedCertificate.load(StringIO(json.dumps(data))) for data in server["ann"].get("grid-manager-certificates", [])],
            "pub-{}".format(str(server_id, "ascii")).encode("ascii"),  # server_id is v0-<key> not pub-v0-key .. for reasons?
        )

        if self._should_we_use_http(self.node_config, server["ann"]):
            s = HTTPNativeStorageServer(
                server_id,
                server["ann"],
                grid_manager_verifier=gm_verifier,
                default_connection_handlers=self._default_connection_handlers,
                tor_provider=self._tor_provider
            )
            s.on_status_changed(lambda _: self._got_connection())
            return s

        handler_overrides = server.get("connections", {})
        s = NativeStorageServer(
            server_id,
            server["ann"],
            self._tub_maker,
            handler_overrides,
            self.node_config,
            self.storage_client_config,
            gm_verifier,
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
            serverid,
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

    def _should_ignore_announcement(self, server_id, ann):
        """
        Determine whether a new storage announcement should be discarded or used
        to update our collection of storage servers.

        :param bytes server_id: The unique identifier for the storage server
            which made the announcement.

        :param dict ann: The announcement.

        :return bool: ``True`` if the announcement should be ignored,
            ``False`` if it should be used to update our local storage server
            state.
        """
        # Let local static configuration always override any announcement for
        # a particular server.
        if server_id in self._static_server_ids:
            log.msg(format="ignoring announcement for static server '%(id)s'",
                    id=server_id,
                    facility="tahoe.storage_broker", umid="AlxzqA",
                    level=log.UNUSUAL)
            return True

        try:
            old = self.servers[server_id]
        except KeyError:
            # We don't know anything about this server.  Let's use the
            # announcement to change that.
            return False
        else:
            # Determine if this announcement is at all difference from the
            # announcement we already have for the server.  If it is the same,
            # we don't need to change anything.
            return old.get_announcement() == ann

    def _got_announcement(self, key_s, ann):
        """
        This callback is given to the introducer and called any time an
        announcement is received which has a valid signature and does not have
        a sequence number less than or equal to a previous sequence number
        seen for that server by that introducer.

        Note sequence numbers are not considered between different introducers
        so if we use more than one introducer it is possible for them to
        deliver us stale announcements in some cases.
        """
        precondition(isinstance(key_s, bytes), key_s)
        precondition(key_s.startswith(b"v0-"), key_s)
        precondition(ann["service-name"] == "storage", ann["service-name"])
        server_id = key_s

        if self._should_ignore_announcement(server_id, ann):
            return

        s = self._make_storage_server(
            server_id,
            {u"ann": ann},
        )

        try:
            old = self.servers.pop(server_id)
        except KeyError:
            pass
        else:
            # It's a replacement, get rid of the old one.
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
        for dsc in list(self.servers.values()):
            dsc.try_to_connect()

    def get_servers_for_psi(self, peer_selection_index, for_upload=False):
        """
        :param for_upload: used to determine if we should include any
        servers that are invalid according to Grid Manager
        processing. When for_upload is True and we have any Grid
        Manager keys configured, any storage servers with invalid or
        missing certificates will be excluded.
        """
        # return a list of server objects (IServers)
        assert self.permute_peers == True
        connected_servers = self.get_connected_servers()
        preferred_servers = frozenset(s for s in connected_servers if s.get_longname() in self.preferred_peers)
        if for_upload:
            # print("upload processing: {}".format([srv.upload_permitted() for srv in connected_servers]))
            connected_servers = [
                srv
                for srv in connected_servers
                if srv.upload_permitted()
            ]

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
        for s in list(self.servers.values()):
            if isinstance(s, NativeStorageServer):
                if serverid == s.get_tubid():
                    return s
        return StubServer(serverid)

@implementer(IDisplayableServer)
class StubServer(object):
    def __init__(self, serverid):
        assert isinstance(serverid, bytes)
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


def _parse_announcement(server_id: bytes, furl: bytes, ann: dict) -> tuple[str, bytes, bytes, bytes, bytes]:
    """
    Parse the furl and announcement, return:

        (nickname, permutation_seed, tubid, short_description, long_description)
    """
    m = re.match(br'pb://(\w+)@', furl)
    assert m, furl
    tubid_s = m.group(1).lower()
    tubid = base32.a2b(tubid_s)
    if "permutation-seed-base32" in ann:
        seed = ann["permutation-seed-base32"]
        if isinstance(seed, str):
            seed = seed.encode("utf-8")
        ps = base32.a2b(seed)
    elif re.search(br'^v0-[0-9a-zA-Z]{52}$', server_id):
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
    if server_id.startswith(b"v0-"):
        # remove v0- prefix from abbreviated name
        short_description = server_id[3:3+8]
    else:
        short_description = server_id[:8]
    nickname = ann.get("nickname", "")

    return (nickname, permutation_seed, tubid, short_description, long_description)


@implementer(IFoolscapStorageServer)
@attr.s(frozen=True)
class _FoolscapStorage(object):
    """
    Abstraction for connecting to a storage server exposed via Foolscap.
    """
    nickname = attr.ib()
    permutation_seed = attr.ib()
    tubid = attr.ib()

    storage_server = attr.ib(validator=provides(IStorageServer))

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
             "grid-manager-certificates": [..],
            }

        *nickname* and *grid-manager-certificates* are optional.

        The furl will be a Unicode string on Python 3; on Python 2 it will be
        either a native (bytes) string or a Unicode string.
        """
        (nickname, permutation_seed, tubid, short_description, long_description) = _parse_announcement(server_id, furl.encode("utf-8"), ann)
        return cls(
            nickname=nickname,
            permutation_seed=permutation_seed,
            tubid=tubid,
            storage_server=storage_server,
            furl=furl.encode("utf-8"),
            short_description=short_description,
            long_description=long_description,
        )

    def connect_to(self, tub, got_connection):
        return tub.connectTo(self._furl, got_connection)


@implementer(IFoolscapStorageServer)
@define
class _NullStorage(object):
    """
    Abstraction for *not* communicating with a storage server of a type with
    which we can't communicate.
    """
    nickname = ""
    permutation_seed = hashlib.sha256(b"").digest()
    tubid = hashlib.sha256(b"").digest()
    storage_server = None

    lease_seed = hashlib.sha256(b"").digest()

    name = "<unsupported>"
    longname: str = "<storage with unsupported protocol>"

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


class AnnouncementNotMatched(Exception):
    """
    A storage server announcement wasn't matched by any of the locally enabled
    plugins.
    """


@attr.s(auto_exc=True)
class MissingPlugin(Exception):
    """
    A particular plugin was requested but is missing
    """

    plugin_name = attr.ib()

    def __str__(self):
        return "Missing plugin '{}'".format(self.plugin_name)


def _storage_from_foolscap_plugin(node_config, config, announcement, get_rref):
    """
    Construct an ``IStorageServer`` from the most locally-preferred plugin
    that is offered in the given announcement.

    :param allmydata.node._Config node_config: The node configuration to
        pass to the plugin.

    :param dict announcement: The storage announcement for the storage
        server we should build
    """
    storage_options = announcement.get(u"storage-options", [])
    plugins = config.get_configured_storage_plugins()

    # for every storage-option that we have enabled locally (in order
    # of preference), see if the announcement asks for such a thing.
    # if it does, great: we return that storage-client
    # otherwise we've run out of options...

    for options in storage_options:
        try:
            plugin = plugins[options[u"name"]]
        except KeyError:
            # we didn't configure this kind of plugin locally, so
            # consider the next announced option
            continue

        furl = options[u"storage-server-FURL"]
        return furl, plugin.get_storage_client(
            node_config,
            options,
            get_rref,
        )

    # none of the storage options in the announcement are configured
    # locally; we can't make a storage-client.
    plugin_names = ", ".join(sorted(option["name"] for option in storage_options))
    raise AnnouncementNotMatched(plugin_names)


def _available_space_from_version(version):
    if version is None:
        return None
    protocol_v1_version = version.get(b'http://allmydata.org/tahoe/protocols/storage/v1', BytesKeyDict())
    available_space = protocol_v1_version.get(b'available-space')
    if available_space is None:
        available_space = protocol_v1_version.get(b'maximum-immutable-share-size', None)
    return available_space


def _make_storage_system(
        node_config: _Config,
        config: StorageClientConfig,
        ann: dict,
        server_id: bytes,
        get_rref: Callable[[], Optional[IRemoteReference]],
) -> IFoolscapStorageServer:
    """
    Create an object for interacting with the storage server described by
    the given announcement.

    :param node_config: The node configuration to pass to any configured
        storage plugins.

    :param config: Configuration specifying desired storage client behavior.

    :param ann: The storage announcement from the storage server we are meant
        to communicate with.

    :param server_id: The unique identifier for the server.

    :param get_rref: A function which returns a remote reference to the
        server-side object which implements this storage system, if one is
        available (otherwise None).

    :return: An object enabling communication via Foolscap with the server
        which generated the announcement.
    """
    unmatched = None
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
            get_rref,
        )
    except AnnouncementNotMatched as e:
        # show a more-specific error to the user for this server
        # (Note this will only be shown if the server _doesn't_ offer
        # anonymous service, which will match below)
        unmatched = _NullStorage('{}: missing plugin "{}"'.format(server_id.decode("utf8"), str(e)))
    else:
        return _FoolscapStorage.from_announcement(
            server_id,
            furl,
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
        storage_server = _StorageServer(get_rref=get_rref)
        return _FoolscapStorage.from_announcement(
            server_id,
            furl,
            ann,
            storage_server,
        )

    # Nothing matched so we can't talk to this server. (There should
    # not be a way to get here without this local being valid)
    assert unmatched is not None, "Expected unmatched plugin error"
    return unmatched


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
    """

    VERSION_DEFAULTS = UnicodeKeyDict({
        "http://allmydata.org/tahoe/protocols/storage/v1" :
        UnicodeKeyDict({ "maximum-immutable-share-size": 2**32 - 1,
          "maximum-mutable-share-size": 2*1000*1000*1000, # maximum prior to v1.9.2
          "tolerates-immutable-read-overrun": False,
          "delete-mutable-shares-with-zero-length-writev": False,
          "available-space": None,
          }),
        "application-version": "unknown: no get_version()",
        })

    def __init__(self, server_id, ann, tub_maker, handler_overrides, node_config, config=None,
                 grid_manager_verifier=None):
        service.MultiService.__init__(self)
        assert isinstance(server_id, bytes)
        self._server_id = server_id
        self.announcement = ann
        self._tub_maker = tub_maker
        self._handler_overrides = handler_overrides

        if config is None:
            config = StorageClientConfig()

        self._grid_manager_verifier = grid_manager_verifier

        self._storage = _make_storage_system(node_config, config, ann, self._server_id, self.get_rref)

        self.last_connect_time = None
        self.last_loss_time = None
        self._rref = None
        self._is_connected = False
        self._reconnector = None
        self._trigger_cb = None
        self._on_status_changed = ObserverList()

    def upload_permitted(self):
        """
        If our client is configured with Grid Manager public-keys, we will
        only upload to storage servers that have a currently-valid
        certificate signed by at least one of the Grid Managers we
        accept.

        :return: True if we should use this server for uploads, False
            otherwise.
        """
        # if we have no Grid Manager keys configured, choice is easy
        if self._grid_manager_verifier is None:
            return True
        return self._grid_manager_verifier()

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
        return "<NativeStorageServer for %r>" % self.get_name()
    def get_serverid(self):
        return self._server_id
    def get_version(self):
        if self._rref:
            return self._rref.version
        return None
    def get_announcement(self):
        return self.announcement

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
        return _available_space_from_version(version)

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

    def stop_connecting(self):
        # used when this descriptor has been superceded by another
        self._reconnector.stopConnecting()

    def try_to_connect(self):
        # used when the broker wants us to hurry up
        self._reconnector.reset()


@async_to_deferred
async def _pick_a_http_server(
        reactor,
        nurls: list[DecodedURL],
        request: Callable[[Any, DecodedURL], defer.Deferred[Any]]
) -> DecodedURL:
    """Pick the first server we successfully send a request to.

    Fires with ``None`` if no server was found, or with the ``DecodedURL`` of
    the first successfully-connected server.
    """
    queries = race([
        request(reactor, nurl).addCallback(lambda _, nurl=nurl: nurl)
        for nurl in nurls
    ])

    _, nurl = await queries
    return nurl


@implementer(IServer)
class HTTPNativeStorageServer(service.MultiService):
    """
    Like ``NativeStorageServer``, but for HTTP clients.

    The notion of being "connected" is less meaningful for HTTP; we just poll
    occasionally, and if we've succeeded at last poll, we assume we're
    "connected".
    """

    def __init__(self, server_id: bytes, announcement, default_connection_handlers: dict[str,str], reactor=reactor, grid_manager_verifier=None, tor_provider: Optional[TorProvider]=None):
        service.MultiService.__init__(self)
        assert isinstance(server_id, bytes)
        self._server_id = server_id
        self.announcement = announcement
        self._on_status_changed = ObserverList()
        self._reactor = reactor
        self._grid_manager_verifier = grid_manager_verifier
        self._storage_client_factory = StorageClientFactory(
            default_connection_handlers, tor_provider
        )

        furl = announcement["anonymous-storage-FURL"].encode("utf-8")
        (
            self._nickname,
            self._permutation_seed,
            self._tubid,
            self._short_description,
            self._long_description
        ) = _parse_announcement(server_id, furl, announcement)
        self._nurls = [
            DecodedURL.from_text(u)
            for u in announcement[ANONYMOUS_STORAGE_NURLS]
        ]
        self._istorage_server : Optional[_HTTPStorageServer] = None

        self._connection_status = connection_status.ConnectionStatus.unstarted()
        self._version = None
        self._last_connect_time = None
        self._connecting_deferred : Optional[defer.Deferred[object]]= None

    def get_permutation_seed(self):
        return self._permutation_seed

    def get_name(self):
        return self._short_description

    def get_longname(self):
        return self._long_description

    def get_tubid(self):
        return self._tubid

    def get_lease_seed(self):
        # Apparently this is what Foolscap version above does?!
        return self._tubid

    def get_foolscap_write_enabler_seed(self):
        return self._tubid

    def get_nickname(self):
        return self._nickname

    def on_status_changed(self, status_changed):
        """
        :param status_changed: a callable taking a single arg (the
            NativeStorageServer) that is notified when we become connected
        """
        return self._on_status_changed.subscribe(status_changed)

    def upload_permitted(self):
        """
        If our client is configured with Grid Manager public-keys, we will
        only upload to storage servers that have a currently-valid
        certificate signed by at least one of the Grid Managers we
        accept.

        :return: True if we should use this server for uploads, False
            otherwise.
        """
        # if we have no Grid Manager keys configured, choice is easy
        if self._grid_manager_verifier is None:
            return True
        return self._grid_manager_verifier()

    # Special methods used by copy.copy() and copy.deepcopy(). When those are
    # used in allmydata.immutable.filenode to copy CheckResults during
    # repair, we want it to treat the IServer instances as singletons, and
    # not attempt to duplicate them..
    def __copy__(self):
        return self

    def __deepcopy__(self, memodict):
        return self

    def __repr__(self):
        return "<HTTPNativeStorageServer for %r>" % self.get_name()

    def get_serverid(self):
        return self._server_id

    def get_version(self):
        return self._version

    def get_announcement(self):
        return self.announcement

    def get_connection_status(self):
        return self._connection_status

    def is_connected(self):
        return self._connection_status.connected

    def get_available_space(self):
        version = self.get_version()
        return _available_space_from_version(version)

    def start_connecting(self, trigger_cb):
        self._lc = LoopingCall(self._connect)
        self._lc.start(1, True)

    def _got_version(self, version):
        self._last_connect_time = time.time()
        self._version = version
        self._connection_status = connection_status.ConnectionStatus(
            True, "connected", [], self._last_connect_time, self._last_connect_time
        )
        self._on_status_changed.notify(self)

    def _failed_to_connect(self, reason):
        self._connection_status = connection_status.ConnectionStatus(
            False, f"failure: {reason}", [], self._last_connect_time, self._last_connect_time
        )
        self._on_status_changed.notify(self)

    def get_storage_server(self):
        """
        See ``IServer.get_storage_server``.
        """
        if self._connection_status.summary == "unstarted":
            return None
        return self._istorage_server

    def stop_connecting(self):
        self._lc.stop()
        if self._connecting_deferred is not None:
            self._connecting_deferred.cancel()

    def try_to_connect(self):
        self._connect()

    def _connect(self) -> defer.Deferred[object]:
        """
        Try to connect to a working storage server.

        If called while a previous ``_connect()`` is already running, it will
        just return the same ``Deferred``.

        ``LoopingCall.stop()`` doesn't cancel ``Deferred``s, unfortunately:
        https://github.com/twisted/twisted/issues/11814. Thus we want to store
        the ``Deferred`` so we can cancel it when necessary.

        We also want to return it so that loop iterations take it into account,
        and a new iteration doesn't start while we're in the middle of the
        previous one.
        """
        # Conceivably try_to_connect() was called on this before, in which case
        # we already are in the middle of connecting. So in that case just
        # return whatever is in progress:
        if self._connecting_deferred is not None:
            return self._connecting_deferred

        def done(_):
            self._connecting_deferred = None

        connecting = self._pick_server_and_get_version()
        # Set a short timeout since we're relying on this for server liveness.
        connecting = connecting.addTimeout(5, self._reactor).addCallbacks(
            self._got_version, self._failed_to_connect
        ).addBoth(done)
        self._connecting_deferred = connecting
        return connecting

    @async_to_deferred
    async def _pick_server_and_get_version(self):
        """
        Minimal implementation of connection logic: pick a server, get its
        version.  This doesn't deal with errors much, so as to minimize
        statefulness.  It does change ``self._istorage_server``, so possibly
        more refactoring would be useful to remove even that much statefulness.
        """
        async def get_istorage_server() -> _HTTPStorageServer:
            if self._istorage_server is not None:
                return self._istorage_server

            # We haven't selected a server yet, so let's do so.

            # TODO This is somewhat inefficient on startup: it takes two successful
            # version() calls before we are live talking to a server, it could only
            # be one. See https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3992

            @async_to_deferred
            async def request(reactor, nurl: DecodedURL):
                # Since we're just using this one off to check if the NURL
                # works, no need for persistent pool or other fanciness.
                pool = HTTPConnectionPool(reactor, persistent=False)
                pool.retryAutomatically = False
                storage_client = await self._storage_client_factory.create_storage_client(
                    nurl, reactor, pool
                )
                return await StorageClientGeneral(storage_client).get_version()

            nurl = await _pick_a_http_server(reactor, self._nurls, request)

            # If we've gotten this far, we've found a working NURL.
            storage_client = await self._storage_client_factory.create_storage_client(
                    nurl, cast(IReactorTime, reactor), None
            )
            self._istorage_server = _HTTPStorageServer.from_http_client(storage_client)
            return self._istorage_server

        try:
            storage_server = await get_istorage_server()

            # Get the version from the remote server.
            version = await storage_server.get_version()
            return version
        except Exception as e:
            log.msg(f"Failed to connect to a HTTP storage server: {e}", level=log.CURIOUS)
            raise

    def stopService(self):
        if self._connecting_deferred is not None:
            self._connecting_deferred.cancel()

        result = service.MultiService.stopService(self)
        if self._lc.running:
            self._lc.stop()
        self._failed_to_connect("shut down")

        if self._istorage_server is not None:
            client_shutting_down = self._istorage_server._http_client.shutdown()
            result.addCallback(lambda _: client_shutting_down)

        return result


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
        # Match the wire protocol, which requires 4-tuples for test vectors.
        wire_format_tw_vectors = {
            key: (
                [(start, length, b"eq", data) for (start, length, data) in value[0]],
                value[1],
                value[2],
            ) for (key, value) in tw_vectors.items()
        }
        return self._rref.callRemote(
            "slot_testv_and_readv_and_writev",
            storage_index,
            secrets,
            wire_format_tw_vectors,
            r_vector,
        )

    def advise_corrupt_share(
            self,
            share_type,
            storage_index,
            shnum,
            reason,
    ):
        return self._rref.callRemote(
            "advise_corrupt_share",
            share_type,
            storage_index,
            shnum,
            reason,
        ).addErrback(log.err, "Error from remote call to advise_corrupt_share")



@attr.s(hash=True)
class _FakeRemoteReference(object):
    """
    Emulate a Foolscap RemoteReference, calling a local object instead.
    """
    local_object = attr.ib(type=object)

    @defer.inlineCallbacks
    def callRemote(self, action, *args, **kwargs):
        try:
            result = yield getattr(self.local_object, action)(*args, **kwargs)
            defer.returnValue(result)
        except HTTPClientException as e:
            raise RemoteException((e.code, e.message, e.body))


@attr.s
class _HTTPBucketWriter(object):
    """
    Emulate a ``RIBucketWriter``, but use HTTP protocol underneath.
    """
    client = attr.ib(type=StorageClientImmutables)
    storage_index = attr.ib(type=bytes)
    share_number = attr.ib(type=int)
    upload_secret = attr.ib(type=bytes)
    finished = attr.ib(type=defer.Deferred[bool], factory=defer.Deferred)

    def abort(self):
        return self.client.abort_upload(self.storage_index, self.share_number,
                                        self.upload_secret)

    @defer.inlineCallbacks
    def write(self, offset, data):
        result = yield self.client.write_share_chunk(
            self.storage_index, self.share_number, self.upload_secret, offset, data
        )
        if result.finished:
            self.finished.callback(True)
        defer.returnValue(None)

    def close(self):
        # We're not _really_ closed until all writes have succeeded and we
        # finished writing all the data.
        return self.finished


def _ignore_404(failure: Failure) -> Optional[Failure]:
    """
    Useful for advise_corrupt_share(), since it swallows unknown share numbers
    in Foolscap.
    """
    if failure.check(HTTPClientException) and failure.value.code == http.NOT_FOUND:
        return None
    else:
        return failure


@attr.s(hash=True)
class _HTTPBucketReader(object):
    """
    Emulate a ``RIBucketReader``, but use HTTP protocol underneath.
    """
    client = attr.ib(type=StorageClientImmutables)
    storage_index = attr.ib(type=bytes)
    share_number = attr.ib(type=int)

    def read(self, offset, length):
        return self.client.read_share_chunk(
            self.storage_index, self.share_number, offset, length
        )

    def advise_corrupt_share(self, reason):
       return self.client.advise_corrupt_share(
           self.storage_index, self.share_number,
           str(reason, "utf-8", errors="backslashreplace")
       ).addErrback(_ignore_404)


# WORK IN PROGRESS, for now it doesn't actually implement whole thing.
@implementer(IStorageServer)  # type: ignore
@attr.s
class _HTTPStorageServer(object):
    """
    Talk to remote storage server over HTTP.
    """
    _http_client = attr.ib(type=StorageClient)

    @staticmethod
    def from_http_client(http_client: StorageClient) -> _HTTPStorageServer:
        """
        Create an ``IStorageServer`` from a HTTP ``StorageClient``.
        """
        return _HTTPStorageServer(http_client=http_client)

    def get_version(self) -> defer.Deferred[VersionMessage]:
        return StorageClientGeneral(self._http_client).get_version()

    @defer.inlineCallbacks
    def allocate_buckets(
            self,
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums,
            allocated_size,
            canary
    ):
        upload_secret = urandom(20)
        immutable_client = StorageClientImmutables(self._http_client)
        result = immutable_client.create(
            storage_index, sharenums, allocated_size, upload_secret, renew_secret,
            cancel_secret
        )
        result = yield result
        defer.returnValue(
            (result.already_have, {
                 share_num: _FakeRemoteReference(_HTTPBucketWriter(
                     client=immutable_client,
                     storage_index=storage_index,
                     share_number=share_num,
                     upload_secret=upload_secret
                 ))
                 for share_num in result.allocated
            })
        )

    @defer.inlineCallbacks
    def get_buckets(
            self,
            storage_index
    ):
        immutable_client = StorageClientImmutables(self._http_client)
        share_numbers = yield immutable_client.list_shares(
            storage_index
        )
        defer.returnValue({
            share_num: _FakeRemoteReference(_HTTPBucketReader(
                immutable_client, storage_index, share_num
            ))
            for share_num in share_numbers
        })

    @async_to_deferred
    async def add_lease(
        self,
        storage_index,
        renew_secret,
        cancel_secret
    ):
        client = StorageClientGeneral(self._http_client)
        try:
            await client.add_or_renew_lease(
                storage_index, renew_secret, cancel_secret
            )
        except ClientException as e:
            if e.code == http.NOT_FOUND:
                # Silently do nothing, as is the case for the Foolscap client
                return
            raise

    def advise_corrupt_share(
        self,
        share_type,
        storage_index,
        shnum,
        reason: bytes
    ):
        if share_type == b"immutable":
            client : Union[StorageClientImmutables, StorageClientMutables] = StorageClientImmutables(self._http_client)
        elif share_type == b"mutable":
            client = StorageClientMutables(self._http_client)
        else:
            raise ValueError("Unknown share type")
        return client.advise_corrupt_share(
            storage_index, shnum, str(reason, "utf-8", errors="backslashreplace")
        ).addErrback(_ignore_404)

    @defer.inlineCallbacks
    def slot_readv(self, storage_index, shares, readv):
        mutable_client = StorageClientMutables(self._http_client)
        pending_reads = {}
        reads = {}
        # If shares list is empty, that means list all shares, so we need
        # to do a query to get that.
        if not shares:
            shares = yield mutable_client.list_shares(storage_index)

        # Start all the queries in parallel:
        for share_number in shares:
            share_reads = defer.gatherResults(
                [
                    mutable_client.read_share_chunk(
                        storage_index, share_number, offset, length
                    )
                    for (offset, length) in readv
                ]
            )
            pending_reads[share_number] = share_reads

        # Wait for all the queries to finish:
        for share_number, pending_result in pending_reads.items():
            reads[share_number] = yield pending_result

        return reads

    @defer.inlineCallbacks
    def slot_testv_and_readv_and_writev(
            self,
            storage_index,
            secrets,
            tw_vectors,
            r_vector,
    ):
        mutable_client = StorageClientMutables(self._http_client)
        we_secret, lr_secret, lc_secret = secrets
        client_tw_vectors = {}
        for share_num, (test_vector, data_vector, new_length) in tw_vectors.items():
            client_test_vectors = [
                TestVector(offset=offset, size=size, specimen=specimen)
                for (offset, size, specimen) in test_vector
            ]
            client_write_vectors = [
                WriteVector(offset=offset, data=data) for (offset, data) in data_vector
            ]
            client_tw_vectors[share_num] = TestWriteVectors(
                test_vectors=client_test_vectors,
                write_vectors=client_write_vectors,
                new_length=new_length
            )
        client_read_vectors = [
            ReadVector(offset=offset, size=size)
            for (offset, size) in r_vector
        ]
        try:
            client_result = yield mutable_client.read_test_write_chunks(
                storage_index, we_secret, lr_secret, lc_secret, client_tw_vectors,
                client_read_vectors,
            )
        except ClientException as e:
            if e.code == http.UNAUTHORIZED:
                raise RemoteException("Unauthorized write, possibly you passed the wrong write enabler?")
            raise
        return (client_result.success, client_result.reads)
