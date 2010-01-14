
import time
import os.path
from twisted.trial import unittest
from twisted.application import service
from twisted.internet import defer
from foolscap.api import eventually, fireEventually

from allmydata.util import fileutil, hashutil, pollmixin
from allmydata.storage.server import StorageServer, si_b2a
from allmydata.storage.crawler import ShareCrawler, TimeSliceExceeded

from test_storage import FakeCanary
from common_util import StallMixin

class BucketEnumeratingCrawler(ShareCrawler):
    cpu_slice = 500 # make sure it can complete in a single slice
    slow_start = 0
    def __init__(self, *args, **kwargs):
        ShareCrawler.__init__(self, *args, **kwargs)
        self.all_buckets = []
        self.finished_d = defer.Deferred()
    def process_bucket(self, cycle, prefix, prefixdir, storage_index_b32):
        self.all_buckets.append(storage_index_b32)
    def finished_cycle(self, cycle):
        eventually(self.finished_d.callback, None)

class PacedCrawler(ShareCrawler):
    cpu_slice = 500 # make sure it can complete in a single slice
    slow_start = 0
    def __init__(self, *args, **kwargs):
        ShareCrawler.__init__(self, *args, **kwargs)
        self.countdown = 6
        self.all_buckets = []
        self.finished_d = defer.Deferred()
        self.yield_cb = None
    def process_bucket(self, cycle, prefix, prefixdir, storage_index_b32):
        self.all_buckets.append(storage_index_b32)
        self.countdown -= 1
        if self.countdown == 0:
            # force a timeout. We restore it in yielding()
            self.cpu_slice = -1.0
    def yielding(self, sleep_time):
        self.cpu_slice = 500
        if self.yield_cb:
            self.yield_cb()
    def finished_cycle(self, cycle):
        eventually(self.finished_d.callback, None)

class ConsumingCrawler(ShareCrawler):
    cpu_slice = 0.5
    allowed_cpu_percentage = 0.5
    minimum_cycle_time = 0
    slow_start = 0

    def __init__(self, *args, **kwargs):
        ShareCrawler.__init__(self, *args, **kwargs)
        self.accumulated = 0.0
        self.cycles = 0
        self.last_yield = 0.0
    def process_bucket(self, cycle, prefix, prefixdir, storage_index_b32):
        start = time.time()
        time.sleep(0.05)
        elapsed = time.time() - start
        self.accumulated += elapsed
        self.last_yield += elapsed
    def finished_cycle(self, cycle):
        self.cycles += 1
    def yielding(self, sleep_time):
        self.last_yield = 0.0

class OneShotCrawler(ShareCrawler):
    cpu_slice = 500 # make sure it can complete in a single slice
    slow_start = 0
    def __init__(self, *args, **kwargs):
        ShareCrawler.__init__(self, *args, **kwargs)
        self.counter = 0
        self.finished_d = defer.Deferred()
    def process_bucket(self, cycle, prefix, prefixdir, storage_index_b32):
        self.counter += 1
    def finished_cycle(self, cycle):
        self.finished_d.callback(None)
        self.disownServiceParent()

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

        c = BucketEnumeratingCrawler(ss, statefile, allowed_cpu_percentage=.1)
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

        # it should be legal to call get_state() and get_progress() right
        # away, even before the first tick is performed. No work should have
        # been done yet.
        s = c.get_state()
        p = c.get_progress()
        self.failUnlessEqual(s["last-complete-prefix"], None)
        self.failUnlessEqual(s["current-cycle"], None)
        self.failUnlessEqual(p["cycle-in-progress"], False)

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
        # that should stop in the middle of one of the buckets. Since we
        # aren't using its normal scheduler, we have to save its state
        # manually.
        c.save_state()
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
        # that should stop in the middle of one of the buckets. Since we
        # aren't using its normal scheduler, we have to save its state
        # manually.
        c.save_state()
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
        # that should stop at the end of one of the buckets. Again we must
        # save state manually.
        c.save_state()
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
        c.save_state()

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

        did_check_progress = [False]
        def check_progress():
            c.yield_cb = None
            try:
                p = c.get_progress()
                self.failUnlessEqual(p["cycle-in-progress"], True)
                pct = p["cycle-complete-percentage"]
                # after 6 buckets, we happen to be at 76.17% complete. As
                # long as we create shares in deterministic order, this will
                # continue to be true.
                self.failUnlessEqual(int(pct), 76)
                left = p["remaining-sleep-time"]
                self.failUnless(isinstance(left, float), left)
                self.failUnless(left > 0.0, left)
            except Exception, e:
                did_check_progress[0] = e
            else:
                did_check_progress[0] = True
        c.yield_cb = check_progress

        c.setServiceParent(self.s)
        # that should get through 6 buckets, pause for a little while (and
        # run check_progress()), then resume

        d = c.finished_d
        def _check(ignored):
            if did_check_progress[0] is not True:
                raise did_check_progress[0]
            self.failUnless(did_check_progress[0])
            self.failUnlessEqual(sorted(sis), sorted(c.all_buckets))
            # at this point, the crawler should be sitting in the inter-cycle
            # timer, which should be pegged at the minumum cycle time
            self.failUnless(c.timer)
            self.failUnless(c.sleeping_between_cycles)
            self.failUnlessEqual(c.current_sleep_time, c.minimum_cycle_time)

            p = c.get_progress()
            self.failUnlessEqual(p["cycle-in-progress"], False)
            naptime = p["remaining-wait-time"]
            self.failUnless(isinstance(naptime, float), naptime)
            # min-cycle-time is 300, so this is basically testing that it took
            # less than 290s to crawl
            self.failUnless(naptime > 10.0, naptime)
            soon = p["next-crawl-time"] - time.time()
            self.failUnless(soon > 10.0, soon)

        d.addCallback(_check)
        return d

    def OFF_test_cpu_usage(self):
        # this test can't actually assert anything, because too many
        # buildslave machines are slow. But on a fast developer machine, it
        # can produce interesting results. So if you care about how well the
        # Crawler is accomplishing it's run-slowly goals, re-enable this test
        # and read the stdout when it runs.

        self.basedir = "crawler/Basic/cpu_usage"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        for i in range(10):
            self.write(i, ss, serverid)

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

        start = time.time()
        d = self.stall(delay=4.0)
        def _done(res):
            elapsed = time.time() - start
            percent = 100.0 * c.accumulated / elapsed
            # our buildslaves vary too much in their speeds and load levels,
            # and many of them only manage to hit 7% usage when our target is
            # 50%. So don't assert anything about the results, just log them.
            print
            print "crawler: got %d%% percent when trying for 50%%" % percent
            print "crawler: got %d full cycles" % c.cycles
        d.addCallback(_done)
        return d

    def test_empty_subclass(self):
        self.basedir = "crawler/Basic/empty_subclass"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        for i in range(10):
            self.write(i, ss, serverid)

        statefile = os.path.join(self.basedir, "statefile")
        c = ShareCrawler(ss, statefile)
        c.slow_start = 0
        c.setServiceParent(self.s)

        # we just let it run for a while, to get figleaf coverage of the
        # empty methods in the base class

        def _check():
            return bool(c.state["last-cycle-finished"] is not None)
        d = self.poll(_check)
        def _done(ignored):
            state = c.get_state()
            self.failUnless(state["last-cycle-finished"] is not None)
        d.addCallback(_done)
        return d


    def test_oneshot(self):
        self.basedir = "crawler/Basic/oneshot"
        fileutil.make_dirs(self.basedir)
        serverid = "\x00" * 20
        ss = StorageServer(self.basedir, serverid)
        ss.setServiceParent(self.s)

        for i in range(30):
            self.write(i, ss, serverid)

        statefile = os.path.join(self.basedir, "statefile")
        c = OneShotCrawler(ss, statefile)
        c.setServiceParent(self.s)

        d = c.finished_d
        def _finished_first_cycle(ignored):
            return fireEventually(c.counter)
        d.addCallback(_finished_first_cycle)
        def _check(old_counter):
            # the crawler should do any work after it's been stopped
            self.failUnlessEqual(old_counter, c.counter)
            self.failIf(c.running)
            self.failIf(c.timer)
            self.failIf(c.current_sleep_time)
            s = c.get_state()
            self.failUnlessEqual(s["last-cycle-finished"], 0)
            self.failUnlessEqual(s["current-cycle"], None)
        d.addCallback(_check)
        return d

