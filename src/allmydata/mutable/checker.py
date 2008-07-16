
import struct
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from allmydata import hashtree
from allmydata.util import hashutil, base32
from allmydata.interfaces import ICheckerResults

from common import MODE_CHECK, CorruptShareError
from servermap import ServerMap, ServermapUpdater
from layout import unpack_share, SIGNED_PREFIX

class MutableChecker:

    def __init__(self, node):
        self._node = node
        self.healthy = True
        self.problems = []
        self._storage_index = self._node.get_storage_index()

    def check(self, verify=False, repair=False):
        servermap = ServerMap()
        self.do_verify = verify
        self.do_repair = repair
        u = ServermapUpdater(self._node, servermap, MODE_CHECK)
        d = u.update()
        d.addCallback(self._got_mapupdate_results)
        if verify:
            d.addCallback(self._verify_all_shares)
        d.addCallback(self._maybe_do_repair)
        d.addCallback(self._return_results)
        return d

    def _got_mapupdate_results(self, servermap):
        # the file is healthy if there is exactly one recoverable version, it
        # has at least N distinct shares, and there are no unrecoverable
        # versions: all existing shares will be for the same version.
        self.best_version = None
        if servermap.unrecoverable_versions():
            self.healthy = False
        num_recoverable = len(servermap.recoverable_versions())
        if num_recoverable == 0:
            self.healthy = False
        else:
            if num_recoverable > 1:
                self.healthy = False
            self.best_version = servermap.best_recoverable_version()
            available_shares = servermap.shares_available()
            (num_distinct_shares, k, N) = available_shares[self.best_version]
            if num_distinct_shares < N:
                self.healthy = False

        return servermap

    def _verify_all_shares(self, servermap):
        # read every byte of each share
        if not self.best_version:
            return
        versionmap = servermap.make_versionmap()
        shares = versionmap[self.best_version]
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.best_version
        offsets = dict(offsets_tuple)
        readv = [ (0, offsets["EOF"]) ]
        dl = []
        for (shnum, peerid, timestamp) in shares:
            ss = servermap.connections[peerid]
            d = self._do_read(ss, peerid, self._storage_index, [shnum], readv)
            d.addCallback(self._got_answer, peerid)
            dl.append(d)
        return defer.DeferredList(dl, fireOnOneErrback=True)

    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        # isolate the callRemote to a separate method, so tests can subclass
        # Publish and override it
        d = ss.callRemote("slot_readv", storage_index, shnums, readv)
        return d

    def _got_answer(self, datavs, peerid):
        for shnum,datav in datavs.items():
            data = datav[0]
            try:
                self._got_results_one_share(shnum, peerid, data)
            except CorruptShareError:
                f = failure.Failure()
                self.add_problem(shnum, peerid, f)

    def check_prefix(self, peerid, shnum, data):
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.best_version
        got_prefix = data[:struct.calcsize(SIGNED_PREFIX)]
        if got_prefix != prefix:
            raise CorruptShareError(peerid, shnum,
                                    "prefix mismatch: share changed while we were reading it")

    def _got_results_one_share(self, shnum, peerid, data):
        self.check_prefix(peerid, shnum, data)

        # the [seqnum:signature] pieces are validated by _compare_prefix,
        # which checks their signature against the pubkey known to be
        # associated with this file.

        (seqnum, root_hash, IV, k, N, segsize, datalen, pubkey, signature,
         share_hash_chain, block_hash_tree, share_data,
         enc_privkey) = unpack_share(data)

        # validate [share_hash_chain,block_hash_tree,share_data]

        leaves = [hashutil.block_hash(share_data)]
        t = hashtree.HashTree(leaves)
        if list(t) != block_hash_tree:
            raise CorruptShareError(peerid, shnum, "block hash tree failure")
        share_hash_leaf = t[0]
        t2 = hashtree.IncompleteHashTree(N)
        # root_hash was checked by the signature
        t2.set_hashes({0: root_hash})
        try:
            t2.set_hashes(hashes=share_hash_chain,
                          leaves={shnum: share_hash_leaf})
        except (hashtree.BadHashError, hashtree.NotEnoughHashesError,
                IndexError), e:
            msg = "corrupt hashes: %s" % (e,)
            raise CorruptShareError(peerid, shnum, msg)

        # validate enc_privkey: only possible if we have a write-cap
        if not self._node.is_readonly():
            alleged_privkey_s = self._node._decrypt_privkey(enc_privkey)
            alleged_writekey = hashutil.ssk_writekey_hash(alleged_privkey_s)
            if alleged_writekey != self._node.get_writekey():
                raise CorruptShareError(peerid, shnum, "invalid privkey")

    def _maybe_do_repair(self, res):
        if self.healthy:
            return
        if not self.do_repair:
            return
        pass

    def _return_results(self, res):
        r = Results(self._storage_index)
        r.healthy = self.healthy
        r.problems = self.problems
        return r


    def add_problem(self, shnum, peerid, what):
        self.healthy = False
        self.problems.append( (peerid, self._storage_index, shnum, what) )

class Results:
    implements(ICheckerResults)

    def __init__(self, storage_index):
        self.storage_index = storage_index
        self.storage_index_s = base32.b2a(storage_index)[:6]

    def is_healthy(self):
        return self.healthy

    def html_summary(self):
        if self.healthy:
            return "<span>healthy</span>"
        return "<span>NOT HEALTHY</span>"

    def html(self):
        s = "<div>\n"
        s += "<h1>Checker Results for Mutable SI=%s</h1>\n" % self.storage_index_s
        if self.healthy:
            s += "<h2>Healthy!</h2>\n"
        else:
            s += "<h2>Not Healthy!</h2>\n"
        s += "</div>\n"
        return s

