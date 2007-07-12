
import os.path
from allmydata import node
from allmydata.dirnode import VirtualDriveServer
from allmydata.introducer import Introducer


class IntroducerAndVdrive(node.Node):
    PORTNUMFILE = "introducer.port"
    NODETYPE = "introducer"
    VDRIVEDIR = "vdrive"
    ENCODING_PARAMETERS_FILE = "encoding_parameters"
    DEFAULT_K, DEFAULT_DESIRED, DEFAULT_N = 3, 7, 10

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.urls = {}
        self.read_encoding_parameters()

    def tub_ready(self):
        i = Introducer()
        r = self.add_service(i)
        self.urls["introducer"] = self.tub.registerReference(r, "introducer")
        self.log(" introducer is at %s" % self.urls["introducer"])
        f = open(os.path.join(self.basedir, "introducer.furl"), "w")
        f.write(self.urls["introducer"] + "\n")
        f.close()

        vdrive_dir = os.path.join(self.basedir, self.VDRIVEDIR)
        vds = self.add_service(VirtualDriveServer(vdrive_dir))
        vds_furl = self.tub.registerReference(vds, "vdrive")
        vds.set_furl(vds_furl)
        self.urls["vdrive"] = vds_furl
        self.log(" vdrive is at %s" % self.urls["vdrive"])
        f = open(os.path.join(self.basedir, "vdrive.furl"), "w")
        f.write(self.urls["vdrive"] + "\n")
        f.close()

        encoding_parameters = self.read_encoding_parameters()
        i.set_encoding_parameters(encoding_parameters)

    def read_encoding_parameters(self):
        k, desired, n = self.DEFAULT_K, self.DEFAULT_DESIRED, self.DEFAULT_N
        PARAM_FILE = os.path.join(self.basedir, self.ENCODING_PARAMETERS_FILE)
        if os.path.exists(PARAM_FILE):
            f = open(PARAM_FILE, "r")
            data = f.read().strip()
            f.close()
            k,desired,n = data.split()
            k = int(k); desired = int(desired); n = int(n)
        return k, desired, n

