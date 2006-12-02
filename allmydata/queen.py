
from foolscap import Tub, Referenceable
from foolscap.eventual import eventually
from twisted.application import service
from twisted.python import log
import os.path
from allmydata.util.iputil import get_local_ip_for
from allmydata.util import idlib
from zope.interface import implements
from allmydata.interfaces import RIQueenRoster

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
        log.msg("lost contact with %s" % idlib.b2a(nodeid))
        del self.phonebook[nodeid]
        del self.connections[nodeid]
        eventually(self._announce_lost_peer, nodeid)

    def _announce_lost_peer(self, lost_nodeid):
        for targetnode in self.connections.values():
            targetnode.callRemote("lost_peers", lost_peers=[lost_nodeid])



class Queen(service.MultiService):
    CERTFILE = "queen.pem"
    PORTNUMFILE = "queen.port"

    def __init__(self):
        service.MultiService.__init__(self)
        if os.path.exists(self.CERTFILE):
            self.tub = Tub(certData=open(self.CERTFILE, "rb").read())
        else:
            self.tub = Tub()
            f = open(self.CERTFILE, "wb")
            f.write(self.tub.getCertData())
            f.close()
        portnum = 0
        if os.path.exists(self.PORTNUMFILE):
            portnum = int(open(self.PORTNUMFILE, "r").read())
        self.tub.listenOn("tcp:%d" % portnum)
        # we must wait until our service has started before we can find out
        # our IP address and thus do tub.setLocation, and we can't register
        # any services with the Tub until after that point
        self.tub.setServiceParent(self)
        self.urls = {}

        AUTHKEYSFILEBASE = "authorized_keys."
        for f in os.listdir("."):
            if f.startswith(AUTHKEYSFILEBASE):
                portnum = int(f[len(AUTHKEYSFILEBASE):])
                from allmydata import manhole
                m = manhole.AuthorizedKeysManhole(portnum, f)
                m.setServiceParent(self)
                log.msg("AuthorizedKeysManhole listening on %d" % portnum)

    def _setup_tub(self, local_ip):
        l = self.tub.getListeners()[0]
        portnum = l.getPortnum()
        self.tub.setLocation("%s:%d" % (local_ip, portnum))
        if not os.path.exists(self.PORTNUMFILE):
            # record which port we're listening on, so we can grab the same
            # one next time
            f = open(self.PORTNUMFILE, "w")
            f.write("%d\n" % portnum)
            f.close()
        self.tub.setLocation("%s:%d" % (local_ip, l.getPortnum()))
        return local_ip

    def _setup_services(self, local_ip):
        r = Roster()
        r.setServiceParent(self)
        self.urls["roster"] = self.tub.registerReference(r, "roster")
        log.msg(" roster is at %s" % self.urls["roster"])

    def startService(self):
        # note: this class can only be started and stopped once.
        service.MultiService.startService(self)
        log.msg("queen running")
        d = get_local_ip_for()
        d.addCallback(self._setup_tub)
        d.addCallback(self._setup_services)

