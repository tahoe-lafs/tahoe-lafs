
import time
import os.path
from twisted.trial import unittest
from twisted.application import service
from twisted.internet import defer
from foolscap.api import fireEventually

from allmydata.util import fileutil, hashutil
from allmydata.storage.server import StorageServer, si_b2a
from allmydata.storage.crawler import ShareCrawler

from allmydata.test.test_storage import FakeCanary
from allmydata.test.common import CrawlerTestMixin
from allmydata.test.common_util import StallMixin


class EnumeratingCrawler(ShareCrawler):
    cpu_slice = 500 # make sure it can complete in a single slice
    slow_start = 0

    def __init__(self, *args, **kwargs):
        ShareCrawler.__init__(self, *args, **kwargs)
        self.sharesets = []

    def process_bucket(self, cycle, prefix, prefixdir, storage_index_b32):
        self.sharesets.append(storage_index_b32)


class ConsumingCrawler(ShareCrawler):
    cpu_slice = 0.5
    allowed_cpu_proportion = 0.5
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


class Basic(unittest.TestCase, StallMixin, CrawlerTestMixin):
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

    def create(self, basedir):
        self.basedir = basedir
        fileutil.make_dirs(basedir)
        self.serverid = "\x00" * 20
        server = StorageServer(basedir, self.serverid)
        server.setServiceParent(self.s)
        return server

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

    def test_service(self):
        ss = self.create("crawler/Basic/service")

        sis = [self.write(i, ss, self.serverid) for i in range(10)]

        statefile = os.path.join(self.basedir, "statefile")
        c = EnumeratingCrawler(ss, statefile)
        c.setServiceParent(self.s)

        # it should be legal to call get_state() and get_progress() right
        # away, even before the first tick is performed. No work should have
        # been done yet.
        s = c.get_state()
        p = c.get_progress()
        self.failUnlessEqual(s["last-complete-prefix"], None)
        self.failUnlessEqual(s["current-cycle"], None)
        self.failUnlessEqual(p["cycle-in-progress"], False)

        d = self._after_prefix(None, 'sg', c)
        def _after_sg_prefix(state):
            p = c.get_progress()
            self.failUnlessEqual(p["cycle-in-progress"], True)
            pct = p["cycle-complete-percentage"]
            # After the 'sg' prefix, we happen to be 76.17% complete and to
            # have processed 6 sharesets. As long as we create shares in
            # deterministic order, this will continue to be true.
            self.failUnlessEqual(int(pct), 76)
            self.failUnlessEqual(len(c.sharesets), 6)

            return c.set_hook('after_cycle')
        d.addCallback(_after_sg_prefix)

        def _after_first_cycle(ignored):
            self.failUnlessEqual(sorted(sis), sorted(c.sharesets))
        d.addCallback(_after_first_cycle)
        d.addBoth(self._wait_for_yield, c)

        # Check that a new crawler picks up on the state file correctly.
        def _new_crawler(ign):
            c2 = EnumeratingCrawler(ss, statefile)
            c2.setServiceParent(self.s)

            d2 = c2.set_hook('after_cycle')
            def _after_first_cycle2(ignored):
                self.failUnlessEqual(sorted(sis), sorted(c2.sharesets))
            d2.addCallback(_after_first_cycle2)
            d2.addBoth(self._wait_for_yield, c2)
            return d2
        d.addCallback(_new_crawler)
        return d

    def OFF_test_cpu_usage(self):
        # This test can't actually assert anything, because too many
        # buildslave machines are slow. But on a fast developer machine, it
        # can produce interesting results. So if you care about how well the
        # Crawler is accomplishing it's run-slowly goals, re-enable this test
        # and read the stdout when it runs.

        ss = self.create("crawler/Basic/cpu_usage")

        for i in range(10):
            self.write(i, ss, self.serverid)

        statefile = os.path.join(self.basedir, "statefile")
        c = ConsumingCrawler(ss, statefile)
        c.setServiceParent(self.s)

        # This will run as fast as it can, consuming about 50ms per call to
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
        d.addBoth(self._wait_for_yield, c)
        return d

    def test_empty_subclass(self):
        ss = self.create("crawler/Basic/empty_subclass")

        for i in range(10):
            self.write(i, ss, self.serverid)

        statefile = os.path.join(self.basedir, "statefile")
        c = ShareCrawler(ss, statefile)
        c.slow_start = 0
        c.setServiceParent(self.s)

        # We just let it run for a while, to get coverage of the
        # empty methods in the base class.

        d = defer.succeed(None)
        d.addBoth(self._wait_for_yield, c)
        return d

    def test_oneshot(self):
        ss = self.create("crawler/Basic/oneshot")

        for i in range(30):
            self.write(i, ss, self.serverid)

        statefile = os.path.join(self.basedir, "statefile")
        c = EnumeratingCrawler(ss, statefile)
        c.setServiceParent(self.s)

        d = c.set_hook('after_cycle')
        def _after_first_cycle(ignored):
            c.disownServiceParent()
            return fireEventually(len(c.sharesets))
        d.addCallback(_after_first_cycle)
        def _check(old_counter):
            # The crawler shouldn't do any work after it has been stopped.
            self.failUnlessEqual(old_counter, len(c.sharesets))
            self.failIf(c.running)
            self.failIf(c.timer)
            self.failIf(c.current_sleep_time)
            s = c.get_state()
            self.failUnlessEqual(s["last-cycle-finished"], 0)
            self.failUnlessEqual(s["current-cycle"], None)
        d.addCallback(_check)
        return d

