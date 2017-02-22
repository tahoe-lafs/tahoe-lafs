from twisted.trial import unittest
from foolscap.api import flushEventualQueue
from allmydata.monitor import Monitor
from allmydata.mutable.common import CorruptShareError
from .util import PublishMixin, corrupt, CheckerMixin

class Checker(unittest.TestCase, CheckerMixin, PublishMixin):
    def setUp(self):
        return self.publish_one()


    def test_check_good(self):
        d = self._fn.check(Monitor())
        d.addCallback(self.check_good, "test_check_good")
        return d

    def test_check_mdmf_good(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_good, "test_check_mdmf_good")
        return d

    def test_check_no_shares(self):
        for shares in self._storage._peers.values():
            shares.clear()
        d = self._fn.check(Monitor())
        d.addCallback(self.check_bad, "test_check_no_shares")
        return d

    def test_check_mdmf_no_shares(self):
        d = self.publish_mdmf()
        def _then(ignored):
            for share in self._storage._peers.values():
                share.clear()
        d.addCallback(_then)
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_check_mdmf_no_shares")
        return d

    def test_check_not_enough_shares(self):
        for shares in self._storage._peers.values():
            for shnum in shares.keys():
                if shnum > 0:
                    del shares[shnum]
        d = self._fn.check(Monitor())
        d.addCallback(self.check_bad, "test_check_not_enough_shares")
        return d

    def test_check_mdmf_not_enough_shares(self):
        d = self.publish_mdmf()
        def _then(ignored):
            for shares in self._storage._peers.values():
                for shnum in shares.keys():
                    if shnum > 0:
                        del shares[shnum]
        d.addCallback(_then)
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_check_mdmf_not_enougH_shares")
        return d


    def test_check_all_bad_sig(self):
        d = corrupt(None, self._storage, 1) # bad sig
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_check_all_bad_sig")
        return d

    def test_check_mdmf_all_bad_sig(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, 1))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_bad, "test_check_mdmf_all_bad_sig")
        return d

    def test_verify_mdmf_all_bad_sharedata(self):
        d = self.publish_mdmf()
        # On 8 of the shares, corrupt the beginning of the share data.
        # The signature check during the servermap update won't catch this.
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "share_data", range(8)))
        # On 2 of the shares, corrupt the end of the share data.
        # The signature check during the servermap update won't catch
        # this either, and the retrieval process will have to process
        # all of the segments before it notices.
        d.addCallback(lambda ignored:
            # the block hash tree comes right after the share data, so if we
            # corrupt a little before the block hash tree, we'll corrupt in the
            # last block of each share.
            corrupt(None, self._storage, "block_hash_tree", [8, 9], -5))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        # The verifier should flag the file as unhealthy, and should
        # list all 10 shares as bad.
        d.addCallback(self.check_bad, "test_verify_mdmf_all_bad_sharedata")
        def _check_num_bad(r):
            self.failIf(r.is_recoverable())
            smap = r.get_servermap()
            self.failUnlessEqual(len(smap.get_bad_shares()), 10)
        d.addCallback(_check_num_bad)
        return d

    def test_check_all_bad_blocks(self):
        d = corrupt(None, self._storage, "share_data", [9]) # bad blocks
        # the Checker won't notice this.. it doesn't look at actual data
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_good, "test_check_all_bad_blocks")
        return d


    def test_check_mdmf_all_bad_blocks(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "share_data"))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor()))
        d.addCallback(self.check_good, "test_check_mdmf_all_bad_blocks")
        return d

    def test_verify_good(self):
        d = self._fn.check(Monitor(), verify=True)
        d.addCallback(self.check_good, "test_verify_good")
        return d

    def test_verify_all_bad_sig(self):
        d = corrupt(None, self._storage, 1) # bad sig
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_all_bad_sig")
        return d

    def test_verify_one_bad_sig(self):
        d = corrupt(None, self._storage, 1, [9]) # bad sig
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_one_bad_sig")
        return d

    def test_verify_one_bad_block(self):
        d = corrupt(None, self._storage, "share_data", [9]) # bad blocks
        # the Verifier *will* notice this, since it examines every byte
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_one_bad_block")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "block hash tree failure",
                      "test_verify_one_bad_block")
        return d

    def test_verify_one_bad_sharehash(self):
        d = corrupt(None, self._storage, "share_hash_chain", [9], 5)
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_one_bad_sharehash")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "corrupt hashes",
                      "test_verify_one_bad_sharehash")
        return d

    def test_verify_one_bad_encprivkey(self):
        d = corrupt(None, self._storage, "enc_privkey", [9]) # bad privkey
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_one_bad_encprivkey")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "invalid privkey",
                      "test_verify_one_bad_encprivkey")
        return d

    def test_verify_one_bad_encprivkey_uncheckable(self):
        d = corrupt(None, self._storage, "enc_privkey", [9]) # bad privkey
        readonly_fn = self._fn.get_readonly()
        # a read-only node has no way to validate the privkey
        d.addCallback(lambda ignored:
            readonly_fn.check(Monitor(), verify=True))
        d.addCallback(self.check_good,
                      "test_verify_one_bad_encprivkey_uncheckable")
        return d


    def test_verify_mdmf_good(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_good, "test_verify_mdmf_good")
        return d


    def test_verify_mdmf_one_bad_block(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "share_data", [1]))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        # We should find one bad block here
        d.addCallback(self.check_bad, "test_verify_mdmf_one_bad_block")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "block hash tree failure",
                      "test_verify_mdmf_one_bad_block")
        return d


    def test_verify_mdmf_bad_encprivkey(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "enc_privkey", [0]))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_mdmf_bad_encprivkey")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "privkey",
                      "test_verify_mdmf_bad_encprivkey")
        return d


    def test_verify_mdmf_bad_sig(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, 1, [1]))
        d.addCallback(lambda ignored:
            self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_bad, "test_verify_mdmf_bad_sig")
        return d


    def test_verify_mdmf_bad_encprivkey_uncheckable(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, "enc_privkey", [1]))
        d.addCallback(lambda ignored:
            self._fn.get_readonly())
        d.addCallback(lambda fn:
            fn.check(Monitor(), verify=True))
        d.addCallback(self.check_good,
                      "test_verify_mdmf_bad_encprivkey_uncheckable")
        return d

    def test_verify_sdmf_empty(self):
        d = self.publish_sdmf("")
        d.addCallback(lambda ignored: self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_good, "test_verify_sdmf")
        d.addCallback(flushEventualQueue)
        return d

    def test_verify_mdmf_empty(self):
        d = self.publish_mdmf("")
        d.addCallback(lambda ignored: self._fn.check(Monitor(), verify=True))
        d.addCallback(self.check_good, "test_verify_mdmf")
        d.addCallback(flushEventualQueue)
        return d
