
import os, stat, time, re
from allmydata.interfaces import RIStorageServer
from allmydata import node

from twisted.internet import reactor
from twisted.application.internet import TimerService
from foolscap.logging import log

import allmydata
from allmydata.storage import StorageServer
from allmydata.upload import Uploader
from allmydata.download import Downloader
from allmydata.checker import Checker
from allmydata.offloaded import Helper
from allmydata.control import ControlServer
from allmydata.introducer import IntroducerClient
from allmydata.util import hashutil, base32, testutil
from allmydata.filenode import FileNode
from allmydata.dirnode import NewDirectoryNode
from allmydata.mutable import MutableFileNode
from allmydata.stats import StatsProvider
from allmydata.interfaces import IURI, INewDirectoryURI, \
     IReadonlyNewDirectoryURI, IFileURI, IMutableFileURI

KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

class Client(node.Node, testutil.PollMixin):
    PORTNUMFILE = "client.port"
    STOREDIR = 'storage'
    NODETYPE = "client"
    SUICIDE_PREVENTION_HOTLINE_FILE = "suicide_prevention_hotline"

    # we're pretty narrow-minded right now
    OLDEST_SUPPORTED_VERSION = allmydata.__version__

    # this is a tuple of (needed, desired, total, max_segment_size). 'needed'
    # is the number of shares required to reconstruct a file. 'desired' means
    # that we will abort an upload unless we can allocate space for at least
    # this many. 'total' is the total number of shares created by encoding.
    # If everybody has room then this is is how many we will upload.
    DEFAULT_ENCODING_PARAMETERS = {"k": 3,
                                   "happy": 7,
                                   "n": 10,
                                   "max_segment_size": 1*MiB,
                                   }

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.logSource="Client"
        self.nickname = self.get_config("nickname")
        if self.nickname is None:
            self.nickname = "<unspecified>"
        self.init_introducer_client()
        self.init_stats_provider()
        self.init_lease_secret()
        self.init_storage()
        self.init_control()
        run_helper = self.get_config("run_helper")
        if run_helper:
            self.init_helper()
        helper_furl = self.get_config("helper.furl")
        self.add_service(Uploader(helper_furl))
        self.add_service(Downloader())
        self.add_service(Checker())
        # ControlServer and Helper are attached after Tub startup

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

    def init_introducer_client(self):
        self.introducer_furl = self.get_config("introducer.furl", required=True)
        ic = IntroducerClient(self.tub, self.introducer_furl,
                              self.nickname,
                              str(allmydata.__version__),
                              str(self.OLDEST_SUPPORTED_VERSION))
        self.introducer_client = ic
        ic.setServiceParent(self)
        # nodes that want to upload and download will need storage servers
        ic.subscribe_to("storage")

    def init_stats_provider(self):
        gatherer_furl = self.get_config('stats_gatherer.furl')
        if gatherer_furl:
            self.stats_provider = StatsProvider(self, gatherer_furl)
            self.add_service(self.stats_provider)
        else:
            self.stats_provider = None

    def init_lease_secret(self):
        def make_secret():
            return base32.b2a(os.urandom(hashutil.CRYPTO_VAL_SIZE)) + "\n"
        secret_s = self.get_or_create_private_config("secret", make_secret)
        self._lease_secret = base32.a2b(secret_s)

    def init_storage(self):
        # should we run a storage server (and publish it for others to use)?
        provide_storage = (self.get_config("no_storage") is None)
        if not provide_storage:
            return
        readonly_storage = (self.get_config("readonly_storage") is not None)

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
        discard_storage = self.get_config("debug_discard_storage") is not None
        ss = StorageServer(storedir, sizelimit,
                           discard_storage, readonly_storage,
                           self.stats_provider)
        self.add_service(ss)
        d = self.when_tub_ready()
        # we can't do registerReference until the Tub is ready
        def _publish(res):
            furl_file = os.path.join(self.basedir, "private", "storage.furl")
            furl = self.tub.registerReference(ss, furlFile=furl_file)
            ri_name = RIStorageServer.__remote_name__
            self.introducer_client.publish(furl, "storage", ri_name)
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init", level=log.BAD)

    def init_control(self):
        d = self.when_tub_ready()
        def _publish(res):
            c = ControlServer()
            c.setServiceParent(self)
            control_url = self.tub.registerReference(c)
            self.write_private_config("control.furl", control_url + "\n")
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init", level=log.BAD)

    def init_helper(self):
        d = self.when_tub_ready()
        def _publish(self):
            h = Helper(os.path.join(self.basedir, "helper"))
            h.setServiceParent(self)
            # TODO: this is confusing. BASEDIR/private/helper.furl is created
            # by the helper. BASEDIR/helper.furl is consumed by the client
            # who wants to use the helper. I like having the filename be the
            # same, since that makes 'cp' work smoothly, but the difference
            # between config inputs and generated outputs is hard to see.
            helper_furlfile = os.path.join(self.basedir,
                                           "private", "helper.furl")
            self.tub.registerReference(h, furlFile=helper_furlfile)
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init", level=log.BAD)

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

    def get_all_peerids(self):
        return self.introducer_client.get_all_peerids()

    def get_permuted_peers(self, service_name, key):
        """
        @return: list of (peerid, connection,)
        """
        assert isinstance(service_name, str)
        assert isinstance(key, str)
        return self.introducer_client.get_permuted_peers(service_name, key)

    def get_encoding_parameters(self):
        return self.DEFAULT_ENCODING_PARAMETERS
        p = self.introducer_client.encoding_parameters # a tuple
        # TODO: make the 0.7.1 introducer publish a dict instead of a tuple
        params = {"k": p[0],
                  "happy": p[1],
                  "n": p[2],
                  }
        if len(p) == 3:
            # TODO: compatibility with 0.7.0 Introducer that doesn't specify
            # segment_size
            self.log("Introducer didn't provide max_segment_size, using 1MiB",
                     level=log.UNUSUAL)
            params["max_segment_size"] = 1*MiB
        return params

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

    def upload(self, uploadable):
        uploader = self.getServiceNamed("uploader")
        return uploader.upload(uploadable)

    def list_uploads(self):
        uploader = self.getServiceNamed("uploader")
        return uploader.list_uploads()

    def list_downloads(self):
        downloader = self.getServiceNamed("downloader")
        return downloader.list_downloads()
