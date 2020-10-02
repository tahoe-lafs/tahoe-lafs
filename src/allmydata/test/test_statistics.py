"""
Tests for allmydata.util.statistics.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from six.moves import StringIO  # native string StringIO

from twisted.trial import unittest

from allmydata.util import statistics


class Statistics(unittest.TestCase):
    def should_assert(self, msg, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
            self.fail(msg)
        except AssertionError:
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
        self.assertEqual(f(5, 3), f(5, 2))

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
