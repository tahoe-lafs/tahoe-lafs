
import os.path
from allmydata import node
from allmydata.filetable import VirtualDriveServer
from allmydata.introducer import Introducer


class IntroducerAndVdrive(node.Node):
    PORTNUMFILE = "introducer.port"
    NODETYPE = "introducer"

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.urls = {}

    def tub_ready(self):
        r = self.add_service(Introducer())
        self.urls["introducer"] = self.tub.registerReference(r, "introducer")
        self.log(" introducer is at %s" % self.urls["introducer"])
        f = open(os.path.join(self.basedir, "introducer.furl"), "w")
        f.write(self.urls["introducer"] + "\n")
        f.close()

        vds = self.add_service(VirtualDriveServer(self.basedir))
        self.urls["vdrive"] = self.tub.registerReference(vds, "vdrive")
        self.log(" vdrive is at %s" % self.urls["vdrive"])
        f = open(os.path.join(self.basedir, "vdrive.furl"), "w")
        f.write(self.urls["vdrive"] + "\n")
        f.close()

