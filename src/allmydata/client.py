
import os, sha, stat, time, re
from foolscap import Referenceable, SturdyRef
from zope.interface import implements
from allmydata.interfaces import RIClient
from allmydata import node

from twisted.internet import reactor
from twisted.application.internet import TimerService
from twisted.python import log

import allmydata
from allmydata.storage import StorageServer
from allmydata.upload import Uploader
from allmydata.download import Downloader
from allmydata.checker import Checker
from allmydata.offloaded import Helper
from allmydata.control import ControlServer
from allmydata.introducer import IntroducerClient
from allmydata.util import hashutil, idlib, testutil
from allmydata.filenode import FileNode
from allmydata.dirnode import NewDirectoryNode
from allmydata.mutable import MutableFileNode
from allmydata.interfaces import IURI, INewDirectoryURI, \
     IReadonlyNewDirectoryURI, IFileURI, IMutableFileURI

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
        self.init_lease_secret()
        self.init_storage()
        self.init_options()
        helper_furl = self.get_config("helper.furl")
        self.add_service(Uploader(helper_furl))
        self.add_service(Downloader())
        self.add_service(Checker())
        # ControlServer and Helper are attached after Tub startup

        self.introducer_furl = self.get_config("introducer.furl", required=True)

        hotline_file = os.path.join(self.basedir,
                                    self.SUICIDE_PREVENTION_HOTLINE_FILE)
        if os.path.exists(hotline_file):
            age = time.time() - os.stat(hotline_file)[stat.ST_MTIME]
            self.log("hotline file noticed (%ds old), starting timer" % age)
            hotline = TimerService(1.0, self._check_hotline, hotline_file)
            hotline.setServiceParent(self)

        webport = self.get_config("webport")
        if webport:
            self.init_web(webport) # strports string

    def init_lease_secret(self):
        def make_secret():
            return idlib.b2a(os.urandom(hashutil.CRYPTO_VAL_SIZE)) + "\n"
        secret_s = self.get_or_create_private_config("secret", make_secret)
        self._lease_secret = idlib.a2b(secret_s)

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
        self.log("init_web(webport=%s)", args=(webport,))

        from allmydata.webish import WebishServer
        nodeurl_path = os.path.join(self.basedir, "node.url")
        ws = WebishServer(webport, nodeurl_path)
        if self.get_config("webport_allow_localfile") is not None:
            ws.allow_local_access(True)
        self.add_service(ws)

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
        node.Node.tub_ready(self)

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
        self.register_helper()

    def register_control(self):
        c = ControlServer()
        c.setServiceParent(self)
        control_url = self.tub.registerReference(c)
        self.write_private_config("control.furl", control_url + "\n")

    def register_helper(self):
        run_helper = self.get_config("run_helper")
        if not run_helper:
            return
        h = Helper(os.path.join(self.basedir, "helper"))
        h.setServiceParent(self)
        # TODO: this is confusing. BASEDIR/private/helper.furl is created by
        # the helper. BASEDIR/helper.furl is consumed by the client who wants
        # to use the helper. I like having the filename be the same, since
        # that makes 'cp' work smoothly, but the difference between config
        # inputs and generated outputs is hard to see.
        helper_furlfile = os.path.join(self.basedir, "private", "helper.furl")
        self.tub.registerReference(h, furlFile=helper_furlfile)

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
            permuted = sha.new(key + peerid).digest()
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
        return hashutil.my_renewal_secret_hash(self._lease_secret)

    def get_cancel_secret(self):
        return hashutil.my_cancel_secret_hash(self._lease_secret)

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


    # these four methods are the primitives for creating filenodes and
    # dirnodes. The first takes a URI and produces a filenode or (new-style)
    # dirnode. The other three create brand-new filenodes/dirnodes.

    def create_node_from_uri(self, u):
        # this returns synchronously.
        u = IURI(u)
        if IReadonlyNewDirectoryURI.providedBy(u):
            # new-style read-only dirnodes
            return NewDirectoryNode(self).init_from_uri(u)
        if INewDirectoryURI.providedBy(u):
            # new-style dirnodes
            return NewDirectoryNode(self).init_from_uri(u)
        if IFileURI.providedBy(u):
            # CHK
            return FileNode(u, self)
        assert IMutableFileURI.providedBy(u), u
        return MutableFileNode(self).init_from_uri(u)

    def create_empty_dirnode(self):
        n = NewDirectoryNode(self)
        d = n.create()
        d.addCallback(lambda res: n)
        return d

    def create_mutable_file(self, contents=""):
        n = MutableFileNode(self)
        d = n.create(contents)
        d.addCallback(lambda res: n)
        return d

    def upload(self, uploadable, options={}):
        uploader = self.getServiceNamed("uploader")
        return uploader.upload(uploadable, options)

