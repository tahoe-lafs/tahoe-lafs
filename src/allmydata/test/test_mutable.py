
import struct
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import failure
from allmydata import uri, download
from allmydata.util import base32, testutil
from allmydata.util.idlib import shortnodeid_b2a
from allmydata.util.hashutil import tagged_hash
from allmydata.encode import NotEnoughSharesError
from allmydata.interfaces import IURI, IMutableFileURI, IUploadable
from foolscap.eventual import eventually, fireEventually
from foolscap.logging import log
import sha

from allmydata.mutable.node import MutableFileNode, BackoffAgent
from allmydata.mutable.common import DictOfSets, ResponseCache, \
     MODE_CHECK, MODE_ANYTHING, MODE_WRITE, MODE_READ, \
     UnrecoverableFileError, UncoordinatedWriteError
from allmydata.mutable.retrieve import Retrieve
from allmydata.mutable.publish import Publish
from allmydata.mutable.servermap import ServerMap, ServermapUpdater
from allmydata.mutable.layout import unpack_header, unpack_share

# this "FastMutableFileNode" exists solely to speed up tests by using smaller
# public/private keys. Once we switch to fast DSA-based keys, we can get rid
# of this.

class FastMutableFileNode(MutableFileNode):
    SIGNATURE_KEY_SIZE = 522

# this "FakeStorage" exists to put the share data in RAM and avoid using real
# network connections, both to speed up the tests and to reduce the amount of
# non-mutable.py code being exercised.

class FakeStorage:
    # this class replaces the collection of storage servers, allowing the
    # tests to examine and manipulate the published shares. It also lets us
    # control the order in which read queries are answered, to exercise more
    # of the error-handling code in Retrieve .
    #
    # Note that we ignore the storage index: this FakeStorage instance can
    # only be used for a single storage index.


    def __init__(self):
        self._peers = {}
        # _sequence is used to cause the responses to occur in a specific
        # order. If it is in use, then we will defer queries instead of
        # answering them right away, accumulating the Deferreds in a dict. We
        # don't know exactly how many queries we'll get, so exactly one
        # second after the first query arrives, we will release them all (in
        # order).
        self._sequence = None
        self._pending = {}

    def read(self, peerid, storage_index):
        shares = self._peers.get(peerid, {})
        if self._sequence is None:
            return defer.succeed(shares)
        d = defer.Deferred()
        if not self._pending:
            reactor.callLater(1.0, self._fire_readers)
        self._pending[peerid] = (d, shares)
        return d

    def _fire_readers(self):
        pending = self._pending
        self._pending = {}
        extra = []
        for peerid in self._sequence:
            if peerid in pending:
                d, shares = pending.pop(peerid)
                eventually(d.callback, shares)
        for (d, shares) in pending.values():
            eventually(d.callback, shares)

    def write(self, peerid, storage_index, shnum, offset, data):
        if peerid not in self._peers:
            self._peers[peerid] = {}
        shares = self._peers[peerid]
        f = StringIO()
        f.write(shares.get(shnum, ""))
        f.seek(offset)
        f.write(data)
        shares[shnum] = f.getvalue()


class FakeStorageServer:
    def __init__(self, peerid, storage):
        self.peerid = peerid
        self.storage = storage
        self.queries = 0
    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        d = fireEventually()
        d.addCallback(lambda res: _call())
        return d

    def slot_readv(self, storage_index, shnums, readv):
        d = self.storage.read(self.peerid, storage_index)
        def _read(shares):
            response = {}
            for shnum in shares:
                if shnums and shnum not in shnums:
                    continue
                vector = response[shnum] = []
                for (offset, length) in readv:
                    assert isinstance(offset, (int, long)), offset
                    assert isinstance(length, (int, long)), length
                    vector.append(shares[shnum][offset:offset+length])
            return response
        d.addCallback(_read)
        return d

    def slot_testv_and_readv_and_writev(self, storage_index, secrets,
                                        tw_vectors, read_vector):
        # always-pass: parrot the test vectors back to them.
        readv = {}
        for shnum, (testv, writev, new_length) in tw_vectors.items():
            for (offset, length, op, specimen) in testv:
                assert op in ("le", "eq", "ge")
            # TODO: this isn't right, the read is controlled by read_vector,
            # not by testv
            readv[shnum] = [ specimen
                             for (offset, length, op, specimen)
                             in testv ]
            for (offset, data) in writev:
                self.storage.write(self.peerid, storage_index, shnum,
                                   offset, data)
        answer = (True, readv)
        return fireEventually(answer)


# our "FakeClient" has just enough functionality of the real Client to let
# the tests run.

class FakeClient:
    mutable_file_node_class = FastMutableFileNode

    def __init__(self, num_peers=10):
        self._storage = FakeStorage()
        self._num_peers = num_peers
        self._peerids = [tagged_hash("peerid", "%d" % i)[:20]
                         for i in range(self._num_peers)]
        self._connections = dict([(peerid, FakeStorageServer(peerid,
                                                             self._storage))
                                  for peerid in self._peerids])
        self.nodeid = "fakenodeid"

    def log(self, msg, **kw):
        return log.msg(msg, **kw)

    def get_renewal_secret(self):
        return "I hereby permit you to renew my files"
    def get_cancel_secret(self):
        return "I hereby permit you to cancel my leases"

    def create_mutable_file(self, contents=""):
        n = self.mutable_file_node_class(self)
        d = n.create(contents)
        d.addCallback(lambda res: n)
        return d

    def notify_retrieve(self, r):
        pass
    def notify_publish(self, p):
        pass
    def notify_mapupdate(self, u):
        pass

    def create_node_from_uri(self, u):
        u = IURI(u)
        assert IMutableFileURI.providedBy(u), u
        res = self.mutable_file_node_class(self).init_from_uri(u)
        return res

    def get_permuted_peers(self, service_name, key):
        """
        @return: list of (peerid, connection,)
        """
        results = []
        for (peerid, connection) in self._connections.items():
            assert isinstance(peerid, str)
            permuted = sha.new(key + peerid).digest()
            results.append((permuted, peerid, connection))
        results.sort()
        results = [ (r[1],r[2]) for r in results]
        return results

    def upload(self, uploadable):
        assert IUploadable.providedBy(uploadable)
        d = uploadable.get_size()
        d.addCallback(lambda length: uploadable.read(length))
        #d.addCallback(self.create_mutable_file)
        def _got_data(datav):
            data = "".join(datav)
            #newnode = FastMutableFileNode(self)
            return uri.LiteralFileURI(data)
        d.addCallback(_got_data)
        return d


def flip_bit(original, byte_offset):
    return (original[:byte_offset] +
            chr(ord(original[byte_offset]) ^ 0x01) +
            original[byte_offset+1:])

def corrupt(res, s, offset, shnums_to_corrupt=None):
    # if shnums_to_corrupt is None, corrupt all shares. Otherwise it is a
    # list of shnums to corrupt.
    for peerid in s._peers:
        shares = s._peers[peerid]
        for shnum in shares:
            if (shnums_to_corrupt is not None
                and shnum not in shnums_to_corrupt):
                continue
            data = shares[shnum]
            (version,
             seqnum,
             root_hash,
             IV,
             k, N, segsize, datalen,
             o) = unpack_header(data)
            if isinstance(offset, tuple):
                offset1, offset2 = offset
            else:
                offset1 = offset
                offset2 = 0
            if offset1 == "pubkey":
                real_offset = 107
            elif offset1 in o:
                real_offset = o[offset1]
            else:
                real_offset = offset1
            real_offset = int(real_offset) + offset2
            assert isinstance(real_offset, int), offset
            shares[shnum] = flip_bit(data, real_offset)
    return res

class Filenode(unittest.TestCase, testutil.ShouldFailMixin):
    def setUp(self):
        self.client = FakeClient()

    def test_create(self):
        d = self.client.create_mutable_file()
        def _created(n):
            self.failUnless(isinstance(n, FastMutableFileNode))
            peer0 = self.client._peerids[0]
            shnums = self.client._storage._peers[peer0].keys()
            self.failUnlessEqual(len(shnums), 1)
        d.addCallback(_created)
        return d

    def test_serialize(self):
        n = MutableFileNode(self.client)
        calls = []
        def _callback(*args, **kwargs):
            self.failUnlessEqual(args, (4,) )
            self.failUnlessEqual(kwargs, {"foo": 5})
            calls.append(1)
            return 6
        d = n._do_serialized(_callback, 4, foo=5)
        def _check_callback(res):
            self.failUnlessEqual(res, 6)
            self.failUnlessEqual(calls, [1])
        d.addCallback(_check_callback)

        def _errback():
            raise ValueError("heya")
        d.addCallback(lambda res:
                      self.shouldFail(ValueError, "_check_errback", "heya",
                                      n._do_serialized, _errback))
        return d

    def test_upload_and_download(self):
        d = self.client.create_mutable_file()
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            d.addCallback(lambda smap: smap.dump(StringIO()))
            d.addCallback(lambda sio:
                          self.failUnless("3-of-10" in sio.getvalue()))
            d.addCallback(lambda res: n.overwrite("contents 1"))
            d.addCallback(lambda res: self.failUnlessIdentical(res, None))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.overwrite("contents 2"))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            d.addCallback(lambda res: n.download(download.Data()))
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            d.addCallback(lambda smap: n.upload("contents 3", smap))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            d.addCallback(lambda res: n.get_servermap(MODE_ANYTHING))
            d.addCallback(lambda smap:
                          n.download_version(smap,
                                             smap.best_recoverable_version()))
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            return d
        d.addCallback(_created)
        return d

    def test_create_with_initial_contents(self):
        d = self.client.create_mutable_file("contents 1")
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.overwrite("contents 2"))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d

    def failUnlessCurrentSeqnumIs(self, n, expected_seqnum):
        d = n.get_servermap(MODE_READ)
        d.addCallback(lambda servermap: servermap.best_recoverable_version())
        d.addCallback(lambda verinfo:
                      self.failUnlessEqual(verinfo[0], expected_seqnum))
        return d

    def test_modify(self):
        def _modifier(old_contents):
            return old_contents + "line2"
        def _non_modifier(old_contents):
            return old_contents
        def _none_modifier(old_contents):
            return None
        def _error_modifier(old_contents):
            raise ValueError("oops")
        calls = []
        def _ucw_error_modifier(old_contents):
            # simulate an UncoordinatedWriteError once
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents + "line3"

        d = self.client.create_mutable_file("line1")
        def _created(n):
            d = n.modify(_modifier)
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2))

            d.addCallback(lambda res: n.modify(_non_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2))

            d.addCallback(lambda res: n.modify(_none_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2))

            d.addCallback(lambda res:
                          self.shouldFail(ValueError, "error_modifier", None,
                                          n.modify, _error_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2))

            d.addCallback(lambda res: n.modify(_ucw_error_modifier))
            d.addCallback(lambda res: self.failUnlessEqual(len(calls), 2))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3))

            return d
        d.addCallback(_created)
        return d

    def test_modify_backoffer(self):
        def _modifier(old_contents):
            return old_contents + "line2"
        calls = []
        def _ucw_error_modifier(old_contents):
            # simulate an UncoordinatedWriteError once
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents + "line3"
        def _always_ucw_error_modifier(old_contents):
            raise UncoordinatedWriteError("simulated")
        def _backoff_stopper(node, f):
            return f
        def _backoff_pauser(node, f):
            d = defer.Deferred()
            reactor.callLater(0.5, d.callback, None)
            return d

        # the give-up-er will hit its maximum retry count quickly
        giveuper = BackoffAgent()
        giveuper._delay = 0.1
        giveuper.factor = 1

        d = self.client.create_mutable_file("line1")
        def _created(n):
            d = n.modify(_modifier)
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2))

            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "_backoff_stopper", None,
                                          n.modify, _ucw_error_modifier,
                                          _backoff_stopper))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2))

            def _reset_ucw_error_modifier(res):
                calls[:] = []
                return res
            d.addCallback(_reset_ucw_error_modifier)
            d.addCallback(lambda res: n.modify(_ucw_error_modifier,
                                               _backoff_pauser))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3))

            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "giveuper", None,
                                          n.modify, _always_ucw_error_modifier,
                                          giveuper.delay))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3))

            return d
        d.addCallback(_created)
        return d

    def test_upload_and_download_full_size_keys(self):
        self.client.mutable_file_node_class = MutableFileNode
        d = self.client.create_mutable_file()
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            d.addCallback(lambda smap: smap.dump(StringIO()))
            d.addCallback(lambda sio:
                          self.failUnless("3-of-10" in sio.getvalue()))
            d.addCallback(lambda res: n.overwrite("contents 1"))
            d.addCallback(lambda res: self.failUnlessIdentical(res, None))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.overwrite("contents 2"))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            d.addCallback(lambda res: n.download(download.Data()))
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            d.addCallback(lambda smap: n.upload("contents 3", smap))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            d.addCallback(lambda res: n.get_servermap(MODE_ANYTHING))
            d.addCallback(lambda smap:
                          n.download_version(smap,
                                             smap.best_recoverable_version()))
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            return d
        d.addCallback(_created)
        return d


class MakeShares(unittest.TestCase):
    def test_encrypt(self):
        c = FakeClient()
        fn = FastMutableFileNode(c)
        CONTENTS = "some initial contents"
        d = fn.create(CONTENTS)
        def _created(res):
            p = Publish(fn, None)
            p.salt = "SALT" * 4
            p.readkey = "\x00" * 16
            p.newdata = CONTENTS
            p.required_shares = 3
            p.total_shares = 10
            p.setup_encoding_parameters()
            return p._encrypt_and_encode()
        d.addCallback(_created)
        def _done(shares_and_shareids):
            (shares, share_ids) = shares_and_shareids
            self.failUnlessEqual(len(shares), 10)
            for sh in shares:
                self.failUnless(isinstance(sh, str))
                self.failUnlessEqual(len(sh), 7)
            self.failUnlessEqual(len(share_ids), 10)
        d.addCallback(_done)
        return d

    def test_generate(self):
        c = FakeClient()
        fn = FastMutableFileNode(c)
        CONTENTS = "some initial contents"
        d = fn.create(CONTENTS)
        def _created(res):
            p = Publish(fn, None)
            self._p = p
            p.newdata = CONTENTS
            p.required_shares = 3
            p.total_shares = 10
            p.setup_encoding_parameters()
            p._new_seqnum = 3
            p.salt = "SALT" * 4
            # make some fake shares
            shares_and_ids = ( ["%07d" % i for i in range(10)], range(10) )
            p._privkey = fn.get_privkey()
            p._encprivkey = fn.get_encprivkey()
            p._pubkey = fn.get_pubkey()
            return p._generate_shares(shares_and_ids)
        d.addCallback(_created)
        def _generated(res):
            p = self._p
            final_shares = p.shares
            root_hash = p.root_hash
            self.failUnlessEqual(len(root_hash), 32)
            self.failUnless(isinstance(final_shares, dict))
            self.failUnlessEqual(len(final_shares), 10)
            self.failUnlessEqual(sorted(final_shares.keys()), range(10))
            for i,sh in final_shares.items():
                self.failUnless(isinstance(sh, str))
                # feed the share through the unpacker as a sanity-check
                pieces = unpack_share(sh)
                (u_seqnum, u_root_hash, IV, k, N, segsize, datalen,
                 pubkey, signature, share_hash_chain, block_hash_tree,
                 share_data, enc_privkey) = pieces
                self.failUnlessEqual(u_seqnum, 3)
                self.failUnlessEqual(u_root_hash, root_hash)
                self.failUnlessEqual(k, 3)
                self.failUnlessEqual(N, 10)
                self.failUnlessEqual(segsize, 21)
                self.failUnlessEqual(datalen, len(CONTENTS))
                self.failUnlessEqual(pubkey, p._pubkey.serialize())
                sig_material = struct.pack(">BQ32s16s BBQQ",
                                           0, p._new_seqnum, root_hash, IV,
                                           k, N, segsize, datalen)
                self.failUnless(p._pubkey.verify(sig_material, signature))
                #self.failUnlessEqual(signature, p._privkey.sign(sig_material))
                self.failUnless(isinstance(share_hash_chain, dict))
                self.failUnlessEqual(len(share_hash_chain), 4) # ln2(10)++
                for shnum,share_hash in share_hash_chain.items():
                    self.failUnless(isinstance(shnum, int))
                    self.failUnless(isinstance(share_hash, str))
                    self.failUnlessEqual(len(share_hash), 32)
                self.failUnless(isinstance(block_hash_tree, list))
                self.failUnlessEqual(len(block_hash_tree), 1) # very small tree
                self.failUnlessEqual(IV, "SALT"*4)
                self.failUnlessEqual(len(share_data), len("%07d" % 1))
                self.failUnlessEqual(enc_privkey, fn.get_encprivkey())
        d.addCallback(_generated)
        return d

    # TODO: when we publish to 20 peers, we should get one share per peer on 10
    # when we publish to 3 peers, we should get either 3 or 4 shares per peer
    # when we publish to zero peers, we should get a NotEnoughSharesError

class Servermap(unittest.TestCase):
    def setUp(self):
        # publish a file and create shares, which can then be manipulated
        # later.
        num_peers = 20
        self._client = FakeClient(num_peers)
        self._storage = self._client._storage
        d = self._client.create_mutable_file("New contents go here")
        def _created(node):
            self._fn = node
        d.addCallback(_created)
        return d

    def make_servermap(self, mode=MODE_CHECK):
        smu = ServermapUpdater(self._fn, ServerMap(), mode)
        d = smu.update()
        return d

    def update_servermap(self, oldmap, mode=MODE_CHECK):
        smu = ServermapUpdater(self._fn, oldmap, mode)
        d = smu.update()
        return d

    def failUnlessOneRecoverable(self, sm, num_shares):
        self.failUnlessEqual(len(sm.recoverable_versions()), 1)
        self.failUnlessEqual(len(sm.unrecoverable_versions()), 0)
        best = sm.best_recoverable_version()
        self.failIfEqual(best, None)
        self.failUnlessEqual(sm.recoverable_versions(), set([best]))
        self.failUnlessEqual(len(sm.shares_available()), 1)
        self.failUnlessEqual(sm.shares_available()[best], (num_shares, 3))
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
        # this more stops at k+epsilon, and epsilon=k, so 6 shares
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
        d.addCallback(lambda sm: us(sm, mode=MODE_CHECK))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))
        d.addCallback(lambda sm: us(sm, mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 10))

        return d

    def test_mark_bad(self):
        d = defer.succeed(None)
        ms = self.make_servermap
        us = self.update_servermap

        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessOneRecoverable(sm, 6))
        def _made_map(sm):
            v = sm.best_recoverable_version()
            vm = sm.make_versionmap()
            shares = list(vm[v])
            self.failUnlessEqual(len(shares), 6)
            self._corrupted = set()
            # mark the first 5 shares as corrupt, then update the servermap.
            # The map should not have the marked shares it in any more, and
            # new shares should be found to replace the missing ones.
            for (shnum, peerid, timestamp) in shares:
                if shnum < 5:
                    self._corrupted.add( (peerid, shnum) )
                    sm.mark_bad_share(peerid, shnum)
            return self.update_servermap(sm, MODE_WRITE)
        d.addCallback(_made_map)
        def _check_map(sm):
            # this should find all 5 shares that weren't marked bad
            v = sm.best_recoverable_version()
            vm = sm.make_versionmap()
            shares = list(vm[v])
            for (peerid, shnum) in self._corrupted:
                peer_shares = sm.shares_on_peer(peerid)
                self.failIf(shnum in peer_shares,
                            "%d was in %s" % (shnum, peer_shares))
            self.failUnlessEqual(len(shares), 5)
        d.addCallback(_check_map)
        return d

    def failUnlessNoneRecoverable(self, sm):
        self.failUnlessEqual(len(sm.recoverable_versions()), 0)
        self.failUnlessEqual(len(sm.unrecoverable_versions()), 0)
        best = sm.best_recoverable_version()
        self.failUnlessEqual(best, None)
        self.failUnlessEqual(len(sm.shares_available()), 0)

    def test_no_shares(self):
        self._client._storage._peers = {} # delete all shares
        ms = self.make_servermap
        d = defer.succeed(None)

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
        self.failUnlessEqual(len(sm.recoverable_versions()), 0)
        self.failUnlessEqual(len(sm.unrecoverable_versions()), 1)
        best = sm.best_recoverable_version()
        self.failUnlessEqual(best, None)
        self.failUnlessEqual(len(sm.shares_available()), 1)
        self.failUnlessEqual(sm.shares_available().values()[0], (2,3) )

    def test_not_quite_enough_shares(self):
        s = self._client._storage
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
        d.addCallback(lambda res: ms(mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda res: ms(mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))

        return d



class Roundtrip(unittest.TestCase, testutil.ShouldFailMixin):
    def setUp(self):
        # publish a file and create shares, which can then be manipulated
        # later.
        self.CONTENTS = "New contents go here"
        num_peers = 20
        self._client = FakeClient(num_peers)
        self._storage = self._client._storage
        d = self._client.create_mutable_file(self.CONTENTS)
        def _created(node):
            self._fn = node
        d.addCallback(_created)
        return d

    def make_servermap(self, mode=MODE_READ, oldmap=None):
        if oldmap is None:
            oldmap = ServerMap()
        smu = ServermapUpdater(self._fn, oldmap, mode)
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
        r = Retrieve(self._fn, servermap, version)
        return r.download()

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


    def _test_corrupt_all(self, offset, substring,
                          should_succeed=False, corrupt_early=True):
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
                    allproblems = [str(f) for f in servermap.problems]
                    self.failUnless(substring in "".join(allproblems))
                return
            if should_succeed:
                d1 = self._fn.download_best_version()
                d1.addCallback(lambda new_contents:
                               self.failUnlessEqual(new_contents, self.CONTENTS))
                return d1
            else:
                return self.shouldFail(NotEnoughSharesError,
                                       "_corrupt_all(offset=%s)" % (offset,),
                                       substring,
                                       self._fn.download_best_version)
        d.addCallback(_do_retrieve)
        return d

    def test_corrupt_all_verbyte(self):
        # when the version byte is not 0, we hit an assertion error in
        # unpack_share().
        return self._test_corrupt_all(0, "AssertionError")

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
            self.failUnless(servermap.problems)
            self.failUnless("pubkey doesn't match fingerprint"
                            in str(servermap.problems[0]))
            ver = servermap.best_recoverable_version()
            r = Retrieve(self._fn, servermap, ver)
            return r.download()
        d.addCallback(_do_retrieve)
        d.addCallback(lambda new_contents:
                      self.failUnlessEqual(new_contents, self.CONTENTS))
        return d

    def test_corrupt_some(self):
        # corrupt the data of first five shares (so the servermap thinks
        # they're good but retrieve marks them as bad), so that the
        # MODE_READ set of 6 will be insufficient, forcing node.download to
        # retry with more servers.
        corrupt(None, self._storage, "share_data", range(5))
        d = self.make_servermap()
        def _do_retrieve(servermap):
            ver = servermap.best_recoverable_version()
            self.failUnless(ver)
            return self._fn.download_best_version()
        d.addCallback(_do_retrieve)
        d.addCallback(lambda new_contents:
                      self.failUnlessEqual(new_contents, self.CONTENTS))
        return d

    def test_download_fails(self):
        corrupt(None, self._storage, "signature")
        d = self.shouldFail(UnrecoverableFileError, "test_download_anyway",
                            "no recoverable versions",
                            self._fn.download_best_version)
        return d


class MultipleEncodings(unittest.TestCase):
    def setUp(self):
        self.CONTENTS = "New contents go here"
        num_peers = 20
        self._client = FakeClient(num_peers)
        self._storage = self._client._storage
        d = self._client.create_mutable_file(self.CONTENTS)
        def _created(node):
            self._fn = node
        d.addCallback(_created)
        return d

    def _encode(self, k, n, data):
        # encode 'data' into a peerid->shares dict.

        fn2 = FastMutableFileNode(self._client)
        # init_from_uri populates _uri, _writekey, _readkey, _storage_index,
        # and _fingerprint
        fn = self._fn
        fn2.init_from_uri(fn.get_uri())
        # then we copy over other fields that are normally fetched from the
        # existing shares
        fn2._pubkey = fn._pubkey
        fn2._privkey = fn._privkey
        fn2._encprivkey = fn._encprivkey
        fn2._current_seqnum = 0
        fn2._current_roothash = "\x00" * 32
        # and set the encoding parameters to something completely different
        fn2._required_shares = k
        fn2._total_shares = n

        s = self._client._storage
        s._peers = {} # clear existing storage
        p2 = Publish(fn2, None)
        d = p2.publish(data)
        def _published(res):
            shares = s._peers
            s._peers = {}
            return shares
        d.addCallback(_published)
        return d

    def make_servermap(self, mode=MODE_READ, oldmap=None):
        if oldmap is None:
            oldmap = ServerMap()
        smu = ServermapUpdater(self._fn, oldmap, mode)
        d = smu.update()
        return d

    def test_multiple_encodings(self):
        # we encode the same file in two different ways (3-of-10 and 4-of-9),
        # then mix up the shares, to make sure that download survives seeing
        # a variety of encodings. This is actually kind of tricky to set up.

        contents1 = "Contents for encoding 1 (3-of-10) go here"
        contents2 = "Contents for encoding 2 (4-of-9) go here"
        contents3 = "Contents for encoding 3 (4-of-7) go here"

        # we make a retrieval object that doesn't know what encoding
        # parameters to use
        fn3 = FastMutableFileNode(self._client)
        fn3.init_from_uri(self._fn.get_uri())

        # now we upload a file through fn1, and grab its shares
        d = self._encode(3, 10, contents1)
        def _encoded_1(shares):
            self._shares1 = shares
        d.addCallback(_encoded_1)
        d.addCallback(lambda res: self._encode(4, 9, contents2))
        def _encoded_2(shares):
            self._shares2 = shares
        d.addCallback(_encoded_2)
        d.addCallback(lambda res: self._encode(4, 7, contents3))
        def _encoded_3(shares):
            self._shares3 = shares
        d.addCallback(_encoded_3)

        def _merge(res):
            log.msg("merging sharelists")
            # we merge the shares from the two sets, leaving each shnum in
            # its original location, but using a share from set1 or set2
            # according to the following sequence:
            #
            #  4-of-9  a  s2
            #  4-of-9  b  s2
            #  4-of-7  c   s3
            #  4-of-9  d  s2
            #  3-of-9  e s1
            #  3-of-9  f s1
            #  3-of-9  g s1
            #  4-of-9  h  s2
            #
            # so that neither form can be recovered until fetch [f], at which
            # point version-s1 (the 3-of-10 form) should be recoverable. If
            # the implementation latches on to the first version it sees,
            # then s2 will be recoverable at fetch [g].

            # Later, when we implement code that handles multiple versions,
            # we can use this framework to assert that all recoverable
            # versions are retrieved, and test that 'epsilon' does its job

            places = [2, 2, 3, 2, 1, 1, 1, 2]

            sharemap = {}

            for i,peerid in enumerate(self._client._peerids):
                peerid_s = shortnodeid_b2a(peerid)
                for shnum in self._shares1.get(peerid, {}):
                    if shnum < len(places):
                        which = places[shnum]
                    else:
                        which = "x"
                    self._client._storage._peers[peerid] = peers = {}
                    in_1 = shnum in self._shares1[peerid]
                    in_2 = shnum in self._shares2.get(peerid, {})
                    in_3 = shnum in self._shares3.get(peerid, {})
                    #print peerid_s, shnum, which, in_1, in_2, in_3
                    if which == 1:
                        if in_1:
                            peers[shnum] = self._shares1[peerid][shnum]
                            sharemap[shnum] = peerid
                    elif which == 2:
                        if in_2:
                            peers[shnum] = self._shares2[peerid][shnum]
                            sharemap[shnum] = peerid
                    elif which == 3:
                        if in_3:
                            peers[shnum] = self._shares3[peerid][shnum]
                            sharemap[shnum] = peerid

            # we don't bother placing any other shares
            # now sort the sequence so that share 0 is returned first
            new_sequence = [sharemap[shnum]
                            for shnum in sorted(sharemap.keys())]
            self._client._storage._sequence = new_sequence
            log.msg("merge done")
        d.addCallback(_merge)
        d.addCallback(lambda res: fn3.download_best_version())
        def _retrieved(new_contents):
            # the current specified behavior is "first version recoverable"
            self.failUnlessEqual(new_contents, contents1)
        d.addCallback(_retrieved)
        return d


class Utils(unittest.TestCase):
    def test_dict_of_sets(self):
        ds = DictOfSets()
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

    def _do_inside(self, c, x_start, x_length, y_start, y_length):
        # we compare this against sets of integers
        x = set(range(x_start, x_start+x_length))
        y = set(range(y_start, y_start+y_length))
        should_be_inside = x.issubset(y)
        self.failUnlessEqual(should_be_inside, c._inside(x_start, x_length,
                                                         y_start, y_length),
                             str((x_start, x_length, y_start, y_length)))

    def test_cache_inside(self):
        c = ResponseCache()
        x_start = 10
        x_length = 5
        for y_start in range(8, 17):
            for y_length in range(8):
                self._do_inside(c, x_start, x_length, y_start, y_length)

    def _do_overlap(self, c, x_start, x_length, y_start, y_length):
        # we compare this against sets of integers
        x = set(range(x_start, x_start+x_length))
        y = set(range(y_start, y_start+y_length))
        overlap = bool(x.intersection(y))
        self.failUnlessEqual(overlap, c._does_overlap(x_start, x_length,
                                                      y_start, y_length),
                             str((x_start, x_length, y_start, y_length)))

    def test_cache_overlap(self):
        c = ResponseCache()
        x_start = 10
        x_length = 5
        for y_start in range(8, 17):
            for y_length in range(8):
                self._do_overlap(c, x_start, x_length, y_start, y_length)

    def test_cache(self):
        c = ResponseCache()
        # xdata = base62.b2a(os.urandom(100))[:100]
        xdata = "1Ex4mdMaDyOl9YnGBM3I4xaBF97j8OQAg1K3RBR01F2PwTP4HohB3XpACuku8Xj4aTQjqJIR1f36mEj3BCNjXaJmPBEZnnHL0U9l"
        ydata = "4DCUQXvkEPnnr9Lufikq5t21JsnzZKhzxKBhLhrBB6iIcBOWRuT4UweDhjuKJUre8A4wOObJnl3Kiqmlj4vjSLSqUGAkUD87Y3vs"
        nope = (None, None)
        c.add("v1", 1, 0, xdata, "time0")
        c.add("v1", 1, 2000, ydata, "time1")
        self.failUnlessEqual(c.read("v2", 1, 10, 11), nope)
        self.failUnlessEqual(c.read("v1", 2, 10, 11), nope)
        self.failUnlessEqual(c.read("v1", 1, 0, 10), (xdata[:10], "time0"))
        self.failUnlessEqual(c.read("v1", 1, 90, 10), (xdata[90:], "time0"))
        self.failUnlessEqual(c.read("v1", 1, 300, 10), nope)
        self.failUnlessEqual(c.read("v1", 1, 2050, 5), (ydata[50:55], "time1"))
        self.failUnlessEqual(c.read("v1", 1, 0, 101), nope)
        self.failUnlessEqual(c.read("v1", 1, 99, 1), (xdata[99:100], "time0"))
        self.failUnlessEqual(c.read("v1", 1, 100, 1), nope)
        self.failUnlessEqual(c.read("v1", 1, 1990, 9), nope)
        self.failUnlessEqual(c.read("v1", 1, 1990, 10), nope)
        self.failUnlessEqual(c.read("v1", 1, 1990, 11), nope)
        self.failUnlessEqual(c.read("v1", 1, 1990, 15), nope)
        self.failUnlessEqual(c.read("v1", 1, 1990, 19), nope)
        self.failUnlessEqual(c.read("v1", 1, 1990, 20), nope)
        self.failUnlessEqual(c.read("v1", 1, 1990, 21), nope)
        self.failUnlessEqual(c.read("v1", 1, 1990, 25), nope)
        self.failUnlessEqual(c.read("v1", 1, 1999, 25), nope)

        # optional: join fragments
        c = ResponseCache()
        c.add("v1", 1, 0, xdata[:10], "time0")
        c.add("v1", 1, 10, xdata[10:20], "time1")
        #self.failUnlessEqual(c.read("v1", 1, 0, 20), (xdata[:20], "time0"))

