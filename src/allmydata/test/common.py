
import os
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from twisted.application import service
from foolscap.eventual import flushEventualQueue
from allmydata import uri, dirnode, client
from allmydata.introducer.server import IntroducerNode
from allmydata.interfaces import IURI, IMutableFileNode, IFileNode, \
     FileTooLargeError, ICheckable
from allmydata.immutable import checker
from allmydata.immutable.encode import NotEnoughSharesError
from allmydata.util import log, testutil, fileutil

def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

class FakeCHKFileNode:
    """I provide IFileNode, but all of my data is stored in a class-level
    dictionary."""
    implements(IFileNode)
    all_contents = {}

    def __init__(self, u, client):
        self.client = client
        self.my_uri = u.to_string()

    def get_uri(self):
        return self.my_uri
    def get_readonly_uri(self):
        return self.my_uri
    def get_verifier(self):
        return IURI(self.my_uri).get_verifier()
    def check(self, verify=False, repair=False):
        r = checker.Results(None)
        r.healthy = True
        r.problems = []
        return defer.succeed(r)
    def is_mutable(self):
        return False
    def is_readonly(self):
        return True

    def download(self, target):
        if self.my_uri not in self.all_contents:
            f = failure.Failure(NotEnoughSharesError())
            target.fail(f)
            return defer.fail(f)
        data = self.all_contents[self.my_uri]
        target.open(len(data))
        target.write(data)
        target.close()
        return defer.maybeDeferred(target.finish)
    def download_to_data(self):
        if self.my_uri not in self.all_contents:
            return defer.fail(NotEnoughSharesError())
        data = self.all_contents[self.my_uri]
        return defer.succeed(data)
    def get_size(self):
        data = self.all_contents[self.my_uri]
        return len(data)

def make_chk_file_uri(size):
    return uri.CHKFileURI(key=os.urandom(16),
                          uri_extension_hash=os.urandom(32),
                          needed_shares=3,
                          total_shares=10,
                          size=size)

def create_chk_filenode(client, contents):
    u = make_chk_file_uri(len(contents))
    n = FakeCHKFileNode(u, client)
    FakeCHKFileNode.all_contents[u.to_string()] = contents
    return n


class FakeMutableFileNode:
    """I provide IMutableFileNode, but all of my data is stored in a
    class-level dictionary."""

    implements(IMutableFileNode, ICheckable)
    MUTABLE_SIZELIMIT = 10000
    all_contents = {}

    def __init__(self, client):
        self.client = client
        self.my_uri = make_mutable_file_uri()
        self.storage_index = self.my_uri.storage_index
    def create(self, initial_contents, key_generator=None):
        if len(initial_contents) > self.MUTABLE_SIZELIMIT:
            raise FileTooLargeError("SDMF is limited to one segment, and "
                                    "%d > %d" % (len(initial_contents),
                                                 self.MUTABLE_SIZELIMIT))
        self.all_contents[self.storage_index] = initial_contents
        return defer.succeed(self)
    def init_from_uri(self, myuri):
        self.my_uri = IURI(myuri)
        self.storage_index = self.my_uri.storage_index
        return self
    def get_uri(self):
        return self.my_uri.to_string()
    def get_readonly(self):
        return self.my_uri.get_readonly()
    def get_readonly_uri(self):
        return self.my_uri.get_readonly().to_string()
    def is_readonly(self):
        return self.my_uri.is_readonly()
    def is_mutable(self):
        return self.my_uri.is_mutable()
    def get_writekey(self):
        return "\x00"*16
    def get_size(self):
        return "?" # TODO: see mutable.MutableFileNode.get_size

    def get_storage_index(self):
        return self.storage_index

    def check(self, verify=False, repair=False):
        r = checker.Results(None)
        r.healthy = True
        r.problems = []
        return defer.succeed(r)

    def deep_check(self, verify=False, repair=False):
        d = self.check(verify, repair)
        def _done(r):
            dr = checker.DeepCheckResults(self.storage_index)
            dr.add_check(r)
            return dr
        d.addCallback(_done)
        return d

    def download_best_version(self):
        return defer.succeed(self.all_contents[self.storage_index])
    def overwrite(self, new_contents):
        if len(new_contents) > self.MUTABLE_SIZELIMIT:
            raise FileTooLargeError("SDMF is limited to one segment, and "
                                    "%d > %d" % (len(new_contents),
                                                 self.MUTABLE_SIZELIMIT))
        assert not self.is_readonly()
        self.all_contents[self.storage_index] = new_contents
        return defer.succeed(None)
    def modify(self, modifier):
        # this does not implement FileTooLargeError, but the real one does
        return defer.maybeDeferred(self._modify, modifier)
    def _modify(self, modifier):
        assert not self.is_readonly()
        old_contents = self.all_contents[self.storage_index]
        self.all_contents[self.storage_index] = modifier(old_contents)
        return None

    def download(self, target):
        if self.storage_index not in self.all_contents:
            f = failure.Failure(NotEnoughSharesError())
            target.fail(f)
            return defer.fail(f)
        data = self.all_contents[self.storage_index]
        target.open(len(data))
        target.write(data)
        target.close()
        return defer.maybeDeferred(target.finish)
    def download_to_data(self):
        if self.storage_index not in self.all_contents:
            return defer.fail(NotEnoughSharesError())
        data = self.all_contents[self.storage_index]
        return defer.succeed(data)

def make_mutable_file_uri():
    return uri.WriteableSSKFileURI(writekey=os.urandom(16),
                                   fingerprint=os.urandom(32))
def make_verifier_uri():
    return uri.SSKVerifierURI(storage_index=os.urandom(16),
                              fingerprint=os.urandom(32))

class FakeDirectoryNode(dirnode.NewDirectoryNode):
    """This offers IDirectoryNode, but uses a FakeMutableFileNode for the
    backing store, so it doesn't go to the grid. The child data is still
    encrypted and serialized, so this isn't useful for tests that want to
    look inside the dirnodes and check their contents.
    """
    filenode_class = FakeMutableFileNode

class LoggingServiceParent(service.MultiService):
    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)


class SystemTestMixin(testutil.SignalMixin, testutil.PollMixin,
                      testutil.StallMixin):

    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()
    def tearDown(self):
        log.msg("shutting down SystemTest services")
        d = self.sparent.stopService()
        d.addBoth(flush_but_dont_ignore)
        return d

    def getdir(self, subdir):
        return os.path.join(self.basedir, subdir)

    def add_service(self, s):
        s.setServiceParent(self.sparent)
        return s

    def set_up_nodes(self, NUMCLIENTS=5):
        self.numclients = NUMCLIENTS
        iv_dir = self.getdir("introducer")
        if not os.path.isdir(iv_dir):
            fileutil.make_dirs(iv_dir)
        f = open(os.path.join(iv_dir, "webport"), "w")
        f.write("tcp:0:interface=127.0.0.1\n")
        f.close()
        iv = IntroducerNode(basedir=iv_dir)
        self.introducer = self.add_service(iv)
        d = self.introducer.when_tub_ready()
        d.addCallback(self._set_up_nodes_2)
        return d

    def _set_up_nodes_2(self, res):
        q = self.introducer
        self.introducer_furl = q.introducer_url
        self.clients = []
        basedirs = []
        for i in range(self.numclients):
            basedir = self.getdir("client%d" % i)
            basedirs.append(basedir)
            fileutil.make_dirs(basedir)
            if i == 0:
                # client[0] runs a webserver and a helper, no key_generator
                open(os.path.join(basedir, "webport"), "w").write("tcp:0:interface=127.0.0.1")
                open(os.path.join(basedir, "sizelimit"), "w").write("10GB\n")
            open(os.path.join(basedir, "introducer.furl"), "w").write(self.introducer_furl)

        # start client[0], wait for it's tub to be ready (at which point it
        # will have registered the helper furl).
        c = self.add_service(client.Client(basedir=basedirs[0]))
        self.clients.append(c)
        d = c.when_tub_ready()
        def _ready(res):
            # this starts the rest of the clients
            for i in range(1, self.numclients):
                c = self.add_service(client.Client(basedir=basedirs[i]))
                self.clients.append(c)
            log.msg("STARTING")
            return self.wait_for_connections()
        d.addCallback(_ready)
        def _connected(res):
            log.msg("CONNECTED")
            # now find out where the web port was
            l = self.clients[0].getServiceNamed("webish").listener
            port = l._port.getHost().port
            self.webish_url = "http://localhost:%d/" % port
        d.addCallback(_connected)
        return d

    def _check_connections(self):
        for c in self.clients:
            ic = c.introducer_client
            if not ic.connected_to_introducer():
                return False
            if len(ic.get_all_peerids()) != self.numclients:
                return False
        return True

    def wait_for_connections(self, ignored=None):
        # TODO: replace this with something that takes a list of peerids and
        # fires when they've all been heard from, instead of using a count
        # and a threshold
        return self.poll(self._check_connections, timeout=200)
