from twisted.trial import unittest
from allmydata.monitor import Monitor
from allmydata.mutable.common import MODE_CHECK, MODE_READ
from .util import PublishMixin, CheckerMixin


class MultipleVersions(unittest.TestCase, PublishMixin, CheckerMixin):

    def setUp(self):
        return self.publish_multiple()

    def test_multiple_versions(self):
        # if we see a mix of versions in the grid, download_best_version
        # should get the latest one
        self._set_versions(dict([(i,2) for i in (0,2,4,6,8)]))
        d = self._fn.download_best_version()
        d.addCallback(lambda res: self.failUnlessEqual(res, self.CONTENTS[4]))
        # and the checker should report problems
        d.addCallback(lambda res: self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_multiple_versions")

        # but if everything is at version 2, that's what we should download
        d.addCallback(lambda res:
                      self._set_versions(dict([(i,2) for i in range(10)])))
        d.addCallback(lambda res: self._fn.download_best_version())
        d.addCallback(lambda res: self.failUnlessEqual(res, self.CONTENTS[2]))
        # if exactly one share is at version 3, we should still get v2
        d.addCallback(lambda res:
                      self._set_versions({0:3}))
        d.addCallback(lambda res: self._fn.download_best_version())
        d.addCallback(lambda res: self.failUnlessEqual(res, self.CONTENTS[2]))
        # but the servermap should see the unrecoverable version. This
        # depends upon the single newer share being queried early.
        d.addCallback(lambda res: self._fn.get_servermap(MODE_READ))
        def _check_smap(smap):
            self.failUnlessEqual(len(smap.unrecoverable_versions()), 1)
            newer = smap.unrecoverable_newer_versions()
            self.failUnlessEqual(len(newer), 1)
            verinfo, health = newer.items()[0]
            self.failUnlessEqual(verinfo[0], 4)
            self.failUnlessEqual(health, (1,3))
            self.failIf(smap.needs_merge())
        d.addCallback(_check_smap)
        # if we have a mix of two parallel versions (s4a and s4b), we could
        # recover either
        d.addCallback(lambda res:
                      self._set_versions({0:3,2:3,4:3,6:3,8:3,
                                          1:4,3:4,5:4,7:4,9:4}))
        d.addCallback(lambda res: self._fn.get_servermap(MODE_READ))
        def _check_smap_mixed(smap):
            self.failUnlessEqual(len(smap.unrecoverable_versions()), 0)
            newer = smap.unrecoverable_newer_versions()
            self.failUnlessEqual(len(newer), 0)
            self.failUnless(smap.needs_merge())
        d.addCallback(_check_smap_mixed)
        d.addCallback(lambda res: self._fn.download_best_version())
        d.addCallback(lambda res: self.failUnless(res == self.CONTENTS[3] or
                                                  res == self.CONTENTS[4]))
        return d

    def test_replace(self):
        # if we see a mix of versions in the grid, we should be able to
        # replace them all with a newer version

        # if exactly one share is at version 3, we should download (and
        # replace) v2, and the result should be v4. Note that the index we
        # give to _set_versions is different than the sequence number.
        target = dict([(i,2) for i in range(10)]) # seqnum3
        target[0] = 3 # seqnum4
        self._set_versions(target)

        def _modify(oldversion, servermap, first_time):
            return oldversion + " modified"
        d = self._fn.modify(_modify)
        d.addCallback(lambda res: self._fn.download_best_version())
        expected = self.CONTENTS[2] + " modified"
        d.addCallback(lambda res: self.failUnlessEqual(res, expected))
        # and the servermap should indicate that the outlier was replaced too
        d.addCallback(lambda res: self._fn.get_servermap(MODE_CHECK))
        def _check_smap(smap):
            self.failUnlessEqual(smap.highest_seqnum(), 5)
            self.failUnlessEqual(len(smap.unrecoverable_versions()), 0)
            self.failUnlessEqual(len(smap.recoverable_versions()), 1)
        d.addCallback(_check_smap)
        return d
