import os, random, struct
from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IConsumer
from twisted.python import failure
from twisted.application import service
from twisted.web.error import Error as WebError
from foolscap.api import flushEventualQueue, fireEventually
from allmydata import uri, dirnode, client
from allmydata.introducer.server import IntroducerNode
from allmydata.interfaces import IMutableFileNode, IFileNode, \
     FileTooLargeError, NotEnoughSharesError, ICheckable
from allmydata.check_results import CheckResults, CheckAndRepairResults, \
     DeepCheckResults, DeepCheckAndRepairResults
from allmydata.mutable.common import CorruptShareError
from allmydata.mutable.layout import unpack_header
from allmydata.storage.server import storage_index_to_dir
from allmydata.storage.mutable import MutableShareFile
from allmydata.util import hashutil, log, fileutil, pollmixin
from allmydata.util.assertutil import precondition
from allmydata.stats import StatsGathererService
from allmydata.key_generator import KeyGeneratorService
import common_util as testutil
from allmydata import immutable


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
    bad_shares = {}

    def __init__(self, filecap):
        precondition(isinstance(filecap, uri.CHKFileURI), filecap)
        self.my_uri = filecap
        self.storage_index = self.my_uri.storage_index

    def get_uri(self):
        return self.my_uri.to_string()
    def get_readonly_uri(self):
        return self.my_uri.to_string()
    def get_cap(self):
        return self.my_uri
    def get_verify_cap(self):
        return self.my_uri.get_verify_cap()
    def get_repair_cap(self):
        return self.my_uri.get_verify_cap()
    def get_storage_index(self):
        return self.storage_index

    def check(self, monitor, verify=False, add_lease=False):
        r = CheckResults(self.my_uri, self.storage_index)
        is_bad = self.bad_shares.get(self.storage_index, None)
        data = {}
        data["count-shares-needed"] = 3
        data["count-shares-expected"] = 10
        data["count-good-share-hosts"] = 10
        data["count-wrong-shares"] = 0
        nodeid = "\x00"*20
        data["list-corrupt-shares"] = []
        data["sharemap"] = {1: [nodeid]}
        data["servers-responding"] = [nodeid]
        data["count-recoverable-versions"] = 1
        data["count-unrecoverable-versions"] = 0
        if is_bad:
             r.set_healthy(False)
             r.set_recoverable(True)
             data["count-shares-good"] = 9
             data["list-corrupt-shares"] = [(nodeid, self.storage_index, 0)]
             r.problems = failure.Failure(CorruptShareError(is_bad))
        else:
             r.set_healthy(True)
             r.set_recoverable(True)
             data["count-shares-good"] = 10
             r.problems = []
        r.set_data(data)
        r.set_needs_rebalancing(False)
        return defer.succeed(r)
    def check_and_repair(self, monitor, verify=False, add_lease=False):
        d = self.check(verify)
        def _got(cr):
            r = CheckAndRepairResults(self.storage_index)
            r.pre_repair_results = r.post_repair_results = cr
            return r
        d.addCallback(_got)
        return d

    def is_mutable(self):
        return False
    def is_readonly(self):
        return True

    def download(self, target):
        if self.my_uri.to_string() not in self.all_contents:
            f = failure.Failure(NotEnoughSharesError(None, 0, 3))
            target.fail(f)
            return defer.fail(f)
        data = self.all_contents[self.my_uri.to_string()]
        target.open(len(data))
        target.write(data)
        target.close()
        return defer.maybeDeferred(target.finish)
    def download_to_data(self):
        if self.my_uri.to_string() not in self.all_contents:
            return defer.fail(NotEnoughSharesError(None, 0, 3))
        data = self.all_contents[self.my_uri.to_string()]
        return defer.succeed(data)
    def get_size(self):
        try:
            data = self.all_contents[self.my_uri.to_string()]
        except KeyError, le:
            raise NotEnoughSharesError(le, 0, 3)
        return len(data)
    def read(self, consumer, offset=0, size=None):
        d = self.download_to_data()
        def _got(data):
            start = offset
            if size is not None:
                end = offset + size
            else:
                end = len(data)
            consumer.write(data[start:end])
            return consumer
        d.addCallback(_got)
        return d

def make_chk_file_cap(size):
    return uri.CHKFileURI(key=os.urandom(16),
                          uri_extension_hash=os.urandom(32),
                          needed_shares=3,
                          total_shares=10,
                          size=size)
def make_chk_file_uri(size):
    return make_chk_file_cap(size).to_string()

def create_chk_filenode(contents):
    filecap = make_chk_file_cap(len(contents))
    n = FakeCHKFileNode(filecap)
    FakeCHKFileNode.all_contents[filecap.to_string()] = contents
    return n


class FakeMutableFileNode:
    """I provide IMutableFileNode, but all of my data is stored in a
    class-level dictionary."""

    implements(IMutableFileNode, ICheckable)
    MUTABLE_SIZELIMIT = 10000
    all_contents = {}
    bad_shares = {}

    def __init__(self, storage_broker, secret_holder,
                 default_encoding_parameters, history):
        self.init_from_cap(make_mutable_file_cap())
    def create(self, contents, key_generator=None, keysize=None):
        initial_contents = self._get_initial_contents(contents)
        if len(initial_contents) > self.MUTABLE_SIZELIMIT:
            raise FileTooLargeError("SDMF is limited to one segment, and "
                                    "%d > %d" % (len(initial_contents),
                                                 self.MUTABLE_SIZELIMIT))
        self.all_contents[self.storage_index] = initial_contents
        return defer.succeed(self)
    def _get_initial_contents(self, contents):
        if isinstance(contents, str):
            return contents
        if contents is None:
            return ""
        assert callable(contents), "%s should be callable, not %s" % \
               (contents, type(contents))
        return contents(self)
    def init_from_cap(self, filecap):
        assert isinstance(filecap, (uri.WriteableSSKFileURI,
                                    uri.ReadonlySSKFileURI))
        self.my_uri = filecap
        self.storage_index = self.my_uri.storage_index
        return self
    def get_cap(self):
        return self.my_uri
    def get_readcap(self):
        return self.my_uri.get_readonly()
    def get_uri(self):
        return self.my_uri.to_string()
    def get_readonly(self):
        return self.my_uri.get_readonly()
    def get_readonly_uri(self):
        return self.my_uri.get_readonly().to_string()
    def get_verify_cap(self):
        return self.my_uri.get_verify_cap()
    def is_readonly(self):
        return self.my_uri.is_readonly()
    def is_mutable(self):
        return self.my_uri.is_mutable()
    def get_writekey(self):
        return "\x00"*16
    def get_size(self):
        return "?" # TODO: see mutable.MutableFileNode.get_size
    def get_size_of_best_version(self):
        return defer.succeed(len(self.all_contents[self.storage_index]))

    def get_storage_index(self):
        return self.storage_index

    def check(self, monitor, verify=False, add_lease=False):
        r = CheckResults(self.my_uri, self.storage_index)
        is_bad = self.bad_shares.get(self.storage_index, None)
        data = {}
        data["count-shares-needed"] = 3
        data["count-shares-expected"] = 10
        data["count-good-share-hosts"] = 10
        data["count-wrong-shares"] = 0
        data["list-corrupt-shares"] = []
        nodeid = "\x00"*20
        data["sharemap"] = {"seq1-abcd-sh0": [nodeid]}
        data["servers-responding"] = [nodeid]
        data["count-recoverable-versions"] = 1
        data["count-unrecoverable-versions"] = 0
        if is_bad:
             r.set_healthy(False)
             r.set_recoverable(True)
             data["count-shares-good"] = 9
             r.problems = failure.Failure(CorruptShareError("peerid",
                                                            0, # shnum
                                                            is_bad))
        else:
             r.set_healthy(True)
             r.set_recoverable(True)
             data["count-shares-good"] = 10
             r.problems = []
        r.set_data(data)
        r.set_needs_rebalancing(False)
        return defer.succeed(r)

    def check_and_repair(self, monitor, verify=False, add_lease=False):
        d = self.check(verify)
        def _got(cr):
            r = CheckAndRepairResults(self.storage_index)
            r.pre_repair_results = r.post_repair_results = cr
            return r
        d.addCallback(_got)
        return d

    def deep_check(self, verify=False, add_lease=False):
        d = self.check(verify)
        def _done(r):
            dr = DeepCheckResults(self.storage_index)
            dr.add_check(r, [])
            return dr
        d.addCallback(_done)
        return d

    def deep_check_and_repair(self, verify=False, add_lease=False):
        d = self.check_and_repair(verify)
        def _done(r):
            dr = DeepCheckAndRepairResults(self.storage_index)
            dr.add_check(r, [])
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
        self.all_contents[self.storage_index] = modifier(old_contents, None, True)
        return None

    def download(self, target):
        if self.storage_index not in self.all_contents:
            f = failure.Failure(NotEnoughSharesError(None, 0, 3))
            target.fail(f)
            return defer.fail(f)
        data = self.all_contents[self.storage_index]
        target.open(len(data))
        target.write(data)
        target.close()
        return defer.maybeDeferred(target.finish)
    def download_to_data(self):
        if self.storage_index not in self.all_contents:
            return defer.fail(NotEnoughSharesError(None, 0, 3))
        data = self.all_contents[self.storage_index]
        return defer.succeed(data)

def make_mutable_file_cap():
    return uri.WriteableSSKFileURI(writekey=os.urandom(16),
                                   fingerprint=os.urandom(32))
def make_mutable_file_uri():
    return make_mutable_file_cap().to_string()

def make_verifier_uri():
    return uri.SSKVerifierURI(storage_index=os.urandom(16),
                              fingerprint=os.urandom(32)).to_string()

class FakeDirectoryNode(dirnode.DirectoryNode):
    """This offers IDirectoryNode, but uses a FakeMutableFileNode for the
    backing store, so it doesn't go to the grid. The child data is still
    encrypted and serialized, so this isn't useful for tests that want to
    look inside the dirnodes and check their contents.
    """
    filenode_class = FakeMutableFileNode

class LoggingServiceParent(service.MultiService):
    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)


class SystemTestMixin(pollmixin.PollMixin, testutil.StallMixin):

    # SystemTestMixin tests tend to be a lot of work, and we have a few
    # buildslaves that are pretty slow, and every once in a while these tests
    # run up against the default 120 second timeout. So increase the default
    # timeout. Individual test cases can override this, of course.
    timeout = 300

    def setUp(self):
        self.sparent = service.MultiService()
        self.sparent.startService()

        self.stats_gatherer = None
        self.stats_gatherer_furl = None
        self.key_generator_svc = None
        self.key_generator_furl = None

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

    def set_up_nodes(self, NUMCLIENTS=5,
                     use_stats_gatherer=False, use_key_generator=False):
        self.numclients = NUMCLIENTS
        iv_dir = self.getdir("introducer")
        if not os.path.isdir(iv_dir):
            fileutil.make_dirs(iv_dir)
            f = open(os.path.join(iv_dir, "webport"), "w")
            f.write("tcp:0:interface=127.0.0.1\n")
            f.close()
            if SYSTEM_TEST_CERTS:
                os.mkdir(os.path.join(iv_dir, "private"))
                f = open(os.path.join(iv_dir, "private", "node.pem"), "w")
                f.write(SYSTEM_TEST_CERTS[0])
                f.close()
        iv = IntroducerNode(basedir=iv_dir)
        self.introducer = self.add_service(iv)
        d = self.introducer.when_tub_ready()
        d.addCallback(self._get_introducer_web)
        if use_stats_gatherer:
            d.addCallback(self._set_up_stats_gatherer)
        if use_key_generator:
            d.addCallback(self._set_up_key_generator)
        d.addCallback(self._set_up_nodes_2)
        if use_stats_gatherer:
            d.addCallback(self._grab_stats)
        return d

    def _get_introducer_web(self, res):
        f = open(os.path.join(self.getdir("introducer"), "node.url"), "r")
        self.introweb_url = f.read().strip()
        f.close()

    def _set_up_stats_gatherer(self, res):
        statsdir = self.getdir("stats_gatherer")
        fileutil.make_dirs(statsdir)
        self.stats_gatherer_svc = StatsGathererService(statsdir)
        self.stats_gatherer = self.stats_gatherer_svc.stats_gatherer
        self.add_service(self.stats_gatherer_svc)

        d = fireEventually()
        sgf = os.path.join(statsdir, 'stats_gatherer.furl')
        def check_for_furl():
            return os.path.exists(sgf)
        d.addCallback(lambda junk: self.poll(check_for_furl, timeout=30))
        def get_furl(junk):
            self.stats_gatherer_furl = file(sgf, 'rb').read().strip()
        d.addCallback(get_furl)
        return d

    def _set_up_key_generator(self, res):
        kgsdir = self.getdir("key_generator")
        fileutil.make_dirs(kgsdir)

        self.key_generator_svc = KeyGeneratorService(kgsdir,
                                                     display_furl=False,
                                                     default_key_size=522)
        self.key_generator_svc.key_generator.pool_size = 4
        self.key_generator_svc.key_generator.pool_refresh_delay = 60
        self.add_service(self.key_generator_svc)

        d = fireEventually()
        def check_for_furl():
            return os.path.exists(os.path.join(kgsdir, 'key_generator.furl'))
        d.addCallback(lambda junk: self.poll(check_for_furl, timeout=30))
        def get_furl(junk):
            kgf = os.path.join(kgsdir, 'key_generator.furl')
            self.key_generator_furl = file(kgf, 'rb').read().strip()
        d.addCallback(get_furl)
        return d

    def _set_up_nodes_2(self, res):
        q = self.introducer
        self.introducer_furl = q.introducer_url
        self.clients = []
        basedirs = []
        for i in range(self.numclients):
            basedir = self.getdir("client%d" % i)
            basedirs.append(basedir)
            fileutil.make_dirs(os.path.join(basedir, "private"))
            if len(SYSTEM_TEST_CERTS) > (i+1):
                f = open(os.path.join(basedir, "private", "node.pem"), "w")
                f.write(SYSTEM_TEST_CERTS[i+1])
                f.close()

            def write(name, value):
                open(os.path.join(basedir, name), "w").write(value+"\n")
            if i == 0:
                # clients[0] runs a webserver and a helper, no key_generator
                write("webport", "tcp:0:interface=127.0.0.1")
                write("run_helper", "yes")
                write("keepalive_timeout", "600")
            if i == 3:
                # clients[3] runs a webserver and uses a helper, uses
                # key_generator
                write("webport", "tcp:0:interface=127.0.0.1")
                write("disconnect_timeout", "1800")
                if self.key_generator_furl:
                    kgf = "%s\n" % (self.key_generator_furl,)
                    write("key_generator.furl", kgf)
            write("introducer.furl", self.introducer_furl)
            if self.stats_gatherer_furl:
                write("stats_gatherer.furl", self.stats_gatherer_furl)

        # give subclasses a chance to append liens to the node's tahoe.cfg
        # files before they are launched.
        self._set_up_nodes_extra_config()

        # start clients[0], wait for it's tub to be ready (at which point it
        # will have registered the helper furl).
        c = self.add_service(client.Client(basedir=basedirs[0]))
        self.clients.append(c)
        c.set_default_mutable_keysize(522)
        d = c.when_tub_ready()
        def _ready(res):
            f = open(os.path.join(basedirs[0],"private","helper.furl"), "r")
            helper_furl = f.read()
            f.close()
            self.helper_furl = helper_furl
            if self.numclients >= 4:
                f = open(os.path.join(basedirs[3],"helper.furl"), "w")
                f.write(helper_furl)
                f.close()

            # this starts the rest of the clients
            for i in range(1, self.numclients):
                c = self.add_service(client.Client(basedir=basedirs[i]))
                self.clients.append(c)
                c.set_default_mutable_keysize(522)
            log.msg("STARTING")
            return self.wait_for_connections()
        d.addCallback(_ready)
        def _connected(res):
            log.msg("CONNECTED")
            # now find out where the web port was
            l = self.clients[0].getServiceNamed("webish").listener
            port = l._port.getHost().port
            self.webish_url = "http://localhost:%d/" % port
            if self.numclients >=4:
                # and the helper-using webport
                l = self.clients[3].getServiceNamed("webish").listener
                port = l._port.getHost().port
                self.helper_webish_url = "http://localhost:%d/" % port
        d.addCallback(_connected)
        return d

    def _set_up_nodes_extra_config(self):
        # for overriding by subclasses
        pass

    def _grab_stats(self, res):
        d = self.stats_gatherer.poll()
        return d

    def bounce_client(self, num):
        c = self.clients[num]
        d = c.disownServiceParent()
        # I think windows requires a moment to let the connection really stop
        # and the port number made available for re-use. TODO: examine the
        # behavior, see if this is really the problem, see if we can do
        # better than blindly waiting for a second.
        d.addCallback(self.stall, 1.0)
        def _stopped(res):
            new_c = client.Client(basedir=self.getdir("client%d" % num))
            self.clients[num] = new_c
            new_c.set_default_mutable_keysize(522)
            self.add_service(new_c)
            return new_c.when_tub_ready()
        d.addCallback(_stopped)
        d.addCallback(lambda res: self.wait_for_connections())
        def _maybe_get_webport(res):
            if num == 0:
                # now find out where the web port was
                l = self.clients[0].getServiceNamed("webish").listener
                port = l._port.getHost().port
                self.webish_url = "http://localhost:%d/" % port
        d.addCallback(_maybe_get_webport)
        return d

    def add_extra_node(self, client_num, helper_furl=None,
                       add_to_sparent=False):
        # usually this node is *not* parented to our self.sparent, so we can
        # shut it down separately from the rest, to exercise the
        # connection-lost code
        basedir = self.getdir("client%d" % client_num)
        if not os.path.isdir(basedir):
            fileutil.make_dirs(basedir)
        open(os.path.join(basedir, "introducer.furl"), "w").write(self.introducer_furl)
        if helper_furl:
            f = open(os.path.join(basedir, "helper.furl") ,"w")
            f.write(helper_furl+"\n")
            f.close()

        c = client.Client(basedir=basedir)
        self.clients.append(c)
        c.set_default_mutable_keysize(522)
        self.numclients += 1
        if add_to_sparent:
            c.setServiceParent(self.sparent)
        else:
            c.startService()
        d = self.wait_for_connections()
        d.addCallback(lambda res: c)
        return d

    def _check_connections(self):
        for c in self.clients:
            if not c.connected_to_introducer():
                return False
            sb = c.get_storage_broker()
            if len(sb.get_all_servers()) != self.numclients:
                return False
        return True

    def wait_for_connections(self, ignored=None):
        # TODO: replace this with something that takes a list of peerids and
        # fires when they've all been heard from, instead of using a count
        # and a threshold
        return self.poll(self._check_connections, timeout=200)


# our system test uses the same Tub certificates each time, to avoid the
# overhead of key generation
SYSTEM_TEST_CERTS = [
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDVaFw0wOTA3MjUyMjQyMDVaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAxHCWajrR
2h/iurw8k93m8WUdE3xypJiiAITw7GkKlKbCLD+dEce2MXwVVYca0n/MZZsj89Cu
Ko0lLjksMseoSDoj98iEmVpaY5mc2ntpQ+FXdoEmPP234XRWEg2HQ+EaK6+WkGQg
DDXQvFJCVCQk/n1MdAwZZ6vqf2ITzSuD44kCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQBn6qPKGdFjWJy7sOOTUFfm/THhHQqAh1pBDLkjR+OtzuobCoP8n8J1LNG3Yxds
Jj7NWQL7X5TfOlfoi7e9jK0ujGgWh3yYU6PnHzJLkDiDT3LCSywQuGXCjh0tOStS
2gaCmmAK2cfxSStKzNcewl2Zs8wHMygq8TLFoZ6ozN1+xQ==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDEcJZqOtHaH+K6vDyT3ebxZR0TfHKkmKIAhPDsaQqUpsIsP50R
x7YxfBVVhxrSf8xlmyPz0K4qjSUuOSwyx6hIOiP3yISZWlpjmZzae2lD4Vd2gSY8
/bfhdFYSDYdD4Rorr5aQZCAMNdC8UkJUJCT+fUx0DBlnq+p/YhPNK4PjiQIDAQAB
AoGAZyDMdrymiyMOPwavrtlicvyohSBid3MCKc+hRBvpSB0790r2RO1aAySndp1V
QYmCXx1RhKDbrs8m49t0Dryu5T+sQrFl0E3usAP3vvXWeh4jwJ9GyiRWy4xOEuEQ
3ewjbEItHqA/bRJF0TNtbOmZTDC7v9FRPf2bTAyFfTZep5kCQQD33q1RA8WUYtmQ
IArgHqt69i421lpXlOgqotFHwTx4FiGgVzDQCDuXU6txB9EeKRM340poissav/n6
bkLZ7/VDAkEAyuIPkeI59sE5NnmW+N47NbCfdM1Smy1YxZpv942EmP9Veub5N0dw
iK5bLAgEguUIjpTsh3BRmsE9Xd+ItmnRQwJBAMZhbg19G1EbnE0BmDKv2UbcaThy
bnPSNc6J6T2opqDl9ZvCrMqTDD6dNIWOYAvni/4a556sFsoeBBAu10peBskCQE6S
cB86cuJagLLVMh/dySaI6ahNoFFSpY+ZuQUxfInYUR2Q+DFtbGqyw8JwtHaRBthZ
WqU1XZVGg2KooISsxIsCQQD1PS7//xHLumBb0jnpL7n6W8gmiTyzblT+0otaCisP
fN6rTlwV1o8VsOUAz0rmKO5RArCbkmb01WtMgPCDBYkk
-----END RSA PRIVATE KEY-----
""", # 0
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDVaFw0wOTA3MjUyMjQyMDVaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAs9CALdmW
kJ6r0KPSLdGCA8rzQKxWayrMckT22ZtbRv3aw6VA96dWclpY+T2maV0LrAzmMSL8
n61ydJHM33iYDOyWbwHWN45XCjY/e20PL54XUl/DmbBHEhQVQLIfCldcRcnWEfoO
iOhDJfWpDO1dmP/aOYLdkZCZvBtPAfyUqRcCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQAN9eaCREkzzk4yPIaWYkWHg3Igs1vnOR/iDw3OjyxO/xJFP2lkA2WtrwL2RTRq
dxA8gwdPyrWgdiZElwZH8mzTJ4OdUXLSMclLOg9kvH6gtSvhLztfEDwDP1wRhikh
OeWWu2GIC+uqFCI1ftoGgU+aIa6yrHswf66rrQvBSSvJPQ==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQCz0IAt2ZaQnqvQo9It0YIDyvNArFZrKsxyRPbZm1tG/drDpUD3
p1ZyWlj5PaZpXQusDOYxIvyfrXJ0kczfeJgM7JZvAdY3jlcKNj97bQ8vnhdSX8OZ
sEcSFBVAsh8KV1xFydYR+g6I6EMl9akM7V2Y/9o5gt2RkJm8G08B/JSpFwIDAQAB
AoGBAIUy5zCPpSP+FeJY6CG+t6Pdm/IFd4KtUoM3KPCrT6M3+uzApm6Ny9Crsor2
qyYTocjSSVaOxzn1fvpw4qWLrH1veUf8ozMs8Z0VuPHD1GYUGjOXaBPXb5o1fQL9
h7pS5/HrDDPN6wwDNTsxRf/fP58CnfwQUhwdoxcx8TnVmDQxAkEA6N3jBXt/Lh0z
UbXHhv3QBOcqLZA2I4tY7wQzvUvKvVmCJoW1tfhBdYQWeQv0jzjL5PzrrNY8hC4l
8+sFM3h5TwJBAMWtbFIEZfRSG1JhHK3evYHDTZnr/j+CdoWuhzP5RkjkIKsiLEH7
2ZhA7CdFQLZF14oXy+g1uVCzzfB2WELtUbkCQQDKrb1XWzrBlzbAipfkXWs9qTmj
uJ32Z+V6+0xRGPOXxJ0sDDqw7CeFMfchWg98zLFiV+SEZV78qPHtkAPR3ayvAkB+
hUMhM4N13t9x2IoclsXAOhp++9bdG0l0woHyuAdOPATUw6iECwf4NQVxFRgYEZek
4Ro3Y7taddrHn1dabr6xAkAic47OoLOROYLpljmJJO0eRe3Z5IFe+0D2LfhAW3LQ
JU+oGq5pCjfnoaDElRRZn0+GmunnWeQEYKoflTi/lI9d
-----END RSA PRIVATE KEY-----
""", # 1
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDZaFw0wOTA3MjUyMjQyMDZaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAsxG7LTrz
DF+9wegOR/BRJhjSumPUbYQnNAUKtPraFsGjAJILP44AHdnHt1MONLgTeX1ynapo
q6O/q5cdKtBB7uEh7FpkLCCwpZt/m0y79cynn8AmWoQVgl8oS0567UmPeJnTzFPv
dmT5dlaQALeX5YGceAsEvhmAsdOMttaor38CAwEAATANBgkqhkiG9w0BAQQFAAOB
gQA345rxotfvh2kfgrmRzAyGewVBV4r23Go30GSZir8X2GoH3qKNwO4SekAohuSw
AiXzLUbwIdSRSqaLFxSC7Duqc9eIeFDAWjeEmpfFLBNiw3K8SLA00QrHCUXnECTD
b/Kk6OGuvPOiuuONVjEuEcRdCH3/Li30D0AhJaMynjhQJQ==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQCzEbstOvMMX73B6A5H8FEmGNK6Y9RthCc0BQq0+toWwaMAkgs/
jgAd2ce3Uw40uBN5fXKdqmiro7+rlx0q0EHu4SHsWmQsILClm3+bTLv1zKefwCZa
hBWCXyhLTnrtSY94mdPMU+92ZPl2VpAAt5flgZx4CwS+GYCx04y21qivfwIDAQAB
AoGBAIlhFg/aRPL+VM9539LzHN60dp8GzceDdqwjHhbAySZiQlLCuJx2rcI4/U65
CpIJku9G/fLV9N2RkA/trDPXeGyqCTJfnNzyZcvvMscRMFqSGyc21Y0a+GS8bIxt
1R2B18epSVMsWSWWMypeEgsfv29LV7oSWG8UKaqQ9+0h63DhAkEA4i2L/rori/Fb
wpIBfA+xbXL/GmWR7xPW+3nG3LdLQpVzxz4rIsmtO9hIXzvYpcufQbwgVACyMmRf
TMABeSDM7wJBAMquEdTaVXjGfH0EJ7z95Ys2rYTiCXjBfyEOi6RXXReqV9SXNKlN
aKsO22zYecpkAjY1EdUdXWP/mNVEybjpZnECQQCcuh0JPS5RwcTo9c2rjyBOjGIz
g3B1b5UIG2FurmCrWe6pgO3ZJFEzZ/L2cvz0Hj5UCa2JKBZTDvRutZoPumfnAkAb
nSW+y1Rz1Q8m9Ub4v9rjYbq4bRd/RVWtyk6KQIDldYbr5wH8wxgsniSVKtVFFuUa
P5bDY3HS6wMGo42cTOhxAkAcdweQSQ3j7mfc5vh71HeAC1v/VAKGehGOUdeEIQNl
Sb2WuzpZkbfsrVzW6MdlgY6eE7ufRswhDPLWPC8MP0d1
-----END RSA PRIVATE KEY-----
""", # 2
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDZaFw0wOTA3MjUyMjQyMDZaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAxnH+pbOS
qlJlsHpKUQtV0oN1Mv+ESG+yUDxStFFGjkJv/UIRzpxqFqY/6nJ3D03kZsDdcXyi
CfV9hPYQaVNMn6z+puPmIagfBQ0aOyuI+nUhCttZIYD9071BjW5bCMX5NZWL/CZm
E0HdAZ77H6UrRckJ7VR8wAFpihBxD5WliZcCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQAwXqY1Sjvp9JSTHKklu7s0T6YmH/BKSXrHpS2xO69svK+ze5/+5td3jPn4Qe50
xwRNZSFmSLuJLfCO32QJSJTB7Vs5D3dNTZ2i8umsaodm97t8hit7L75nXRGHKH//
xDVWAFB9sSgCQyPMRkL4wB4YSfRhoSKVwMvaz+XRZDUU0A==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXAIBAAKBgQDGcf6ls5KqUmWwekpRC1XSg3Uy/4RIb7JQPFK0UUaOQm/9QhHO
nGoWpj/qcncPTeRmwN1xfKIJ9X2E9hBpU0yfrP6m4+YhqB8FDRo7K4j6dSEK21kh
gP3TvUGNblsIxfk1lYv8JmYTQd0BnvsfpStFyQntVHzAAWmKEHEPlaWJlwIDAQAB
AoGAdHNMlXwtItm7ZrY8ihZ2xFP0IHsk60TwhHkBp2LSXoTKJvnwbSgIcUYZ18BX
8Zkp4MpoqEIU7HcssyuaMdR572huV2w0D/2gYJQLQ5JapaR3hMox3YG4wjXasN1U
1iZt7JkhKlOy+ElL5T9mKTE1jDsX2RAv4WALzMpYFo7vs4ECQQDxqrPaqRQ5uYS/
ejmIk05nM3Q1zmoLtMDrfRqrjBhaf/W3hqGihiqN2kL3PIIYcxSRWiyNlYXjElsR
2sllBTe3AkEA0jcMHVThwKt1+Ce5VcE7N6hFfbsgISTjfJ+Q3K2NkvJkmtE8ZRX5
XprssnPN8owkfF5yuKbcSZL3uvaaSGN9IQJAfTVnN9wwOXQwHhDSbDt9/KRBCnum
n+gHqDrKLaVJHOJ9SZf8eLswoww5c+UqtkYxmtlwie61Tp+9BXQosilQ4wJBAIZ1
XVNZmriBM4jR59L5MOZtxF0ilu98R+HLsn3kqLyIPF9mXCoQPxwLHkEan213xFKk
mt6PJDIPRlOZLqAEuuECQFQMCrn0VUwPg8E40pxMwgMETvVflPs/oZK1Iu+b7+WY
vBptAyhMu31fHQFnJpiUOyHqSZnOZyEn1Qu2lszNvUg=
-----END RSA PRIVATE KEY-----
""", # 3
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDZaFw0wOTA3MjUyMjQyMDZaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAnjiOwipn
jigDuNMfNG/tBJhPwYUHhSbQdvrTubhsxw1oOq5XpNqUwRtC8hktOKM3hghyqExP
62EOi0aJBkRhtwtPSLBCINptArZLfkog/nTIqVv4eLEzJ19nTi/llHHWKcgA6XTI
sU/snUhGlySA3RpETvXqIJTauQRZz0kToSUCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQCQ+u/CsX5WC5m0cLrpyIS6qZa62lrB3mj9H1aIQhisT5kRsMz3FJ1aOaS8zPRz
w0jhyRmamCcSsWf5WK539iOtsXbKMdAyjNtkQO3g+fnsLgmznAjjst24jfr+XU59
0amiy1U6TY93gtEBZHtiLldPdUMsTuFbBlqbcMBQ50x9rA==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXAIBAAKBgQCeOI7CKmeOKAO40x80b+0EmE/BhQeFJtB2+tO5uGzHDWg6rlek
2pTBG0LyGS04ozeGCHKoTE/rYQ6LRokGRGG3C09IsEIg2m0Ctkt+SiD+dMipW/h4
sTMnX2dOL+WUcdYpyADpdMixT+ydSEaXJIDdGkRO9eoglNq5BFnPSROhJQIDAQAB
AoGAAPrst3s3xQOucjismtCOsVaYN+SxFTwWUoZfRWlFEz6cBLELzfOktEWM9p79
TrqEH4px22UNobGqO2amdql5yXwEFVhYQkRB8uDA8uVaqpL8NLWTGPRXxZ2DSU+n
7/FLf/TWT3ti/ZtXaPVRj6E2/Mq9AVEVOjUYzkNjM02OxcECQQDKEqmPbdZq2URU
7RbUxkq5aTp8nzAgbpUsgBGQ9PDAymhj60BDEP0q28Ssa7tU70pRnQ3AZs9txgmL
kK2g97FNAkEAyHH9cIb6qXOAJPIr/xamFGr5uuYw9TJPz/hfVkVimW/aZnBB+e6Q
oALJBDKJWeYPzdNbouJYg8MeU0qWdZ5DOQJADUk+1sxc/bd9U6wnBSRog1pU2x7I
VkmPC1b8ULCaJ8LnLDKqjf5O9wNuIfwPXB1DoKwX3F+mIcyUkhWYJO5EPQJAUj5D
KMqZSrGzYHVlC/M1Daee88rDR7fu+3wDUhiCDkbQq7tftrbl7GF4LRq3NIWq8l7I
eJq6isWiSbaO6Y+YMQJBAJFBpVhlY5Px2BX5+Hsfq6dSP3sVVc0eHkdsoZFFxq37
fksL/q2vlPczvBihgcxt+UzW/UrNkelOuX3i57PDvFs=
-----END RSA PRIVATE KEY-----
""", # 4
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQyMDZaFw0wOTA3MjUyMjQyMDZaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAsCQuudDF
zgmY5tDpT0TkUo8fpJ5JcvgCkLFpSDD8REpXhLFkHWhTmTj3CAxfv4lA3sQzHZxe
4S9YCb5c/VTbFEdgwc/wlxMmJiz2jYghdmWPBb8pBEk31YihIhC+u4kex6gJBH5y
ixiZ3PPRRMaOBBo+ZfM50XIyWbFOOM/7FwcCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQB4cFURaiiUx6n8eS4j4Vxrii5PtsaNEI4acANFSYknGd0xTP4vnmoivNmo5fWE
Q4hYtGezNu4a9MnNhcQmI20KzXmvhLJtkwWCgGOVJtMem8hDWXSALV1Ih8hmVkGS
CI1elfr9eyguunGp9eMMQfKhWH52WHFA0NYa0Kpv5BY33A==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICWwIBAAKBgQCwJC650MXOCZjm0OlPRORSjx+knkly+AKQsWlIMPxESleEsWQd
aFOZOPcIDF+/iUDexDMdnF7hL1gJvlz9VNsUR2DBz/CXEyYmLPaNiCF2ZY8FvykE
STfViKEiEL67iR7HqAkEfnKLGJnc89FExo4EGj5l8znRcjJZsU44z/sXBwIDAQAB
AoGABA7xXKqoxBSIh1js5zypHhXaHsre2l1Igdj0mgs25MPpvE7yBZNvyan8Vx0h
36Hj8r4Gh3og3YNfvem67sNTwNwONY0ep+Xho/3vG0jFATGduSXdcT04DusgZNqg
UJqW75cqxrD6o/nya5wUoN9NL5pcd5AgVMdOYvJGbrwQuaECQQDiCs/5dsUkUkeC
Tlur1wh0wJpW4Y2ctO3ncRdnAoAA9y8dELHXMqwKE4HtlyzHY7Bxds/BDh373EVK
rsdl+v9JAkEAx3xTmsOQvWa1tf/O30sdItVpGogKDvYqkLCNthUzPaL85BWB03E2
xunHcVVlqAOE5tFuw0/UEyEkOaGlNTJTzwJAPIVel9FoCUiKYuYt/z1swy3KZRaw
/tMmm4AZHvh5Y0jLcYHFy/OCQpRkhkOitqQHWunPyEXKW2PnnY5cTv68GQJAHG7H
B88KCUTjb25nkQIGxBlA4swzCtDhXkAb4rEA3a8mdmfuWjHPyeg2ShwO4jSmM7P0
Iph1NMjLff9hKcTjlwJARpItOFkYEdtSODC7FMm7KRKQnNB27gFAizsOYWD4D2b7
w1FTEZ/kSA9wSNhyNGt7dgUo6zFhm2u973HBCUb3dg==
-----END RSA PRIVATE KEY-----
""", # 5
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAvhTRj1dA
NOfse/UBeTfMekZKxZHsNPr+qBYaveWAHDded/BMyMgaMV2n6HQdiDaRjJkzjHCF
3xBtpIJeEGUqfrF0ob8BIZXy3qk68eX/0CVUbgmjSBN44ahlo63NshyXmZtEAkRV
VE/+cRKw3N2wtuTed5xwfNcL6dg4KTOEYEkCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQCN+CLuVwLeWjSdVbdizYyrOVckqtwiIHG9BbGMlcIdm0qpvD7V7/sN2csk5LaT
BNiHi1t5628/4UHqqodYmFw8ri8ItFwB+MmTJi11CX6dIP9OUhS0qO8Z/BKtot7H
j04oNwl+WqZZfHIYwTIEL0HBn60nOvCQPDtnWG2BhpUxMA==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQC+FNGPV0A05+x79QF5N8x6RkrFkew0+v6oFhq95YAcN1538EzI
yBoxXafodB2INpGMmTOMcIXfEG2kgl4QZSp+sXShvwEhlfLeqTrx5f/QJVRuCaNI
E3jhqGWjrc2yHJeZm0QCRFVUT/5xErDc3bC25N53nHB81wvp2DgpM4RgSQIDAQAB
AoGALl2BqIdN4Bnac3oV++2CcSkIQB0SEvJOf820hDGhCEDxSCxTbn5w9S21MVxx
f7Jf2n3cNxuTbA/jzscGDtW+gXCs+WAbAr5aOqHLUPGEobhKQrQT2hrxQHyv3UFp
0tIl9eXFknOyVAaUJ3athK5tyjSiCZQQHLGzeLaDSKVAPqECQQD1GK7DkTcLaSvw
hoTJ3dBK3JoKT2HHLitfEE0QV58mkqFMjofpe+nyeKWvEb/oB4WBp/cfTvtf7DJK
zl1OSf11AkEAxomWmJeub0xpqksCmnVI1Jt1mvmcE4xpIcXq8sxzLHRc2QOv0kTw
IcFl4QcN6EQBmE+8kl7Tx8SPAVKfJMoZBQJAGsUFYYrczjxAdlba7glyFJsfn/yn
m0+poQpwwFYxpc7iGzB+G7xTAw62WfbAVSFtLYog7aR8xC9SFuWPP1vJeQJBAILo
xBj3ovgWTXIRJbVM8mnl28UFI0msgsHXK9VOw/6i93nMuYkPFbtcN14KdbwZ42dX
5EIrLr+BNr4riW4LqDUCQQCbsEEpTmj3upKUOONPt+6CH/OOMjazUzYHZ/3ORHGp
Q3Wt+I4IrR/OsiACSIQAhS4kBfk/LGggnj56DrWt+oBl
-----END RSA PRIVATE KEY-----
""", #6
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAtKhx6sEA
jn6HWc6T2klwlPn0quyHtATIw8V3ezP46v6g2rRS7dTywo4GTP4vX58l+sC9z9Je
qhQ1rWSwMK4FmnDMZCu7AVO7oMIXpXdSz7l0bgCnNjvbpkA2pOfbB1Z8oj8iebff
J33ID5DdkmCzqYVtKpII1o/5z7Jo292JYy8CAwEAATANBgkqhkiG9w0BAQQFAAOB
gQA0PYMA07wo9kEH4fv9TCfo+zz42Px6lUxrQBPxBvDiGYhk2kME/wX0IcoZPKTV
WyBGmDAYWvFaHWbrbbTOfzlLWfYrDD913hCi9cO8iF8oBqRjIlkKcxAoe7vVg5Az
ydVcrY+zqULJovWwyNmH1QNIQfMat0rj7fylwjiS1y/YsA==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXAIBAAKBgQC0qHHqwQCOfodZzpPaSXCU+fSq7Ie0BMjDxXd7M/jq/qDatFLt
1PLCjgZM/i9fnyX6wL3P0l6qFDWtZLAwrgWacMxkK7sBU7ugwheld1LPuXRuAKc2
O9umQDak59sHVnyiPyJ5t98nfcgPkN2SYLOphW0qkgjWj/nPsmjb3YljLwIDAQAB
AoGAU4CYRv22mCZ7wVLunDLdyr5ODMMPZnHfqj2XoGbBYz0WdIBs5GlNXAfxeZzz
oKsbDvAPzANcphh5RxAHMDj/dT8rZOez+eJrs1GEV+crl1T9p83iUkAuOJFtgUgf
TtQBL9vHaj7DfvCEXcBPmN/teDFmAAOyUNbtuhTkRa3PbuECQQDwaqZ45Kr0natH
V312dqlf9ms8I6e873pAu+RvA3BAWczk65eGcRjEBxVpTvNEcYKFrV8O5ZYtolrr
VJl97AfdAkEAwF4w4KJ32fLPVoPnrYlgLw86NejMpAkixblm8cn51avPQmwbtahb
BZUuca22IpgDpjeEk5SpEMixKe/UjzxMewJBALy4q2cY8U3F+u6sshLtAPYQZIs3
3fNE9W2dUKsIQvRwyZMlkLN7UhqHCPq6e+HNTM0MlCMIfAPkf4Rdy4N6ZY0CQCKE
BAMaQ6TwgzFDw5sIjiCDe+9WUPmRxhJyHL1/fvtOs4Z4fVRP290ZklbFU2vLmMQH
LBuKzfb7+4XJyXrV1+cCQBqfPFQQZLr5UgccABYQ2jnWVbJPISJ5h2b0cwXt+pz/
8ODEYLjqWr9K8dtbgwdpzwbkaGhQYpyvsguMvNPMohs=
-----END RSA PRIVATE KEY-----
""", #7
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAnBfNHycn
5RnYzDN4EWTk2q1BBxA6ZYtlG1WPkj5iKeaYKzUk58zBL7mNOA0ucq+yTwh9C4IC
EutWPaKBSKY5XI+Rdebh+Efq+urtOLgfJHlfcCraEx7hYN+tqqMVgEgnO/MqIsn1
I1Fvnp89mSYbQ9tmvhSH4Hm+nbeK6iL2tIsCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQBt9zxfsKWoyyV764rRb6XThuTDMNSDaVofqePEWjudAbDu6tp0pHcrL0XpIrnT
3iPgD47pdlwQNbGJ7xXwZu2QTOq+Lv62E6PCL8FljDVoYqR3WwJFFUigNvBT2Zzu
Pxx7KUfOlm/M4XUSMu31sNJ0kQniBwpkW43YmHVNFb/R7g==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQCcF80fJyflGdjMM3gRZOTarUEHEDpli2UbVY+SPmIp5pgrNSTn
zMEvuY04DS5yr7JPCH0LggIS61Y9ooFIpjlcj5F15uH4R+r66u04uB8keV9wKtoT
HuFg362qoxWASCc78yoiyfUjUW+enz2ZJhtD22a+FIfgeb6dt4rqIva0iwIDAQAB
AoGBAIHstcnWd7iUeQYPWUNxLaRvTY8pjNH04yWLZEOgNWkXDVX5mExw++RTmB4t
qpm/cLWkJSEtB7jjthb7ao0j/t2ljqfr6kAbClDv3zByAEDhOu8xB/5ne6Ioo+k2
dygC+GcVcobhv8qRU+z0fpeXSP8yS1bQQHOaa17bSGsncvHRAkEAzwsn8jBTOqaW
6Iymvr7Aql++LiwEBrqMMRVyBZlkux4hiKa2P7XXEL6/mOPR0aI2LuCqE2COrO7R
0wAFZ54bjwJBAMEAe6cs0zI3p3STHwA3LoSZB81lzLhGUnYBvOq1yoDSlJCOYpld
YM1y3eC0vwiOnEu3GG1bhkW+h6Kx0I/qyUUCQBiH9NqwORxI4rZ4+8S76y4EnA7y
biOx9KxYIyNgslutTUHYpt1TmUDFqQPfclvJQWw6eExFc4Iv5bJ/XSSSyicCQGyY
5PrwEfYTsrm5fpwUcKxTnzxHp6WYjBWybKZ0m/lYhBfCxmAdVrbDh21Exqj99Zv0
7l26PhdIWfGFtCEGrzECQQCtPyXa3ostSceR7zEKxyn9QBCNXKARfNNTBja6+VRE
qDC6jLqzu/SoOYaqa13QzCsttO2iZk8Ygfy3Yz0n37GE
-----END RSA PRIVATE KEY-----
""", #8
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEA4mnLf+x0
CWKDKP5PLZ87t2ReSDE/J5QoI5VhE0bXaahdhPrQTC2wvOpT+N9nzEpI9ASh/ejV
kYGlc03nNKRL7zyVM1UyGduEwsRssFMqfyJhI1p+VmxDMWNplex7mIAheAdskPj3
pwi2CP4VIMjOj368AXvXItPzeCfAhYhEVaMCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQAEzmwq5JFI5Z0dX20m9rq7NKgwRyAH3h5aE8bdjO8nEc69qscfDRx79Lws3kK8
A0LG0DhxKB8cTNu3u+jy81tjcC4pLNQ5IKap9ksmP7RtIHfTA55G8M3fPl2ZgDYQ
ZzsWAZvTNXd/eme0SgOzD10rfntA6ZIgJTWHx3E0RkdwKw==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDiact/7HQJYoMo/k8tnzu3ZF5IMT8nlCgjlWETRtdpqF2E+tBM
LbC86lP432fMSkj0BKH96NWRgaVzTec0pEvvPJUzVTIZ24TCxGywUyp/ImEjWn5W
bEMxY2mV7HuYgCF4B2yQ+PenCLYI/hUgyM6PfrwBe9ci0/N4J8CFiERVowIDAQAB
AoGAQYTl+8XcKl8Un4dAOG6M5FwqIHAH25c3Klzu85obehrbvUCriG/sZi7VT/6u
VeLlS6APlJ+NNgczbrOLhaNJyYzjICSt8BI96PldFUzCEkVlgE+29pO7RNoZmDYB
dSGyIDrWdVYfdzpir6kC0KDcrpA16Sc+/bK6Q8ALLRpC7QECQQD7F7fhIQ03CKSk
lS4mgDuBQrB/52jXgBumtjp71ANNeaWR6+06KDPTLysM+olsh97Q7YOGORbrBnBg
Y2HPnOgjAkEA5taZaMfdFa8V1SPcX7mgCLykYIujqss0AmauZN/24oLdNE8HtTBF
OLaxE6PnQ0JWfx9KGIy3E0V3aFk5FWb0gQJBAO4KFEaXgOG1jfCBhNj3JHJseMso
5Nm4F366r0MJQYBHXNGzqphB2K/Svat2MKX1QSUspk2u/a0d05dtYCLki6UCQHWS
sChyQ+UbfF9HGKOZBC3vBzo1ZXNEdIUUj5bJjBHq3YgbCK38nAU66A482TmkvDGb
Wj4OzeB+7Ua0yyJfggECQQDVlAa8HqdAcrbEwI/YfPydFsavBJ0KtcIGK2owQ+dk
dhlDnpXDud/AtX4Ft2LaquQ15fteRrYjjwI9SFGytjtp
-----END RSA PRIVATE KEY-----
""", #9
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAueLfowPT
kXXtHeU2FZSz2mJhHmjqeyI1oMoyyggonccx65vMxaRfljnz2dOjVVYpCOn/LrdP
wVxHO8KNDsmQeWPRjnnBa2dFqqOnp/8gEJFJBW7K/gI9se6o+xe9QIWBq6d/fKVR
BURJe5TycLogzZuxQn1xHHILa3XleYuHAbMCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQBEC1lfC3XK0galQC96B7faLpnQmhn5lX2FUUoFIQQtBTetoE+gTqnLSOIZcOK4
pkT3YvxUvgOV0LOLClryo2IknMMGWRSAcXtVUBBLRHVTSSuVUyyLr5kdRU7B4E+l
OU0j8Md/dzlkm//K1bzLyUaPq204ofH8su2IEX4b3IGmAQ==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICWwIBAAKBgQC54t+jA9ORde0d5TYVlLPaYmEeaOp7IjWgyjLKCCidxzHrm8zF
pF+WOfPZ06NVVikI6f8ut0/BXEc7wo0OyZB5Y9GOecFrZ0Wqo6en/yAQkUkFbsr+
Aj2x7qj7F71AhYGrp398pVEFREl7lPJwuiDNm7FCfXEccgtrdeV5i4cBswIDAQAB
AoGAO4PnJHNaLs16AMNdgKVevEIZZDolMQ1v7C4w+ryH/JRFaHE2q+UH8bpWV9zK
A82VT9RTrqpkb71S1VBiB2UDyz263XdAI/N2HcIVMmfKb72oV4gCI1KOv4DfFwZv
tVVcIdVEDBOZ2TgqK4opGOgWMDqgIAl2z3PbsIoNylZHEJECQQDtQeJFhEJGH4Qz
BGpdND0j2nnnJyhOFHJqikJNdul3uBwmxTK8FPEUUH/rtpyUan3VMOyDx3kX4OQg
GDNSb32rAkEAyJIZIJ0EMRHVedyWsfqR0zTGKRQ+qsc3sCfyUhFksWms9jsSS0DT
tVeTdC3F6EIAdpKOGhSyfBTU4jxwbFc0GQJADI4L9znEeAl66Wg2aLA2/Aq3oK/F
xjv2wgSG9apxOFCZzMNqp+FD0Jth6YtEReZMuldYbLDFi6nu6HPfY2Fa+QJAdpm1
lAxk6yMxiZK/5VRWoH6HYske2Vtd+aNVbePtF992ME/z3F3kEkpL3hom+dT1cyfs
MU3l0Ot8ip7Ul6vlGQJAegNzpcfl2GFSdWQMxQ+nN3woKnPqpR1M3jgnqvo7L4Xe
JW3vRxvfdrUuzdlvZ/Pbsu/vOd+cuIa4h0yD5q3N+g==
-----END RSA PRIVATE KEY-----
""", #10
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wODA3MjUyMjQ3NThaFw0wOTA3MjUyMjQ3NThaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAruBhwk+J
XdlwfKXXN8K+43JyEYCV7Fp7ZiES4t4AEJuQuBqJVMxpzeZzu2t/vVb59ThaxxtY
NGD3Xy6Og5dTv//ztWng8P7HwwvfbrUICU6zo6JAhg7kfaNa116krCYOkC/cdJWt
o5W+zsDmI1jUVGH0D73h29atc1gn6wLpAsMCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQAEJ/ITGJ9lK/rk0yHcenW8SHsaSTlZMuJ4yEiIgrJ2t71Rd6mtCC/ljx9USvvK
bF500whTiZlnWgKi02boBEKa44z/DytF6pljeNPefBQSqZyUByGEb/8Mn58Idyls
q4/d9iKXMPvbpQdcesOzgOffFZevLQSWyPRaIdYBOOiYUA==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQCu4GHCT4ld2XB8pdc3wr7jcnIRgJXsWntmIRLi3gAQm5C4GolU
zGnN5nO7a3+9Vvn1OFrHG1g0YPdfLo6Dl1O///O1aeDw/sfDC99utQgJTrOjokCG
DuR9o1rXXqSsJg6QL9x0la2jlb7OwOYjWNRUYfQPveHb1q1zWCfrAukCwwIDAQAB
AoGAcZAXC/dYrlBpIxkTRQu7qLqGZuVI9t7fabgqqpceFargdR4Odrn0L5jrKRer
MYrM8bjyAoC4a/NYUUBLnhrkcCQWO9q5fSQuFKFVWHY53SM63Qdqk8Y9Fmy/h/4c
UtwZ5BWkUWItvnTMgb9bFcvSiIhEcNQauypnMpgNknopu7kCQQDlSQT10LkX2IGT
bTUhPcManx92gucaKsPONKq2mP+1sIciThevRTZWZsxyIuoBBY43NcKKi8NlZCtj
hhSbtzYdAkEAw0B93CXfso8g2QIMj/HJJz/wNTLtg+rriXp6jh5HWe6lKWRVrce+
1w8Qz6OI/ZP6xuQ9HNeZxJ/W6rZPW6BGXwJAHcTuRPA1p/fvUvHh7Q/0zfcNAbkb
QlV9GL/TzmNtB+0EjpqvDo2g8XTlZIhN85YCEf8D5DMjSn3H+GMHN/SArQJBAJlW
MIGPjNoh5V4Hae4xqBOW9wIQeM880rUo5s5toQNTk4mqLk9Hquwh/MXUXGUora08
2XGpMC1midXSTwhaGmkCQQCdivptFEYl33PrVbxY9nzHynpp4Mi89vQF0cjCmaYY
N8L+bvLd4BU9g6hRS8b59lQ6GNjryx2bUnCVtLcey4Jd
-----END RSA PRIVATE KEY-----
""", #11
]

# To disable the pre-computed tub certs, uncomment this line.
#SYSTEM_TEST_CERTS = []

TEST_DATA="\x02"*(immutable.upload.Uploader.URI_LIT_SIZE_THRESHOLD+1)

class ShareManglingMixin(SystemTestMixin):

    def setUp(self):
        # Set self.basedir to a temp dir which has the name of the current
        # test method in its name.
        self.basedir = self.mktemp()

        d = defer.maybeDeferred(SystemTestMixin.setUp, self)
        d.addCallback(lambda x: self.set_up_nodes())

        def _upload_a_file(ignored):
            cl0 = self.clients[0]
            # We need multiple segments to test crypttext hash trees that are
            # non-trivial (i.e. they have more than just one hash in them).
            cl0.DEFAULT_ENCODING_PARAMETERS['max_segment_size'] = 12
            d2 = cl0.upload(immutable.upload.Data(TEST_DATA, convergence=""))
            def _after_upload(u):
                filecap = u.uri
                self.uri = uri.CHKFileURI.init_from_string(filecap)
                return cl0.create_node_from_uri(filecap)
            d2.addCallback(_after_upload)
            return d2
        d.addCallback(_upload_a_file)

        def _stash_it(filenode):
            self.filenode = filenode
        d.addCallback(_stash_it)
        return d

    def find_shares(self, unused=None):
        """Locate shares on disk. Returns a dict that maps
        (clientnum,sharenum) to a string that contains the share container
        (copied directly from the disk, containing leases etc). You can
        modify this dict and then call replace_shares() to modify the shares.
        """
        shares = {} # k: (i, sharenum), v: data

        for i, c in enumerate(self.clients):
            sharedir = c.getServiceNamed("storage").sharedir
            for (dirp, dirns, fns) in os.walk(sharedir):
                for fn in fns:
                    try:
                        sharenum = int(fn)
                    except TypeError:
                        # Whoops, I guess that's not a share file then.
                        pass
                    else:
                        data = open(os.path.join(sharedir, dirp, fn), "rb").read()
                        shares[(i, sharenum)] = data

        return shares

    def replace_shares(self, newshares, storage_index):
        """Replace shares on disk. Takes a dictionary in the same form
        as find_shares() returns."""

        for i, c in enumerate(self.clients):
            sharedir = c.getServiceNamed("storage").sharedir
            for (dirp, dirns, fns) in os.walk(sharedir):
                for fn in fns:
                    try:
                        sharenum = int(fn)
                    except TypeError:
                        # Whoops, I guess that's not a share file then.
                        pass
                    else:
                        pathtosharefile = os.path.join(sharedir, dirp, fn)
                        os.unlink(pathtosharefile)
            for ((clientnum, sharenum), newdata) in newshares.iteritems():
                if clientnum == i:
                    fullsharedirp=os.path.join(sharedir, storage_index_to_dir(storage_index))
                    fileutil.make_dirs(fullsharedirp)
                    wf = open(os.path.join(fullsharedirp, str(sharenum)), "wb")
                    wf.write(newdata)
                    wf.close()

    def _delete_a_share(self, unused=None, sharenum=None):
        """ Delete one share. """

        shares = self.find_shares()
        ks = shares.keys()
        if sharenum is not None:
            k = [ key for key in shares.keys() if key[1] == sharenum ][0]
        else:
            k = random.choice(ks)
        del shares[k]
        self.replace_shares(shares, storage_index=self.uri.storage_index)

        return unused

    def _corrupt_a_share(self, unused, corruptor_func, sharenum):
        shares = self.find_shares()
        ks = [ key for key in shares.keys() if key[1] == sharenum ]
        assert ks, (shares.keys(), sharenum)
        k = ks[0]
        shares[k] = corruptor_func(shares[k])
        self.replace_shares(shares, storage_index=self.uri.storage_index)
        return corruptor_func

    def _corrupt_all_shares(self, unused, corruptor_func):
        """ All shares on disk will be corrupted by corruptor_func. """
        shares = self.find_shares()
        for k in shares.keys():
            self._corrupt_a_share(unused, corruptor_func, k[1])
        return corruptor_func

    def _corrupt_a_random_share(self, unused, corruptor_func):
        """ Exactly one share on disk will be corrupted by corruptor_func. """
        shares = self.find_shares()
        ks = shares.keys()
        k = random.choice(ks)
        self._corrupt_a_share(unused, corruptor_func, k[1])
        return k[1]

    def _count_reads(self):
        sum_of_read_counts = 0
        for thisclient in self.clients:
            counters = thisclient.stats_provider.get_stats()['counters']
            sum_of_read_counts += counters.get('storage_server.read', 0)
        return sum_of_read_counts

    def _count_allocates(self):
        sum_of_allocate_counts = 0
        for thisclient in self.clients:
            counters = thisclient.stats_provider.get_stats()['counters']
            sum_of_allocate_counts += counters.get('storage_server.allocate', 0)
        return sum_of_allocate_counts

    def _count_writes(self):
        sum_of_write_counts = 0
        for thisclient in self.clients:
            counters = thisclient.stats_provider.get_stats()['counters']
            sum_of_write_counts += counters.get('storage_server.write', 0)
        return sum_of_write_counts

    def _download_and_check_plaintext(self, unused=None):
        d = self.clients[1].downloader.download_to_data(self.uri)

        def _after_download(result):
            self.failUnlessEqual(result, TEST_DATA)
        d.addCallback(_after_download)
        return d

class ShouldFailMixin:
    def shouldFail(self, expected_failure, which, substring,
                   callable, *args, **kwargs):
        """Assert that a function call raises some exception. This is a
        Deferred-friendly version of TestCase.assertRaises() .

        Suppose you want to verify the following function:

         def broken(a, b, c):
             if a < 0:
                 raise TypeError('a must not be negative')
             return defer.succeed(b+c)

        You can use:
            d = self.shouldFail(TypeError, 'test name',
                                'a must not be negative',
                                broken, -4, 5, c=12)
        in your test method. The 'test name' string will be included in the
        error message, if any, because Deferred chains frequently make it
        difficult to tell which assertion was tripped.

        The substring= argument, if not None, must appear inside the
        stringified Failure, or the test will fail.
        """

        assert substring is None or isinstance(substring, str)
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnless(substring in str(res),
                                    "substring '%s' not in '%s'"
                                    % (substring, str(res)))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d

class WebErrorMixin:
    def explain_web_error(self, f):
        # an error on the server side causes the client-side getPage() to
        # return a failure(t.web.error.Error), and its str() doesn't show the
        # response body, which is where the useful information lives. Attach
        # this method as an errback handler, and it will reveal the hidden
        # message.
        f.trap(WebError)
        print "Web Error:", f.value, ":", f.value.response
        return f

    def _shouldHTTPError(self, res, which, validator):
        if isinstance(res, failure.Failure):
            res.trap(WebError)
            return validator(res)
        else:
            self.fail("%s was supposed to Error, not get '%s'" % (which, res))

    def shouldHTTPError(self, which,
                        code=None, substring=None, response_substring=None,
                        callable=None, *args, **kwargs):
        # returns a Deferred with the response body
        assert substring is None or isinstance(substring, str)
        assert callable
        def _validate(f):
            if code is not None:
                self.failUnlessEqual(f.value.status, str(code))
            if substring:
                code_string = str(f)
                self.failUnless(substring in code_string,
                                "substring '%s' not in '%s'"
                                % (substring, code_string))
            response_body = f.value.response
            if response_substring:
                self.failUnless(response_substring in response_body,
                                "response substring '%s' not in '%s'"
                                % (response_substring, response_body))
            return response_body
        d = defer.maybeDeferred(callable, *args, **kwargs)
        d.addBoth(self._shouldHTTPError, which, _validate)
        return d

class ErrorMixin(WebErrorMixin):
    def explain_error(self, f):
        if f.check(defer.FirstError):
            print "First Error:", f.value.subFailure
        return f

class MemoryConsumer:
    implements(IConsumer)
    def __init__(self):
        self.chunks = []
        self.done = False
    def registerProducer(self, p, streaming):
        if streaming:
            # call resumeProducing once to start things off
            p.resumeProducing()
        else:
            while not self.done:
                p.resumeProducing()
    def write(self, data):
        self.chunks.append(data)
    def unregisterProducer(self):
        self.done = True

def download_to_data(n, offset=0, size=None):
    d = n.read(MemoryConsumer(), offset, size)
    d.addCallback(lambda mc: "".join(mc.chunks))
    return d

def corrupt_field(data, offset, size, debug=False):
    if random.random() < 0.5:
        newdata = testutil.flip_one_bit(data, offset, size)
        if debug:
            log.msg("testing: corrupting offset %d, size %d flipping one bit orig: %r, newdata: %r" % (offset, size, data[offset:offset+size], newdata[offset:offset+size]))
        return newdata
    else:
        newval = testutil.insecurerandstr(size)
        if debug:
            log.msg("testing: corrupting offset %d, size %d randomizing field, orig: %r, newval: %r" % (offset, size, data[offset:offset+size], newval))
        return data[:offset]+newval+data[offset+size:]

def _corrupt_nothing(data):
    """Leave the data pristine. """
    return data

def _corrupt_file_version_number(data):
    """Scramble the file data -- the share file version number have one bit
    flipped or else will be changed to a random value."""
    return corrupt_field(data, 0x00, 4)

def _corrupt_size_of_file_data(data):
    """Scramble the file data -- the field showing the size of the share data
    within the file will be set to one smaller."""
    return corrupt_field(data, 0x04, 4)

def _corrupt_sharedata_version_number(data):
    """Scramble the file data -- the share data version number will have one
    bit flipped or else will be changed to a random value, but not 1 or 2."""
    return corrupt_field(data, 0x0c, 4)
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    newsharevernum = sharevernum
    while newsharevernum in (1, 2):
        newsharevernum = random.randrange(0, 2**32)
    newsharevernumbytes = struct.pack(">L", newsharevernum)
    return data[:0x0c] + newsharevernumbytes + data[0x0c+4:]

def _corrupt_sharedata_version_number_to_plausible_version(data):
    """Scramble the file data -- the share data version number will be
    changed to 2 if it is 1 or else to 1 if it is 2."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        newsharevernum = 2
    else:
        newsharevernum = 1
    newsharevernumbytes = struct.pack(">L", newsharevernum)
    return data[:0x0c] + newsharevernumbytes + data[0x0c+4:]

def _corrupt_segment_size(data):
    """Scramble the file data -- the field showing the size of the segment
    will have one bit flipped or else be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x04, 4, debug=False)
    else:
        return corrupt_field(data, 0x0c+0x04, 8, debug=False)

def _corrupt_size_of_sharedata(data):
    """Scramble the file data -- the field showing the size of the data
    within the share data will have one bit flipped or else will be changed
    to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x08, 4)
    else:
        return corrupt_field(data, 0x0c+0x0c, 8)

def _corrupt_offset_of_sharedata(data):
    """Scramble the file data -- the field showing the offset of the data
    within the share data will have one bit flipped or else be changed to a
    random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x0c, 4)
    else:
        return corrupt_field(data, 0x0c+0x14, 8)

def _corrupt_offset_of_ciphertext_hash_tree(data):
    """Scramble the file data -- the field showing the offset of the
    ciphertext hash tree within the share data will have one bit flipped or
    else be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x14, 4, debug=False)
    else:
        return corrupt_field(data, 0x0c+0x24, 8, debug=False)

def _corrupt_offset_of_block_hashes(data):
    """Scramble the file data -- the field showing the offset of the block
    hash tree within the share data will have one bit flipped or else will be
    changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x18, 4)
    else:
        return corrupt_field(data, 0x0c+0x2c, 8)

def _corrupt_offset_of_block_hashes_to_truncate_crypttext_hashes(data):
    """Scramble the file data -- the field showing the offset of the block
    hash tree within the share data will have a multiple of hash size
    subtracted from it, thus causing the downloader to download an incomplete
    crypttext hash tree."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        curval = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
        newval = random.randrange(0, max(1, (curval/hashutil.CRYPTO_VAL_SIZE)/2))*hashutil.CRYPTO_VAL_SIZE
        newvalstr = struct.pack(">L", newval)
        return data[:0x0c+0x18]+newvalstr+data[0x0c+0x18+4:]
    else:
        curval = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]
        newval = random.randrange(0, max(1, (curval/hashutil.CRYPTO_VAL_SIZE)/2))*hashutil.CRYPTO_VAL_SIZE
        newvalstr = struct.pack(">Q", newval)
        return data[:0x0c+0x2c]+newvalstr+data[0x0c+0x2c+8:]

def _corrupt_offset_of_share_hashes(data):
    """Scramble the file data -- the field showing the offset of the share
    hash tree within the share data will have one bit flipped or else will be
    changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x1c, 4)
    else:
        return corrupt_field(data, 0x0c+0x34, 8)

def _corrupt_offset_of_uri_extension(data):
    """Scramble the file data -- the field showing the offset of the uri
    extension will have one bit flipped or else will be changed to a random
    value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x20, 4)
    else:
        return corrupt_field(data, 0x0c+0x3c, 8)

def _corrupt_offset_of_uri_extension_to_force_short_read(data, debug=False):
    """Scramble the file data -- the field showing the offset of the uri
    extension will be set to the size of the file minus 3. This means when
    the client tries to read the length field from that location it will get
    a short read -- the result string will be only 3 bytes long, not the 4 or
    8 bytes necessary to do a successful struct.unpack."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    # The "-0x0c" in here is to skip the server-side header in the share
    # file, which the client doesn't see when seeking and reading.
    if sharevernum == 1:
        if debug:
            log.msg("testing: corrupting offset %d, size %d, changing %d to %d (len(data) == %d)" % (0x2c, 4, struct.unpack(">L", data[0x2c:0x2c+4])[0], len(data)-0x0c-3, len(data)))
        return data[:0x2c] + struct.pack(">L", len(data)-0x0c-3) + data[0x2c+4:]
    else:
        if debug:
            log.msg("testing: corrupting offset %d, size %d, changing %d to %d (len(data) == %d)" % (0x48, 8, struct.unpack(">Q", data[0x48:0x48+8])[0], len(data)-0x0c-3, len(data)))
        return data[:0x48] + struct.pack(">Q", len(data)-0x0c-3) + data[0x48+8:]

def _corrupt_mutable_share_data(data):
    prefix = data[:32]
    assert prefix == MutableShareFile.MAGIC, "This function is designed to corrupt mutable shares of v1, and the magic number doesn't look right: %r vs %r" % (prefix, MutableShareFile.MAGIC)
    data_offset = MutableShareFile.DATA_OFFSET
    sharetype = data[data_offset:data_offset+1]
    assert sharetype == "\x00", "non-SDMF mutable shares not supported"
    (version, ig_seqnum, ig_roothash, ig_IV, ig_k, ig_N, ig_segsize,
     ig_datalen, offsets) = unpack_header(data[data_offset:])
    assert version == 0, "this function only handles v0 SDMF files"
    start = data_offset + offsets["share_data"]
    length = data_offset + offsets["enc_privkey"] - start
    return corrupt_field(data, start, length)

def _corrupt_share_data(data):
    """Scramble the file data -- the field containing the share data itself
    will have one bit flipped or else will be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways, not v%d." % sharevernum
    if sharevernum == 1:
        sharedatasize = struct.unpack(">L", data[0x0c+0x08:0x0c+0x08+4])[0]

        return corrupt_field(data, 0x0c+0x24, sharedatasize)
    else:
        sharedatasize = struct.unpack(">Q", data[0x0c+0x08:0x0c+0x0c+8])[0]

        return corrupt_field(data, 0x0c+0x44, sharedatasize)

def _corrupt_crypttext_hash_tree(data):
    """Scramble the file data -- the field containing the crypttext hash tree
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        crypttexthashtreeoffset = struct.unpack(">L", data[0x0c+0x14:0x0c+0x14+4])[0]
        blockhashesoffset = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
    else:
        crypttexthashtreeoffset = struct.unpack(">Q", data[0x0c+0x24:0x0c+0x24+8])[0]
        blockhashesoffset = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]

    return corrupt_field(data, crypttexthashtreeoffset, blockhashesoffset-crypttexthashtreeoffset)

def _corrupt_block_hashes(data):
    """Scramble the file data -- the field containing the block hash tree
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        blockhashesoffset = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
        sharehashesoffset = struct.unpack(">L", data[0x0c+0x1c:0x0c+0x1c+4])[0]
    else:
        blockhashesoffset = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]
        sharehashesoffset = struct.unpack(">Q", data[0x0c+0x34:0x0c+0x34+8])[0]

    return corrupt_field(data, blockhashesoffset, sharehashesoffset-blockhashesoffset)

def _corrupt_share_hashes(data):
    """Scramble the file data -- the field containing the share hash chain
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        sharehashesoffset = struct.unpack(">L", data[0x0c+0x1c:0x0c+0x1c+4])[0]
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
    else:
        sharehashesoffset = struct.unpack(">Q", data[0x0c+0x34:0x0c+0x34+8])[0]
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]

    return corrupt_field(data, sharehashesoffset, uriextoffset-sharehashesoffset)

def _corrupt_length_of_uri_extension(data):
    """Scramble the file data -- the field showing the length of the uri
    extension will have one bit flipped or else will be changed to a random
    value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
        return corrupt_field(data, uriextoffset, 4)
    else:
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]
        return corrupt_field(data, uriextoffset, 8)

def _corrupt_uri_extension(data):
    """Scramble the file data -- the field containing the uri extension will
    have one bit flipped or else will be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
        uriextlen = struct.unpack(">L", data[0x0c+uriextoffset:0x0c+uriextoffset+4])[0]
    else:
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]
        uriextlen = struct.unpack(">Q", data[0x0c+uriextoffset:0x0c+uriextoffset+8])[0]

    return corrupt_field(data, uriextoffset, uriextlen)
