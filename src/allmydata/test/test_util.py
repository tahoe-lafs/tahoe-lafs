
def foo(): pass # keep the line number constant

import os, time
from StringIO import StringIO
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python.failure import Failure
from twisted.python import log

from allmydata.util import base32, idlib, humanreadable, mathutil, hashutil
from allmydata.util import assertutil, fileutil, deferredutil, abbreviate
from allmydata.util import limiter, time_format, pollmixin, cachedir
from allmydata.util import statistics, dictutil, pipeline
from allmydata.util import log as tahoe_log

class Base32(unittest.TestCase):
    def test_b2a_matches_Pythons(self):
        import base64
        y = "\x12\x34\x45\x67\x89\x0a\xbc\xde\xf0"
        x = base64.b32encode(y)
        while x and x[-1] == '=':
            x = x[:-1]
        x = x.lower()
        self.failUnlessEqual(base32.b2a(y), x)
    def test_b2a(self):
        self.failUnlessEqual(base32.b2a("\x12\x34"), "ci2a")
    def test_b2a_or_none(self):
        self.failUnlessEqual(base32.b2a_or_none(None), None)
        self.failUnlessEqual(base32.b2a_or_none("\x12\x34"), "ci2a")
    def test_a2b(self):
        self.failUnlessEqual(base32.a2b("ci2a"), "\x12\x34")
        self.failUnlessRaises(AssertionError, base32.a2b, "b0gus")

class IDLib(unittest.TestCase):
    def test_nodeid_b2a(self):
        self.failUnlessEqual(idlib.nodeid_b2a("\x00"*20), "a"*32)

class NoArgumentException(Exception):
    def __init__(self):
        pass

class HumanReadable(unittest.TestCase):
    def test_repr(self):
        hr = humanreadable.hr
        self.failUnlessEqual(hr(foo), "<foo() at test_util.py:2>")
        self.failUnlessEqual(hr(self.test_repr),
                             "<bound method HumanReadable.test_repr of <allmydata.test.test_util.HumanReadable testMethod=test_repr>>")
        self.failUnlessEqual(hr(1L), "1")
        self.failUnlessEqual(hr(10**40),
                             "100000000000000000...000000000000000000")
        self.failUnlessEqual(hr(self), "<allmydata.test.test_util.HumanReadable testMethod=test_repr>")
        self.failUnlessEqual(hr([1,2]), "[1, 2]")
        self.failUnlessEqual(hr({1:2}), "{1:2}")
        try:
            raise ValueError
        except Exception, e:
            self.failUnless(
                hr(e) == "<ValueError: ()>" # python-2.4
                or hr(e) == "ValueError()") # python-2.5
        try:
            raise ValueError("oops")
        except Exception, e:
            self.failUnless(
                hr(e) == "<ValueError: 'oops'>" # python-2.4
                or hr(e) == "ValueError('oops',)") # python-2.5
        try:
            raise NoArgumentException
        except Exception, e:
            self.failUnless(
                hr(e) == "<NoArgumentException>" # python-2.4
                or hr(e) == "NoArgumentException()") # python-2.5


class MyList(list):
    pass

class Math(unittest.TestCase):
    def test_div_ceil(self):
        f = mathutil.div_ceil
        self.failUnlessEqual(f(0, 1), 0)
        self.failUnlessEqual(f(0, 2), 0)
        self.failUnlessEqual(f(0, 3), 0)
        self.failUnlessEqual(f(1, 3), 1)
        self.failUnlessEqual(f(2, 3), 1)
        self.failUnlessEqual(f(3, 3), 1)
        self.failUnlessEqual(f(4, 3), 2)
        self.failUnlessEqual(f(5, 3), 2)
        self.failUnlessEqual(f(6, 3), 2)
        self.failUnlessEqual(f(7, 3), 3)

    def test_next_multiple(self):
        f = mathutil.next_multiple
        self.failUnlessEqual(f(5, 1), 5)
        self.failUnlessEqual(f(5, 2), 6)
        self.failUnlessEqual(f(5, 3), 6)
        self.failUnlessEqual(f(5, 4), 8)
        self.failUnlessEqual(f(5, 5), 5)
        self.failUnlessEqual(f(5, 6), 6)
        self.failUnlessEqual(f(32, 1), 32)
        self.failUnlessEqual(f(32, 2), 32)
        self.failUnlessEqual(f(32, 3), 33)
        self.failUnlessEqual(f(32, 4), 32)
        self.failUnlessEqual(f(32, 5), 35)
        self.failUnlessEqual(f(32, 6), 36)
        self.failUnlessEqual(f(32, 7), 35)
        self.failUnlessEqual(f(32, 8), 32)
        self.failUnlessEqual(f(32, 9), 36)
        self.failUnlessEqual(f(32, 10), 40)
        self.failUnlessEqual(f(32, 11), 33)
        self.failUnlessEqual(f(32, 12), 36)
        self.failUnlessEqual(f(32, 13), 39)
        self.failUnlessEqual(f(32, 14), 42)
        self.failUnlessEqual(f(32, 15), 45)
        self.failUnlessEqual(f(32, 16), 32)
        self.failUnlessEqual(f(32, 17), 34)
        self.failUnlessEqual(f(32, 18), 36)
        self.failUnlessEqual(f(32, 589), 589)

    def test_pad_size(self):
        f = mathutil.pad_size
        self.failUnlessEqual(f(0, 4), 0)
        self.failUnlessEqual(f(1, 4), 3)
        self.failUnlessEqual(f(2, 4), 2)
        self.failUnlessEqual(f(3, 4), 1)
        self.failUnlessEqual(f(4, 4), 0)
        self.failUnlessEqual(f(5, 4), 3)

    def test_is_power_of_k(self):
        f = mathutil.is_power_of_k
        for i in range(1, 100):
            if i in (1, 2, 4, 8, 16, 32, 64):
                self.failUnless(f(i, 2), "but %d *is* a power of 2" % i)
            else:
                self.failIf(f(i, 2), "but %d is *not* a power of 2" % i)
        for i in range(1, 100):
            if i in (1, 3, 9, 27, 81):
                self.failUnless(f(i, 3), "but %d *is* a power of 3" % i)
            else:
                self.failIf(f(i, 3), "but %d is *not* a power of 3" % i)

    def test_next_power_of_k(self):
        f = mathutil.next_power_of_k
        self.failUnlessEqual(f(0,2), 1)
        self.failUnlessEqual(f(1,2), 1)
        self.failUnlessEqual(f(2,2), 2)
        self.failUnlessEqual(f(3,2), 4)
        self.failUnlessEqual(f(4,2), 4)
        for i in range(5, 8): self.failUnlessEqual(f(i,2), 8, "%d" % i)
        for i in range(9, 16): self.failUnlessEqual(f(i,2), 16, "%d" % i)
        for i in range(17, 32): self.failUnlessEqual(f(i,2), 32, "%d" % i)
        for i in range(33, 64): self.failUnlessEqual(f(i,2), 64, "%d" % i)
        for i in range(65, 100): self.failUnlessEqual(f(i,2), 128, "%d" % i)

        self.failUnlessEqual(f(0,3), 1)
        self.failUnlessEqual(f(1,3), 1)
        self.failUnlessEqual(f(2,3), 3)
        self.failUnlessEqual(f(3,3), 3)
        for i in range(4, 9): self.failUnlessEqual(f(i,3), 9, "%d" % i)
        for i in range(10, 27): self.failUnlessEqual(f(i,3), 27, "%d" % i)
        for i in range(28, 81): self.failUnlessEqual(f(i,3), 81, "%d" % i)
        for i in range(82, 200): self.failUnlessEqual(f(i,3), 243, "%d" % i)

    def test_ave(self):
        f = mathutil.ave
        self.failUnlessEqual(f([1,2,3]), 2)
        self.failUnlessEqual(f([0,0,0,4]), 1)
        self.failUnlessAlmostEqual(f([0.0, 1.0, 1.0]), .666666666666)

    def test_round_sigfigs(self):
        f = mathutil.round_sigfigs
        self.failUnlessEqual(f(22.0/3, 4), 7.3330000000000002)

class Statistics(unittest.TestCase):
    def should_assert(self, msg, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
            self.fail(msg)
        except AssertionError, e:
            pass

    def failUnlessListEqual(self, a, b, msg = None):
        self.failUnlessEqual(len(a), len(b))
        for i in range(len(a)):
            self.failUnlessEqual(a[i], b[i], msg)

    def failUnlessListAlmostEqual(self, a, b, places = 7, msg = None):
        self.failUnlessEqual(len(a), len(b))
        for i in range(len(a)):
            self.failUnlessAlmostEqual(a[i], b[i], places, msg)

    def test_binomial_coeff(self):
        f = statistics.binomial_coeff
        self.failUnlessEqual(f(20, 0), 1)
        self.failUnlessEqual(f(20, 1), 20)
        self.failUnlessEqual(f(20, 2), 190)
        self.failUnlessEqual(f(20, 8), f(20, 12))
        self.should_assert("Should assert if n < k", f, 2, 3)

    def test_binomial_distribution_pmf(self):
        f = statistics.binomial_distribution_pmf

        pmf_comp = f(2, .1)
        pmf_stat = [0.81, 0.18, 0.01]
        self.failUnlessListAlmostEqual(pmf_comp, pmf_stat)

        # Summing across a PMF should give the total probability 1
        self.failUnlessAlmostEqual(sum(pmf_comp), 1)
        self.should_assert("Should assert if not 0<=p<=1", f, 1, -1)
        self.should_assert("Should assert if n < 1", f, 0, .1)

        out = StringIO()
        statistics.print_pmf(pmf_comp, out=out)
        lines = out.getvalue().splitlines()
        self.failUnlessEqual(lines[0], "i=0: 0.81")
        self.failUnlessEqual(lines[1], "i=1: 0.18")
        self.failUnlessEqual(lines[2], "i=2: 0.01")

    def test_survival_pmf(self):
        f = statistics.survival_pmf
        # Cross-check binomial-distribution method against convolution
        # method.
        p_list = [.9999] * 100 + [.99] * 50 + [.8] * 20
        pmf1 = statistics.survival_pmf_via_conv(p_list)
        pmf2 = statistics.survival_pmf_via_bd(p_list)
        self.failUnlessListAlmostEqual(pmf1, pmf2)
        self.failUnlessTrue(statistics.valid_pmf(pmf1))
        self.should_assert("Should assert if p_i > 1", f, [1.1]);
        self.should_assert("Should assert if p_i < 0", f, [-.1]);

    def test_repair_count_pmf(self):
        survival_pmf = statistics.binomial_distribution_pmf(5, .9)
        repair_pmf = statistics.repair_count_pmf(survival_pmf, 3)
        # repair_pmf[0] == sum(survival_pmf[0,1,2,5])
        # repair_pmf[1] == survival_pmf[4]
        # repair_pmf[2] = survival_pmf[3]
        self.failUnlessListAlmostEqual(repair_pmf,
                                       [0.00001 + 0.00045 + 0.0081 + 0.59049,
                                        .32805,
                                        .0729,
                                        0, 0, 0])

    def test_repair_cost(self):
        survival_pmf = statistics.binomial_distribution_pmf(5, .9)
        bwcost = statistics.bandwidth_cost_function
        cost = statistics.mean_repair_cost(bwcost, 1000,
                                           survival_pmf, 3, ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 558.90)
        cost = statistics.mean_repair_cost(bwcost, 1000,
                                           survival_pmf, 3, ul_dl_ratio=8.0)
        self.failUnlessAlmostEqual(cost, 1664.55)

        # I haven't manually checked the math beyond here -warner
        cost = statistics.eternal_repair_cost(bwcost, 1000,
                                              survival_pmf, 3,
                                              discount_rate=0, ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 65292.056074766246)
        cost = statistics.eternal_repair_cost(bwcost, 1000,
                                              survival_pmf, 3,
                                              discount_rate=0.05,
                                              ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 9133.6097158191551)

    def test_convolve(self):
        f = statistics.convolve
        v1 = [ 1, 2, 3 ]
        v2 = [ 4, 5, 6 ]
        v3 = [ 7, 8 ]
        v1v2result = [ 4, 13, 28, 27, 18 ]
        # Convolution is commutative
        r1 = f(v1, v2)
        r2 = f(v2, v1)
        self.failUnlessListEqual(r1, r2, "Convolution should be commutative")
        self.failUnlessListEqual(r1, v1v2result, "Didn't match known result")
        # Convolution is associative
        r1 = f(f(v1, v2), v3)
        r2 = f(v1, f(v2, v3))
        self.failUnlessListEqual(r1, r2, "Convolution should be associative")
        # Convolution is distributive
        r1 = f(v3, [ a + b for a, b in zip(v1, v2) ])
        tmp1 = f(v3, v1)
        tmp2 = f(v3, v2)
        r2 = [ a + b for a, b in zip(tmp1, tmp2) ]
        self.failUnlessListEqual(r1, r2, "Convolution should be distributive")
        # Convolution is scalar multiplication associative
        tmp1 = f(v1, v2)
        r1 = [ a * 4 for a in tmp1 ]
        tmp2 = [ a * 4 for a in v1 ]
        r2 = f(tmp2, v2)
        self.failUnlessListEqual(r1, r2, "Convolution should be scalar multiplication associative")

    def test_find_k(self):
        f = statistics.find_k
        g = statistics.pr_file_loss
        plist = [.9] * 10 + [.8] * 10 # N=20
        t = .0001
        k = f(plist, t)
        self.failUnlessEqual(k, 10)
        self.failUnless(g(plist, k) < t)

    def test_pr_file_loss(self):
        f = statistics.pr_file_loss
        plist = [.5] * 10
        self.failUnlessEqual(f(plist, 3), .0546875)

    def test_pr_backup_file_loss(self):
        f = statistics.pr_backup_file_loss
        plist = [.5] * 10
        self.failUnlessEqual(f(plist, .5, 3), .02734375)


class Asserts(unittest.TestCase):
    def should_assert(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except AssertionError, e:
            return str(e)
        except Exception, e:
            self.fail("assert failed with non-AssertionError: %s" % e)
        self.fail("assert was not caught")

    def should_not_assert(self, func, *args, **kwargs):
        if "re" in kwargs:
            regexp = kwargs["re"]
            del kwargs["re"]
        try:
            func(*args, **kwargs)
        except AssertionError, e:
            self.fail("assertion fired when it should not have: %s" % e)
        except Exception, e:
            self.fail("assertion (which shouldn't have failed) failed with non-AssertionError: %s" % e)
        return # we're happy


    def test_assert(self):
        f = assertutil._assert
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual(m, "'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("othermsg: 'message2' <type 'str'>", m)

    def test_precondition(self):
        f = assertutil.precondition
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual("precondition: 'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("precondition: 'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("precondition: othermsg: 'message2' <type 'str'>", m)

    def test_postcondition(self):
        f = assertutil.postcondition
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual("postcondition: 'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("postcondition: 'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("postcondition: othermsg: 'message2' <type 'str'>", m)

class FileUtil(unittest.TestCase):
    def mkdir(self, basedir, path, mode=0777):
        fn = os.path.join(basedir, path)
        fileutil.make_dirs(fn, mode)

    def touch(self, basedir, path, mode=None, data="touch\n"):
        fn = os.path.join(basedir, path)
        f = open(fn, "w")
        f.write(data)
        f.close()
        if mode is not None:
            os.chmod(fn, mode)

    def test_rm_dir(self):
        basedir = "util/FileUtil/test_rm_dir"
        fileutil.make_dirs(basedir)
        # create it again to test idempotency
        fileutil.make_dirs(basedir)
        d = os.path.join(basedir, "doomed")
        self.mkdir(d, "a/b")
        self.touch(d, "a/b/1.txt")
        self.touch(d, "a/b/2.txt", 0444)
        self.touch(d, "a/b/3.txt", 0)
        self.mkdir(d, "a/c")
        self.touch(d, "a/c/1.txt")
        self.touch(d, "a/c/2.txt", 0444)
        self.touch(d, "a/c/3.txt", 0)
        os.chmod(os.path.join(d, "a/c"), 0444)
        self.mkdir(d, "a/d")
        self.touch(d, "a/d/1.txt")
        self.touch(d, "a/d/2.txt", 0444)
        self.touch(d, "a/d/3.txt", 0)
        os.chmod(os.path.join(d, "a/d"), 0)

        fileutil.rm_dir(d)
        self.failIf(os.path.exists(d))
        # remove it again to test idempotency
        fileutil.rm_dir(d)

    def test_remove_if_possible(self):
        basedir = "util/FileUtil/test_remove_if_possible"
        fileutil.make_dirs(basedir)
        self.touch(basedir, "here")
        fn = os.path.join(basedir, "here")
        fileutil.remove_if_possible(fn)
        self.failIf(os.path.exists(fn))
        fileutil.remove_if_possible(fn) # should be idempotent
        fileutil.rm_dir(basedir)
        fileutil.remove_if_possible(fn) # should survive errors

    def test_open_or_create(self):
        basedir = "util/FileUtil/test_open_or_create"
        fileutil.make_dirs(basedir)
        fn = os.path.join(basedir, "here")
        f = fileutil.open_or_create(fn)
        f.write("stuff.")
        f.close()
        f = fileutil.open_or_create(fn)
        f.seek(0, 2)
        f.write("more.")
        f.close()
        f = open(fn, "r")
        data = f.read()
        f.close()
        self.failUnlessEqual(data, "stuff.more.")

    def test_NamedTemporaryDirectory(self):
        basedir = "util/FileUtil/test_NamedTemporaryDirectory"
        fileutil.make_dirs(basedir)
        td = fileutil.NamedTemporaryDirectory(dir=basedir)
        name = td.name
        self.failUnless(basedir in name)
        self.failUnless(basedir in repr(td))
        self.failUnless(os.path.isdir(name))
        del td
        # it is conceivable that we need to force gc here, but I'm not sure
        self.failIf(os.path.isdir(name))

    def test_rename(self):
        basedir = "util/FileUtil/test_rename"
        fileutil.make_dirs(basedir)
        self.touch(basedir, "here")
        fn = os.path.join(basedir, "here")
        fn2 = os.path.join(basedir, "there")
        fileutil.rename(fn, fn2)
        self.failIf(os.path.exists(fn))
        self.failUnless(os.path.exists(fn2))

    def test_du(self):
        basedir = "util/FileUtil/test_du"
        fileutil.make_dirs(basedir)
        d = os.path.join(basedir, "space-consuming")
        self.mkdir(d, "a/b")
        self.touch(d, "a/b/1.txt", data="a"*10)
        self.touch(d, "a/b/2.txt", data="b"*11)
        self.mkdir(d, "a/c")
        self.touch(d, "a/c/1.txt", data="c"*12)
        self.touch(d, "a/c/2.txt", data="d"*13)

        used = fileutil.du(basedir)
        self.failUnlessEqual(10+11+12+13, used)

class PollMixinTests(unittest.TestCase):
    def setUp(self):
        self.pm = pollmixin.PollMixin()

    def test_PollMixin_True(self):
        d = self.pm.poll(check_f=lambda : True,
                         pollinterval=0.1)
        return d

    def test_PollMixin_False_then_True(self):
        i = iter([False, True])
        d = self.pm.poll(check_f=i.next,
                         pollinterval=0.1)
        return d

    def test_timeout(self):
        d = self.pm.poll(check_f=lambda: False,
                         pollinterval=0.01,
                         timeout=1)
        def _suc(res):
            self.fail("poll should have failed, not returned %s" % (res,))
        def _err(f):
            f.trap(pollmixin.TimeoutError)
            return None # success
        d.addCallbacks(_suc, _err)
        return d

class DeferredUtilTests(unittest.TestCase):
    def test_gather_results(self):
        d1 = defer.Deferred()
        d2 = defer.Deferred()
        res = deferredutil.gatherResults([d1, d2])
        d1.errback(ValueError("BAD"))
        def _callb(res):
            self.fail("Should have errbacked, not resulted in %s" % (res,))
        def _errb(thef):
            thef.trap(ValueError)
        res.addCallbacks(_callb, _errb)
        return res

    def test_success(self):
        d1, d2 = defer.Deferred(), defer.Deferred()
        good = []
        bad = []
        dlss = deferredutil.DeferredListShouldSucceed([d1,d2])
        dlss.addCallbacks(good.append, bad.append)
        d1.callback(1)
        d2.callback(2)
        self.failUnlessEqual(good, [[1,2]])
        self.failUnlessEqual(bad, [])

    def test_failure(self):
        d1, d2 = defer.Deferred(), defer.Deferred()
        good = []
        bad = []
        dlss = deferredutil.DeferredListShouldSucceed([d1,d2])
        dlss.addCallbacks(good.append, bad.append)
        d1.addErrback(lambda _ignore: None)
        d2.addErrback(lambda _ignore: None)
        d1.callback(1)
        d2.errback(ValueError())
        self.failUnlessEqual(good, [])
        self.failUnlessEqual(len(bad), 1)
        f = bad[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(ValueError))

class HashUtilTests(unittest.TestCase):

    def test_random_key(self):
        k = hashutil.random_key()
        self.failUnlessEqual(len(k), hashutil.KEYLEN)

    def test_sha256d(self):
        h1 = hashutil.tagged_hash("tag1", "value")
        h2 = hashutil.tagged_hasher("tag1")
        h2.update("value")
        h2a = h2.digest()
        h2b = h2.digest()
        self.failUnlessEqual(h1, h2a)
        self.failUnlessEqual(h2a, h2b)

    def test_sha256d_truncated(self):
        h1 = hashutil.tagged_hash("tag1", "value", 16)
        h2 = hashutil.tagged_hasher("tag1", 16)
        h2.update("value")
        h2 = h2.digest()
        self.failUnlessEqual(len(h1), 16)
        self.failUnlessEqual(len(h2), 16)
        self.failUnlessEqual(h1, h2)

    def test_chk(self):
        h1 = hashutil.convergence_hash(3, 10, 1000, "data", "secret")
        h2 = hashutil.convergence_hasher(3, 10, 1000, "secret")
        h2.update("data")
        h2 = h2.digest()
        self.failUnlessEqual(h1, h2)

    def test_hashers(self):
        h1 = hashutil.block_hash("foo")
        h2 = hashutil.block_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.uri_extension_hash("foo")
        h2 = hashutil.uri_extension_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.plaintext_hash("foo")
        h2 = hashutil.plaintext_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.crypttext_hash("foo")
        h2 = hashutil.crypttext_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.crypttext_segment_hash("foo")
        h2 = hashutil.crypttext_segment_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.plaintext_segment_hash("foo")
        h2 = hashutil.plaintext_segment_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

    def test_constant_time_compare(self):
        self.failUnless(hashutil.constant_time_compare("a", "a"))
        self.failUnless(hashutil.constant_time_compare("ab", "ab"))
        self.failIf(hashutil.constant_time_compare("a", "b"))
        self.failIf(hashutil.constant_time_compare("a", "aa"))

    def _testknown(self, hashf, expected_a, *args):
        got = hashf(*args)
        got_a = base32.b2a(got)
        self.failUnlessEqual(got_a, expected_a)

    def test_known_answers(self):
        # assert backwards compatibility
        self._testknown(hashutil.storage_index_hash, "qb5igbhcc5esa6lwqorsy7e6am", "")
        self._testknown(hashutil.block_hash, "msjr5bh4evuh7fa3zw7uovixfbvlnstr5b65mrerwfnvjxig2jvq", "")
        self._testknown(hashutil.uri_extension_hash, "wthsu45q7zewac2mnivoaa4ulh5xvbzdmsbuyztq2a5fzxdrnkka", "")
        self._testknown(hashutil.plaintext_hash, "5lz5hwz3qj3af7n6e3arblw7xzutvnd3p3fjsngqjcb7utf3x3da", "")
        self._testknown(hashutil.crypttext_hash, "itdj6e4njtkoiavlrmxkvpreosscssklunhwtvxn6ggho4rkqwga", "")
        self._testknown(hashutil.crypttext_segment_hash, "aovy5aa7jej6ym5ikgwyoi4pxawnoj3wtaludjz7e2nb5xijb7aa", "")
        self._testknown(hashutil.plaintext_segment_hash, "4fdgf6qruaisyukhqcmoth4t3li6bkolbxvjy4awwcpprdtva7za", "")
        self._testknown(hashutil.convergence_hash, "3mo6ni7xweplycin6nowynw2we", 3, 10, 100, "", "converge")
        self._testknown(hashutil.my_renewal_secret_hash, "ujhr5k5f7ypkp67jkpx6jl4p47pyta7hu5m527cpcgvkafsefm6q", "")
        self._testknown(hashutil.my_cancel_secret_hash, "rjwzmafe2duixvqy6h47f5wfrokdziry6zhx4smew4cj6iocsfaa", "")
        self._testknown(hashutil.file_renewal_secret_hash, "hzshk2kf33gzbd5n3a6eszkf6q6o6kixmnag25pniusyaulqjnia", "", "si")
        self._testknown(hashutil.file_cancel_secret_hash, "bfciwvr6w7wcavsngxzxsxxaszj72dej54n4tu2idzp6b74g255q", "", "si")
        self._testknown(hashutil.bucket_renewal_secret_hash, "e7imrzgzaoashsncacvy3oysdd2m5yvtooo4gmj4mjlopsazmvuq", "", "\x00"*20)
        self._testknown(hashutil.bucket_cancel_secret_hash, "dvdujeyxeirj6uux6g7xcf4lvesk632aulwkzjar7srildvtqwma", "", "\x00"*20)
        self._testknown(hashutil.hmac, "c54ypfi6pevb3nvo6ba42jtglpkry2kbdopqsi7dgrm4r7tw5sra", "tag", "")
        self._testknown(hashutil.mutable_rwcap_key_hash, "6rvn2iqrghii5n4jbbwwqqsnqu", "iv", "wk")
        self._testknown(hashutil.ssk_writekey_hash, "ykpgmdbpgbb6yqz5oluw2q26ye", "")
        self._testknown(hashutil.ssk_write_enabler_master_hash, "izbfbfkoait4dummruol3gy2bnixrrrslgye6ycmkuyujnenzpia", "")
        self._testknown(hashutil.ssk_write_enabler_hash, "fuu2dvx7g6gqu5x22vfhtyed7p4pd47y5hgxbqzgrlyvxoev62tq", "wk", "\x00"*20)
        self._testknown(hashutil.ssk_pubkey_fingerprint_hash, "3opzw4hhm2sgncjx224qmt5ipqgagn7h5zivnfzqycvgqgmgz35q", "")
        self._testknown(hashutil.ssk_readkey_hash, "vugid4as6qbqgeq2xczvvcedai", "")
        self._testknown(hashutil.ssk_readkey_data_hash, "73wsaldnvdzqaf7v4pzbr2ae5a", "iv", "rk")
        self._testknown(hashutil.ssk_storage_index_hash, "j7icz6kigb6hxrej3tv4z7ayym", "")


class Abbreviate(unittest.TestCase):
    def test_time(self):
        a = abbreviate.abbreviate_time
        self.failUnlessEqual(a(None), "unknown")
        self.failUnlessEqual(a(0), "0 seconds")
        self.failUnlessEqual(a(1), "1 second")
        self.failUnlessEqual(a(2), "2 seconds")
        self.failUnlessEqual(a(119), "119 seconds")
        MIN = 60
        self.failUnlessEqual(a(2*MIN), "2 minutes")
        self.failUnlessEqual(a(60*MIN), "60 minutes")
        self.failUnlessEqual(a(179*MIN), "179 minutes")
        HOUR = 60*MIN
        self.failUnlessEqual(a(180*MIN), "3 hours")
        self.failUnlessEqual(a(4*HOUR), "4 hours")
        DAY = 24*HOUR
        MONTH = 30*DAY
        self.failUnlessEqual(a(2*DAY), "2 days")
        self.failUnlessEqual(a(2*MONTH), "2 months")
        YEAR = 365*DAY
        self.failUnlessEqual(a(5*YEAR), "5 years")

    def test_space(self):
        tests_si = [(None, "unknown"),
                    (0, "0 B"),
                    (1, "1 B"),
                    (999, "999 B"),
                    (1000, "1000 B"),
                    (1023, "1023 B"),
                    (1024, "1.02 kB"),
                    (20*1000, "20.00 kB"),
                    (1024*1024, "1.05 MB"),
                    (1000*1000, "1.00 MB"),
                    (1000*1000*1000, "1.00 GB"),
                    (1000*1000*1000*1000, "1.00 TB"),
                    (1000*1000*1000*1000*1000, "1.00 PB"),
                    (1234567890123456, "1.23 PB"),
                    ]
        for (x, expected) in tests_si:
            got = abbreviate.abbreviate_space(x, SI=True)
            self.failUnlessEqual(got, expected)

        tests_base1024 = [(None, "unknown"),
                          (0, "0 B"),
                          (1, "1 B"),
                          (999, "999 B"),
                          (1000, "1000 B"),
                          (1023, "1023 B"),
                          (1024, "1.00 kiB"),
                          (20*1024, "20.00 kiB"),
                          (1000*1000, "976.56 kiB"),
                          (1024*1024, "1.00 MiB"),
                          (1024*1024*1024, "1.00 GiB"),
                          (1024*1024*1024*1024, "1.00 TiB"),
                          (1000*1000*1000*1000*1000, "909.49 TiB"),
                          (1024*1024*1024*1024*1024, "1.00 PiB"),
                          (1234567890123456, "1.10 PiB"),
                    ]
        for (x, expected) in tests_base1024:
            got = abbreviate.abbreviate_space(x, SI=False)
            self.failUnlessEqual(got, expected)

        self.failUnlessEqual(abbreviate.abbreviate_space_both(1234567),
                             "(1.23 MB, 1.18 MiB)")

    def test_parse_space(self):
        p = abbreviate.parse_abbreviated_size
        self.failUnlessEqual(p(""), None)
        self.failUnlessEqual(p(None), None)
        self.failUnlessEqual(p("123"), 123)
        self.failUnlessEqual(p("123B"), 123)
        self.failUnlessEqual(p("2K"), 2000)
        self.failUnlessEqual(p("2kb"), 2000)
        self.failUnlessEqual(p("2KiB"), 2048)
        self.failUnlessEqual(p("10MB"), 10*1000*1000)
        self.failUnlessEqual(p("10MiB"), 10*1024*1024)
        self.failUnlessEqual(p("5G"), 5*1000*1000*1000)
        self.failUnlessEqual(p("4GiB"), 4*1024*1024*1024)
        e = self.failUnlessRaises(ValueError, p, "12 cubits")
        self.failUnless("12 cubits" in str(e))

class Limiter(unittest.TestCase):
    timeout = 480 # This takes longer than 240 seconds on Francois's arm box.

    def job(self, i, foo):
        self.calls.append( (i, foo) )
        self.simultaneous += 1
        self.peak_simultaneous = max(self.simultaneous, self.peak_simultaneous)
        d = defer.Deferred()
        def _done():
            self.simultaneous -= 1
            d.callback("done %d" % i)
        reactor.callLater(1.0, _done)
        return d

    def bad_job(self, i, foo):
        raise ValueError("bad_job %d" % i)

    def test_limiter(self):
        self.calls = []
        self.simultaneous = 0
        self.peak_simultaneous = 0
        l = limiter.ConcurrencyLimiter()
        dl = []
        for i in range(20):
            dl.append(l.add(self.job, i, foo=str(i)))
        d = defer.DeferredList(dl, fireOnOneErrback=True)
        def _done(res):
            self.failUnlessEqual(self.simultaneous, 0)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
        d.addCallback(_done)
        return d

    def test_errors(self):
        self.calls = []
        self.simultaneous = 0
        self.peak_simultaneous = 0
        l = limiter.ConcurrencyLimiter()
        dl = []
        for i in range(20):
            dl.append(l.add(self.job, i, foo=str(i)))
        d2 = l.add(self.bad_job, 21, "21")
        d = defer.DeferredList(dl, fireOnOneErrback=True)
        def _most_done(res):
            results = []
            for (success, result) in res:
                self.failUnlessEqual(success, True)
                results.append(result)
            results.sort()
            expected_results = ["done %d" % i for i in range(20)]
            expected_results.sort()
            self.failUnlessEqual(results, expected_results)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
            def _good(res):
                self.fail("should have failed, not got %s" % (res,))
            def _err(f):
                f.trap(ValueError)
                self.failUnless("bad_job 21" in str(f))
            d2.addCallbacks(_good, _err)
            return d2
        d.addCallback(_most_done)
        def _all_done(res):
            self.failUnlessEqual(self.simultaneous, 0)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
        d.addCallback(_all_done)
        return d

class TimeFormat(unittest.TestCase):
    def test_epoch(self):
        return self._help_test_epoch()

    def test_epoch_in_London(self):
        # Europe/London is a particularly troublesome timezone.  Nowadays, its
        # offset from GMT is 0.  But in 1970, its offset from GMT was 1.
        # (Apparently in 1970 Britain had redefined standard time to be GMT+1
        # and stayed in standard time all year round, whereas today
        # Europe/London standard time is GMT and Europe/London Daylight
        # Savings Time is GMT+1.)  The current implementation of
        # time_format.iso_utc_time_to_localseconds() breaks if the timezone is
        # Europe/London.  (As soon as this unit test is done then I'll change
        # that implementation to something that works even in this case...)
        origtz = os.environ.get('TZ')
        os.environ['TZ'] = "Europe/London"
        if hasattr(time, 'tzset'):
            time.tzset()
        try:
            return self._help_test_epoch()
        finally:
            if origtz is None:
                del os.environ['TZ']
            else:
                os.environ['TZ'] = origtz
            if hasattr(time, 'tzset'):
                time.tzset()

    def _help_test_epoch(self):
        origtzname = time.tzname
        s = time_format.iso_utc_time_to_seconds("1970-01-01T00:00:01")
        self.failUnlessEqual(s, 1.0)
        s = time_format.iso_utc_time_to_seconds("1970-01-01_00:00:01")
        self.failUnlessEqual(s, 1.0)
        s = time_format.iso_utc_time_to_seconds("1970-01-01 00:00:01")
        self.failUnlessEqual(s, 1.0)

        self.failUnlessEqual(time_format.iso_utc(1.0), "1970-01-01_00:00:01")
        self.failUnlessEqual(time_format.iso_utc(1.0, sep=" "),
                             "1970-01-01 00:00:01")

        now = time.time()
        isostr = time_format.iso_utc(now)
        timestamp = time_format.iso_utc_time_to_seconds(isostr)
        self.failUnlessEqual(int(timestamp), int(now))

        def my_time():
            return 1.0
        self.failUnlessEqual(time_format.iso_utc(t=my_time),
                             "1970-01-01_00:00:01")
        e = self.failUnlessRaises(ValueError,
                                  time_format.iso_utc_time_to_seconds,
                                  "invalid timestring")
        self.failUnless("not a complete ISO8601 timestamp" in str(e))
        s = time_format.iso_utc_time_to_seconds("1970-01-01_00:00:01.500")
        self.failUnlessEqual(s, 1.5)

        # Look for daylight-savings-related errors.
        thatmomentinmarch = time_format.iso_utc_time_to_seconds("2009-03-20 21:49:02.226536")
        self.failUnlessEqual(thatmomentinmarch, 1237585742.226536)
        self.failUnlessEqual(origtzname, time.tzname)

class CacheDir(unittest.TestCase):
    def test_basic(self):
        basedir = "test_util/CacheDir/test_basic"

        def _failIfExists(name):
            absfn = os.path.join(basedir, name)
            self.failIf(os.path.exists(absfn),
                        "%s exists but it shouldn't" % absfn)

        def _failUnlessExists(name):
            absfn = os.path.join(basedir, name)
            self.failUnless(os.path.exists(absfn),
                            "%s doesn't exist but it should" % absfn)

        cdm = cachedir.CacheDirectoryManager(basedir)
        a = cdm.get_file("a")
        b = cdm.get_file("b")
        c = cdm.get_file("c")
        f = open(a.get_filename(), "wb"); f.write("hi"); f.close(); del f
        f = open(b.get_filename(), "wb"); f.write("hi"); f.close(); del f
        f = open(c.get_filename(), "wb"); f.write("hi"); f.close(); del f

        _failUnlessExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        cdm.check()

        _failUnlessExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        del a
        # this file won't be deleted yet, because it isn't old enough
        cdm.check()
        _failUnlessExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        # we change the definition of "old" to make everything old
        cdm.old = -10

        cdm.check()
        _failIfExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        cdm.old = 60*60

        del b

        cdm.check()
        _failIfExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        b2 = cdm.get_file("b")

        cdm.check()
        _failIfExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

ctr = [0]
class EqButNotIs:
    def __init__(self, x):
        self.x = x
        self.hash = ctr[0]
        ctr[0] += 1
    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.x,)
    def __hash__(self):
        return self.hash
    def __le__(self, other):
        return self.x <= other
    def __lt__(self, other):
        return self.x < other
    def __ge__(self, other):
        return self.x >= other
    def __gt__(self, other):
        return self.x > other
    def __ne__(self, other):
        return self.x != other
    def __eq__(self, other):
        return self.x == other

class DictUtil(unittest.TestCase):
    def _help_test_empty_dict(self, klass):
        d1 = klass()
        d2 = klass({})

        self.failUnless(d1 == d2, "d1: %r, d2: %r" % (d1, d2,))
        self.failUnless(len(d1) == 0)
        self.failUnless(len(d2) == 0)

    def _help_test_nonempty_dict(self, klass):
        d1 = klass({'a': 1, 'b': "eggs", 3: "spam",})
        d2 = klass({'a': 1, 'b': "eggs", 3: "spam",})

        self.failUnless(d1 == d2)
        self.failUnless(len(d1) == 3, "%s, %s" % (len(d1), d1,))
        self.failUnless(len(d2) == 3)

    def _help_test_eq_but_notis(self, klass):
        d = klass({'a': 3, 'b': EqButNotIs(3), 'c': 3})
        d.pop('b')

        d.clear()
        d['a'] = 3
        d['b'] = EqButNotIs(3)
        d['c'] = 3
        d.pop('b')

        d.clear()
        d['b'] = EqButNotIs(3)
        d['a'] = 3
        d['c'] = 3
        d.pop('b')

        d.clear()
        d['a'] = EqButNotIs(3)
        d['c'] = 3
        d['a'] = 3

        d.clear()
        fake3 = EqButNotIs(3)
        fake7 = EqButNotIs(7)
        d[fake3] = fake7
        d[3] = 7
        d[3] = 8
        self.failUnless(filter(lambda x: x is 8,  d.itervalues()))
        self.failUnless(filter(lambda x: x is fake7,  d.itervalues()))
        # The real 7 should have been ejected by the d[3] = 8.
        self.failUnless(not filter(lambda x: x is 7,  d.itervalues()))
        self.failUnless(filter(lambda x: x is fake3,  d.iterkeys()))
        self.failUnless(filter(lambda x: x is 3,  d.iterkeys()))
        d[fake3] = 8

        d.clear()
        d[3] = 7
        fake3 = EqButNotIs(3)
        fake7 = EqButNotIs(7)
        d[fake3] = fake7
        d[3] = 8
        self.failUnless(filter(lambda x: x is 8,  d.itervalues()))
        self.failUnless(filter(lambda x: x is fake7,  d.itervalues()))
        # The real 7 should have been ejected by the d[3] = 8.
        self.failUnless(not filter(lambda x: x is 7,  d.itervalues()))
        self.failUnless(filter(lambda x: x is fake3,  d.iterkeys()))
        self.failUnless(filter(lambda x: x is 3,  d.iterkeys()))
        d[fake3] = 8

    def test_all(self):
        self._help_test_eq_but_notis(dictutil.UtilDict)
        self._help_test_eq_but_notis(dictutil.NumDict)
        self._help_test_eq_but_notis(dictutil.ValueOrderedDict)
        self._help_test_nonempty_dict(dictutil.UtilDict)
        self._help_test_nonempty_dict(dictutil.NumDict)
        self._help_test_nonempty_dict(dictutil.ValueOrderedDict)
        self._help_test_eq_but_notis(dictutil.UtilDict)
        self._help_test_eq_but_notis(dictutil.NumDict)
        self._help_test_eq_but_notis(dictutil.ValueOrderedDict)

    def test_dict_of_sets(self):
        ds = dictutil.DictOfSets()
        ds.add(1, "a")
        ds.add(2, "b")
        ds.add(2, "b")
        ds.add(2, "c")
        self.failUnlessEqual(ds[1], set(["a"]))
        self.failUnlessEqual(ds[2], set(["b", "c"]))
        ds.discard(3, "d") # should not raise an exception
        ds.discard(2, "b")
        self.failUnlessEqual(ds[2], set(["c"]))
        ds.discard(2, "c")
        self.failIf(2 in ds)

        ds.union(1, ["a", "e"])
        ds.union(3, ["f"])
        self.failUnlessEqual(ds[1], set(["a","e"]))
        self.failUnlessEqual(ds[3], set(["f"]))
        ds2 = dictutil.DictOfSets()
        ds2.add(3, "f")
        ds2.add(3, "g")
        ds2.add(4, "h")
        ds.update(ds2)
        self.failUnlessEqual(ds[1], set(["a","e"]))
        self.failUnlessEqual(ds[3], set(["f", "g"]))
        self.failUnlessEqual(ds[4], set(["h"]))

    def test_move(self):
        d1 = {1: "a", 2: "b"}
        d2 = {2: "c", 3: "d"}
        dictutil.move(1, d1, d2)
        self.failUnlessEqual(d1, {2: "b"})
        self.failUnlessEqual(d2, {1: "a", 2: "c", 3: "d"})

        d1 = {1: "a", 2: "b"}
        d2 = {2: "c", 3: "d"}
        dictutil.move(2, d1, d2)
        self.failUnlessEqual(d1, {1: "a"})
        self.failUnlessEqual(d2, {2: "b", 3: "d"})

        d1 = {1: "a", 2: "b"}
        d2 = {2: "c", 3: "d"}
        self.failUnlessRaises(KeyError, dictutil.move, 5, d1, d2, strict=True)

    def test_subtract(self):
        d1 = {1: "a", 2: "b"}
        d2 = {2: "c", 3: "d"}
        d3 = dictutil.subtract(d1, d2)
        self.failUnlessEqual(d3, {1: "a"})

        d1 = {1: "a", 2: "b"}
        d2 = {2: "c"}
        d3 = dictutil.subtract(d1, d2)
        self.failUnlessEqual(d3, {1: "a"})

    def test_utildict(self):
        d = dictutil.UtilDict({1: "a", 2: "b"})
        d.del_if_present(1)
        d.del_if_present(3)
        self.failUnlessEqual(d, {2: "b"})
        def eq(a, b):
            return a == b
        self.failUnlessRaises(TypeError, eq, d, "not a dict")

        d = dictutil.UtilDict({1: "b", 2: "a"})
        self.failUnlessEqual(d.items_sorted_by_value(),
                             [(2, "a"), (1, "b")])
        self.failUnlessEqual(d.items_sorted_by_key(),
                             [(1, "b"), (2, "a")])
        self.failUnlessEqual(repr(d), "{1: 'b', 2: 'a'}")
        self.failUnless(1 in d)

        d2 = dictutil.UtilDict({3: "c", 4: "d"})
        self.failUnless(d != d2)
        self.failUnless(d2 > d)
        self.failUnless(d2 >= d)
        self.failUnless(d <= d2)
        self.failUnless(d < d2)
        self.failUnlessEqual(d[1], "b")
        self.failUnlessEqual(sorted(list([k for k in d])), [1,2])

        d3 = d.copy()
        self.failUnlessEqual(d, d3)
        self.failUnless(isinstance(d3, dictutil.UtilDict))

        d4 = d.fromkeys([3,4], "e")
        self.failUnlessEqual(d4, {3: "e", 4: "e"})

        self.failUnlessEqual(d.get(1), "b")
        self.failUnlessEqual(d.get(3), None)
        self.failUnlessEqual(d.get(3, "default"), "default")
        self.failUnlessEqual(sorted(list(d.items())),
                             [(1, "b"), (2, "a")])
        self.failUnlessEqual(sorted(list(d.iteritems())),
                             [(1, "b"), (2, "a")])
        self.failUnlessEqual(sorted(d.keys()), [1, 2])
        self.failUnlessEqual(sorted(d.values()), ["a", "b"])
        x = d.setdefault(1, "new")
        self.failUnlessEqual(x, "b")
        self.failUnlessEqual(d[1], "b")
        x = d.setdefault(3, "new")
        self.failUnlessEqual(x, "new")
        self.failUnlessEqual(d[3], "new")
        del d[3]

        x = d.popitem()
        self.failUnless(x in [(1, "b"), (2, "a")])
        x = d.popitem()
        self.failUnless(x in [(1, "b"), (2, "a")])
        self.failUnlessRaises(KeyError, d.popitem)

    def test_numdict(self):
        d = dictutil.NumDict({"a": 1, "b": 2})

        d.add_num("a", 10, 5)
        d.add_num("c", 20, 5)
        d.add_num("d", 30)
        self.failUnlessEqual(d, {"a": 11, "b": 2, "c": 25, "d": 30})

        d.subtract_num("a", 10)
        d.subtract_num("e", 10)
        d.subtract_num("f", 10, 15)
        self.failUnlessEqual(d, {"a": 1, "b": 2, "c": 25, "d": 30,
                                 "e": -10, "f": 5})

        self.failUnlessEqual(d.sum(), sum([1, 2, 25, 30, -10, 5]))

        d = dictutil.NumDict()
        d.inc("a")
        d.inc("a")
        d.inc("b", 5)
        self.failUnlessEqual(d, {"a": 2, "b": 6})
        d.dec("a")
        d.dec("c")
        d.dec("d", 5)
        self.failUnlessEqual(d, {"a": 1, "b": 6, "c": -1, "d": 4})
        self.failUnlessEqual(d.items_sorted_by_key(),
                             [("a", 1), ("b", 6), ("c", -1), ("d", 4)])
        self.failUnlessEqual(d.items_sorted_by_value(),
                             [("c", -1), ("a", 1), ("d", 4), ("b", 6)])
        self.failUnlessEqual(d.item_with_largest_value(), ("b", 6))

        d = dictutil.NumDict({"a": 1, "b": 2})
        self.failUnlessEqual(repr(d), "{'a': 1, 'b': 2}")
        self.failUnless("a" in d)

        d2 = dictutil.NumDict({"c": 3, "d": 4})
        self.failUnless(d != d2)
        self.failUnless(d2 > d)
        self.failUnless(d2 >= d)
        self.failUnless(d <= d2)
        self.failUnless(d < d2)
        self.failUnlessEqual(d["a"], 1)
        self.failUnlessEqual(sorted(list([k for k in d])), ["a","b"])
        def eq(a, b):
            return a == b
        self.failUnlessRaises(TypeError, eq, d, "not a dict")

        d3 = d.copy()
        self.failUnlessEqual(d, d3)
        self.failUnless(isinstance(d3, dictutil.NumDict))

        d4 = d.fromkeys(["a","b"], 5)
        self.failUnlessEqual(d4, {"a": 5, "b": 5})

        self.failUnlessEqual(d.get("a"), 1)
        self.failUnlessEqual(d.get("c"), 0)
        self.failUnlessEqual(d.get("c", 5), 5)
        self.failUnlessEqual(sorted(list(d.items())),
                             [("a", 1), ("b", 2)])
        self.failUnlessEqual(sorted(list(d.iteritems())),
                             [("a", 1), ("b", 2)])
        self.failUnlessEqual(sorted(d.keys()), ["a", "b"])
        self.failUnlessEqual(sorted(d.values()), [1, 2])
        self.failUnless(d.has_key("a"))
        self.failIf(d.has_key("c"))

        x = d.setdefault("c", 3)
        self.failUnlessEqual(x, 3)
        self.failUnlessEqual(d["c"], 3)
        x = d.setdefault("c", 5)
        self.failUnlessEqual(x, 3)
        self.failUnlessEqual(d["c"], 3)
        del d["c"]

        x = d.popitem()
        self.failUnless(x in [("a", 1), ("b", 2)])
        x = d.popitem()
        self.failUnless(x in [("a", 1), ("b", 2)])
        self.failUnlessRaises(KeyError, d.popitem)

        d.update({"c": 3})
        d.update({"c": 4, "d": 5})
        self.failUnlessEqual(d, {"c": 4, "d": 5})

    def test_del_if_present(self):
        d = {1: "a", 2: "b"}
        dictutil.del_if_present(d, 1)
        dictutil.del_if_present(d, 3)
        self.failUnlessEqual(d, {2: "b"})

    def test_valueordereddict(self):
        d = dictutil.ValueOrderedDict()
        d["a"] = 3
        d["b"] = 2
        d["c"] = 1

        self.failUnlessEqual(d, {"a": 3, "b": 2, "c": 1})
        self.failUnlessEqual(d.items(), [("c", 1), ("b", 2), ("a", 3)])
        self.failUnlessEqual(d.values(), [1, 2, 3])
        self.failUnlessEqual(d.keys(), ["c", "b", "a"])
        self.failUnlessEqual(repr(d), "<ValueOrderedDict {c: 1, b: 2, a: 3}>")
        def eq(a, b):
            return a == b
        self.failIf(d == {"a": 4})
        self.failUnless(d != {"a": 4})

        x = d.setdefault("d", 0)
        self.failUnlessEqual(x, 0)
        self.failUnlessEqual(d["d"], 0)
        x = d.setdefault("d", -1)
        self.failUnlessEqual(x, 0)
        self.failUnlessEqual(d["d"], 0)

        x = d.remove("e", "default", False)
        self.failUnlessEqual(x, "default")
        self.failUnlessRaises(KeyError, d.remove, "e", "default", True)
        x = d.remove("d", 5)
        self.failUnlessEqual(x, 0)

        x = d.__getitem__("c")
        self.failUnlessEqual(x, 1)
        x = d.__getitem__("e", "default", False)
        self.failUnlessEqual(x, "default")
        self.failUnlessRaises(KeyError, d.__getitem__, "e", "default", True)

        self.failUnlessEqual(d.popitem(), ("c", 1))
        self.failUnlessEqual(d.popitem(), ("b", 2))
        self.failUnlessEqual(d.popitem(), ("a", 3))
        self.failUnlessRaises(KeyError, d.popitem)

        d = dictutil.ValueOrderedDict({"a": 3, "b": 2, "c": 1})
        x = d.pop("d", "default", False)
        self.failUnlessEqual(x, "default")
        self.failUnlessRaises(KeyError, d.pop, "d", "default", True)
        x = d.pop("b")
        self.failUnlessEqual(x, 2)
        self.failUnlessEqual(d.items(), [("c", 1), ("a", 3)])

        d = dictutil.ValueOrderedDict({"a": 3, "b": 2, "c": 1})
        x = d.pop_from_list(1) # pop the second item, b/2
        self.failUnlessEqual(x, "b")
        self.failUnlessEqual(d.items(), [("c", 1), ("a", 3)])

    def test_auxdict(self):
        d = dictutil.AuxValueDict()
        # we put the serialized form in the auxdata
        d.set_with_aux("key", ("filecap", "metadata"), "serialized")

        self.failUnlessEqual(d.keys(), ["key"])
        self.failUnlessEqual(d["key"], ("filecap", "metadata"))
        self.failUnlessEqual(d.get_aux("key"), "serialized")
        def _get_missing(key):
            return d[key]
        self.failUnlessRaises(KeyError, _get_missing, "nonkey")
        self.failUnlessEqual(d.get("nonkey"), None)
        self.failUnlessEqual(d.get("nonkey", "nonvalue"), "nonvalue")
        self.failUnlessEqual(d.get_aux("nonkey"), None)
        self.failUnlessEqual(d.get_aux("nonkey", "nonvalue"), "nonvalue")

        d["key"] = ("filecap2", "metadata2")
        self.failUnlessEqual(d["key"], ("filecap2", "metadata2"))
        self.failUnlessEqual(d.get_aux("key"), None)

        d.set_with_aux("key2", "value2", "aux2")
        self.failUnlessEqual(sorted(d.keys()), ["key", "key2"])
        del d["key2"]
        self.failUnlessEqual(d.keys(), ["key"])
        self.failIf("key2" in d)
        self.failUnlessRaises(KeyError, _get_missing, "key2")
        self.failUnlessEqual(d.get("key2"), None)
        self.failUnlessEqual(d.get_aux("key2"), None)
        d["key2"] = "newvalue2"
        self.failUnlessEqual(d.get("key2"), "newvalue2")
        self.failUnlessEqual(d.get_aux("key2"), None)

        d = dictutil.AuxValueDict({1:2,3:4})
        self.failUnlessEqual(sorted(d.keys()), [1,3])
        self.failUnlessEqual(d[1], 2)
        self.failUnlessEqual(d.get_aux(1), None)

        d = dictutil.AuxValueDict([ (1,2), (3,4) ])
        self.failUnlessEqual(sorted(d.keys()), [1,3])
        self.failUnlessEqual(d[1], 2)
        self.failUnlessEqual(d.get_aux(1), None)

        d = dictutil.AuxValueDict(one=1, two=2)
        self.failUnlessEqual(sorted(d.keys()), ["one","two"])
        self.failUnlessEqual(d["one"], 1)
        self.failUnlessEqual(d.get_aux("one"), None)

class Pipeline(unittest.TestCase):
    def pause(self, *args, **kwargs):
        d = defer.Deferred()
        self.calls.append( (d, args, kwargs) )
        return d

    def failUnlessCallsAre(self, expected):
        #print self.calls
        #print expected
        self.failUnlessEqual(len(self.calls), len(expected), self.calls)
        for i,c in enumerate(self.calls):
            self.failUnlessEqual(c[1:], expected[i], str(i))

    def test_basic(self):
        self.calls = []
        finished = []
        p = pipeline.Pipeline(100)

        d = p.flush() # fires immediately
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        finished = []

        d = p.add(10, self.pause, "one")
        # the call should start right away, and our return Deferred should
        # fire right away
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessCallsAre([ ( ("one",) , {} ) ])
        self.failUnlessEqual(p.gauge, 10)

        # pipeline: [one]

        finished = []
        d = p.add(20, self.pause, "two", kw=2)
        # pipeline: [one, two]

        # the call and the Deferred should fire right away
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessCallsAre([ ( ("one",) , {} ),
                                  ( ("two",) , {"kw": 2} ),
                                  ])
        self.failUnlessEqual(p.gauge, 30)

        self.calls[0][0].callback("one-result")
        # pipeline: [two]
        self.failUnlessEqual(p.gauge, 20)

        finished = []
        d = p.add(90, self.pause, "three", "posarg1")
        # pipeline: [two, three]
        flushed = []
        fd = p.flush()
        fd.addCallbacks(flushed.append, log.err)
        self.failUnlessEqual(flushed, [])

        # the call will be made right away, but the return Deferred will not,
        # because the pipeline is now full.
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 0)
        self.failUnlessCallsAre([ ( ("one",) , {} ),
                                  ( ("two",) , {"kw": 2} ),
                                  ( ("three", "posarg1"), {} ),
                                  ])
        self.failUnlessEqual(p.gauge, 110)

        self.failUnlessRaises(pipeline.SingleFileError, p.add, 10, self.pause)

        # retiring either call will unblock the pipeline, causing the #3
        # Deferred to fire
        self.calls[2][0].callback("three-result")
        # pipeline: [two]

        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessEqual(flushed, [])

        # retiring call#2 will finally allow the flush() Deferred to fire
        self.calls[1][0].callback("two-result")
        self.failUnlessEqual(len(flushed), 1)

    def test_errors(self):
        self.calls = []
        p = pipeline.Pipeline(100)

        d1 = p.add(200, self.pause, "one")
        d2 = p.flush()

        finished = []
        d1.addBoth(finished.append)
        self.failUnlessEqual(finished, [])

        flushed = []
        d2.addBoth(flushed.append)
        self.failUnlessEqual(flushed, [])

        self.calls[0][0].errback(ValueError("oops"))

        self.failUnlessEqual(len(finished), 1)
        f = finished[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        self.failUnlessIn("PipelineError", str(f.value))
        self.failUnlessIn("ValueError", str(f.value))
        r = repr(f.value)
        self.failUnless("ValueError" in r, r)
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        # now that the pipeline is in the failed state, any new calls will
        # fail immediately

        d3 = p.add(20, self.pause, "two")

        finished = []
        d3.addBoth(finished.append)
        self.failUnlessEqual(len(finished), 1)
        f = finished[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        r = repr(f.value)
        self.failUnless("ValueError" in r, r)
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        d4 = p.flush()
        flushed = []
        d4.addBoth(flushed.append)
        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

    def test_errors2(self):
        self.calls = []
        p = pipeline.Pipeline(100)

        d1 = p.add(10, self.pause, "one")
        d2 = p.add(20, self.pause, "two")
        d3 = p.add(30, self.pause, "three")
        d4 = p.flush()

        # one call fails, then the second one succeeds: make sure
        # ExpandableDeferredList tolerates the second one

        flushed = []
        d4.addBoth(flushed.append)
        self.failUnlessEqual(flushed, [])

        self.calls[0][0].errback(ValueError("oops"))
        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        self.calls[1][0].callback("two-result")
        self.calls[2][0].errback(ValueError("three-error"))

class SampleError(Exception):
    pass

class Log(unittest.TestCase):
    def test_err(self):
        if not hasattr(self, "flushLoggedErrors"):
            # without flushLoggedErrors, we can't get rid of the
            # twisted.log.err that tahoe_log records, so we can't keep this
            # test from [ERROR]ing
            raise unittest.SkipTest("needs flushLoggedErrors from Twisted-2.5.0")
        try:
            raise SampleError("simple sample")
        except:
            f = Failure()
        tahoe_log.err(format="intentional sample error",
                      failure=f, level=tahoe_log.OPERATIONAL, umid="wO9UoQ")
        self.flushLoggedErrors(SampleError)
