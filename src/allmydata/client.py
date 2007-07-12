
import os, sha, stat, time, re
from foolscap import Referenceable, SturdyRef
from zope.interface import implements
from allmydata.interfaces import RIClient
from allmydata import node

from twisted.internet import reactor
from twisted.application.internet import TimerService
from twisted.python import log

import allmydata
from allmydata.Crypto.Util.number import bytes_to_long
from allmydata.storageserver import StorageServer
from allmydata.upload import Uploader
from allmydata.download import Downloader
from allmydata.webish import WebishServer
from allmydata.control import ControlServer
from allmydata.introducer import IntroducerClient
from allmydata.vdrive import VirtualDrive

class Client(node.Node, Referenceable):
    implements(RIClient)
    PORTNUMFILE = "client.port"
    STOREDIR = 'storage'
    NODETYPE = "client"
    WEBPORTFILE = "webport"
    INTRODUCER_FURL_FILE = "introducer.furl"
    MY_FURL_FILE = "myself.furl"
    SUICIDE_PREVENTION_HOTLINE_FILE = "suicide_prevention_hotline"
    SIZELIMIT_FILE = "sizelimit"

    # we're pretty narrow-minded right now
    OLDEST_SUPPORTED_VERSION = allmydata.__version__

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.my_furl = None
        self.introducer_client = None
        self.init_storage()
        self.add_service(Uploader())
        self.add_service(Downloader())
        self.add_service(VirtualDrive())
        WEBPORTFILE = os.path.join(self.basedir, self.WEBPORTFILE)
        if os.path.exists(WEBPORTFILE):
            f = open(WEBPORTFILE, "r")
            webport = f.read().strip() # strports string
            f.close()
            self.add_service(WebishServer(webport))

        INTRODUCER_FURL_FILE = os.path.join(self.basedir,
                                            self.INTRODUCER_FURL_FILE)
        f = open(INTRODUCER_FURL_FILE, "r")
        self.introducer_furl = f.read().strip()
        f.close()

        hotline_file = os.path.join(self.basedir,
                                    self.SUICIDE_PREVENTION_HOTLINE_FILE)
        if os.path.exists(hotline_file):
            hotline = TimerService(1.0, self._check_hotline, hotline_file)
            hotline.setServiceParent(self)

    def init_storage(self):
        storedir = os.path.join(self.basedir, self.STOREDIR)
        sizelimit = None
        SIZELIMIT_FILE = os.path.join(self.basedir,
                                      self.SIZELIMIT_FILE)
        if os.path.exists(SIZELIMIT_FILE):
            f = open(SIZELIMIT_FILE, "r")
            data = f.read().strip()
            f.close()
            m = re.match(r"^(\d+)([kKmMgG]?[bB]?)$", data)
            if not m:
                log.msg("SIZELIMIT_FILE contains unparseable value %s" % data)
            else:
                number, suffix = m.groups()
                suffix = suffix.upper()
                if suffix.endswith("B"):
                    suffix = suffix[:-1]
                multiplier = {"": 1,
                              "K": 1000,
                              "M": 1000 * 1000,
                              "G": 1000 * 1000 * 1000,
                              }[suffix]
                sizelimit = int(number) * multiplier
        self.add_service(StorageServer(storedir, sizelimit))

    def _check_hotline(self, hotline_file):
        if os.path.exists(hotline_file):
            mtime = os.stat(hotline_file)[stat.ST_MTIME]
            if mtime > time.time() - 10.0:
                return
        self.log("hotline missing or too old, shutting down")
        reactor.stop()

    def tub_ready(self):
        self.log("tub_ready")

        my_old_name = None
        MYSELF_FURL_PATH = os.path.join(self.basedir, self.MY_FURL_FILE)
        if os.path.exists(MYSELF_FURL_PATH):
            my_old_furl = open(MYSELF_FURL_PATH, "r").read().strip()
            sturdy = SturdyRef(my_old_furl)
            my_old_name = sturdy.name

        self.my_furl = self.tub.registerReference(self, my_old_name)
        f = open(MYSELF_FURL_PATH, "w")
        f.write(self.my_furl)
        f.close()

        ic = IntroducerClient(self.tub, self.introducer_furl, self.my_furl)
        self.introducer_client = ic
        ic.setServiceParent(self)

        self.register_control()

    def register_control(self):
        c = ControlServer()
        c.setServiceParent(self)
        control_url = self.tub.registerReference(c)
        f = open("control.furl", "w")
        f.write(control_url + "\n")
        f.close()
        os.chmod("control.furl", 0600)


    def remote_get_versions(self):
        return str(allmydata.__version__), str(self.OLDEST_SUPPORTED_VERSION)

    def remote_get_service(self, name):
        if name in ("storageserver",):
            return self.getServiceNamed(name)
        raise RuntimeError("I am unwilling to give you service %s" % name)


    def get_all_peerids(self):
        if not self.introducer_client:
            return []
        return self.introducer_client.connections.iterkeys()

    def get_permuted_peers(self, key):
        """
        @return: list of (permuted-peerid, peerid, connection,)
        """
        results = []
        for peerid, connection in self.introducer_client.connections.iteritems():
            assert isinstance(peerid, str)
            permuted = bytes_to_long(sha.new(key + peerid).digest())
            results.append((permuted, peerid, connection))
        results.sort()
        return results

    def get_encoding_parameters(self):
        if not self.introducer_client:
            return None
        return self.introducer_client.encoding_parameters

    def connected_to_introducer(self):
        if self.introducer_client:
            return self.introducer_client.connected_to_introducer()
        return False
