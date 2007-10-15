
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
from allmydata.checker import Checker
from allmydata.control import ControlServer
from allmydata.introducer import IntroducerClient
from allmydata.vdrive import VirtualDrive
from allmydata.util import hashutil, idlib, testutil

class Client(node.Node, Referenceable, testutil.PollMixin):
    implements(RIClient)
    PORTNUMFILE = "client.port"
    STOREDIR = 'storage'
    NODETYPE = "client"
    SUICIDE_PREVENTION_HOTLINE_FILE = "suicide_prevention_hotline"

    # we're pretty narrow-minded right now
    OLDEST_SUPPORTED_VERSION = allmydata.__version__

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.logSource="Client"
        self.my_furl = None
        self.introducer_client = None
        self.init_secret()
        self.init_storage()
        self.init_options()
        self.add_service(Uploader())
        self.add_service(Downloader())
        self.add_service(Checker())
        self.add_service(VirtualDrive())
        webport = self.get_config("webport")
        if webport:
            self.init_web(webport) # strports string

        self.introducer_furl = self.get_config("introducer.furl", required=True)

        hotline_file = os.path.join(self.basedir,
                                    self.SUICIDE_PREVENTION_HOTLINE_FILE)
        if os.path.exists(hotline_file):
            age = time.time() - os.stat(hotline_file)[stat.ST_MTIME]
            self.log("hotline file noticed (%ds old), starting timer" % age)
            hotline = TimerService(1.0, self._check_hotline, hotline_file)
            hotline.setServiceParent(self)

    def init_secret(self):
        def make_secret():
            return idlib.b2a(os.urandom(16)) + "\n"
        secret_s = self.get_or_create_config("secret", make_secret,
                                             filemode=0600)
        self._secret = idlib.a2b(secret_s)

    def init_storage(self):
        storedir = os.path.join(self.basedir, self.STOREDIR)
        sizelimit = None

        data = self.get_config("sizelimit")
        if data:
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
        no_storage = self.get_config("debug_no_storage") is not None
        self.add_service(StorageServer(storedir, sizelimit, no_storage))

    def init_options(self):
        self.push_to_ourselves = None
        if self.get_config("push_to_ourselves") is not None:
            self.push_to_ourselves = True

    def init_web(self, webport):
        from allmydata.webish import WebishServer
        # this must be called after the VirtualDrive is attached
        ws = WebishServer(webport)
        if self.get_config("webport_allow_localfile") is not None:
            ws.allow_local_access(True)
        self.add_service(ws)
        vd = self.getServiceNamed("vdrive")
        startfile = os.path.join(self.basedir, "start.html")
        nodeurl_file = os.path.join(self.basedir, "node.url")
        d = vd.when_private_root_available()
        d.addCallback(ws.create_start_html, startfile, nodeurl_file)


    def _check_hotline(self, hotline_file):
        if os.path.exists(hotline_file):
            mtime = os.stat(hotline_file)[stat.ST_MTIME]
            if mtime > time.time() - 20.0:
                return
            else:
                self.log("hotline file too old, shutting down")
        else:
            self.log("hotline file missing, shutting down")
        reactor.stop()

    def tub_ready(self):
        self.log("tub_ready")

        # we use separate get_config/write_config here because we want to
        # update the connection hints each time.
        my_old_name = None
        my_old_furl = self.get_config("myself.furl")
        if my_old_furl is not None:
            sturdy = SturdyRef(my_old_furl)
            my_old_name = sturdy.name

        self.my_furl = self.tub.registerReference(self, my_old_name)
        self.write_config("myself.furl", self.my_furl + "\n")

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

    def remote_get_nodeid(self):
        return self.nodeid

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

    def get_renewal_secret(self):
        return hashutil.my_renewal_secret_hash(self._secret)

    def get_cancel_secret(self):
        return hashutil.my_cancel_secret_hash(self._secret)

    def debug_wait_for_client_connections(self, num_clients):
        """Return a Deferred that fires (with None) when we have connections
        to the given number of peers. Useful for tests that set up a
        temporary test network and need to know when it is safe to proceed
        with an upload or download."""
        def _check():
            current_clients = list(self.get_all_peerids())
            return len(current_clients) >= num_clients
        d = self.poll(_check, 0.5)
        d.addCallback(lambda res: None)
        return d

