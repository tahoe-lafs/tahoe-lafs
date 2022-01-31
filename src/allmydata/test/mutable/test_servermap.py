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
from testtools.matchers import Equals, NotEquals, HasLength
from twisted.internet import defer
from allmydata.monitor import Monitor
from allmydata.mutable.common import \
     MODE_CHECK, MODE_ANYTHING, MODE_WRITE, MODE_READ
from allmydata.mutable.publish import MutableData
from allmydata.mutable.servermap import ServerMap, ServermapUpdater
from .util import PublishMixin

class Servermap(AsyncTestCase, PublishMixin):
    def setUp(self):
        super(Servermap, self).setUp()
        return self.publish_one()

    def make_servermap(self, mode=MODE_CHECK, fn=None, sb=None,
                       update_range=None):
        if fn is None:
            fn = self._fn
        if sb is None:
            sb = self._storage_broker
        smu = ServermapUpdater(fn, sb, Monitor(),
                               ServerMap(), mode, update_range=update_range)
        d = smu.update()
        return d

    def update_servermap(self, oldmap, mode=MODE_CHECK):
        smu = ServermapUpdater(self._fn, self._storage_broker, Monitor(),
                               oldmap, mode)
        d = smu.update()
        return d

    def failUnlessOneRecoverable(self, sm, num_shares):
        self.assertThat(sm.recoverable_versions(), HasLength(1))
        self.assertThat(sm.unrecoverable_versions(), HasLength(0))
        best = sm.best_recoverable_version()
        self.assertThat(best, NotEquals(None))
        self.assertThat(sm.recoverable_versions(), Equals(set([best])))
        self.assertThat(sm.shares_available(), HasLength(1))
        self.assertThat(sm.shares_available()[best], Equals((num_shares, 3, 10)))
        shnum, servers = list(sm.make_sharemap().items())[0]
        server = list(servers)[0]
        self.assertThat(sm.version_on_server(server, shnum), Equals(best))
        self.assertThat(sm.version_on_server(server, 666), Equals(None))
        return sm

    def test_basic(self):
        d = defer.succeed(None)
        ms = self.make_servermap
        us = self.update_servermap

        d.addCallback(lambda res: ms(mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda res: ms(mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda res: ms(mode=MODE_READ))
        # this mode stops at k+epsilon, and epsilon=k, so 6 shares
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 6))
        d.addCallback(lambda res: ms(mode=MODE_ANYTHING))
        # this mode stops at 'k' shares
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 3))

        # and can we re-use the same servermap? Note that these are sorted in
        # increasing order of number of servers queried, since once a server
        # gets into the servermap, we'll always ask it for an update.
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 3))
        d.addCallback(lambda sm: us(sm, mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 6))
        d.addCallback(lambda sm: us(sm, mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda sm: us(sm, mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda sm: us(sm, mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))

        return d

    def test_fetch_privkey(self):
        d = defer.succeed(None)
        # use the sibling filenode (which hasn't been used yet), and make
        # sure it can fetch the privkey. The file is small, so the privkey
        # will be fetched on the first (query) pass.
        d.addCallback(lambda res: self.make_servermap(MODE_WRITE, self._fn2))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))

        # create a new file, which is large enough to knock the privkey out
        # of the early part of the file
        LARGE = b"These are Larger contents" * 200 # about 5KB
        LARGE_uploadable = MutableData(LARGE)
        d.addCallback(lambda res: self._nodemaker.create_mutable_file(LARGE_uploadable))
        def _created(large_fn):
            large_fn2 = self._nodemaker.create_from_cap(large_fn.get_uri())
            return self.make_servermap(MODE_WRITE, large_fn2)
        d.addCallback(_created)
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        return d


    def test_mark_bad(self):
        d = defer.succeed(None)
        ms = self.make_servermap

        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 6))
        def _made_map(sm):
            v = sm.best_recoverable_version()
            vm = sm.make_versionmap()
            shares = list(vm[v])
            self.assertThat(shares, HasLength(6))
            self._corrupted = set()
            # mark the first 5 shares as corrupt, then update the servermap.
            # The map should not have the marked shares it in any more, and
            # new shares should be found to replace the missing ones.
            for (shnum, server, timestamp) in shares:
                if shnum < 5:
                    self._corrupted.add( (server, shnum) )
                    sm.mark_bad_share(server, shnum, b"")
            return self.update_servermap(sm, MODE_WRITE)
        d.addCallback(_made_map)
        def _check_map(sm):
            # this should find all 5 shares that weren't marked bad
            v = sm.best_recoverable_version()
            vm = sm.make_versionmap()
            shares = list(vm[v])
            for (server, shnum) in self._corrupted:
                server_shares = sm.debug_shares_on_server(server)
                self.assertFalse(shnum in server_shares, "%d was in %s" % (shnum, server_shares))
            self.assertThat(shares, HasLength(5))
        d.addCallback(_check_map)
        return d

    def failUnlessNoneRecoverable(self, sm):
        self.assertThat(sm.recoverable_versions(), HasLength(0))
        self.assertThat(sm.unrecoverable_versions(), HasLength(0))
        best = sm.best_recoverable_version()
        self.assertThat(best, Equals(None))
        self.assertThat(sm.shares_available(), HasLength(0))

    def test_no_shares(self):
        self._storage._peers = {} # delete all shares
        ms = self.make_servermap
        d = defer.succeed(None)
#
        d.addCallback(lambda res: ms(mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessNoneRecoverable(sm))

        d.addCallback(lambda res: ms(mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessNoneRecoverable(sm))

        d.addCallback(lambda res: ms(mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessNoneRecoverable(sm))

        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessNoneRecoverable(sm))

        return d

    def failUnlessNotQuiteEnough(self, sm):
        self.assertThat(sm.recoverable_versions(), HasLength(0))
        self.assertThat(sm.unrecoverable_versions(), HasLength(1))
        best = sm.best_recoverable_version()
        self.assertThat(best, Equals(None))
        self.assertThat(sm.shares_available(), HasLength(1))
        self.assertThat(list(sm.shares_available().values())[0], Equals((2,3,10)))
        return sm

    def test_not_quite_enough_shares(self):
        s = self._storage
        ms = self.make_servermap
        num_shares = len(s._peers)
        for peerid in s._peers:
            s._peers[peerid] = {}
            num_shares -= 1
            if num_shares == 2:
                break
        # now there ought to be only two shares left
        assert len([peerid for peerid in s._peers if s._peers[peerid]]) == 2

        d = defer.succeed(None)

        d.addCallback(lambda res: ms(mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda sm:
                      self.assertThat(sm.make_sharemap(), HasLength(2)))
        d.addCallback(lambda res: ms(mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda res: ms(mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))

        return d


    def test_servermapupdater_finds_mdmf_files(self):
        # setUp already published an MDMF file for us. We just need to
        # make sure that when we run the ServermapUpdater, the file is
        # reported to have one recoverable version.
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.publish_mdmf())
        d.addCallback(lambda ignored:
            self.make_servermap(mode=MODE_CHECK))
        # Calling make_servermap also updates the servermap in the mode
        # that we specify, so we just need to see what it says.
        def _check_servermap(sm):
            self.assertThat(sm.recoverable_versions(), HasLength(1))
        d.addCallback(_check_servermap)
        return d


    def test_fetch_update(self):
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.publish_mdmf())
        d.addCallback(lambda ignored:
            self.make_servermap(mode=MODE_WRITE, update_range=(1, 2)))
        def _check_servermap(sm):
            # 10 shares
            self.assertThat(sm.update_data, HasLength(10))
            # one version
            for data in sm.update_data.values():
                self.assertThat(data, HasLength(1))
        d.addCallback(_check_servermap)
        return d


    def test_servermapupdater_finds_sdmf_files(self):
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.publish_sdmf())
        d.addCallback(lambda ignored:
            self.make_servermap(mode=MODE_CHECK))
        d.addCallback(lambda servermap:
            self.assertThat(servermap.recoverable_versions(), HasLength(1)))
        return d
