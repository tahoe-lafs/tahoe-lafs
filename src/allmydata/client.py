
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
from allmydata.storage import StorageServer
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
    WEB_ALLOW_LOCAL_ACCESS_FILE = "webport_allow_localfile"
    INTRODUCER_FURL_FILE = "introducer.furl"
    MY_FURL_FILE = "myself.furl"
    SUICIDE_PREVENTION_HOTLINE_FILE = "suicide_prevention_hotline"
    SIZELIMIT_FILE = "sizelimit"
    PUSH_TO_OURSELVES_FILE = "push_to_ourselves"

    # we're pretty narrow-minded right now
    OLDEST_SUPPORTED_VERSION = allmydata.__version__

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.logSource="Client"
        self.my_furl = None
        self.introducer_client = None
        self.init_storage()
        self.init_options()
        self.add_service(Uploader())
        self.add_service(Downloader())
        self.add_service(VirtualDrive())
        try:
            webport = open(os.path.join(self.basedir, self.WEBPORTFILE),
                           "r").read().strip() # strports string
        except EnvironmentError:
            pass # absent or unreadable webport file
        else:
            self.init_web(webport)

        INTRODUCER_FURL_FILE = os.path.join(self.basedir,
                                            self.INTRODUCER_FURL_FILE)
        self.introducer_furl = open(INTRODUCER_FURL_FILE, "r").read().strip()

        hotline_file = os.path.join(self.basedir,
                                    self.SUICIDE_PREVENTION_HOTLINE_FILE)
        if os.path.exists(hotline_file):
            self.log("hotline file noticed, starting timer")
            hotline = TimerService(1.0, self._check_hotline, hotline_file)
            hotline.setServiceParent(self)

    def init_storage(self):
        storedir = os.path.join(self.basedir, self.STOREDIR)
        sizelimit = None

        try:
            data = open(os.path.join(self.basedir, self.SIZELIMIT_FILE),
                        "r").read().strip()
        except EnvironmentError:
            pass # absent or unreadable sizelimit file
        else:
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
        NOSTORAGE_FILE = os.path.join(self.basedir, "debug_no_storage")
        no_storage = os.path.exists(NOSTORAGE_FILE)
        self.add_service(StorageServer(storedir, sizelimit, no_storage))

    def init_options(self):
        self.push_to_ourselves = None
        filename = os.path.join(self.basedir, self.PUSH_TO_OURSELVES_FILE)
        if os.path.exists(filename):
            self.push_to_ourselves = True

    def init_web(self, webport):
        # this must be called after the VirtualDrive is attached
        ws = WebishServer(webport)
        ws.allow_local_access(os.path.exists(os.path.join(self.basedir,
                              self.WEB_ALLOW_LOCAL_ACCESS_FILE)))
        self.add_service(ws)
        vd = self.getServiceNamed("vdrive")
        startfile = os.path.join(self.basedir, "start.html")
        d = vd.when_private_root_available()
        d.addCallback(ws.create_start_html, startfile)


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
        try:
            my_old_furl = open(os.path.join(self.basedir, self.MY_FURL_FILE),
                               "r").read().strip()
        except EnvironmentError:
            pass # absent or unreadable myfurl file
        else:
            sturdy = SturdyRef(my_old_furl)
            my_old_name = sturdy.name

        self.my_furl = self.tub.registerReference(self, my_old_name)
        open(os.path.join(self.basedir, self.MY_FURL_FILE),
             "w").write(self.my_furl + "\n")

        ic = IntroducerClient(self.tub, self.introducer_furl, self.my_furl)
        self.introducer_client = ic
        ic.setServiceParent(self)

        self.register_control()

    def register_control(self):
        c = ControlServer()
        c.setServiceParent(self)
        control_url = self.tub.registerReference(c)
        control_furl_file = os.path.join(self.basedir, "control.furl")
        open(control_furl_file, "w").write(control_url + "\n")
        os.chmod(control_furl_file, 0600)


    def remote_get_versions(self):
        return str(allmydata.__version__), str(self.OLDEST_SUPPORTED_VERSION)

    def remote_get_service(self, name):
        if name in ("storageserver",):
            return self.getServiceNamed(name)
        raise RuntimeError("I am unwilling to give you service %s" % name)


    def get_all_peerids(self):
        if not self.introducer_client:
            return []
        return self.introducer_client.get_all_peerids()

    def get_permuted_peers(self, key, include_myself=True):
        """
        @return: list of (permuted-peerid, peerid, connection,)
        """
        results = []
        for peerid, connection in self.introducer_client.get_all_peers():
            assert isinstance(peerid, str)
            if not include_myself and peerid == self.nodeid:
                self.log("get_permuted_peers: removing myself from the list")
                continue
            permuted = bytes_to_long(sha.new(key + peerid).digest())
            results.append((permuted, peerid, connection))
        results.sort()
        return results

    def get_push_to_ourselves(self):
        return self.push_to_ourselves

    def get_encoding_parameters(self):
        if not self.introducer_client:
            return None
        return self.introducer_client.encoding_parameters

    def connected_to_introducer(self):
        if self.introducer_client:
            return self.introducer_client.connected_to_introducer()
        return False
