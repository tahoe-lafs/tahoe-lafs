
import os, shutil
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import failure
from allmydata import uri
from allmydata.util.consumer import download_to_data
from allmydata.immutable import upload
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

    # untested
    def _pick_a_share_from(self, server):
        (id, ss) = server
        for (i_shnum, i_serverid, i_sharefile) in self.shares:
            if i_serverid == id:
                return (i_shnum, i_sharefile)
        raise AssertionError("server %r had no shares" % server)

    # untested
    def _copy_all_shares_from(self, from_servers, to_server):
        serverids = [id for (id, ss) in from_servers]
        for (i_shnum, i_serverid, i_sharefile) in self.shares:
            if i_serverid in serverids:
                self._copy_share((i_shnum, i_sharefile), to_server)

    # untested
    def _copy_share(self, share, to_server):
         (sharenum, sharefile) = share
         (id, ss) = to_server
         # FIXME: this doesn't work because we only have a LocalWrapper
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

    # untested
    def _add_server(self, server_number, readonly=False):
        ss = self.g.make_server(server_number, readonly)
        self.g.add_server(server_number, ss)
        self.shares = self.find_shares(self.uri)

    def _set_up(self, testdir, num_clients=1, num_servers=10):
        self.basedir = "download/" + testdir
        self.set_up_grid(num_clients=num_clients, num_servers=num_servers)

        self.c0 = self.g.clients[0]
        sb = self.c0.nodemaker.storage_broker
        self.servers = [(id, ss) for (id, ss) in sb.get_all_servers()]

        data = upload.Data(immutable_plaintext, convergence="")
        d = self.c0.upload(data)
        def _uploaded(ur):
            self.uri = ur.uri
            self.shares = self.find_shares(self.uri)
        d.addCallback(_uploaded)
        return d

    def test_10_good_sanity_check(self):
        d = self._set_up("test_10_good_sanity_check")
        d.addCallback(lambda ign: self.download_immutable())
        return d

    def test_3_good_7_hung(self):
        d = self._set_up("test_3_good_7_hung")
        d.addCallback(lambda ign: self._hang(self.servers[3:]))
        d.addCallback(lambda ign: self.download_immutable())
        return d

    def test_3_good_7_noshares(self):
        d = self._set_up("test_3_good_7_noshares")
        d.addCallback(lambda ign: self._delete_all_shares_from(self.servers[3:]))
        d.addCallback(lambda ign: self.download_immutable())
        return d

    def test_2_good_8_broken_fail(self):
        d = self._set_up("test_2_good_8_broken_fail")
        d.addCallback(lambda ign: self._break(self.servers[2:]))
        d.addCallback(lambda ign:
                      self.shouldFail(NotEnoughSharesError, "test_2_good_8_broken_fail",
                                      "Failed to get enough shareholders: have 2, need 3",
                                      self.download_immutable))
        return d

    def test_2_good_8_noshares_fail(self):
        d = self._set_up("test_2_good_8_noshares_fail")
        d.addCallback(lambda ign: self._delete_all_shares_from(self.servers[2:]))
        d.addCallback(lambda ign:
                      self.shouldFail(NotEnoughSharesError, "test_2_good_8_noshares_fail",
                                      "Failed to get enough shareholders: have 2, need 3",
                                      self.download_immutable))
        return d

    def test_2_good_8_hung_then_1_recovers(self):
        recovered = defer.Deferred()
        d = self._set_up("test_2_good_8_hung_then_1_recovers")
        d.addCallback(lambda ign: self._hang(self.servers[2:3], until=recovered))
        d.addCallback(lambda ign: self._hang(self.servers[3:]))
        d.addCallback(lambda ign: self.download_immutable())
        reactor.callLater(5, recovered.callback, None)
        return d

    def test_2_good_8_hung_then_1_recovers_with_2_shares(self):
        recovered = defer.Deferred()
        d = self._set_up("test_2_good_8_hung_then_1_recovers_with_2_shares")
        d.addCallback(lambda ign: self._copy_all_shares_from(self.servers[0:1], self.servers[2]))
        d.addCallback(lambda ign: self._hang(self.servers[2:3], until=recovered))
        d.addCallback(lambda ign: self._hang(self.servers[3:]))
        d.addCallback(lambda ign: self.download_immutable())
        reactor.callLater(5, recovered.callback, None)
        return d

    def download_immutable(self):
        n = self.c0.create_node_from_uri(self.uri)
        d = download_to_data(n)
        def _got_data(data):
            self.failUnlessEqual(data, immutable_plaintext)
        d.addCallback(_got_data)
        return d

    # unused
    def download_mutable(self):
        n = self.c0.create_node_from_uri(self.uri)
        d = n.download_best_version()
        def _got_data(data):
            self.failUnlessEqual(data, mutable_plaintext)
        d.addCallback(_got_data)
        return d
