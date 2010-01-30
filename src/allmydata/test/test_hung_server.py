
import os, shutil
from twisted.trial import unittest
from twisted.internet import defer, reactor
from allmydata import uri
from allmydata.util.consumer import download_to_data
from allmydata.immutable import upload
from allmydata.mutable.common import UnrecoverableFileError
from allmydata.storage.common import storage_index_to_dir
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common import ShouldFailMixin
from allmydata.interfaces import NotEnoughSharesError

immutable_plaintext = "data" * 10000
mutable_plaintext = "muta" * 10000

class HungServerDownloadTest(GridTestMixin, ShouldFailMixin, unittest.TestCase):
    timeout = 30

    def _break(self, servers):
        for (id, ss) in servers:
            self.g.break_server(id)

    def _hang(self, servers, **kwargs):
        for (id, ss) in servers:
            self.g.hang_server(id, **kwargs)

    def _delete_all_shares_from(self, servers):
        serverids = [id for (id, ss) in servers]
        for (i_shnum, i_serverid, i_sharefile) in self.shares:
            if i_serverid in serverids:
                os.unlink(i_sharefile)

    def _copy_all_shares_from(self, from_servers, to_server):
        serverids = [id for (id, ss) in from_servers]
        for (i_shnum, i_serverid, i_sharefile) in self.shares:
            if i_serverid in serverids:
                self._copy_share((i_shnum, i_sharefile), to_server)

    def _copy_share(self, share, to_server):
         (sharenum, sharefile) = share
         (id, ss) = to_server
         shares_dir = os.path.join(ss.original.storedir, "shares")
         si = uri.from_string(self.uri).get_storage_index()
         si_dir = os.path.join(shares_dir, storage_index_to_dir(si))
         if not os.path.exists(si_dir):
             os.makedirs(si_dir)
         new_sharefile = os.path.join(si_dir, str(sharenum))
         shutil.copy(sharefile, new_sharefile)
         self.shares = self.find_shares(self.uri)
         # Make sure that the storage server has the share.
         self.failUnless((sharenum, ss.original.my_nodeid, new_sharefile)
                         in self.shares)

    def _set_up(self, mutable, testdir, num_clients=1, num_servers=10):
        self.mutable = mutable
        if mutable:
            self.basedir = "hung_server/mutable_" + testdir
        else:
            self.basedir = "hung_server/immutable_" + testdir

        self.set_up_grid(num_clients=num_clients, num_servers=num_servers)

        self.c0 = self.g.clients[0]
        nm = self.c0.nodemaker
        self.servers = [(id, ss) for (id, ss) in nm.storage_broker.get_all_servers()]

        if mutable:
            d = nm.create_mutable_file(mutable_plaintext)
            def _uploaded_mutable(node):
                self.uri = node.get_uri()
                self.shares = self.find_shares(self.uri)
            d.addCallback(_uploaded_mutable)
        else:
            data = upload.Data(immutable_plaintext, convergence="")
            d = self.c0.upload(data)
            def _uploaded_immutable(upload_res):
                self.uri = upload_res.uri
                self.shares = self.find_shares(self.uri)
            d.addCallback(_uploaded_immutable)
        return d

    def _check_download(self):
        n = self.c0.create_node_from_uri(self.uri)
        if self.mutable:
            d = n.download_best_version()
            expected_plaintext = mutable_plaintext
        else:
            d = download_to_data(n)
            expected_plaintext = immutable_plaintext
        def _got_data(data):
            self.failUnlessEqual(data, expected_plaintext)
        d.addCallback(_got_data)
        return d

    def _should_fail_download(self):
        if self.mutable:
            return self.shouldFail(UnrecoverableFileError, self.basedir,
                                   "no recoverable versions",
                                   self._check_download)
        else:
            return self.shouldFail(NotEnoughSharesError, self.basedir,
                                   "Failed to get enough shareholders",
                                   self._check_download)


    def test_10_good_sanity_check(self):
        d = defer.succeed(None)
        for mutable in [False, True]:
            d.addCallback(lambda ign: self._set_up(mutable, "test_10_good_sanity_check"))
            d.addCallback(lambda ign: self._check_download())
        return d

    def test_10_good_copied_share(self):
        d = defer.succeed(None)
        for mutable in [False, True]:
            d.addCallback(lambda ign: self._set_up(mutable, "test_10_good_copied_share"))
            d.addCallback(lambda ign: self._copy_all_shares_from(self.servers[2:3], self.servers[0]))
            d.addCallback(lambda ign: self._check_download())
            return d

    def test_3_good_7_noshares(self):
        d = defer.succeed(None)
        for mutable in [False, True]:
            d.addCallback(lambda ign: self._set_up(mutable, "test_3_good_7_noshares"))
            d.addCallback(lambda ign: self._delete_all_shares_from(self.servers[3:]))
            d.addCallback(lambda ign: self._check_download())
        return d

    def test_2_good_8_broken_fail(self):
        d = defer.succeed(None)
        for mutable in [False, True]:
            d.addCallback(lambda ign: self._set_up(mutable, "test_2_good_8_broken_fail"))
            d.addCallback(lambda ign: self._break(self.servers[2:]))
            d.addCallback(lambda ign: self._should_fail_download())
        return d

    def test_2_good_8_noshares_fail(self):
        d = defer.succeed(None)
        for mutable in [False, True]:
            d.addCallback(lambda ign: self._set_up(mutable, "test_2_good_8_noshares_fail"))
            d.addCallback(lambda ign: self._delete_all_shares_from(self.servers[2:]))
            d.addCallback(lambda ign: self._should_fail_download())
        return d

    def test_2_good_8_broken_copied_share(self):
        d = defer.succeed(None)
        for mutable in [False, True]:
            d.addCallback(lambda ign: self._set_up(mutable, "test_2_good_8_broken_copied_share"))
            d.addCallback(lambda ign: self._copy_all_shares_from(self.servers[2:3], self.servers[0]))
            d.addCallback(lambda ign: self._break(self.servers[2:]))
            d.addCallback(lambda ign: self._check_download())
        return d

    def test_2_good_8_broken_duplicate_share_fail(self):
        d = defer.succeed(None)
        for mutable in [False, True]:
            d.addCallback(lambda ign: self._set_up(mutable, "test_2_good_8_broken_duplicate_share_fail"))
            d.addCallback(lambda ign: self._copy_all_shares_from(self.servers[1:2], self.servers[0]))
            d.addCallback(lambda ign: self._break(self.servers[2:]))
            d.addCallback(lambda ign: self._should_fail_download())
        return d

    # The tests below do not currently pass for mutable files.

    def test_3_good_7_hung(self):
        d = defer.succeed(None)
        for mutable in [False]:
            d.addCallback(lambda ign: self._set_up(mutable, "test_3_good_7_hung"))
            d.addCallback(lambda ign: self._hang(self.servers[3:]))
            d.addCallback(lambda ign: self._check_download())
        return d

    def test_2_good_8_hung_then_1_recovers(self):
        d = defer.succeed(None)
        for mutable in [False]:
            recovered = defer.Deferred()
            d.addCallback(lambda ign: self._set_up(mutable, "test_2_good_8_hung_then_1_recovers"))
            d.addCallback(lambda ign: self._hang(self.servers[2:3], until=recovered))
            d.addCallback(lambda ign: self._hang(self.servers[3:]))
            d.addCallback(lambda ign: reactor.callLater(5, recovered.callback, None))
            d.addCallback(lambda ign: self._check_download())
        return d

    def test_2_good_8_hung_then_1_recovers_with_2_shares(self):
        d = defer.succeed(None)
        for mutable in [False]:
            recovered = defer.Deferred()
            d.addCallback(lambda ign: self._set_up(mutable, "test_2_good_8_hung_then_1_recovers_with_2_shares"))
            d.addCallback(lambda ign: self._copy_all_shares_from(self.servers[0:1], self.servers[2]))
            d.addCallback(lambda ign: self._hang(self.servers[2:3], until=recovered))
            d.addCallback(lambda ign: self._hang(self.servers[3:]))
            d.addCallback(lambda ign: reactor.callLater(5, recovered.callback, None))
            d.addCallback(lambda ign: self._check_download())
        return d
