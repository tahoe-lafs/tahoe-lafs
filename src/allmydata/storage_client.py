
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


import re
import time
import hashlib
import json
import attr
from datetime import datetime

from zope.interface import implementer
from twisted.internet import defer
from twisted.application import service
from eliot import (
    log_call,
)
from foolscap.api import eventually
from allmydata.interfaces import (
    IStorageBroker,
    IDisplayableServer,
    IServer,
    IStorageServer,
)
from allmydata.crypto import ed25519
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


@implementer(IStorageBroker)
class StorageFarmBroker(service.MultiService):
    """I live on the client, and know about storage servers. For each server
    that is participating in a grid, I either maintain a connection to it or
    remember enough information to establish a connection to it on demand.
    I'm also responsible for subscribing to the IntroducerClient to find out
    about new servers as they are announced by the Introducer.
    """
    def __init__(self, permute_peers, tub_maker, preferred_peers=(), grid_manager_keys=[]):
        service.MultiService.__init__(self)
        assert permute_peers # False not implemented yet
        self.permute_peers = permute_peers
        self._tub_maker = tub_maker
        self.preferred_peers = preferred_peers
        self._grid_manager_keys = grid_manager_keys

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
                pass
            else:
                self._static_server_ids.add(server_id)
                self.servers[server_id] = storage_server
                storage_server.setServiceParent(self)
                storage_server.start_connecting(self._trigger_connections)

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
            self._grid_manager_keys,
            [],  # XXX FIXME? need grid_manager_certs too?
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
        s = NativeStorageServer(serverid, ann.copy(), self._tub_maker, {}, [], [])
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
        # this is called by NativeStorageClient when it is connected
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

        grid_manager_certs = ann.get("grid-manager-certificates", [])
        print("certs for {}: {}".format(key_s, grid_manager_certs))
        s = NativeStorageServer(server_id, ann, self._tub_maker, {}, self._grid_manager_keys, grid_manager_certs)
        s.on_status_changed(lambda _: self._got_connection())
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
        for s in self.servers.values():
            if isinstance(s, NativeStorageServer):
                if serverid == s._tubid:
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


def parse_grid_manager_data(gm_data):
    """
    :param gm_data: some data that might be JSON that might be a valid
       Grid Manager Certificate

    :returns: json data of a valid Grid Manager certificate, or an
        exception if the data is not valid.
    """

    required_keys = allowed_keys = [
        'certificate',
        'signature',
    ]

    js = json.loads(gm_data)
    for k in js.keys():
        if k not in allowed_keys:
            raise ValueError(
                "Grid Manager certificate JSON may not contain '{}'".format(
                    k,
                )
            )
    for k in required_keys:
        if k not in js:
            raise ValueError(
                "Grid Manager certificate JSON must contain '{}'".format(
                    k,
                )
            )
    return js


def validate_grid_manager_certificate(gm_key, alleged_cert):
    """
    :param gm_key: a VerifyingKey instance, a Grid Manager's public
        key.

    :param cert: dict with "certificate" and "signature" keys, where
        "certificate" contains a JSON-serialized certificate for a Storage
        Server (comes from a Grid Manager).

    :return: False if the signature is invalid or the certificate is
        expired.
    """
    try:
        gm_key.verify(
            base32.a2b(alleged_cert['signature'].encode('ascii')),
            alleged_cert['certificate'].encode('ascii'),
        )
    except ed25519.BadSignature:
        return False
    # signature is valid; now we can load the actual data
    cert = json.loads(alleged_cert['certificate'])
    now = datetime.utcnow()
    expires = datetime.utcfromtimestamp(cert['expires'])
    # cert_pubkey = keyutil.parse_pubkey(cert['public_key'].encode('ascii'))
    if expires < now:
        return False  # certificate is expired
    return True


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

    def __init__(self, server_id, ann, tub_maker, handler_overrides, grid_manager_keys, grid_manager_certs):
        # print("CREATE {}: {}".format(server_id, grid_manager_certs))
        service.MultiService.__init__(self)
        assert isinstance(server_id, str)
        self._server_id = server_id
        self.announcement = ann
        self._tub_maker = tub_maker
        self._handler_overrides = handler_overrides

        # XXX we should validate as much as we can about the
        # certificates right now -- the only thing we HAVE to be lazy
        # about is the expiry, which should be checked before any
        # possible upload...

        # any public-keys which the user has configured (if none, it
        # means use any storage servers)
        self._grid_manager_keys = grid_manager_keys
        # print("keys: {}".format(self._grid_manager_keys))
        # any storage-certificates that this storage-server included
        # in its announcement
        self._grid_manager_certificates = grid_manager_certs
        # print("certs: {}".format(self._grid_manager_certificates))

        assert "anonymous-storage-FURL" in ann, ann
        furl = str(ann["anonymous-storage-FURL"])
        m = re.match(r'pb://(\w+)@', furl)
        assert m, furl
        tubid_s = m.group(1).lower()
        self._tubid = base32.a2b(tubid_s)
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
        self._permutation_seed = ps

        assert server_id
        self._long_description = server_id
        if server_id.startswith("v0-"):
            # remove v0- prefix from abbreviated name
            self._short_description = server_id[3:3+8]
        else:
            self._short_description = server_id[:8]

        self.last_connect_time = None
        self.last_loss_time = None
        self.remote_host = None
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
        # print("upload permitted? {}".format(self._server_id))
        # if we have no Grid Manager keys configured, choice is easy
        if not self._grid_manager_keys:
            # print("{} no grid manager keys at all (so yes)".format(self._server_id))
            return True

        # XXX probably want to cache the answer to this? (ignoring
        # that for now because certificates expire, so .. slightly
        # more complex)
        if not self._grid_manager_certificates:
            # print("{} no grid-manager certificates {} (so no)".format(self._server_id, self._grid_manager_certificates))
            return False
        for gm_key in self._grid_manager_keys:
            for cert in self._grid_manager_certificates:
                if validate_grid_manager_certificate(gm_key, cert):
                    # print("valid: {}\n{}".format(gm_key, cert))
                    return True
        # print("didn't validate {} keys".format(len(self._grid_manager_keys)))
        return False


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
    def get_permutation_seed(self):
        return self._permutation_seed
    def get_version(self):
        if self._rref:
            return self._rref.version
        return None
    def get_name(self): # keep methodname short
        # TODO: decide who adds [] in the short description. It should
        # probably be the output side, not here.
        return self._short_description
    def get_longname(self):
        return self._long_description
    def get_lease_seed(self):
        return self._tubid
    def get_foolscap_write_enabler_seed(self):
        return self._tubid

    def get_nickname(self):
        return self.announcement.get("nickname", "")
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
        furl = str(self.announcement["anonymous-storage-FURL"])
        self._trigger_cb = trigger_cb
        self._reconnector = self._tub.connectTo(furl, self._got_connection)

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
        # Pass in an accessor for our _rref attribute.  The value of the
        # attribute may change over time as connections are lost and
        # re-established.  The _StorageServer should always be able to get the
        # most up-to-date value.
        return _StorageServer(get_rref=self.get_rref)

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
