
import os.path
from foolscap import Referenceable
from foolscap.eventual import eventually
from twisted.application import service
from twisted.python import log
from allmydata.util import idlib
from zope.interface import implements
from allmydata.interfaces import RIQueenRoster
from allmydata import node

class Roster(service.MultiService, Referenceable):
    implements(RIQueenRoster)

    def __init__(self):
        service.MultiService.__init__(self)
        self.phonebook = {}
        self.connections = {}

    def remote_hello(self, nodeid, node, pburl):
        log.msg("contact from %s" % idlib.b2a(nodeid))
        eventually(self._educate_the_new_peer, node)
        eventually(self._announce_new_peer, nodeid, pburl)
        self.phonebook[nodeid] = pburl
        self.connections[nodeid] = node
        node.notifyOnDisconnect(self._lost_node, nodeid)

    def _educate_the_new_peer(self, node):
        node.callRemote("add_peers", new_peers=list(self.phonebook.items()))

    def _announce_new_peer(self, new_nodeid, new_node_pburl):
        for targetnode in self.connections.values():
            targetnode.callRemote("add_peers",
                                  new_peers=[(new_nodeid, new_node_pburl)])

    def _lost_node(self, nodeid):
        log.msg("roster: lost contact with %s" % idlib.b2a(nodeid))
        del self.phonebook[nodeid]
        del self.connections[nodeid]
        eventually(self._announce_lost_peer, nodeid)

    def _announce_lost_peer(self, lost_nodeid):
        for targetnode in self.connections.values():
            targetnode.callRemote("lost_peers", lost_peers=[lost_nodeid])



class Queen(node.Node):
    CERTFILE = "queen.pem"
    PORTNUMFILE = "queen.port"
    NODETYPE = "queen"

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.urls = {}

    def tub_ready(self):
        r = self.add_service(Roster())
        self.urls["roster"] = self.tub.registerReference(r, "roster")
        self.log(" roster is at %s" % self.urls["roster"])
        f = open(os.path.join(self.basedir, "roster_pburl"), "w")
        f.write(self.urls["roster"] + "\n")
        f.close()


