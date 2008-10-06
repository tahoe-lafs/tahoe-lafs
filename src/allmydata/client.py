
import os, stat, time, re, weakref
from allmydata.interfaces import RIStorageServer
from allmydata import node

from zope.interface import implements
from twisted.internet import reactor
from twisted.application.internet import TimerService
from foolscap import Referenceable
from foolscap.logging import log
from pycryptopp.publickey import rsa

import allmydata
from allmydata.storage import StorageServer
from allmydata.immutable.upload import Uploader
from allmydata.immutable.download import Downloader
from allmydata.immutable.filenode import FileNode, LiteralFileNode
from allmydata.offloaded import Helper
from allmydata.control import ControlServer
from allmydata.introducer.client import IntroducerClient
from allmydata.util import hashutil, base32, testutil
from allmydata.uri import LiteralFileURI
from allmydata.dirnode import NewDirectoryNode
from allmydata.mutable.node import MutableFileNode, MutableWatcher
from allmydata.stats import StatsProvider
from allmydata.interfaces import IURI, INewDirectoryURI, IStatsProducer, \
     IReadonlyNewDirectoryURI, IFileURI, IMutableFileURI, RIStubClient

KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

class StubClient(Referenceable):
    implements(RIStubClient)

def _make_secret():
    return base32.b2a(os.urandom(hashutil.CRYPTO_VAL_SIZE)) + "\n"

class Client(node.Node, testutil.PollMixin):
    implements(IStatsProducer)

    PORTNUMFILE = "client.port"
    STOREDIR = 'storage'
    NODETYPE = "client"
    SUICIDE_PREVENTION_HOTLINE_FILE = "suicide_prevention_hotline"

    # This means that if a storage server treats me as though I were a
    # 1.0.0 storage client, it will work as they expect.
    OLDEST_SUPPORTED_VERSION = "1.0.0"

    # this is a tuple of (needed, desired, total, max_segment_size). 'needed'
    # is the number of shares required to reconstruct a file. 'desired' means
    # that we will abort an upload unless we can allocate space for at least
    # this many. 'total' is the total number of shares created by encoding.
    # If everybody has room then this is is how many we will upload.
    DEFAULT_ENCODING_PARAMETERS = {"k": 3,
                                   "happy": 7,
                                   "n": 10,
                                   "max_segment_size": 128*KiB,
                                   }

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.started_timestamp = time.time()
        self.logSource="Client"
        self.init_introducer_client()
        self.init_stats_provider()
        self.init_lease_secret()
        self.init_storage()
        self.init_control()
        if self.get_config("helper", "enabled", False, boolean=True):
            self.init_helper()
        self.init_client()
        self._key_generator = None
        key_gen_furl = self.get_config("client", "key_generator.furl", None)
        if key_gen_furl:
            self.init_key_gen(key_gen_furl)
        # ControlServer and Helper are attached after Tub startup
        self.init_ftp_server()

        hotline_file = os.path.join(self.basedir,
                                    self.SUICIDE_PREVENTION_HOTLINE_FILE)
        if os.path.exists(hotline_file):
            age = time.time() - os.stat(hotline_file)[stat.ST_MTIME]
            self.log("hotline file noticed (%ds old), starting timer" % age)
            hotline = TimerService(1.0, self._check_hotline, hotline_file)
            hotline.setServiceParent(self)

        webport = self.get_config("node", "web.port", None)
        if webport:
            self.init_web(webport) # strports string

    def read_old_config_files(self):
        node.Node.read_old_config_files(self)
        copy = self._copy_config_from_file
        copy("introducer.furl", "client", "introducer.furl")
        copy("helper.furl", "client", "helper.furl")
        copy("key_generator.furl", "client", "key_generator.furl")
        copy("stats_gatherer.furl", "client", "stats_gatherer.furl")
        if os.path.exists(os.path.join(self.basedir, "no_storage")):
            self.set_config("storage", "enabled", "false")
        if os.path.exists(os.path.join(self.basedir, "readonly_storage")):
            self.set_config("storage", "readonly", "true")
        copy("sizelimit", "storage", "sizelimit")
        if os.path.exists(os.path.join(self.basedir, "debug_discard_storage")):
            self.set_config("storage", "debug_discard", "true")
        if os.path.exists(os.path.join(self.basedir, "run_helper")):
            self.set_config("helper", "enabled", "true")

    def init_introducer_client(self):
        self.introducer_furl = self.get_config("client", "introducer.furl")
        ic = IntroducerClient(self.tub, self.introducer_furl,
                              self.nickname,
                              str(allmydata.__version__),
                              str(self.OLDEST_SUPPORTED_VERSION))
        self.introducer_client = ic
        # hold off on starting the IntroducerClient until our tub has been
        # started, so we'll have a useful address on our RemoteReference, so
        # that the introducer's status page will show us.
        d = self.when_tub_ready()
        def _start_introducer_client(res):
            ic.setServiceParent(self)
            # nodes that want to upload and download will need storage servers
            ic.subscribe_to("storage")
        d.addCallback(_start_introducer_client)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="URyI5w")

    def init_stats_provider(self):
        gatherer_furl = self.get_config("client", "stats_gatherer.furl", None)
        self.stats_provider = StatsProvider(self, gatherer_furl)
        self.add_service(self.stats_provider)
        self.stats_provider.register_producer(self)

    def get_stats(self):
        return { 'node.uptime': time.time() - self.started_timestamp }

    def init_lease_secret(self):
        secret_s = self.get_or_create_private_config("secret", _make_secret)
        self._lease_secret = base32.a2b(secret_s)

    def init_storage(self):
        # should we run a storage server (and publish it for others to use)?
        if not self.get_config("storage", "enabled", True, boolean=True):
            return
        readonly = self.get_config("storage", "readonly", False, boolean=True)

        storedir = os.path.join(self.basedir, self.STOREDIR)

        sizelimit = None
        data = self.get_config("storage", "sizelimit", None)
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
        discard = self.get_config("storage", "debug_discard", False,
                                  boolean=True)
        ss = StorageServer(storedir, sizelimit, discard, readonly,
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
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="aLGBKw")

    def init_client(self):
        helper_furl = self.get_config("client", "helper.furl", None)
        convergence_s = self.get_or_create_private_config('convergence', _make_secret)
        self.convergence = base32.a2b(convergence_s)
        self._node_cache = weakref.WeakValueDictionary() # uri -> node
        self.add_service(Uploader(helper_furl, self.stats_provider))
        self.add_service(Downloader(self.stats_provider))
        self.add_service(MutableWatcher(self.stats_provider))
        def _publish(res):
            # we publish an empty object so that the introducer can count how
            # many clients are connected and see what versions they're
            # running.
            sc = StubClient()
            furl = self.tub.registerReference(sc)
            ri_name = RIStubClient.__remote_name__
            self.introducer_client.publish(furl, "stub_client", ri_name)
        d = self.when_tub_ready()
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="OEHq3g")

    def init_control(self):
        d = self.when_tub_ready()
        def _publish(res):
            c = ControlServer()
            c.setServiceParent(self)
            control_url = self.tub.registerReference(c)
            self.write_private_config("control.furl", control_url + "\n")
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="d3tNXA")

    def init_helper(self):
        d = self.when_tub_ready()
        def _publish(self):
            h = Helper(os.path.join(self.basedir, "helper"), self.stats_provider)
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
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="K0mW5w")

    def init_key_gen(self, key_gen_furl):
        d = self.when_tub_ready()
        def _subscribe(self):
            self.tub.connectTo(key_gen_furl, self._got_key_generator)
        d.addCallback(_subscribe)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="z9DMzw")

    def _got_key_generator(self, key_generator):
        self._key_generator = key_generator
        key_generator.notifyOnDisconnect(self._lost_key_generator)

    def _lost_key_generator(self):
        self._key_generator = None

    def init_web(self, webport):
        self.log("init_web(webport=%s)", args=(webport,))

        from allmydata.webish import WebishServer
        nodeurl_path = os.path.join(self.basedir, "node.url")
        ws = WebishServer(webport, nodeurl_path)
        self.add_service(ws)

    def init_ftp_server(self):
        if not self.get_config("ftpd", "enabled", False, boolean=True):
            return
        portstr = self.get_config("ftpd", "ftp.port", "8021")
        accountfile = self.get_config("ftpd", "ftp.accounts.file", None)
        accounturl = self.get_config("ftpd", "ftp.accounts.url", None)

        from allmydata import ftpd
        s = ftpd.FTPServer(self, portstr, accountfile, accounturl)
        s.setServiceParent(self)

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
    def get_nickname_for_peerid(self, peerid):
        return self.introducer_client.get_nickname_for_peerid(peerid)

    def get_permuted_peers(self, service_name, key):
        """
        @return: list of (peerid, connection,)
        """
        assert isinstance(service_name, str)
        assert isinstance(key, str)
        return self.introducer_client.get_permuted_peers(service_name, key)

    def get_encoding_parameters(self):
        return self.DEFAULT_ENCODING_PARAMETERS

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
        u_s = u.to_string()
        if u_s not in self._node_cache:
            if IReadonlyNewDirectoryURI.providedBy(u):
                # new-style read-only dirnodes
                node = NewDirectoryNode(self).init_from_uri(u)
            elif INewDirectoryURI.providedBy(u):
                # new-style dirnodes
                node = NewDirectoryNode(self).init_from_uri(u)
            elif IFileURI.providedBy(u):
                if isinstance(u, LiteralFileURI):
                    node = LiteralFileNode(u, self) # LIT
                else:
                    node = FileNode(u, self) # CHK
            else:
                assert IMutableFileURI.providedBy(u), u
                node = MutableFileNode(self).init_from_uri(u)
            self._node_cache[u_s] = node
        return self._node_cache[u_s]

    def notify_publish(self, publish_status, size):
        self.getServiceNamed("mutable-watcher").notify_publish(publish_status,
                                                               size)
    def notify_retrieve(self, retrieve_status):
        self.getServiceNamed("mutable-watcher").notify_retrieve(retrieve_status)
    def notify_mapupdate(self, update_status):
        self.getServiceNamed("mutable-watcher").notify_mapupdate(update_status)

    def create_empty_dirnode(self):
        n = NewDirectoryNode(self)
        d = n.create(self._generate_pubprivkeys)
        d.addCallback(lambda res: n)
        return d

    def create_mutable_file(self, contents=""):
        n = MutableFileNode(self)
        d = n.create(contents, self._generate_pubprivkeys)
        d.addCallback(lambda res: n)
        return d

    def _generate_pubprivkeys(self, key_size):
        if self._key_generator:
            d = self._key_generator.callRemote('get_rsa_key_pair', key_size)
            def make_key_objs((verifying_key, signing_key)):
                v = rsa.create_verifying_key_from_string(verifying_key)
                s = rsa.create_signing_key_from_string(signing_key)
                return v, s
            d.addCallback(make_key_objs)
            return d
        else:
            # RSA key generation for a 2048 bit key takes between 0.8 and 3.2
            # secs
            signer = rsa.generate(key_size)
            verifier = signer.get_verifying_key()
            return verifier, signer

    def upload(self, uploadable):
        uploader = self.getServiceNamed("uploader")
        return uploader.upload(uploadable)


    def list_all_upload_statuses(self):
        uploader = self.getServiceNamed("uploader")
        return uploader.list_all_upload_statuses()

    def list_all_download_statuses(self):
        downloader = self.getServiceNamed("downloader")
        return downloader.list_all_download_statuses()

    def list_all_mapupdate_statuses(self):
        watcher = self.getServiceNamed("mutable-watcher")
        return watcher.list_all_mapupdate_statuses()
    def list_all_publish_statuses(self):
        watcher = self.getServiceNamed("mutable-watcher")
        return watcher.list_all_publish_statuses()
    def list_all_retrieve_statuses(self):
        watcher = self.getServiceNamed("mutable-watcher")
        return watcher.list_all_retrieve_statuses()

    def list_all_helper_statuses(self):
        try:
            helper = self.getServiceNamed("helper")
        except KeyError:
            return []
        return helper.get_all_upload_statuses()

