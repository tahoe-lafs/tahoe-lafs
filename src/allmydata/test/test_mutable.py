
import os, struct
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import failure
from allmydata import uri
from allmydata.storage.server import StorageServer
from allmydata.immutable import download
from allmydata.util import base32, idlib
from allmydata.util.idlib import shortnodeid_b2a
from allmydata.util.hashutil import tagged_hash
from allmydata.util.fileutil import make_dirs
from allmydata.interfaces import IURI, IMutableFileURI, IUploadable, \
     FileTooLargeError, NotEnoughSharesError, IRepairResults
from allmydata.monitor import Monitor
from allmydata.test.common import ShouldFailMixin
from foolscap.eventual import eventually, fireEventually
from foolscap.logging import log
import sha

from allmydata.mutable.filenode import MutableFileNode, BackoffAgent
from allmydata.mutable.common import ResponseCache, \
     MODE_CHECK, MODE_ANYTHING, MODE_WRITE, MODE_READ, \
     NeedMoreDataError, UnrecoverableFileError, UncoordinatedWriteError, \
     NotEnoughServersError, CorruptShareError
from allmydata.mutable.retrieve import Retrieve
from allmydata.mutable.publish import Publish
from allmydata.mutable.servermap import ServerMap, ServermapUpdater
from allmydata.mutable.layout import unpack_header, unpack_share
from allmydata.mutable.repairer import MustForceRepairError

import common_util as testutil

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
        self._pending_timer = None
        self._special_answers = {}

    def read(self, peerid, storage_index):
        shares = self._peers.get(peerid, {})
        if self._special_answers.get(peerid, []):
            mode = self._special_answers[peerid].pop(0)
            if mode == "fail":
                shares = failure.Failure(IntentionalError())
            elif mode == "none":
                shares = {}
            elif mode == "normal":
                pass
        if self._sequence is None:
            return defer.succeed(shares)
        d = defer.Deferred()
        if not self._pending:
            self._pending_timer = reactor.callLater(1.0, self._fire_readers)
        self._pending[peerid] = (d, shares)
        return d

    def _fire_readers(self):
        self._pending_timer = None
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
    def callRemoteOnly(self, methname, *args, **kwargs):
        d = self.callRemote(methname, *args, **kwargs)
        d.addBoth(lambda ignore: None)
        pass

    def advise_corrupt_share(self, share_type, storage_index, shnum, reason):
        pass

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

    def get_encoding_parameters(self):
        return {"k": 3, "n": 10}

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

    def get_history(self):
        return None

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

def corrupt(res, s, offset, shnums_to_corrupt=None, offset_offset=0):
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
            real_offset = int(real_offset) + offset2 + offset_offset
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
            self.failUnlessEqual(n.get_storage_index(), n._storage_index)
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
            d.addCallback(lambda res: n.get_size_of_best_version())
            d.addCallback(lambda size:
                          self.failUnlessEqual(size, len("contents 1")))
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
            # test a file that is large enough to overcome the
            # mapupdate-to-retrieve data caching (i.e. make the shares larger
            # than the default readsize, which is 2000 bytes). A 15kB file
            # will have 5kB shares.
            d.addCallback(lambda res: n.overwrite("large size file" * 1000))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res:
                          self.failUnlessEqual(res, "large size file" * 1000))
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

    def test_create_with_too_large_contents(self):
        BIG = "a" * (Publish.MAX_SEGMENT_SIZE+1)
        d = self.shouldFail(FileTooLargeError, "too_large",
                            "SDMF is limited to one segment, and %d > %d" %
                            (len(BIG), Publish.MAX_SEGMENT_SIZE),
                            self.client.create_mutable_file, BIG)
        d.addCallback(lambda res: self.client.create_mutable_file("small"))
        def _created(n):
            return self.shouldFail(FileTooLargeError, "too_large_2",
                                   "SDMF is limited to one segment, and %d > %d" %
                                   (len(BIG), Publish.MAX_SEGMENT_SIZE),
                                   n.overwrite, BIG)
        d.addCallback(_created)
        return d

    def failUnlessCurrentSeqnumIs(self, n, expected_seqnum, which):
        d = n.get_servermap(MODE_READ)
        d.addCallback(lambda servermap: servermap.best_recoverable_version())
        d.addCallback(lambda verinfo:
                      self.failUnlessEqual(verinfo[0], expected_seqnum, which))
        return d

    def test_modify(self):
        def _modifier(old_contents, servermap, first_time):
            return old_contents + "line2"
        def _non_modifier(old_contents, servermap, first_time):
            return old_contents
        def _none_modifier(old_contents, servermap, first_time):
            return None
        def _error_modifier(old_contents, servermap, first_time):
            raise ValueError("oops")
        def _toobig_modifier(old_contents, servermap, first_time):
            return "b" * (Publish.MAX_SEGMENT_SIZE+1)
        calls = []
        def _ucw_error_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents + "line3"
        def _ucw_error_non_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once, and don't actually
            # modify the contents on subsequent invocations
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents

        d = self.client.create_mutable_file("line1")
        def _created(n):
            d = n.modify(_modifier)
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "m"))

            d.addCallback(lambda res: n.modify(_non_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "non"))

            d.addCallback(lambda res: n.modify(_none_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "none"))

            d.addCallback(lambda res:
                          self.shouldFail(ValueError, "error_modifier", None,
                                          n.modify, _error_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "err"))

            d.addCallback(lambda res:
                          self.shouldFail(FileTooLargeError, "toobig_modifier",
                                          "SDMF is limited to one segment",
                                          n.modify, _toobig_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "big"))

            d.addCallback(lambda res: n.modify(_ucw_error_modifier))
            d.addCallback(lambda res: self.failUnlessEqual(len(calls), 2))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "ucw"))

            def _reset_ucw_error_modifier(res):
                calls[:] = []
                return res
            d.addCallback(_reset_ucw_error_modifier)

            # in practice, this n.modify call should publish twice: the first
            # one gets a UCWE, the second does not. But our test jig (in
            # which the modifier raises the UCWE) skips over the first one,
            # so in this test there will be only one publish, and the seqnum
            # will only be one larger than the previous test, not two (i.e. 4
            # instead of 5).
            d.addCallback(lambda res: n.modify(_ucw_error_non_modifier))
            d.addCallback(lambda res: self.failUnlessEqual(len(calls), 2))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 4, "ucw"))

            return d
        d.addCallback(_created)
        return d

    def test_modify_backoffer(self):
        def _modifier(old_contents, servermap, first_time):
            return old_contents + "line2"
        calls = []
        def _ucw_error_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents + "line3"
        def _always_ucw_error_modifier(old_contents, servermap, first_time):
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
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "m"))

            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "_backoff_stopper", None,
                                          n.modify, _ucw_error_modifier,
                                          _backoff_stopper))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "line1line2"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "stop"))

            def _reset_ucw_error_modifier(res):
                calls[:] = []
                return res
            d.addCallback(_reset_ucw_error_modifier)
            d.addCallback(lambda res: n.modify(_ucw_error_modifier,
                                               _backoff_pauser))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "pause"))

            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "giveuper", None,
                                          n.modify, _always_ucw_error_modifier,
                                          giveuper.delay))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res,
                                                           "line1line2line3"))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "giveup"))

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

class PublishMixin:
    def publish_one(self):
        # publish a file and create shares, which can then be manipulated
        # later.
        self.CONTENTS = "New contents go here" * 1000
        num_peers = 20
        self._client = FakeClient(num_peers)
        self._storage = self._client._storage
        d = self._client.create_mutable_file(self.CONTENTS)
        def _created(node):
            self._fn = node
            self._fn2 = self._client.create_node_from_uri(node.get_uri())
        d.addCallback(_created)
        return d
    def publish_multiple(self):
        self.CONTENTS = ["Contents 0",
                         "Contents 1",
                         "Contents 2",
                         "Contents 3a",
                         "Contents 3b"]
        self._copied_shares = {}
        num_peers = 20
        self._client = FakeClient(num_peers)
        self._storage = self._client._storage
        d = self._client.create_mutable_file(self.CONTENTS[0]) # seqnum=1
        def _created(node):
            self._fn = node
            # now create multiple versions of the same file, and accumulate
            # their shares, so we can mix and match them later.
            d = defer.succeed(None)
            d.addCallback(self._copy_shares, 0)
            d.addCallback(lambda res: node.overwrite(self.CONTENTS[1])) #s2
            d.addCallback(self._copy_shares, 1)
            d.addCallback(lambda res: node.overwrite(self.CONTENTS[2])) #s3
            d.addCallback(self._copy_shares, 2)
            d.addCallback(lambda res: node.overwrite(self.CONTENTS[3])) #s4a
            d.addCallback(self._copy_shares, 3)
            # now we replace all the shares with version s3, and upload a new
            # version to get s4b.
            rollback = dict([(i,2) for i in range(10)])
            d.addCallback(lambda res: self._set_versions(rollback))
            d.addCallback(lambda res: node.overwrite(self.CONTENTS[4])) #s4b
            d.addCallback(self._copy_shares, 4)
            # we leave the storage in state 4
            return d
        d.addCallback(_created)
        return d

    def _copy_shares(self, ignored, index):
        shares = self._client._storage._peers
        # we need a deep copy
        new_shares = {}
        for peerid in shares:
            new_shares[peerid] = {}
            for shnum in shares[peerid]:
                new_shares[peerid][shnum] = shares[peerid][shnum]
        self._copied_shares[index] = new_shares

    def _set_versions(self, versionmap):
        # versionmap maps shnums to which version (0,1,2,3,4) we want the
        # share to be at. Any shnum which is left out of the map will stay at
        # its current version.
        shares = self._client._storage._peers
        oldshares = self._copied_shares
        for peerid in shares:
            for shnum in shares[peerid]:
                if shnum in versionmap:
                    index = versionmap[shnum]
                    shares[peerid][shnum] = oldshares[index][peerid][shnum]


class Servermap(unittest.TestCase, PublishMixin):
    def setUp(self):
        return self.publish_one()

    def make_servermap(self, mode=MODE_CHECK, fn=None):
        if fn is None:
            fn = self._fn
        smu = ServermapUpdater(fn, Monitor(), ServerMap(), mode)
        d = smu.update()
        return d

    def update_servermap(self, oldmap, mode=MODE_CHECK):
        smu = ServermapUpdater(self._fn, Monitor(), oldmap, mode)
        d = smu.update()
        return d

    def failUnlessOneRecoverable(self, sm, num_shares):
        self.failUnlessEqual(len(sm.recoverable_versions()), 1)
        self.failUnlessEqual(len(sm.unrecoverable_versions()), 0)
        best = sm.best_recoverable_version()
        self.failIfEqual(best, None)
        self.failUnlessEqual(sm.recoverable_versions(), set([best]))
        self.failUnlessEqual(len(sm.shares_available()), 1)
        self.failUnlessEqual(sm.shares_available()[best], (num_shares, 3, 10))
        shnum, peerids = sm.make_sharemap().items()[0]
        peerid = list(peerids)[0]
        self.failUnlessEqual(sm.version_on_peer(peerid, shnum), best)
        self.failUnlessEqual(sm.version_on_peer(peerid, 666), None)
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
        LARGE = "These are Larger contents" * 200 # about 5KB
        d.addCallback(lambda res: self._client.create_mutable_file(LARGE))
        def _created(large_fn):
            large_fn2 = self._client.create_node_from_uri(large_fn.get_uri())
            return self.make_servermap(MODE_WRITE, large_fn2)
        d.addCallback(_created)
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
                    sm.mark_bad_share(peerid, shnum, "")
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
        self.failUnlessEqual(sm.shares_available().values()[0], (2,3,10) )
        return sm

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
        d.addCallback(lambda sm:
                      self.failUnlessEqual(len(sm.make_sharemap()), 2))
        d.addCallback(lambda res: ms(mode=MODE_ANYTHING))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda res: ms(mode=MODE_WRITE))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))
        d.addCallback(lambda res: ms(mode=MODE_READ))
        d.addCallback(lambda sm: self.failUnlessNotQuiteEnough(sm))

        return d



class Roundtrip(unittest.TestCase, testutil.ShouldFailMixin, PublishMixin):
    def setUp(self):
        return self.publish_one()

    def make_servermap(self, mode=MODE_READ, oldmap=None):
        if oldmap is None:
            oldmap = ServerMap()
        smu = ServermapUpdater(self._fn, Monitor(), oldmap, mode)
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

    def test_all_shares_vanished(self):
        d = self.make_servermap()
        def _remove_shares(servermap):
            for shares in self._storage._peers.values():
                shares.clear()
            d1 = self.shouldFail(NotEnoughSharesError,
                                 "test_all_shares_vanished",
                                 "ran out of peers",
                                 self.do_download, servermap)
            return d1
        d.addCallback(_remove_shares)
        return d

    def test_no_servers(self):
        c2 = FakeClient(0)
        self._fn._client = c2
        # if there are no servers, then a MODE_READ servermap should come
        # back empty
        d = self.make_servermap()
        def _check_servermap(servermap):
            self.failUnlessEqual(servermap.best_recoverable_version(), None)
            self.failIf(servermap.recoverable_versions())
            self.failIf(servermap.unrecoverable_versions())
            self.failIf(servermap.all_peers())
        d.addCallback(_check_servermap)
        return d
    test_no_servers.timeout = 15

    def test_no_servers_download(self):
        c2 = FakeClient(0)
        self._fn._client = c2
        d = self.shouldFail(UnrecoverableFileError,
                            "test_no_servers_download",
                            "no recoverable versions",
                            self._fn.download_best_version)
        def _restore(res):
            # a failed download that occurs while we aren't connected to
            # anybody should not prevent a subsequent download from working.
            # This isn't quite the webapi-driven test that #463 wants, but it
            # should be close enough.
            self._fn._client = self._client
            return self._fn.download_best_version()
        def _retrieved(new_contents):
            self.failUnlessEqual(new_contents, self.CONTENTS)
        d.addCallback(_restore)
        d.addCallback(_retrieved)
        return d
    test_no_servers_download.timeout = 15

    def _test_corrupt_all(self, offset, substring,
                          should_succeed=False, corrupt_early=True,
                          failure_checker=None):
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
                return servermap
            if should_succeed:
                d1 = self._fn.download_version(servermap, ver)
                d1.addCallback(lambda new_contents:
                               self.failUnlessEqual(new_contents, self.CONTENTS))
            else:
                d1 = self.shouldFail(NotEnoughSharesError,
                                     "_corrupt_all(offset=%s)" % (offset,),
                                     substring,
                                     self._fn.download_version, servermap, ver)
            if failure_checker:
                d1.addCallback(failure_checker)
            d1.addCallback(lambda res: servermap)
            return d1
        d.addCallback(_do_retrieve)
        return d

    def test_corrupt_all_verbyte(self):
        # when the version byte is not 0, we hit an assertion error in
        # unpack_share().
        d = self._test_corrupt_all(0, "AssertionError")
        def _check_servermap(servermap):
            # and the dump should mention the problems
            s = StringIO()
            dump = servermap.dump(s).getvalue()
            self.failUnless("10 PROBLEMS" in dump, dump)
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


    def test_corrupt_all_seqnum_late(self):
        # corrupting the seqnum between mapupdate and retrieve should result
        # in NotEnoughSharesError, since each share will look invalid
        def _check(res):
            f = res[0]
            self.failUnless(f.check(NotEnoughSharesError))
            self.failUnless("someone wrote to the data since we read the servermap" in str(f))
        return self._test_corrupt_all(1, "ran out of peers",
                                      corrupt_early=False,
                                      failure_checker=_check)

    def test_corrupt_all_block_hash_tree_late(self):
        def _check(res):
            f = res[0]
            self.failUnless(f.check(NotEnoughSharesError))
        return self._test_corrupt_all("block_hash_tree",
                                      "block hash tree failure",
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


class CheckerMixin:
    def check_good(self, r, where):
        self.failUnless(r.is_healthy(), where)
        return r

    def check_bad(self, r, where):
        self.failIf(r.is_healthy(), where)
        return r

    def check_expected_failure(self, r, expected_exception, substring, where):
        for (peerid, storage_index, shnum, f) in r.problems:
            if f.check(expected_exception):
                self.failUnless(substring in str(f),
                                "%s: substring '%s' not in '%s'" %
                                (where, substring, str(f)))
                return
        self.fail("%s: didn't see expected exception %s in problems %s" %
                  (where, expected_exception, r.problems))


class Checker(unittest.TestCase, CheckerMixin, PublishMixin):
    def setUp(self):
        return self.publish_one()


    def test_check_good(self):
        d = self._fn.check(Monitor())
        d.addCallback(self.check_good, "test_check_good")
        return d

    def test_check_no_shares(self):
        for shares in self._storage._peers.values():
            shares.clear()
        d = self._fn.check(Monitor())
        d.addCallback(self.check_bad, "test_check_no_shares")
        return d

    def test_check_not_enough_shares(self):
        for shares in self._storage._peers.values():
            for shnum in shares.keys():
                if shnum > 0:
                    del shares[shnum]
        d = self._fn.check(Monitor())
        d.addCallback(self.check_bad, "test_check_not_enough_shares")
        return d

    def test_check_all_bad_sig(self):
        corrupt(None, self._storage, 1) # bad sig
        d = self._fn.check(Monitor())
        d.addCallback(self.check_bad, "test_check_all_bad_sig")
        return d

    def test_check_all_bad_blocks(self):
        corrupt(None, self._storage, "share_data", [9]) # bad blocks
        # the Checker won't notice this.. it doesn't look at actual data
        d = self._fn.check(Monitor())
        d.addCallback(self.check_good, "test_check_all_bad_blocks")
        return d

    def test_verify_good(self):
        d = self._fn.check(Monitor(), verify=True)
        d.addCallback(self.check_good, "test_verify_good")
        return d

    def test_verify_all_bad_sig(self):
        corrupt(None, self._storage, 1) # bad sig
        d = self._fn.check(Monitor(), verify=True)
        d.addCallback(self.check_bad, "test_verify_all_bad_sig")
        return d

    def test_verify_one_bad_sig(self):
        corrupt(None, self._storage, 1, [9]) # bad sig
        d = self._fn.check(Monitor(), verify=True)
        d.addCallback(self.check_bad, "test_verify_one_bad_sig")
        return d

    def test_verify_one_bad_block(self):
        corrupt(None, self._storage, "share_data", [9]) # bad blocks
        # the Verifier *will* notice this, since it examines every byte
        d = self._fn.check(Monitor(), verify=True)
        d.addCallback(self.check_bad, "test_verify_one_bad_block")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "block hash tree failure",
                      "test_verify_one_bad_block")
        return d

    def test_verify_one_bad_sharehash(self):
        corrupt(None, self._storage, "share_hash_chain", [9], 5)
        d = self._fn.check(Monitor(), verify=True)
        d.addCallback(self.check_bad, "test_verify_one_bad_sharehash")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "corrupt hashes",
                      "test_verify_one_bad_sharehash")
        return d

    def test_verify_one_bad_encprivkey(self):
        corrupt(None, self._storage, "enc_privkey", [9]) # bad privkey
        d = self._fn.check(Monitor(), verify=True)
        d.addCallback(self.check_bad, "test_verify_one_bad_encprivkey")
        d.addCallback(self.check_expected_failure,
                      CorruptShareError, "invalid privkey",
                      "test_verify_one_bad_encprivkey")
        return d

    def test_verify_one_bad_encprivkey_uncheckable(self):
        corrupt(None, self._storage, "enc_privkey", [9]) # bad privkey
        readonly_fn = self._fn.get_readonly()
        # a read-only node has no way to validate the privkey
        d = readonly_fn.check(Monitor(), verify=True)
        d.addCallback(self.check_good,
                      "test_verify_one_bad_encprivkey_uncheckable")
        return d

class Repair(unittest.TestCase, PublishMixin, ShouldFailMixin):

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
            self.failUnless(IRepairResults.providedBy(rres))
            # TODO: examine results

            self.copy_shares()

            initial_shares = self.old_shares[0]
            new_shares = self.old_shares[1]
            # TODO: this really shouldn't change anything. When we implement
            # a "minimal-bandwidth" repairer", change this test to assert:
            #self.failUnlessEqual(new_shares, initial_shares)

            # all shares should be in the same place as before
            self.failUnlessEqual(set(initial_shares.keys()),
                                 set(new_shares.keys()))
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
                self.failUnlessEqual(version0, version1)
                self.failUnlessEqual(seqnum0+1, seqnum1)
                self.failUnlessEqual(k0, k1)
                self.failUnlessEqual(N0, N1)
                self.failUnlessEqual(segsize0, segsize1)
                self.failUnlessEqual(datalen0, datalen1)
        d.addCallback(_check_results)
        return d

    def failIfSharesChanged(self, ignored=None):
        old_shares = self.old_shares[-2]
        current_shares = self.old_shares[-1]
        self.failUnlessEqual(old_shares, current_shares)

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
            pass # TODO
        d.addCallback(_check_repair_results)
        d.addCallback(lambda res: self._fn.get_servermap(MODE_CHECK))
        def _check_smap(smap):
            self.failUnlessEqual(len(smap.recoverable_versions()), 1)
            self.failIf(smap.unrecoverable_versions())
            # now, which should have won?
            roothash_s4a = self.get_roothash_for(3)
            roothash_s4b = self.get_roothash_for(4)
            if roothash_s4b > roothash_s4a:
                expected_contents = self.CONTENTS[4]
            else:
                expected_contents = self.CONTENTS[3]
            new_versionid = smap.best_recoverable_version()
            self.failUnlessEqual(new_versionid[0], 5) # seqnum 5
            d2 = self._fn.download_version(smap, new_versionid)
            d2.addCallback(self.failUnlessEqual, expected_contents)
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
            pass # TODO
        d.addCallback(_check_repair_results)
        d.addCallback(lambda res: self._fn.get_servermap(MODE_CHECK))
        def _check_smap(smap):
            self.failUnlessEqual(len(smap.recoverable_versions()), 1)
            self.failIf(smap.unrecoverable_versions())
            # now, which should have won?
            roothash_s4a = self.get_roothash_for(3)
            expected_contents = self.CONTENTS[3]
            new_versionid = smap.best_recoverable_version()
            self.failUnlessEqual(new_versionid[0], 5) # seqnum 5
            d2 = self._fn.download_version(smap, new_versionid)
            d2.addCallback(self.failUnlessEqual, expected_contents)
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
        smu = ServermapUpdater(self._fn, Monitor(), oldmap, mode)
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


class Utils(unittest.TestCase):
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

class Exceptions(unittest.TestCase):
    def test_repr(self):
        nmde = NeedMoreDataError(100, 50, 100)
        self.failUnless("NeedMoreDataError" in repr(nmde), repr(nmde))
        ucwe = UncoordinatedWriteError()
        self.failUnless("UncoordinatedWriteError" in repr(ucwe), repr(ucwe))

# we can't do this test with a FakeClient, since it uses FakeStorageServer
# instances which always succeed. So we need a less-fake one.

class IntentionalError(Exception):
    pass

class LocalWrapper:
    def __init__(self, original):
        self.original = original
        self.broken = False
        self.post_call_notifier = None
    def callRemote(self, methname, *args, **kwargs):
        def _call():
            if self.broken:
                raise IntentionalError("I was asked to break")
            meth = getattr(self.original, "remote_" + methname)
            return meth(*args, **kwargs)
        d = fireEventually()
        d.addCallback(lambda res: _call())
        if self.post_call_notifier:
            d.addCallback(self.post_call_notifier, methname)
        return d

class LessFakeClient(FakeClient):

    def __init__(self, basedir, num_peers=10):
        self._num_peers = num_peers
        self._peerids = [tagged_hash("peerid", "%d" % i)[:20]
                         for i in range(self._num_peers)]
        self._connections = {}
        for peerid in self._peerids:
            peerdir = os.path.join(basedir, idlib.shortnodeid_b2a(peerid))
            make_dirs(peerdir)
            ss = StorageServer(peerdir, peerid)
            lw = LocalWrapper(ss)
            self._connections[peerid] = lw
        self.nodeid = "fakenodeid"


class Problems(unittest.TestCase, testutil.ShouldFailMixin):
    def test_publish_surprise(self):
        basedir = os.path.join("mutable/CollidingWrites/test_surprise")
        self.client = LessFakeClient(basedir)
        d = self.client.create_mutable_file("contents 1")
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
            d.addCallback(_got_smap1)
            # then modify the file, leaving the old map untouched
            d.addCallback(lambda res: log.msg("starting winning write"))
            d.addCallback(lambda res: n.overwrite("contents 2"))
            # now attempt to modify the file with the old servermap. This
            # will look just like an uncoordinated write, in which every
            # single share got updated between our mapupdate and our publish
            d.addCallback(lambda res: log.msg("starting doomed write"))
            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "test_publish_surprise", None,
                                          n.upload,
                                          "contents 2a", self.old_map))
            return d
        d.addCallback(_created)
        return d

    def test_retrieve_surprise(self):
        basedir = os.path.join("mutable/CollidingWrites/test_retrieve")
        self.client = LessFakeClient(basedir)
        d = self.client.create_mutable_file("contents 1")
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
            d.addCallback(_got_smap1)
            # then modify the file, leaving the old map untouched
            d.addCallback(lambda res: log.msg("starting winning write"))
            d.addCallback(lambda res: n.overwrite("contents 2"))
            # now attempt to retrieve the old version with the old servermap.
            # This will look like someone has changed the file since we
            # updated the servermap.
            d.addCallback(lambda res: n._cache._clear())
            d.addCallback(lambda res: log.msg("starting doomed read"))
            d.addCallback(lambda res:
                          self.shouldFail(NotEnoughSharesError,
                                          "test_retrieve_surprise",
                                          "ran out of peers: have 0 shares (k=3)",
                                          n.download_version,
                                          self.old_map,
                                          self.old_map.best_recoverable_version(),
                                          ))
            return d
        d.addCallback(_created)
        return d

    def test_unexpected_shares(self):
        # upload the file, take a servermap, shut down one of the servers,
        # upload it again (causing shares to appear on a new server), then
        # upload using the old servermap. The last upload should fail with an
        # UncoordinatedWriteError, because of the shares that didn't appear
        # in the servermap.
        basedir = os.path.join("mutable/CollidingWrites/test_unexpexted_shares")
        self.client = LessFakeClient(basedir)
        d = self.client.create_mutable_file("contents 1")
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
                # now shut down one of the servers
                peer0 = list(smap.make_sharemap()[0])[0]
                self.client._connections.pop(peer0)
                # then modify the file, leaving the old map untouched
                log.msg("starting winning write")
                return n.overwrite("contents 2")
            d.addCallback(_got_smap1)
            # now attempt to modify the file with the old servermap. This
            # will look just like an uncoordinated write, in which every
            # single share got updated between our mapupdate and our publish
            d.addCallback(lambda res: log.msg("starting doomed write"))
            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "test_surprise", None,
                                          n.upload,
                                          "contents 2a", self.old_map))
            return d
        d.addCallback(_created)
        return d

    def test_bad_server(self):
        # Break one server, then create the file: the initial publish should
        # complete with an alternate server. Breaking a second server should
        # not prevent an update from succeeding either.
        basedir = os.path.join("mutable/CollidingWrites/test_bad_server")
        self.client = LessFakeClient(basedir, 20)
        # to make sure that one of the initial peers is broken, we have to
        # get creative. We create the keys, so we can figure out the storage
        # index, but we hold off on doing the initial publish until we've
        # broken the server on which the first share wants to be stored.
        n = FastMutableFileNode(self.client)
        d = defer.succeed(None)
        d.addCallback(n._generate_pubprivkeys)
        d.addCallback(n._generated)
        def _break_peer0(res):
            si = n.get_storage_index()
            peerlist = self.client.get_permuted_peers("storage", si)
            peerid0, connection0 = peerlist[0]
            peerid1, connection1 = peerlist[1]
            connection0.broken = True
            self.connection1 = connection1
        d.addCallback(_break_peer0)
        # now let the initial publish finally happen
        d.addCallback(lambda res: n._upload("contents 1", None))
        # that ought to work
        d.addCallback(lambda res: n.download_best_version())
        d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
        # now break the second peer
        def _break_peer1(res):
            self.connection1.broken = True
        d.addCallback(_break_peer1)
        d.addCallback(lambda res: n.overwrite("contents 2"))
        # that ought to work too
        d.addCallback(lambda res: n.download_best_version())
        d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
        return d

    def test_bad_server_overlap(self):
        # like test_bad_server, but with no extra unused servers to fall back
        # upon. This means that we must re-use a server which we've already
        # used. If we don't remember the fact that we sent them one share
        # already, we'll mistakenly think we're experiencing an
        # UncoordinatedWriteError.

        # Break one server, then create the file: the initial publish should
        # complete with an alternate server. Breaking a second server should
        # not prevent an update from succeeding either.
        basedir = os.path.join("mutable/CollidingWrites/test_bad_server")
        self.client = LessFakeClient(basedir, 10)

        peerids = sorted(self.client._connections.keys())
        self.client._connections[peerids[0]].broken = True

        d = self.client.create_mutable_file("contents 1")
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            # now break one of the remaining servers
            def _break_second_server(res):
                self.client._connections[peerids[1]].broken = True
            d.addCallback(_break_second_server)
            d.addCallback(lambda res: n.overwrite("contents 2"))
            # that ought to work too
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d

    def test_publish_all_servers_bad(self):
        # Break all servers: the publish should fail
        basedir = os.path.join("mutable/CollidingWrites/publish_all_servers_bad")
        self.client = LessFakeClient(basedir, 20)
        for connection in self.client._connections.values():
            connection.broken = True
        d = self.shouldFail(NotEnoughServersError,
                            "test_publish_all_servers_bad",
                            "Ran out of non-bad servers",
                            self.client.create_mutable_file, "contents")
        return d

    def test_publish_no_servers(self):
        # no servers at all: the publish should fail
        basedir = os.path.join("mutable/CollidingWrites/publish_no_servers")
        self.client = LessFakeClient(basedir, 0)
        d = self.shouldFail(NotEnoughServersError,
                            "test_publish_no_servers",
                            "Ran out of non-bad servers",
                            self.client.create_mutable_file, "contents")
        return d
    test_publish_no_servers.timeout = 30


    def test_privkey_query_error(self):
        # when a servermap is updated with MODE_WRITE, it tries to get the
        # privkey. Something might go wrong during this query attempt.
        self.client = FakeClient(20)
        # we need some contents that are large enough to push the privkey out
        # of the early part of the file
        LARGE = "These are Larger contents" * 200 # about 5KB
        d = self.client.create_mutable_file(LARGE)
        def _created(n):
            self.uri = n.get_uri()
            self.n2 = self.client.create_node_from_uri(self.uri)
            # we start by doing a map update to figure out which is the first
            # server.
            return n.get_servermap(MODE_WRITE)
        d.addCallback(_created)
        d.addCallback(lambda res: fireEventually(res))
        def _got_smap1(smap):
            peer0 = list(smap.make_sharemap()[0])[0]
            # we tell the server to respond to this peer first, so that it
            # will be asked for the privkey first
            self.client._storage._sequence = [peer0]
            # now we make the peer fail their second query
            self.client._storage._special_answers[peer0] = ["normal", "fail"]
        d.addCallback(_got_smap1)
        # now we update a servermap from a new node (which doesn't have the
        # privkey yet, forcing it to use a separate privkey query). Each
        # query response will trigger a privkey query, and since we're using
        # _sequence to make the peer0 response come back first, we'll send it
        # a privkey query first, and _sequence will again ensure that the
        # peer0 query will also come back before the others, and then
        # _special_answers will make sure that the query raises an exception.
        # The whole point of these hijinks is to exercise the code in
        # _privkey_query_failed. Note that the map-update will succeed, since
        # we'll just get a copy from one of the other shares.
        d.addCallback(lambda res: self.n2.get_servermap(MODE_WRITE))
        # Using FakeStorage._sequence means there will be read requests still
        # floating around.. wait for them to retire
        def _cancel_timer(res):
            if self.client._storage._pending_timer:
                self.client._storage._pending_timer.cancel()
            return res
        d.addBoth(_cancel_timer)
        return d

    def test_privkey_query_missing(self):
        # like test_privkey_query_error, but the shares are deleted by the
        # second query, instead of raising an exception.
        self.client = FakeClient(20)
        LARGE = "These are Larger contents" * 200 # about 5KB
        d = self.client.create_mutable_file(LARGE)
        def _created(n):
            self.uri = n.get_uri()
            self.n2 = self.client.create_node_from_uri(self.uri)
            return n.get_servermap(MODE_WRITE)
        d.addCallback(_created)
        d.addCallback(lambda res: fireEventually(res))
        def _got_smap1(smap):
            peer0 = list(smap.make_sharemap()[0])[0]
            self.client._storage._sequence = [peer0]
            self.client._storage._special_answers[peer0] = ["normal", "none"]
        d.addCallback(_got_smap1)
        d.addCallback(lambda res: self.n2.get_servermap(MODE_WRITE))
        def _cancel_timer(res):
            if self.client._storage._pending_timer:
                self.client._storage._pending_timer.cancel()
            return res
        d.addBoth(_cancel_timer)
        return d
