
from foolscap import Tub, Referenceable
from twisted.application import service
from twisted.python import log
import os.path
from allmydata.util.iputil import get_local_ip_for

class Roster(service.MultiService, Referenceable):
    def remote_hello(self, urls):
        print "contact from %s" % urls

class Queen(service.MultiService):
    CERTFILE = "queen.pem"
    PORTNUMFILE = "queen.port"
    AUTHKEYSFILE = "authorized_keys"

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
        if os.path.exists(self.AUTHKEYSFILE):
            from allmydata import manhole
            m = manhole.AuthorizedKeysManhole(8021, self.AUTHKEYSFILE)
            m.setServiceParent(self)
            log.msg("AuthorizedKeysManhole listening on 8021")

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

