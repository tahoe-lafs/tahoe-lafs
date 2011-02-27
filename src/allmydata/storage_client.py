
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


import time
from zope.interface import implements, Interface
from foolscap.api import eventually
from allmydata.interfaces import IStorageBroker
from allmydata.util import idlib, log
from allmydata.util.assertutil import precondition
from allmydata.util.rrefutil import add_version_to_remote_reference
from allmydata.util.hashutil import sha1

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

class StorageFarmBroker:
    implements(IStorageBroker)
    """I live on the client, and know about storage servers. For each server
    that is participating in a grid, I either maintain a connection to it or
    remember enough information to establish a connection to it on demand.
    I'm also responsible for subscribing to the IntroducerClient to find out
    about new servers as they are announced by the Introducer.
    """
    def __init__(self, tub, permute_peers):
        self.tub = tub
        assert permute_peers # False not implemented yet
        self.permute_peers = permute_peers
        # self.servers maps serverid -> IServer, and keeps track of all the
        # storage servers that we've heard about. Each descriptor manages its
        # own Reconnector, and will give us a RemoteReference when we ask
        # them for it.
        self.servers = {}
        self.introducer_client = None

    # these two are used in unit tests
    def test_add_rref(self, serverid, rref):
        s = NativeStorageServer(serverid, {})
        s.rref = rref
        self.servers[serverid] = s

    def test_add_server(self, serverid, s):
        self.servers[serverid] = s

    def use_introducer(self, introducer_client):
        self.introducer_client = ic = introducer_client
        ic.subscribe_to("storage", self._got_announcement)

    def _got_announcement(self, serverid, ann_d):
        precondition(isinstance(serverid, str), serverid)
        precondition(len(serverid) == 20, serverid)
        assert ann_d["service-name"] == "storage"
        old = self.servers.get(serverid)
        if old:
            if old.get_announcement() == ann_d:
                return # duplicate
            # replacement
            del self.servers[serverid]
            old.stop_connecting()
            # now we forget about them and start using the new one
        dsc = NativeStorageServer(serverid, ann_d)
        self.servers[serverid] = dsc
        dsc.start_connecting(self.tub, self._trigger_connections)
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
        def _permuted(server):
            seed = server.get_permutation_seed()
            return sha1(peer_selection_index + seed).digest()
        return sorted(self.get_connected_servers(), key=_permuted)

    def get_all_serverids(self):
        serverids = set()
        serverids.update(self.servers.keys())
        return frozenset(serverids)

    def get_connected_servers(self):
        return frozenset([s for s in self.get_known_servers()
                          if s.get_rref()])

    def get_known_servers(self):
        return sorted(self.servers.values(), key=lambda s: s.get_serverid())

    def get_nickname_for_serverid(self, serverid):
        if serverid in self.servers:
            return self.servers[serverid].get_nickname()
        return None


class IServer(Interface):
    """I live in the client, and represent a single server."""
    def start_connecting(tub, trigger_cb):
        pass
    def get_nickname():
        pass
    def get_rref():
        pass

class NativeStorageServer:
    """I hold information about a storage server that we want to connect to.
    If we are connected, I hold the RemoteReference, their host address, and
    the their version information. I remember information about when we were
    last connected too, even if we aren't currently connected.

    @ivar announcement_time: when we first heard about this service
    @ivar last_connect_time: when we last established a connection
    @ivar last_loss_time: when we last lost a connection

    @ivar version: the server's versiondict, from the most recent announcement
    @ivar nickname: the server's self-reported nickname (unicode), same

    @ivar rref: the RemoteReference, if connected, otherwise None
    @ivar remote_host: the IAddress, if connected, otherwise None
    """
    implements(IServer)

    VERSION_DEFAULTS = {
        "http://allmydata.org/tahoe/protocols/storage/v1" :
        { "maximum-immutable-share-size": 2**32,
          "tolerates-immutable-read-overrun": False,
          "delete-mutable-shares-with-zero-length-writev": False,
          },
        "application-version": "unknown: no get_version()",
        }

    def __init__(self, serverid, ann_d, min_shares=1):
        self.serverid = serverid
        self._tubid = serverid
        self.announcement = ann_d
        self.min_shares = min_shares

        self.serverid_s = idlib.shortnodeid_b2a(self.serverid)
        self.announcement_time = time.time()
        self.last_connect_time = None
        self.last_loss_time = None
        self.remote_host = None
        self.rref = None
        self._reconnector = None
        self._trigger_cb = None

    def __repr__(self):
        return "<NativeStorageServer for %s>" % self.name()
    def get_serverid(self):
        return self._tubid
    def get_permutation_seed(self):
        return self._tubid
    def get_version(self):
        if self.rref:
            return self.rref.version
        return None
    def name(self): # keep methodname short
        return self.serverid_s
    def longname(self):
        return idlib.nodeid_b2a(self._tubid)
    def get_lease_seed(self):
        return self._tubid
    def get_foolscap_write_enabler_seed(self):
        return self._tubid

    def get_nickname(self):
        return self.announcement["nickname"].decode("utf-8")
    def get_announcement(self):
        return self.announcement
    def get_remote_host(self):
        return self.remote_host
    def get_last_connect_time(self):
        return self.last_connect_time
    def get_last_loss_time(self):
        return self.last_loss_time
    def get_announcement_time(self):
        return self.announcement_time

    def start_connecting(self, tub, trigger_cb):
        furl = self.announcement["FURL"]
        self._trigger_cb = trigger_cb
        self._reconnector = tub.connectTo(furl, self._got_connection)

    def _got_connection(self, rref):
        lp = log.msg(format="got connection to %(name)s, getting versions",
                     name=self.name(),
                     facility="tahoe.storage_broker", umid="coUECQ")
        if self._trigger_cb:
            eventually(self._trigger_cb)
        default = self.VERSION_DEFAULTS
        d = add_version_to_remote_reference(rref, default)
        d.addCallback(self._got_versioned_service, lp)
        d.addErrback(log.err, format="storageclient._got_connection",
                     name=self.name(), umid="Sdq3pg")

    def _got_versioned_service(self, rref, lp):
        log.msg(format="%(name)s provided version info %(version)s",
                name=self.name(), version=rref.version,
                facility="tahoe.storage_broker", umid="SWmJYg",
                level=log.NOISY, parent=lp)

        self.last_connect_time = time.time()
        self.remote_host = rref.getPeer()
        self.rref = rref
        rref.notifyOnDisconnect(self._lost)

    def get_rref(self):
        return self.rref

    def _lost(self):
        log.msg(format="lost connection to %(name)s", name=self.name(),
                facility="tahoe.storage_broker", umid="zbRllw")
        self.last_loss_time = time.time()
        self.rref = None
        self.remote_host = None

    def stop_connecting(self):
        # used when this descriptor has been superceded by another
        self._reconnector.stopConnecting()

    def try_to_connect(self):
        # used when the broker wants us to hurry up
        self._reconnector.reset()

class UnknownServerTypeError(Exception):
    pass
