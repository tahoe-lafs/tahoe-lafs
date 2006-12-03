
import sha
from foolscap import Referenceable
from twisted.application import service
from twisted.python import log
from zope.interface import implements
from allmydata.interfaces import RIClient
from allmydata import node

from twisted.internet import defer

from allmydata.storageserver import StorageServer
from allmydata.upload import Uploader
from allmydata.util import idlib

class Client(node.Node, Referenceable):
    implements(RIClient)
    CERTFILE = "client.pem"
    PORTNUMFILE = "client.port"
    STOREDIR = 'storage'
    NODETYPE = "client"

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.queen = None # self.queen is either None or a RemoteReference
        self.all_peers = set()
        self.connections = {}
        self.add_service(StorageServer(self.STOREDIR))
        self.add_service(Uploader())
        self.queen_pburl = None
        self.queen_connector = None

    def tub_ready(self):
        self.my_pburl = self.tub.registerReference(self)
        self.maybe_connect_to_queen()

    def set_queen_pburl(self, queen_pburl):
        self.queen_pburl = queen_pburl
        self.maybe_connect_to_queen()

    def maybe_connect_to_queen(self):
        if not self.running:
            return
        if not self.my_pburl:
            return
        if self.queen_connector:
            return
        if not self.queen_pburl:
            log.msg("no queen_pburl, cannot connect")
            return
        self.queen_connector = self.tub.connectTo(self.queen_pburl,
                                                  self._got_queen)

    def stopService(self):
        if self.queen_connector:
            self.queen_connector.stopConnecting()
            self.queen_connector = None
        return service.MultiService.stopService(self)

    def _got_queen(self, queen):
        log.msg("connected to queen")
        self.queen = queen
        queen.notifyOnDisconnect(self._lost_queen)
        queen.callRemote("hello",
                         nodeid=self.nodeid, node=self, pburl=self.my_pburl)

    def _lost_queen(self):
        log.msg("lost connection to queen")
        self.queen = None

    def remote_get_service(self, name):
        return self.getServiceNamed(name)

    def remote_add_peers(self, new_peers):
        for nodeid, pburl in new_peers:
            if nodeid == self.nodeid:
                continue
            log.msg("adding peer %s" % idlib.b2a(nodeid))
            if nodeid in self.all_peers:
                log.msg("weird, I already had an entry for them")
            self.all_peers.add(nodeid)
            if nodeid not in self.connections:
                d = self.tub.getReference(pburl)
                def _got_reference(ref):
                    log.msg("connected to %s" % idlib.b2a(nodeid))
                    if nodeid in self.all_peers:
                        self.connections[nodeid] = ref
                d.addCallback(_got_reference)

    def remote_lost_peers(self, lost_peers):
        for nodeid in lost_peers:
            log.msg("lost peer %s" % idlib.b2a(nodeid))
            if nodeid in self.all_peers:
                self.all_peers.remove(nodeid)
            else:
                log.msg("weird, I didn't have an entry for them")
            if nodeid in self.connections:
                del self.connections[nodeid]

    def get_remote_service(self, nodeid, servicename):
        if nodeid not in self.connections:
            return defer.fail(IndexError("no connection to that peer"))
        d = self.connections[nodeid].callRemote("get_service",
                                                name=servicename)
        return d


    def permute_peerids(self, key, max_count=None):
        # TODO: eventually reduce memory consumption by doing an insertion
        # sort of at most max_count elements
        results = []
        for nodeid in self.all_peers:
            permuted = sha.new(key + nodeid).digest()
            results.append((permuted, nodeid))
        results.sort()
        results = [r[1] for r in results]
        if max_count is None:
            return results
        return results[:max_count]
