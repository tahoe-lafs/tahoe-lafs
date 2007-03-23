
import os.path
from foolscap import Referenceable, DeadReferenceError
from foolscap.eventual import eventually
from twisted.application import service
from twisted.python import log
from twisted.internet.error import ConnectionLost, ConnectionDone
from allmydata.util import idlib
from zope.interface import implements
from allmydata.interfaces import RIQueenRoster, RIIntroducer
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
        self.gvd_root = None

    def set_gvd_root(self, root):
        self.gvd_root = root

    def remote_get_global_vdrive(self):
        return self.gvd_root



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

