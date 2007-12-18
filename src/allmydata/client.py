
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
from allmydata.control import ControlServer
from allmydata.introducer import IntroducerClient
from allmydata.util import hashutil, idlib, testutil, observer
from allmydata.filenode import FileNode
from allmydata.dirnode import NewDirectoryNode
from allmydata.mutable import MutableFileNode
from allmydata.interfaces import IURI, INewDirectoryURI, \
     IReadonlyNewDirectoryURI, IFileURI, IMutableFileURI
from allmydata import uri

class Client(node.Node, Referenceable, testutil.PollMixin):
    implements(RIClient)
    PORTNUMFILE = "client.port"
    STOREDIR = 'storage'
    NODETYPE = "client"
    SUICIDE_PREVENTION_HOTLINE_FILE = "suicide_prevention_hotline"
    MY_PRIVATE_DIR_FILE = "my_private_dir.cap"

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
        self.add_service(Uploader())
        self.add_service(Downloader())
        self.add_service(Checker())
        self.private_directory_uri = None
        self._private_uri_observers = None
        self._start_page_observers = None

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

    def _init_start_page(self, privdiruri):
        ws = self.getServiceNamed("webish")
        startfile = os.path.join(self.basedir, "private", "start.html")
        nodeurl_file = os.path.join(self.basedir, "node.url")
        return ws.create_start_html(privdiruri, startfile, nodeurl_file)

    def init_start_page(self):
        if not self._start_page_observers:
            self._start_page_observers = observer.OneShotObserverList()
            d = self.get_private_uri()
            d.addCallback(self._init_start_page)
            d.addCallback(self._start_page_observers.fire)
            d.addErrback(log.err)
        return self._start_page_observers.when_fired()

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

    def _maybe_create_private_directory(self):
        """
        If 'my_private_dir.cap' exists, then I try to read a mutable
        directory URI from it.  If it exists but doesn't contain a well-formed
        read-write mutable directory URI, then I create a new mutable
        directory and write its URI into that file.
        """
        privdirfile = os.path.join(self.basedir, self.MY_PRIVATE_DIR_FILE)
        if os.path.exists(privdirfile):
            try:
                theuri = open(privdirfile, "r").read().strip()
                if not uri.is_string_newdirnode_rw(theuri):
                    raise EnvironmentError("not a well-formed mutable directory uri")
            except EnvironmentError, le:
                d = self.when_tub_ready()
                def _when_tub_ready(res):
                    return self.create_empty_dirnode(wait_for_numpeers=1)
                d.addCallback(_when_tub_ready)
                def _when_created(newdirnode):
                    log.msg("created new private directory: %s" % (newdirnode,))
                    privdiruri = newdirnode.get_uri()
                    self.private_directory_uri = privdiruri
                    open(privdirfile, "w").write(privdiruri + "\n")
                    self._private_uri_observers.fire(privdiruri)
                d.addCallback(_when_created)
                d.addErrback(self._private_uri_observers.fire)
            else:
                self.private_directory_uri = theuri
                log.msg("loaded private directory: %s" % (self.private_directory_uri,))
                self._private_uri_observers.fire(self.private_directory_uri)
        else:
            # If there is no such file then this is how the node is configured
            # to not create a private directory.
            self._private_uri_observers.fire(None)

    def get_private_uri(self):
        """
        Eventually fires with the URI (as a string) to this client's private
        directory, or with None if this client has been configured not to
        create one.
        """
        if self._private_uri_observers is None:
            self._private_uri_observers = observer.OneShotObserverList()
            self._maybe_create_private_directory()
        return self._private_uri_observers.when_fired()

    def init_web(self, webport):
        self.log("init_web(webport=%s)", args=(webport,))

        from allmydata.webish import WebishServer
        ws = WebishServer(webport)
        if self.get_config("webport_allow_localfile") is not None:
            ws.allow_local_access(True)
        self.add_service(ws)
        self.init_start_page()

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

    def register_control(self):
        c = ControlServer()
        c.setServiceParent(self)
        control_url = self.tub.registerReference(c)
        self.write_private_config("control.furl", control_url + "\n")

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

    def create_empty_dirnode(self, wait_for_numpeers=None):
        n = NewDirectoryNode(self)
        d = n.create(wait_for_numpeers=wait_for_numpeers)
        d.addCallback(lambda res: n)
        return d

    def create_mutable_file(self, contents="", wait_for_numpeers=None):
        n = MutableFileNode(self)
        d = n.create(contents, wait_for_numpeers=wait_for_numpeers)
        d.addCallback(lambda res: n)
        return d

    def upload(self, uploadable, wait_for_numpeers=None):
        uploader = self.getServiceNamed("uploader")
        return uploader.upload(uploadable, wait_for_numpeers=wait_for_numpeers)

