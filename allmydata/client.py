
import os.path
from foolscap import Tub, Referenceable
from twisted.application import service
from twisted.python import log
from allmydata.util.iputil import get_local_ip_for

from twisted.internet import reactor
from twisted.internet.base import BlockingResolver
reactor.installResolver(BlockingResolver())

class Storage(service.MultiService, Referenceable):
    pass

class Client(service.MultiService):
    CERTFILE = "client.pem"

    def __init__(self, queen_pburl):
        service.MultiService.__init__(self)
        self.queen_pburl = queen_pburl
        if os.path.exists(self.CERTFILE):
            self.tub = Tub(certData=open(self.CERTFILE, "rb").read())
        else:
            self.tub = Tub()
            f = open(self.CERTFILE, "wb")
            f.write(self.tub.getCertData())
            f.close()
        self.tub.setServiceParent(self)
        self.queen = None # self.queen is either None or a RemoteReference
        self.urls = {}

    def _setup_services(self, local_ip):
        portnum = 0
        l = self.tub.listenOn("tcp:%d" % portnum)
        self.tub.setLocation("%s:%d" % (local_ip, l.getPortnum()))
        s = Storage()
        s.setServiceParent(self)
        self.urls["storage"] = self.tub.registerReference(s, "storage")

    def startService(self):
        # note: this class can only be started and stopped once.
        service.MultiService.startService(self)
        d = get_local_ip_for()
        d.addCallback(self._setup_services)
        if self.queen_pburl:
            self.connector = self.tub.connectTo(self.queen_pburl,
                                                self._got_queen)
        else:
            log.msg("no queen_pburl, cannot connect")

    def stopService(self):
        if self.queen_pburl:
            self.connector.stopConnecting()
        service.MultiService.stopService(self)

    def _got_queen(self, queen):
        log.msg("connected to queen")
        self.queen = queen
        queen.notifyOnDisconnect(self._lost_queen)
        queen.callRemote("hello", urls=self.urls)

    def _lost_queen(self):
        log.msg("lost connection to queen")
        self.queen = None
