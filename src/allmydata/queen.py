
import os.path
from foolscap import Referenceable, DeadReferenceError
from foolscap.eventual import eventually
from twisted.application import service
from twisted.python import log
from twisted.internet.error import ConnectionLost, ConnectionDone
from allmydata.util import idlib
from zope.interface import implements
from allmydata.interfaces import RIQueenRoster
from allmydata import node
from allmydata.filetable import GlobalVirtualDrive


def sendOnly(call, methname, *args, **kwargs):
    d = call(methname, *args, **kwargs)
    def _trap(f):
        f.trap(DeadReferenceError, ConnectionLost, ConnectionDone)
    d.addErrback(_trap)

class Roster(service.MultiService, Referenceable):
    implements(RIQueenRoster)

    def __init__(self):
        service.MultiService.__init__(self)
        self.phonebook = {}
        self.connections = {}
        self.gvd_root = None

    def set_gvd_root(self, root):
        self.gvd_root = root

    def remote_hello(self, nodeid, node, pburl):
        log.msg("roster: contact from %s" % idlib.b2a(nodeid))
        self.phonebook[nodeid] = pburl
        self.connections[nodeid] = node
        eventually(self._educate_the_new_peer,
                   nodeid, node, list(self.phonebook.items()))
        eventually(self._announce_new_peer,
                   nodeid, pburl, list(self.connections.values()))
        node.notifyOnDisconnect(self._lost_node, nodeid)
        return self.gvd_root

    def _educate_the_new_peer(self, nodeid, node, new_peers):
        log.msg("roster: educating %s (%d)" % (idlib.b2a(nodeid)[:4], len(new_peers)))
        node.callRemote("add_peers", new_peers=new_peers)

    def _announce_new_peer(self, new_nodeid, new_node_pburl, peers):
        log.msg("roster: announcing %s to everybody (%d)" % (idlib.b2a(new_nodeid)[:4], len(peers)))
        for targetnode in peers:
            targetnode.callRemote("add_peers",
                                  new_peers=[(new_nodeid, new_node_pburl)])

    def _lost_node(self, nodeid):
        log.msg("roster: lost contact with %s" % idlib.b2a(nodeid))
        del self.phonebook[nodeid]
        del self.connections[nodeid]
        eventually(self._announce_lost_peer, nodeid)

    def _announce_lost_peer(self, lost_nodeid):
        for targetnode in self.connections.values():
            # use sendOnly, because if they go away then we assume it's
            # because they crashed and they've lost all their peer
            # connections anyways.
            sendOnly(targetnode.callRemote, "lost_peers",
                     lost_peers=[lost_nodeid])



class Queen(node.Node):
    CERTFILE = "queen.pem"
    PORTNUMFILE = "queen.port"
    NODETYPE = "queen"

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.gvd = self.add_service(GlobalVirtualDrive(basedir))
        self.urls = {}

    def tub_ready(self):
        r = self.add_service(Roster())
        self.urls["roster"] = self.tub.registerReference(r, "roster")
        self.log(" roster is at %s" % self.urls["roster"])
        f = open(os.path.join(self.basedir, "roster_pburl"), "w")
        f.write(self.urls["roster"] + "\n")
        f.close()
        r.set_gvd_root(self.gvd.get_root())

