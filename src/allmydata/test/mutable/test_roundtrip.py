from cStringIO import StringIO

from twisted.trial import unittest
from twisted.internet import defer

from allmydata.util import base32, consumer
from allmydata.interfaces import NotEnoughSharesError
from allmydata.monitor import Monitor
from allmydata.mutable.common import MODE_READ, UnrecoverableFileError
from allmydata.mutable.servermap import ServerMap, ServermapUpdater
from allmydata.mutable.retrieve import Retrieve
from .util import PublishMixin, make_storagebroker, corrupt
from .. import common_util as testutil

class Roundtrip(unittest.TestCase, testutil.ShouldFailMixin, PublishMixin):
    def setUp(self):
        return self.publish_one()

    def make_servermap(self, mode=MODE_READ, oldmap=None, sb=None):
        if oldmap is None:
            oldmap = ServerMap()
        if sb is None:
            sb = self._storage_broker
        smu = ServermapUpdater(self._fn, sb, Monitor(), oldmap, mode)
        d = smu.update()
        return d

    def abbrev_verinfo(self, verinfo):
        if verinfo is None:
            return None
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = verinfo
        return "%d-%s" % (seqnum, base32.b2a(root_hash)[:4])

    def abbrev_verinfo_dict(self, verinfo_d):
        output = {}
        for verinfo,value in verinfo_d.items():
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = verinfo
            output["%d-%s" % (seqnum, base32.b2a(root_hash)[:4])] = value
        return output

    def dump_servermap(self, servermap):
        print "SERVERMAP", servermap
        print "RECOVERABLE", [self.abbrev_verinfo(v)
                              for v in servermap.recoverable_versions()]
        print "BEST", self.abbrev_verinfo(servermap.best_recoverable_version())
        print "available", self.abbrev_verinfo_dict(servermap.shares_available())

    def do_download(self, servermap, version=None):
        if version is None:
            version = servermap.best_recoverable_version()
        r = Retrieve(self._fn, self._storage_broker, servermap, version)
        c = consumer.MemoryConsumer()
        d = r.download(consumer=c)
        d.addCallback(lambda mc: "".join(mc.chunks))
        return d


    def test_basic(self):
        d = self.make_servermap()
        def _do_retrieve(servermap):
            self._smap = servermap
            #self.dump_servermap(servermap)
            self.failUnlessEqual(len(servermap.recoverable_versions()), 1)
            return self.do_download(servermap)
        d.addCallback(_do_retrieve)
        def _retrieved(new_contents):
            self.failUnlessEqual(new_contents, self.CONTENTS)
        d.addCallback(_retrieved)
        # we should be able to re-use the same servermap, both with and
        # without updating it.
        d.addCallback(lambda res: self.do_download(self._smap))
        d.addCallback(_retrieved)
        d.addCallback(lambda res: self.make_servermap(oldmap=self._smap))
        d.addCallback(lambda res: self.do_download(self._smap))
        d.addCallback(_retrieved)
        # clobbering the pubkey should make the servermap updater re-fetch it
        def _clobber_pubkey(res):
            self._fn._pubkey = None
        d.addCallback(_clobber_pubkey)
        d.addCallback(lambda res: self.make_servermap(oldmap=self._smap))
        d.addCallback(lambda res: self.do_download(self._smap))
        d.addCallback(_retrieved)
        return d

    def test_all_shares_vanished(self):
        d = self.make_servermap()
        def _remove_shares(servermap):
            for shares in self._storage._peers.values():
                shares.clear()
            d1 = self.shouldFail(NotEnoughSharesError,
                                 "test_all_shares_vanished",
                                 "ran out of servers",
                                 self.do_download, servermap)
            return d1
        d.addCallback(_remove_shares)
        return d

    def test_all_but_two_shares_vanished_updated_servermap(self):
        # tests error reporting for ticket #1742
        d = self.make_servermap()
        def _remove_shares(servermap):
            self._version = servermap.best_recoverable_version()
            for shares in self._storage._peers.values()[2:]:
                shares.clear()
            return self.make_servermap(servermap)
        d.addCallback(_remove_shares)
        def _check(updated_servermap):
            d1 = self.shouldFail(NotEnoughSharesError,
                                 "test_all_but_two_shares_vanished_updated_servermap",
                                 "ran out of servers",
                                 self.do_download, updated_servermap, version=self._version)
            return d1
        d.addCallback(_check)
        return d

    def test_no_servers(self):
        sb2 = make_storagebroker(num_peers=0)
        # if there are no servers, then a MODE_READ servermap should come
        # back empty
        d = self.make_servermap(sb=sb2)
        def _check_servermap(servermap):
            self.failUnlessEqual(servermap.best_recoverable_version(), None)
            self.failIf(servermap.recoverable_versions())
            self.failIf(servermap.unrecoverable_versions())
            self.failIf(servermap.all_servers())
        d.addCallback(_check_servermap)
        return d

    def test_no_servers_download(self):
        sb2 = make_storagebroker(num_peers=0)
        self._fn._storage_broker = sb2
        d = self.shouldFail(UnrecoverableFileError,
                            "test_no_servers_download",
                            "no recoverable versions",
                            self._fn.download_best_version)
        def _restore(res):
            # a failed download that occurs while we aren't connected to
            # anybody should not prevent a subsequent download from working.
            # This isn't quite the webapi-driven test that #463 wants, but it
            # should be close enough.
            self._fn._storage_broker = self._storage_broker
            return self._fn.download_best_version()
        def _retrieved(new_contents):
            self.failUnlessEqual(new_contents, self.CONTENTS)
        d.addCallback(_restore)
        d.addCallback(_retrieved)
        return d


    def _test_corrupt_all(self, offset, substring,
                          should_succeed=False,
                          corrupt_early=True,
                          failure_checker=None,
                          fetch_privkey=False):
        d = defer.succeed(None)
        if corrupt_early:
            d.addCallback(corrupt, self._storage, offset)
        d.addCallback(lambda res: self.make_servermap())
        if not corrupt_early:
            d.addCallback(corrupt, self._storage, offset)
        def _do_retrieve(servermap):
            ver = servermap.best_recoverable_version()
            if ver is None and not should_succeed:
                # no recoverable versions == not succeeding. The problem
                # should be noted in the servermap's list of problems.
                if substring:
                    allproblems = [str(f) for f in servermap.get_problems()]
                    self.failUnlessIn(substring, "".join(allproblems))
                return servermap
            if should_succeed:
                d1 = self._fn.download_version(servermap, ver,
                                               fetch_privkey)
                d1.addCallback(lambda new_contents:
                               self.failUnlessEqual(new_contents, self.CONTENTS))
            else:
                d1 = self.shouldFail(NotEnoughSharesError,
                                     "_corrupt_all(offset=%s)" % (offset,),
                                     substring,
                                     self._fn.download_version, servermap,
                                                                ver,
                                                                fetch_privkey)
            if failure_checker:
                d1.addCallback(failure_checker)
            d1.addCallback(lambda res: servermap)
            return d1
        d.addCallback(_do_retrieve)
        return d

    def test_corrupt_all_verbyte(self):
        # when the version byte is not 0 or 1, we hit an UnknownVersionError
        # error in unpack_share().
        d = self._test_corrupt_all(0, "UnknownVersionError")
        def _check_servermap(servermap):
            # and the dump should mention the problems
            s = StringIO()
            dump = servermap.dump(s).getvalue()
            self.failUnless("30 PROBLEMS" in dump, dump)
        d.addCallback(_check_servermap)
        return d

    def test_corrupt_all_seqnum(self):
        # a corrupt sequence number will trigger a bad signature
        return self._test_corrupt_all(1, "signature is invalid")

    def test_corrupt_all_R(self):
        # a corrupt root hash will trigger a bad signature
        return self._test_corrupt_all(9, "signature is invalid")

    def test_corrupt_all_IV(self):
        # a corrupt salt/IV will trigger a bad signature
        return self._test_corrupt_all(41, "signature is invalid")

    def test_corrupt_all_k(self):
        # a corrupt 'k' will trigger a bad signature
        return self._test_corrupt_all(57, "signature is invalid")

    def test_corrupt_all_N(self):
        # a corrupt 'N' will trigger a bad signature
        return self._test_corrupt_all(58, "signature is invalid")

    def test_corrupt_all_segsize(self):
        # a corrupt segsize will trigger a bad signature
        return self._test_corrupt_all(59, "signature is invalid")

    def test_corrupt_all_datalen(self):
        # a corrupt data length will trigger a bad signature
        return self._test_corrupt_all(67, "signature is invalid")

    def test_corrupt_all_pubkey(self):
        # a corrupt pubkey won't match the URI's fingerprint. We need to
        # remove the pubkey from the filenode, or else it won't bother trying
        # to update it.
        self._fn._pubkey = None
        return self._test_corrupt_all("pubkey",
                                      "pubkey doesn't match fingerprint")

    def test_corrupt_all_sig(self):
        # a corrupt signature is a bad one
        # the signature runs from about [543:799], depending upon the length
        # of the pubkey
        return self._test_corrupt_all("signature", "signature is invalid")

    def test_corrupt_all_share_hash_chain_number(self):
        # a corrupt share hash chain entry will show up as a bad hash. If we
        # mangle the first byte, that will look like a bad hash number,
        # causing an IndexError
        return self._test_corrupt_all("share_hash_chain", "corrupt hashes")

    def test_corrupt_all_share_hash_chain_hash(self):
        # a corrupt share hash chain entry will show up as a bad hash. If we
        # mangle a few bytes in, that will look like a bad hash.
        return self._test_corrupt_all(("share_hash_chain",4), "corrupt hashes")

    def test_corrupt_all_block_hash_tree(self):
        return self._test_corrupt_all("block_hash_tree",
                                      "block hash tree failure")

    def test_corrupt_all_block(self):
        return self._test_corrupt_all("share_data", "block hash tree failure")

    def test_corrupt_all_encprivkey(self):
        # a corrupted privkey won't even be noticed by the reader, only by a
        # writer.
        return self._test_corrupt_all("enc_privkey", None, should_succeed=True)


    def test_corrupt_all_encprivkey_late(self):
        # this should work for the same reason as above, but we corrupt
        # after the servermap update to exercise the error handling
        # code.
        # We need to remove the privkey from the node, or the retrieve
        # process won't know to update it.
        self._fn._privkey = None
        return self._test_corrupt_all("enc_privkey",
                                      None, # this shouldn't fail
                                      should_succeed=True,
                                      corrupt_early=False,
                                      fetch_privkey=True)


    # disabled until retrieve tests checkstring on each blockfetch. I didn't
    # just use a .todo because the failing-but-ignored test emits about 30kB
    # of noise.
    def OFF_test_corrupt_all_seqnum_late(self):
        # corrupting the seqnum between mapupdate and retrieve should result
        # in NotEnoughSharesError, since each share will look invalid
        def _check(res):
            f = res[0]
            self.failUnless(f.check(NotEnoughSharesError))
            self.failUnless("uncoordinated write" in str(f))
        return self._test_corrupt_all(1, "ran out of servers",
                                      corrupt_early=False,
                                      failure_checker=_check)


    def test_corrupt_all_block_late(self):
        def _check(res):
            f = res[0]
            self.failUnless(f.check(NotEnoughSharesError))
        return self._test_corrupt_all("share_data", "block hash tree failure",
                                      corrupt_early=False,
                                      failure_checker=_check)


    def test_basic_pubkey_at_end(self):
        # we corrupt the pubkey in all but the last 'k' shares, allowing the
        # download to succeed but forcing a bunch of retries first. Note that
        # this is rather pessimistic: our Retrieve process will throw away
        # the whole share if the pubkey is bad, even though the rest of the
        # share might be good.

        self._fn._pubkey = None
        k = self._fn.get_required_shares()
        N = self._fn.get_total_shares()
        d = defer.succeed(None)
        d.addCallback(corrupt, self._storage, "pubkey",
                      shnums_to_corrupt=range(0, N-k))
        d.addCallback(lambda res: self.make_servermap())
        def _do_retrieve(servermap):
            self.failUnless(servermap.get_problems())
            self.failUnless("pubkey doesn't match fingerprint"
                            in str(servermap.get_problems()[0]))
            ver = servermap.best_recoverable_version()
            r = Retrieve(self._fn, self._storage_broker, servermap, ver)
            c = consumer.MemoryConsumer()
            return r.download(c)
        d.addCallback(_do_retrieve)
        d.addCallback(lambda mc: "".join(mc.chunks))
        d.addCallback(lambda new_contents:
                      self.failUnlessEqual(new_contents, self.CONTENTS))
        return d


    def _test_corrupt_some(self, offset, mdmf=False):
        if mdmf:
            d = self.publish_mdmf()
        else:
            d = defer.succeed(None)
        d.addCallback(lambda ignored:
            corrupt(None, self._storage, offset, range(5)))
        d.addCallback(lambda ignored:
            self.make_servermap())
        def _do_retrieve(servermap):
            ver = servermap.best_recoverable_version()
            self.failUnless(ver)
            return self._fn.download_best_version()
        d.addCallback(_do_retrieve)
        d.addCallback(lambda new_contents:
            self.failUnlessEqual(new_contents, self.CONTENTS))
        return d


    def test_corrupt_some(self):
        # corrupt the data of first five shares (so the servermap thinks
        # they're good but retrieve marks them as bad), so that the
        # MODE_READ set of 6 will be insufficient, forcing node.download to
        # retry with more servers.
        return self._test_corrupt_some("share_data")


    def test_download_fails(self):
        d = corrupt(None, self._storage, "signature")
        d.addCallback(lambda ignored:
            self.shouldFail(UnrecoverableFileError, "test_download_anyway",
                            "no recoverable versions",
                            self._fn.download_best_version))
        return d



    def test_corrupt_mdmf_block_hash_tree(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            self._test_corrupt_all(("block_hash_tree", 12 * 32),
                                   "block hash tree failure",
                                   corrupt_early=True,
                                   should_succeed=False))
        return d


    def test_corrupt_mdmf_block_hash_tree_late(self):
        # Note - there is no SDMF counterpart to this test, as the SDMF
        # files are guaranteed to have exactly one block, and therefore
        # the block hash tree fits within the initial read (#1240).
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            self._test_corrupt_all(("block_hash_tree", 12 * 32),
                                   "block hash tree failure",
                                   corrupt_early=False,
                                   should_succeed=False))
        return d


    def test_corrupt_mdmf_share_data(self):
        d = self.publish_mdmf()
        d.addCallback(lambda ignored:
            # TODO: Find out what the block size is and corrupt a
            # specific block, rather than just guessing.
            self._test_corrupt_all(("share_data", 12 * 40),
                                    "block hash tree failure",
                                    corrupt_early=True,
                                    should_succeed=False))
        return d


    def test_corrupt_some_mdmf(self):
        return self._test_corrupt_some(("share_data", 12 * 40),
                                       mdmf=True)
