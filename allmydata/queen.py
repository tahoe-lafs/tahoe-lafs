
from foolscap import Tub, Referenceable
from twisted.application import service
from twisted.python import log
import os.path

class Roster(service.MultiService, Referenceable):
    pass

class Queen(service.MultiService):
    CERTFILE = "queen.pem"

    def __init__(self):
        service.MultiService.__init__(self)
        if os.path.exists(self.CERTFILE):
            self.tub = Tub(certData=open(self.CERTFILE, "rb").read())
        else:
            self.tub = Tub()
            f = open(self.CERTFILE, "wb")
            f.write(self.tub.getCertData())
            f.close()
        self.urls = {}
        r = Roster()
        r.setServiceParent(self)
        #self.urls["roster"] = self.tub.registerReference(r, "roster")

    def startService(self):
        service.MultiService.startService(self)
        log.msg("queen running")
        #log.msg(" roster is at %s" % self.urls["roster"])
