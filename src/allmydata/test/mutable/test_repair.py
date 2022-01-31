"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from ..common import AsyncTestCase
from testtools.matchers import Equals, HasLength
from allmydata.interfaces import IRepairResults, ICheckAndRepairResults
from allmydata.monitor import Monitor
from allmydata.mutable.common import MODE_CHECK
from allmydata.mutable.layout import unpack_header
from allmydata.mutable.repairer import MustForceRepairError
from ..common import ShouldFailMixin
from .util import PublishMixin

class Repair(AsyncTestCase, PublishMixin, ShouldFailMixin):

    def get_shares(self, s):
        all_shares = {} # maps (peerid, shnum) to share data
        for peerid in s._peers:
            shares = s._peers[peerid]
            for shnum in shares:
                data = shares[shnum]
                all_shares[ (peerid, shnum) ] = data
        return all_shares

    def copy_shares(self, ignored=None):
        self.old_shares.append(self.get_shares(self._storage))

    def test_repair_nop(self):
        self.old_shares = []
        d = self.publish_one()
        d.addCallback(self.copy_shares)
        d.addCallback(lambda res: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        def _check_results(rres):
            self.assertThat(IRepairResults.providedBy(rres), Equals(True))
            self.assertThat(rres.get_successful(), Equals(True))
            # TODO: examine results

            self.copy_shares()

            initial_shares = self.old_shares[0]
            new_shares = self.old_shares[1]
            # TODO: this really shouldn't change anything. When we implement
            # a "minimal-bandwidth" repairer", change this test to assert:
            #self.assertThat(new_shares, Equals(initial_shares))

            # all shares should be in the same place as before
            self.assertThat(set(initial_shares.keys()),
                                 Equals(set(new_shares.keys())))
            # but they should all be at a newer seqnum. The IV will be
            # different, so the roothash will be too.
            for key in initial_shares:
                (version0,
                 seqnum0,
                 root_hash0,
                 IV0,
                 k0, N0, segsize0, datalen0,
                 o0) = unpack_header(initial_shares[key])
                (version1,
                 seqnum1,
                 root_hash1,
                 IV1,
                 k1, N1, segsize1, datalen1,
                 o1) = unpack_header(new_shares[key])
                self.assertThat(version0, Equals(version1))
                self.assertThat(seqnum0+1, Equals(seqnum1))
                self.assertThat(k0, Equals(k1))
                self.assertThat(N0, Equals(N1))
                self.assertThat(segsize0, Equals(segsize1))
                self.assertThat(datalen0, Equals(datalen1))
        d.addCallback(_check_results)
        return d

    def failIfSharesChanged(self, ignored=None):
        old_shares = self.old_shares[-2]
        current_shares = self.old_shares[-1]
        self.assertThat(old_shares, Equals(current_shares))


    def _test_whether_repairable(self, publisher, nshares, expected_result):
        d = publisher()
        def _delete_some_shares(ign):
            shares = self._storage._peers
            for peerid in shares:
                for shnum in list(shares[peerid]):
                    if shnum >= nshares:
                        del shares[peerid][shnum]
        d.addCallback(_delete_some_shares)
        d.addCallback(lambda ign: self._fn.check(Monitor()))
        def _check(cr):
            self.assertThat(cr.is_healthy(), Equals(False))
            self.assertThat(cr.is_recoverable(), Equals(expected_result))
            return cr
        d.addCallback(_check)
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        d.addCallback(lambda crr: self.assertThat(crr.get_successful(), Equals(expected_result)))
        return d

    def test_unrepairable_0shares(self):
        return self._test_whether_repairable(self.publish_one, 0, False)

    def test_mdmf_unrepairable_0shares(self):
        return self._test_whether_repairable(self.publish_mdmf, 0, False)

    def test_unrepairable_1share(self):
        return self._test_whether_repairable(self.publish_one, 1, False)

    def test_mdmf_unrepairable_1share(self):
        return self._test_whether_repairable(self.publish_mdmf, 1, False)

    def test_repairable_5shares(self):
        return self._test_whether_repairable(self.publish_one, 5, True)

    def test_mdmf_repairable_5shares(self):
        return self._test_whether_repairable(self.publish_mdmf, 5, True)

    def _test_whether_checkandrepairable(self, publisher, nshares, expected_result):
        """
        Like the _test_whether_repairable tests, but invoking check_and_repair
        instead of invoking check and then invoking repair.
        """
        d = publisher()
        def _delete_some_shares(ign):
            shares = self._storage._peers
            for peerid in shares:
                for shnum in list(shares[peerid]):
                    if shnum >= nshares:
                        del shares[peerid][shnum]
        d.addCallback(_delete_some_shares)
        d.addCallback(lambda ign: self._fn.check_and_repair(Monitor()))
        d.addCallback(lambda crr: self.assertThat(crr.get_repair_successful(), Equals(expected_result)))
        return d

    def test_unrepairable_0shares_checkandrepair(self):
        return self._test_whether_checkandrepairable(self.publish_one, 0, False)

    def test_mdmf_unrepairable_0shares_checkandrepair(self):
        return self._test_whether_checkandrepairable(self.publish_mdmf, 0, False)

    def test_unrepairable_1share_checkandrepair(self):
        return self._test_whether_checkandrepairable(self.publish_one, 1, False)

    def test_mdmf_unrepairable_1share_checkandrepair(self):
        return self._test_whether_checkandrepairable(self.publish_mdmf, 1, False)

    def test_repairable_5shares_checkandrepair(self):
        return self._test_whether_checkandrepairable(self.publish_one, 5, True)

    def test_mdmf_repairable_5shares_checkandrepair(self):
        return self._test_whether_checkandrepairable(self.publish_mdmf, 5, True)


    def test_merge(self):
        self.old_shares = []
        d = self.publish_multiple()
        # repair will refuse to merge multiple highest seqnums unless you
        # pass force=True
        d.addCallback(lambda res:
                      self._set_versions({0:3,2:3,4:3,6:3,8:3,
                                          1:4,3:4,5:4,7:4,9:4}))
        d.addCallback(self.copy_shares)
        d.addCallback(lambda res: self._fn.check(Monitor()))
        def _try_repair(check_results):
            ex = "There were multiple recoverable versions with identical seqnums, so force=True must be passed to the repair() operation"
            d2 = self.shouldFail(MustForceRepairError, "test_merge", ex,
                                 self._fn.repair, check_results)
            d2.addCallback(self.copy_shares)
            d2.addCallback(self.failIfSharesChanged)
            d2.addCallback(lambda res: check_results)
            return d2
        d.addCallback(_try_repair)
        d.addCallback(lambda check_results:
                      self._fn.repair(check_results, force=True))
        # this should give us 10 shares of the highest roothash
        def _check_repair_results(rres):
            self.assertThat(rres.get_successful(), Equals(True))
            pass # TODO
        d.addCallback(_check_repair_results)
        d.addCallback(lambda res: self._fn.get_servermap(MODE_CHECK))
        def _check_smap(smap):
            self.assertThat(smap.recoverable_versions(), HasLength(1))
            self.assertThat(smap.unrecoverable_versions(), HasLength(0))
            # now, which should have won?
            roothash_s4a = self.get_roothash_for(3)
            roothash_s4b = self.get_roothash_for(4)
            if roothash_s4b > roothash_s4a:
                expected_contents = self.CONTENTS[4]
            else:
                expected_contents = self.CONTENTS[3]
            new_versionid = smap.best_recoverable_version()
            self.assertThat(new_versionid[0], Equals(5)) # seqnum 5
            d2 = self._fn.download_version(smap, new_versionid)
            d2.addCallback(self.assertEqual, expected_contents)
            return d2
        d.addCallback(_check_smap)
        return d

    def test_non_merge(self):
        self.old_shares = []
        d = self.publish_multiple()
        # repair should not refuse a repair that doesn't need to merge. In
        # this case, we combine v2 with v3. The repair should ignore v2 and
        # copy v3 into a new v5.
        d.addCallback(lambda res:
                      self._set_versions({0:2,2:2,4:2,6:2,8:2,
                                          1:3,3:3,5:3,7:3,9:3}))
        d.addCallback(lambda res: self._fn.check(Monitor()))
        d.addCallback(lambda check_results: self._fn.repair(check_results))
        # this should give us 10 shares of v3
        def _check_repair_results(rres):
            self.assertThat(rres.get_successful(), Equals(True))
            pass # TODO
        d.addCallback(_check_repair_results)
        d.addCallback(lambda res: self._fn.get_servermap(MODE_CHECK))
        def _check_smap(smap):
            self.assertThat(smap.recoverable_versions(), HasLength(1))
            self.assertThat(smap.unrecoverable_versions(), HasLength(0))
            # now, which should have won?
            expected_contents = self.CONTENTS[3]
            new_versionid = smap.best_recoverable_version()
            self.assertThat(new_versionid[0], Equals(5)) # seqnum 5
            d2 = self._fn.download_version(smap, new_versionid)
            d2.addCallback(self.assertEquals, expected_contents)
            return d2
        d.addCallback(_check_smap)
        return d

    def get_roothash_for(self, index):
        # return the roothash for the first share we see in the saved set
        shares = self._copied_shares[index]
        for peerid in shares:
            for shnum in shares[peerid]:
                share = shares[peerid][shnum]
                (version, seqnum, root_hash, IV, k, N, segsize, datalen, o) = \
                          unpack_header(share)
                return root_hash

    def test_check_and_repair_readcap(self):
        # we can't currently repair from a mutable readcap: #625
        self.old_shares = []
        d = self.publish_one()
        d.addCallback(self.copy_shares)
        def _get_readcap(res):
            self._fn3 = self._fn.get_readonly()
            # also delete some shares
            for peerid,shares in list(self._storage._peers.items()):
                shares.pop(0, None)
        d.addCallback(_get_readcap)
        d.addCallback(lambda res: self._fn3.check_and_repair(Monitor()))
        def _check_results(crr):
            self.assertThat(ICheckAndRepairResults.providedBy(crr), Equals(True))
            # we should detect the unhealthy, but skip over mutable-readcap
            # repairs until #625 is fixed
            self.assertThat(crr.get_pre_repair_results().is_healthy(), Equals(False))
            self.assertThat(crr.get_repair_attempted(), Equals(False))
            self.assertThat(crr.get_post_repair_results().is_healthy(), Equals(False))
        d.addCallback(_check_results)
        return d

    def test_repair_empty(self):
        # bug 1689: delete one share of an empty mutable file, then repair.
        # In the buggy version, the check that precedes the retrieve+publish
        # cycle uses MODE_READ, instead of MODE_REPAIR, and fails to get the
        # privkey that repair needs.
        d = self.publish_sdmf(b"")
        def _delete_one_share(ign):
            shares = self._storage._peers
            for peerid in shares:
                for shnum in list(shares[peerid]):
                    if shnum == 0:
                        del shares[peerid][shnum]
        d.addCallback(_delete_one_share)
        d.addCallback(lambda ign: self._fn2.check(Monitor()))
        d.addCallback(lambda check_results: self._fn2.repair(check_results))
        def _check(crr):
            self.assertThat(crr.get_successful(), Equals(True))
        d.addCallback(_check)
        return d
