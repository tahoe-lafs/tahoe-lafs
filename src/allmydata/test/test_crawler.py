
import time
import sys
import os.path
from twisted.trial import unittest
from twisted.application import service
from twisted.internet import defer
from foolscap.eventual import eventually

from allmydata.util import fileutil, hashutil, pollmixin
from allmydata.storage.server import StorageServer, si_b2a
from allmydata.storage.crawler import ShareCrawler, TimeSliceExceeded

from test_storage import FakeCanary
from common_util import StallMixin

class BucketEnumeratingCrawler(ShareCrawler):
    cpu_slice = 500 # make sure it can complete in a single slice
    def __init__(self, server, statefile):
        ShareCrawler.__init__(self, server, statefile)
        self.all_buckets = []
        self.finished_d = defer.Deferred()
    def process_bucket(self, prefixdir, storage_index_b32):
        self.all_buckets.append(storage_index_b32)
    def finished_cycle(self):
        eventually(self.finished_d.callback, None)

class PacedCrawler(ShareCrawler):
    cpu_slice = 500 # make sure it can complete in a single slice
    def __init__(self, server, statefile):
        ShareCrawler.__init__(self, server, statefile)
        self.countdown = 6
        self.all_buckets = []
        self.finished_d = defer.Deferred()
    def process_bucket(self, prefixdir, storage_index_b32):
        self.all_buckets.append(storage_index_b32)
        self.countdown -= 1
        if self.countdown == 0:
            # force a timeout. We restore it in yielding()
            self.cpu_slice = -1.0
    def yielding(self, sleep_time):
        self.cpu_slice = 500
    def finished_cycle(self):
        eventually(self.finished_d.callback, None)

class ConsumingCrawler(ShareCrawler):
    cpu_slice = 0.5
    allowed_cpu_percentage = 0.5
    minimum_cycle_time = 0

    def __init__(self, server, statefile):
        ShareCrawler.__init__(self, server, statefile)
        self.accumulated = 0.0
        self.cycles = 0
        self.last_yield = 0.0
    def process_bucket(self, prefixdir, storage_index_b32):
        start = time.time()
        time.sleep(0.05)
        elapsed = time.time() - start
        self.accumulated += elapsed
        self.last_yield += elapsed
    def finished_cycle(self):
        self.cycles += 1
    def yielding(self, sleep_time):
        self.last_yield = 0.0

class Basic(unittest.TestCase, StallMixin, pollmixin.PollMixin):
    def setUp(self):
        self.s = service.MultiService()
        self.s.startService()

    def tearDown(self):
        return self.s.stopService()

    def si(self, i):
        return hashutil.storage_index_hash(str(i))
    def rs(self, i, serverid):
        return hashutil.bucket_renewal_secret_hash(str(i), serverid)
    def cs(self, i, serverid):
        return hashutil.bucket_cancel_secret_hash(str(i), serverid)

    def write(self, i, ss, serverid, tail=0):
        si = self.si(i)
        si = si[:-1] + chr(tail)
        had,made = ss.remote_allocate_buckets(si,
                                              self.rs(i, serverid),
                                              self.cs(i, serverid),
                                              set([0]), 99, FakeCanary())
        made[0].remote_write(0, "data")
        made[0].remote_close()
        return si_b2a(si)

    def test_immediate(self):
        self.basedir = "crawler/Basic/immediate"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        sis = [self.write(i, ss, serverid) for i in range(10)]
        statefile = os.path.join(self.basedir, "statefile")

        c = BucketEnumeratingCrawler(ss, statefile)
        c.load_state()

        c.start_current_prefix(time.time())
        self.failUnlessEqual(sorted(sis), sorted(c.all_buckets))

        # make sure the statefile has been returned to the starting point
        c.finished_d = defer.Deferred()
        c.all_buckets = []
        c.start_current_prefix(time.time())
        self.failUnlessEqual(sorted(sis), sorted(c.all_buckets))

        # check that a new crawler picks up on the state file properly
        c2 = BucketEnumeratingCrawler(ss, statefile)
        c2.load_state()

        c2.start_current_prefix(time.time())
        self.failUnlessEqual(sorted(sis), sorted(c2.all_buckets))

    def test_service(self):
        self.basedir = "crawler/Basic/service"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        sis = [self.write(i, ss, serverid) for i in range(10)]

        statefile = os.path.join(self.basedir, "statefile")
        c = BucketEnumeratingCrawler(ss, statefile)
        c.setServiceParent(self.s)

        d = c.finished_d
        def _check(ignored):
            self.failUnlessEqual(sorted(sis), sorted(c.all_buckets))
        d.addCallback(_check)
        return d

    def test_paced(self):
        self.basedir = "crawler/Basic/paced"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        # put four buckets in each prefixdir
        sis = []
        for i in range(10):
            for tail in range(4):
                sis.append(self.write(i, ss, serverid, tail))

        statefile = os.path.join(self.basedir, "statefile")

        c = PacedCrawler(ss, statefile)
        c.load_state()
        try:
            c.start_current_prefix(time.time())
        except TimeSliceExceeded:
            pass
        # that should stop in the middle of one of the buckets.
        c.cpu_slice = PacedCrawler.cpu_slice
        self.failUnlessEqual(len(c.all_buckets), 6)
        c.start_current_prefix(time.time()) # finish it
        self.failUnlessEqual(len(sis), len(c.all_buckets))
        self.failUnlessEqual(sorted(sis), sorted(c.all_buckets))

        # make sure the statefile has been returned to the starting point
        c.finished_d = defer.Deferred()
        c.all_buckets = []
        c.start_current_prefix(time.time())
        self.failUnlessEqual(sorted(sis), sorted(c.all_buckets))
        del c

        # start a new crawler, it should start from the beginning
        c = PacedCrawler(ss, statefile)
        c.load_state()
        try:
            c.start_current_prefix(time.time())
        except TimeSliceExceeded:
            pass
        # that should stop in the middle of one of the buckets
        c.cpu_slice = PacedCrawler.cpu_slice

        # a third crawler should pick up from where it left off
        c2 = PacedCrawler(ss, statefile)
        c2.all_buckets = c.all_buckets[:]
        c2.load_state()
        c2.countdown = -1
        c2.start_current_prefix(time.time())
        self.failUnlessEqual(len(sis), len(c2.all_buckets))
        self.failUnlessEqual(sorted(sis), sorted(c2.all_buckets))
        del c, c2

        # now stop it at the end of a bucket (countdown=4), to exercise a
        # different place that checks the time
        c = PacedCrawler(ss, statefile)
        c.load_state()
        c.countdown = 4
        try:
            c.start_current_prefix(time.time())
        except TimeSliceExceeded:
            pass
        # that should stop at the end of one of the buckets.
        c.cpu_slice = PacedCrawler.cpu_slice
        self.failUnlessEqual(len(c.all_buckets), 4)
        c.start_current_prefix(time.time()) # finish it
        self.failUnlessEqual(len(sis), len(c.all_buckets))
        self.failUnlessEqual(sorted(sis), sorted(c.all_buckets))
        del c

        # stop it again at the end of the bucket, check that a new checker
        # picks up correctly
        c = PacedCrawler(ss, statefile)
        c.load_state()
        c.countdown = 4
        try:
            c.start_current_prefix(time.time())
        except TimeSliceExceeded:
            pass
        # that should stop at the end of one of the buckets.

        c2 = PacedCrawler(ss, statefile)
        c2.all_buckets = c.all_buckets[:]
        c2.load_state()
        c2.countdown = -1
        c2.start_current_prefix(time.time())
        self.failUnlessEqual(len(sis), len(c2.all_buckets))
        self.failUnlessEqual(sorted(sis), sorted(c2.all_buckets))
        del c, c2

    def test_paced_service(self):
        self.basedir = "crawler/Basic/paced_service"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        sis = [self.write(i, ss, serverid) for i in range(10)]

        statefile = os.path.join(self.basedir, "statefile")
        c = PacedCrawler(ss, statefile)
        c.setServiceParent(self.s)
        # that should get through 6 buckets, pause for a little while, then
        # resume

        d = c.finished_d
        def _check(ignored):
            self.failUnlessEqual(sorted(sis), sorted(c.all_buckets))
            # at this point, the crawler should be sitting in the inter-cycle
            # timer, which should be pegged at the minumum cycle time
            self.failUnless(c.timer)
            self.failUnless(c.sleeping_between_cycles)
            self.failUnlessEqual(c.current_sleep_time, c.minimum_cycle_time)
        d.addCallback(_check)
        return d

    def test_cpu_usage(self):
        self.basedir = "crawler/Basic/cpu_usage"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        sis = [self.write(i, ss, serverid) for i in range(10)]

        statefile = os.path.join(self.basedir, "statefile")
        c = ConsumingCrawler(ss, statefile)
        c.setServiceParent(self.s)

        # this will run as fast as it can, consuming about 50ms per call to
        # process_bucket(), limited by the Crawler to about 50% cpu. We let
        # it run for a few seconds, then compare how much time
        # process_bucket() got vs wallclock time. It should get between 10%
        # and 70% CPU. This is dicey, there's about 100ms of overhead per
        # 300ms slice (saving the state file takes about 150-200us, but we do
        # it 1024 times per cycle, one for each [empty] prefixdir), leaving
        # 200ms for actual processing, which is enough to get through 4
        # buckets each slice, then the crawler sleeps for 300ms/0.5 = 600ms,
        # giving us 900ms wallclock per slice. In 4.0 seconds we can do 4.4
        # slices, giving us about 17 shares, so we merely assert that we've
        # finished at least one cycle in that time.

        # with a short cpu_slice (so we can keep this test down to 4
        # seconds), the overhead is enough to make a nominal 50% usage more
        # like 30%. Forcing sleep_time to 0 only gets us 67% usage.

        # the windows/cygwin buildslaves, which are slow (even by windows
        # standards) and have low-resolution timers, get more like 7% usage.
        # On windows I'll extend the allowable range.

        min_ok = 20
        if "cygwin" in sys.platform.lower() or "win32" in sys.platform.lower():
            min_ok = 3

        start = time.time()
        d = self.stall(delay=4.0)
        def _done(res):
            elapsed = time.time() - start
            percent = 100.0 * c.accumulated / elapsed
            self.failUnless(min_ok < percent < 70, "crawler got %d%%" % percent)
            self.failUnless(c.cycles >= 1, c.cycles)
        d.addCallback(_done)
        return d

    def test_empty_subclass(self):
        self.basedir = "crawler/Basic/empty_subclass"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        sis = [self.write(i, ss, serverid) for i in range(10)]

        statefile = os.path.join(self.basedir, "statefile")
        c = ShareCrawler(ss, statefile)
        c.setServiceParent(self.s)

        # we just let it run for a while, to get figleaf coverage of the
        # empty methods in the base class

        def _check():
            return c.first_cycle_finished
        d = self.poll(_check)
        return d

