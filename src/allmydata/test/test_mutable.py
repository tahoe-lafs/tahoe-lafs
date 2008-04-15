
import itertools, struct, re
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python import failure
from allmydata import mutable, uri, dirnode, download
from allmydata.util.idlib import shortnodeid_b2a
from allmydata.util.hashutil import tagged_hash
from allmydata.encode import NotEnoughPeersError
from allmydata.interfaces import IURI, INewDirectoryURI, \
     IMutableFileURI, IUploadable, IFileURI
from allmydata.filenode import LiteralFileNode
from foolscap.eventual import eventually
from foolscap.logging import log
import sha

#from allmydata.test.common import FakeMutableFileNode
#FakeFilenode = FakeMutableFileNode

class FakeFilenode(mutable.MutableFileNode):
    counter = itertools.count(1)
    all_contents = {}
    all_rw_friends = {}

    def create(self, initial_contents):
        d = mutable.MutableFileNode.create(self, initial_contents)
        def _then(res):
            self.all_contents[self.get_uri()] = initial_contents
            return res
        d.addCallback(_then)
        return d
    def init_from_uri(self, myuri):
        mutable.MutableFileNode.init_from_uri(self, myuri)
        return self
    def _generate_pubprivkeys(self, key_size):
        count = self.counter.next()
        return FakePubKey(count), FakePrivKey(count)
    def _publish(self, initial_contents):
        self.all_contents[self.get_uri()] = initial_contents
        return defer.succeed(self)

    def download_to_data(self):
        if self.is_readonly():
            assert self.all_rw_friends.has_key(self.get_uri()), (self.get_uri(), id(self.all_rw_friends))
            return defer.succeed(self.all_contents[self.all_rw_friends[self.get_uri()]])
        else:
            return defer.succeed(self.all_contents[self.get_uri()])
    def update(self, newdata):
        self.all_contents[self.get_uri()] = newdata
        return defer.succeed(None)
    def overwrite(self, newdata):
        return self.update(newdata)

class FakeStorage:
    # this class replaces the collection of storage servers, allowing the
    # tests to examine and manipulate the published shares. It also lets us
    # control the order in which read queries are answered, to exercise more
    # of the error-handling code in mutable.Retrieve .
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
            return shares
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


class FakePublish(mutable.Publish):

    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        assert ss[0] == peerid
        assert shnums == []
        return defer.maybeDeferred(self._storage.read, peerid, storage_index)

    def _do_testreadwrite(self, peerid, secrets,
                          tw_vectors, read_vector):
        storage_index = self._node._uri.storage_index
        # always-pass: parrot the test vectors back to them.
        readv = {}
        for shnum, (testv, writev, new_length) in tw_vectors.items():
            for (offset, length, op, specimen) in testv:
                assert op in ("le", "eq", "ge")
            readv[shnum] = [ specimen
                             for (offset, length, op, specimen)
                             in testv ]
            for (offset, data) in writev:
                self._storage.write(peerid, storage_index, shnum, offset, data)
        answer = (True, readv)
        return defer.succeed(answer)




class FakeNewDirectoryNode(dirnode.NewDirectoryNode):
    filenode_class = FakeFilenode

class FakeClient:
    def __init__(self, num_peers=10):
        self._num_peers = num_peers
        self._peerids = [tagged_hash("peerid", "%d" % i)[:20]
                         for i in range(self._num_peers)]
        self.nodeid = "fakenodeid"

    def log(self, msg, **kw):
        return log.msg(msg, **kw)

    def get_renewal_secret(self):
        return "I hereby permit you to renew my files"
    def get_cancel_secret(self):
        return "I hereby permit you to cancel my leases"

    def create_empty_dirnode(self):
        n = FakeNewDirectoryNode(self)
        d = n.create()
        d.addCallback(lambda res: n)
        return d

    def create_dirnode_from_uri(self, u):
        return FakeNewDirectoryNode(self).init_from_uri(u)

    def create_mutable_file(self, contents=""):
        n = FakeFilenode(self)
        d = n.create(contents)
        d.addCallback(lambda res: n)
        return d

    def notify_retrieve(self, r):
        pass

    def create_node_from_uri(self, u):
        u = IURI(u)
        if INewDirectoryURI.providedBy(u):
            return self.create_dirnode_from_uri(u)
        if IFileURI.providedBy(u):
            if isinstance(u, uri.LiteralFileURI):
                return LiteralFileNode(u, self)
            else:
                # CHK
                raise RuntimeError("not simulated")
        assert IMutableFileURI.providedBy(u), u
        res = FakeFilenode(self).init_from_uri(u)
        return res

    def get_permuted_peers(self, service_name, key):
        # TODO: include_myself=True
        """
        @return: list of (peerid, connection,)
        """
        peers_and_connections = [(pid, (pid,)) for pid in self._peerids]
        results = []
        for peerid, connection in peers_and_connections:
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
            #newnode = FakeFilenode(self)
            return uri.LiteralFileURI(data)
        d.addCallback(_got_data)
        return d

class FakePubKey:
    def __init__(self, count):
        self.count = count
    def serialize(self):
        return "PUBKEY-%d" % self.count
    def verify(self, msg, signature):
        if signature[:5] != "SIGN(":
            return False
        if signature[5:-1] != msg:
            return False
        if signature[-1] != ")":
            return False
        return True

class FakePrivKey:
    def __init__(self, count):
        self.count = count
    def serialize(self):
        return "PRIVKEY-%d" % self.count
    def sign(self, data):
        return "SIGN(%s)" % data


class Filenode(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()

    def test_create(self):
        d = self.client.create_mutable_file()
        def _created(n):
            d = n.overwrite("contents 1")
            d.addCallback(lambda res: self.failUnlessIdentical(res, None))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.overwrite("contents 2"))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            d.addCallback(lambda res: n.download(download.Data()))
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            d.addCallback(lambda res: n.update("contents 3"))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 3"))
            return d
        d.addCallback(_created)
        return d

    def test_create_with_initial_contents(self):
        d = self.client.create_mutable_file("contents 1")
        def _created(n):
            d = n.download_to_data()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.overwrite("contents 2"))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d


class Publish(unittest.TestCase):
    def test_encrypt(self):
        c = FakeClient()
        fn = FakeFilenode(c)
        # .create usually returns a Deferred, but we happen to know it's
        # synchronous
        CONTENTS = "some initial contents"
        fn.create(CONTENTS)
        p = mutable.Publish(fn)
        target_info = None
        d = defer.maybeDeferred(p._encrypt_and_encode, target_info,
                                CONTENTS, "READKEY", "IV"*8, 3, 10)
        def _done( ((shares, share_ids),
                    required_shares, total_shares,
                    segsize, data_length, target_info2) ):
            self.failUnlessEqual(len(shares), 10)
            for sh in shares:
                self.failUnless(isinstance(sh, str))
                self.failUnlessEqual(len(sh), 7)
            self.failUnlessEqual(len(share_ids), 10)
            self.failUnlessEqual(required_shares, 3)
            self.failUnlessEqual(total_shares, 10)
            self.failUnlessEqual(segsize, 21)
            self.failUnlessEqual(data_length, len(CONTENTS))
            self.failUnlessIdentical(target_info, target_info2)
        d.addCallback(_done)
        return d

    def test_generate(self):
        c = FakeClient()
        fn = FakeFilenode(c)
        # .create usually returns a Deferred, but we happen to know it's
        # synchronous
        CONTENTS = "some initial contents"
        fn.create(CONTENTS)
        p = mutable.Publish(fn)
        # make some fake shares
        shares_and_ids = ( ["%07d" % i for i in range(10)], range(10) )
        target_info = None
        p._privkey = FakePrivKey(0)
        p._encprivkey = "encprivkey"
        p._pubkey = FakePubKey(0)
        d = defer.maybeDeferred(p._generate_shares,
                                (shares_and_ids,
                                 3, 10,
                                 21, # segsize
                                 len(CONTENTS),
                                 target_info),
                                3, # seqnum
                                "IV"*8)
        def _done( (seqnum, root_hash, final_shares, target_info2) ):
            self.failUnlessEqual(seqnum, 3)
            self.failUnlessEqual(len(root_hash), 32)
            self.failUnless(isinstance(final_shares, dict))
            self.failUnlessEqual(len(final_shares), 10)
            self.failUnlessEqual(sorted(final_shares.keys()), range(10))
            for i,sh in final_shares.items():
                self.failUnless(isinstance(sh, str))
                self.failUnlessEqual(len(sh), 381)
                # feed the share through the unpacker as a sanity-check
                pieces = mutable.unpack_share(sh)
                (u_seqnum, u_root_hash, IV, k, N, segsize, datalen,
                 pubkey, signature, share_hash_chain, block_hash_tree,
                 share_data, enc_privkey) = pieces
                self.failUnlessEqual(u_seqnum, 3)
                self.failUnlessEqual(u_root_hash, root_hash)
                self.failUnlessEqual(k, 3)
                self.failUnlessEqual(N, 10)
                self.failUnlessEqual(segsize, 21)
                self.failUnlessEqual(datalen, len(CONTENTS))
                self.failUnlessEqual(pubkey, FakePubKey(0).serialize())
                sig_material = struct.pack(">BQ32s16s BBQQ",
                                           0, seqnum, root_hash, IV,
                                           k, N, segsize, datalen)
                self.failUnlessEqual(signature,
                                     FakePrivKey(0).sign(sig_material))
                self.failUnless(isinstance(share_hash_chain, dict))
                self.failUnlessEqual(len(share_hash_chain), 4) # ln2(10)++
                for shnum,share_hash in share_hash_chain.items():
                    self.failUnless(isinstance(shnum, int))
                    self.failUnless(isinstance(share_hash, str))
                    self.failUnlessEqual(len(share_hash), 32)
                self.failUnless(isinstance(block_hash_tree, list))
                self.failUnlessEqual(len(block_hash_tree), 1) # very small tree
                self.failUnlessEqual(IV, "IV"*8)
                self.failUnlessEqual(len(share_data), len("%07d" % 1))
                self.failUnlessEqual(enc_privkey, "encprivkey")
            self.failUnlessIdentical(target_info, target_info2)
        d.addCallback(_done)
        return d

    def setup_for_sharemap(self, num_peers):
        c = FakeClient(num_peers)
        fn = FakeFilenode(c)
        s = FakeStorage()
        # .create usually returns a Deferred, but we happen to know it's
        # synchronous
        CONTENTS = "some initial contents"
        fn.create(CONTENTS)
        p = FakePublish(fn)
        p._storage_index = "\x00"*32
        p._new_seqnum = 3
        p._read_size = 1000
        #r = mutable.Retrieve(fn)
        p._storage = s
        return c, p

    def shouldFail(self, expected_failure, which, call, *args, **kwargs):
        substring = kwargs.pop("substring", None)
        d = defer.maybeDeferred(call, *args, **kwargs)
        def _done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnless(substring in str(res),
                                    "substring '%s' not in '%s'"
                                    % (substring, str(res)))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(_done)
        return d

    def test_sharemap_20newpeers(self):
        c, p = self.setup_for_sharemap(20)

        total_shares = 10
        d = p._query_peers(total_shares)
        def _done(target_info):
            (target_map, shares_per_peer) = target_info
            shares_per_peer = {}
            for shnum in target_map:
                for (peerid, old_seqnum, old_R) in target_map[shnum]:
                    #print "shnum[%d]: send to %s [oldseqnum=%s]" % \
                    #      (shnum, idlib.b2a(peerid), old_seqnum)
                    if peerid not in shares_per_peer:
                        shares_per_peer[peerid] = 1
                    else:
                        shares_per_peer[peerid] += 1
            # verify that we're sending only one share per peer
            for peerid, count in shares_per_peer.items():
                self.failUnlessEqual(count, 1)
        d.addCallback(_done)
        return d

    def test_sharemap_3newpeers(self):
        c, p = self.setup_for_sharemap(3)

        total_shares = 10
        d = p._query_peers(total_shares)
        def _done(target_info):
            (target_map, shares_per_peer) = target_info
            shares_per_peer = {}
            for shnum in target_map:
                for (peerid, old_seqnum, old_R) in target_map[shnum]:
                    if peerid not in shares_per_peer:
                        shares_per_peer[peerid] = 1
                    else:
                        shares_per_peer[peerid] += 1
            # verify that we're sending 3 or 4 shares per peer
            for peerid, count in shares_per_peer.items():
                self.failUnless(count in (3,4), count)
        d.addCallback(_done)
        return d

    def test_sharemap_nopeers(self):
        c, p = self.setup_for_sharemap(0)

        total_shares = 10
        d = self.shouldFail(NotEnoughPeersError, "test_sharemap_nopeers",
                            p._query_peers, total_shares)
        return d

    def test_write(self):
        total_shares = 10
        c, p = self.setup_for_sharemap(20)
        p._privkey = FakePrivKey(0)
        p._encprivkey = "encprivkey"
        p._pubkey = FakePubKey(0)
        # make some fake shares
        CONTENTS = "some initial contents"
        shares_and_ids = ( ["%07d" % i for i in range(10)], range(10) )
        d = defer.maybeDeferred(p._query_peers, total_shares)
        IV = "IV"*8
        d.addCallback(lambda target_info:
                      p._generate_shares( (shares_and_ids,
                                           3, total_shares,
                                           21, # segsize
                                           len(CONTENTS),
                                           target_info),
                                          3, # seqnum
                                          IV))
        d.addCallback(p._send_shares, IV)
        def _done((surprised, dispatch_map)):
            self.failIf(surprised, "surprised!")
        d.addCallback(_done)
        return d

class FakeRetrieve(mutable.Retrieve):
    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        d = defer.maybeDeferred(self._storage.read, peerid, storage_index)
        def _read(shares):
            response = {}
            for shnum in shares:
                if shnums and shnum not in shnums:
                    continue
                vector = response[shnum] = []
                for (offset, length) in readv:
                    vector.append(shares[shnum][offset:offset+length])
            return response
        d.addCallback(_read)
        return d

    def _deserialize_pubkey(self, pubkey_s):
        mo = re.search(r"^PUBKEY-(\d+)$", pubkey_s)
        if not mo:
            raise RuntimeError("mangled pubkey")
        count = mo.group(1)
        return FakePubKey(int(count))


class Roundtrip(unittest.TestCase):

    def setup_for_publish(self, num_peers):
        c = FakeClient(num_peers)
        fn = FakeFilenode(c)
        s = FakeStorage()
        # .create usually returns a Deferred, but we happen to know it's
        # synchronous
        fn.create("")
        p = FakePublish(fn)
        p._storage = s
        r = FakeRetrieve(fn)
        r._storage = s
        return c, s, fn, p, r

    def test_basic(self):
        c, s, fn, p, r = self.setup_for_publish(20)
        contents = "New contents go here"
        d = p.publish(contents)
        def _published(res):
            return r.retrieve()
        d.addCallback(_published)
        def _retrieved(new_contents):
            self.failUnlessEqual(contents, new_contents)
        d.addCallback(_retrieved)
        return d

    def flip_bit(self, original, byte_offset):
        return (original[:byte_offset] +
                chr(ord(original[byte_offset]) ^ 0x01) +
                original[byte_offset+1:])


    def shouldFail(self, expected_failure, which, substring,
                    callable, *args, **kwargs):
        assert substring is None or isinstance(substring, str)
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnless(substring in str(res),
                                    "substring '%s' not in '%s'"
                                    % (substring, str(res)))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d

    def _corrupt_all(self, offset, substring, refetch_pubkey=False,
                     should_succeed=False):
        c, s, fn, p, r = self.setup_for_publish(20)
        contents = "New contents go here"
        d = p.publish(contents)
        def _published(res):
            if refetch_pubkey:
                # clear the pubkey, to force a fetch
                r._pubkey = None
            for peerid in s._peers:
                shares = s._peers[peerid]
                for shnum in shares:
                    data = shares[shnum]
                    (version,
                     seqnum,
                     root_hash,
                     IV,
                     k, N, segsize, datalen,
                     o) = mutable.unpack_header(data)
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
                    shares[shnum] = self.flip_bit(data, real_offset)
        d.addCallback(_published)
        if should_succeed:
            d.addCallback(lambda res: r.retrieve())
        else:
            d.addCallback(lambda res:
                          self.shouldFail(NotEnoughPeersError,
                                          "_corrupt_all(offset=%s)" % (offset,),
                                          substring,
                                          r.retrieve))
        return d

    def test_corrupt_all_verbyte(self):
        # when the version byte is not 0, we hit an assertion error in
        # unpack_share().
        return self._corrupt_all(0, "AssertionError")

    def test_corrupt_all_seqnum(self):
        # a corrupt sequence number will trigger a bad signature
        return self._corrupt_all(1, "signature is invalid")

    def test_corrupt_all_R(self):
        # a corrupt root hash will trigger a bad signature
        return self._corrupt_all(9, "signature is invalid")

    def test_corrupt_all_IV(self):
        # a corrupt salt/IV will trigger a bad signature
        return self._corrupt_all(41, "signature is invalid")

    def test_corrupt_all_k(self):
        # a corrupt 'k' will trigger a bad signature
        return self._corrupt_all(57, "signature is invalid")

    def test_corrupt_all_N(self):
        # a corrupt 'N' will trigger a bad signature
        return self._corrupt_all(58, "signature is invalid")

    def test_corrupt_all_segsize(self):
        # a corrupt segsize will trigger a bad signature
        return self._corrupt_all(59, "signature is invalid")

    def test_corrupt_all_datalen(self):
        # a corrupt data length will trigger a bad signature
        return self._corrupt_all(67, "signature is invalid")

    def test_corrupt_all_pubkey(self):
        # a corrupt pubkey won't match the URI's fingerprint
        return self._corrupt_all("pubkey", "pubkey doesn't match fingerprint",
                                 refetch_pubkey=True)

    def test_corrupt_all_sig(self):
        # a corrupt signature is a bad one
        # the signature runs from about [543:799], depending upon the length
        # of the pubkey
        return self._corrupt_all("signature", "signature is invalid",
                                 refetch_pubkey=True)

    def test_corrupt_all_share_hash_chain_number(self):
        # a corrupt share hash chain entry will show up as a bad hash. If we
        # mangle the first byte, that will look like a bad hash number,
        # causing an IndexError
        return self._corrupt_all("share_hash_chain", "corrupt hashes")

    def test_corrupt_all_share_hash_chain_hash(self):
        # a corrupt share hash chain entry will show up as a bad hash. If we
        # mangle a few bytes in, that will look like a bad hash.
        return self._corrupt_all(("share_hash_chain",4), "corrupt hashes")

    def test_corrupt_all_block_hash_tree(self):
        return self._corrupt_all("block_hash_tree", "block hash tree failure")

    def test_corrupt_all_block(self):
        return self._corrupt_all("share_data", "block hash tree failure")

    def test_corrupt_all_encprivkey(self):
        # a corrupted privkey won't even be noticed by the reader
        return self._corrupt_all("enc_privkey", None, should_succeed=True)

    def test_short_read(self):
        c, s, fn, p, r = self.setup_for_publish(20)
        contents = "New contents go here"
        d = p.publish(contents)
        def _published(res):
            # force a short read, to make Retrieve._got_results re-send the
            # queries. But don't make it so short that we can't read the
            # header.
            r._read_size = mutable.HEADER_LENGTH + 10
            return r.retrieve()
        d.addCallback(_published)
        def _retrieved(new_contents):
            self.failUnlessEqual(contents, new_contents)
        d.addCallback(_retrieved)
        return d

    def test_basic_sequenced(self):
        c, s, fn, p, r = self.setup_for_publish(20)
        s._sequence = c._peerids[:]
        contents = "New contents go here"
        d = p.publish(contents)
        def _published(res):
            return r.retrieve()
        d.addCallback(_published)
        def _retrieved(new_contents):
            self.failUnlessEqual(contents, new_contents)
        d.addCallback(_retrieved)
        return d

    def test_basic_pubkey_at_end(self):
        # we corrupt the pubkey in all but the last 'k' shares, allowing the
        # download to succeed but forcing a bunch of retries first. Note that
        # this is rather pessimistic: our Retrieve process will throw away
        # the whole share if the pubkey is bad, even though the rest of the
        # share might be good.
        c, s, fn, p, r = self.setup_for_publish(20)
        s._sequence = c._peerids[:]
        contents = "New contents go here"
        d = p.publish(contents)
        def _published(res):
            r._pubkey = None
            homes = [peerid for peerid in c._peerids
                     if s._peers.get(peerid, {})]
            k = fn.get_required_shares()
            homes_to_corrupt = homes[:-k]
            for peerid in homes_to_corrupt:
                shares = s._peers[peerid]
                for shnum in shares:
                    data = shares[shnum]
                    (version,
                     seqnum,
                     root_hash,
                     IV,
                     k, N, segsize, datalen,
                     o) = mutable.unpack_header(data)
                    offset = 107 # pubkey
                    shares[shnum] = self.flip_bit(data, offset)
            return r.retrieve()
        d.addCallback(_published)
        def _retrieved(new_contents):
            self.failUnlessEqual(contents, new_contents)
        d.addCallback(_retrieved)
        return d

    def _encode(self, c, s, fn, k, n, data):
        # encode 'data' into a peerid->shares dict.

        fn2 = FakeFilenode(c)
        # init_from_uri populates _uri, _writekey, _readkey, _storage_index,
        # and _fingerprint
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

        p2 = FakePublish(fn2)
        p2._storage = s
        p2._storage._peers = {} # clear existing storage
        d = p2.publish(data)
        def _published(res):
            shares = s._peers
            s._peers = {}
            return shares
        d.addCallback(_published)
        return d

    def test_multiple_encodings(self):
        # we encode the same file in two different ways (3-of-10 and 4-of-9),
        # then mix up the shares, to make sure that download survives seeing
        # a variety of encodings. This is actually kind of tricky to set up.
        c, s, fn, p, r = self.setup_for_publish(20)
        # we ignore fn, p, and r

        contents1 = "Contents for encoding 1 (3-of-10) go here"
        contents2 = "Contents for encoding 2 (4-of-9) go here"
        contents3 = "Contents for encoding 3 (4-of-7) go here"

        # we make a retrieval object that doesn't know what encoding
        # parameters to use
        fn3 = FakeFilenode(c)
        fn3.init_from_uri(fn.get_uri())

        # now we upload a file through fn1, and grab its shares
        d = self._encode(c, s, fn, 3, 10, contents1)
        def _encoded_1(shares):
            self._shares1 = shares
        d.addCallback(_encoded_1)
        d.addCallback(lambda res: self._encode(c, s, fn, 4, 9, contents2))
        def _encoded_2(shares):
            self._shares2 = shares
        d.addCallback(_encoded_2)
        d.addCallback(lambda res: self._encode(c, s, fn, 4, 7, contents3))
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

            for i,peerid in enumerate(c._peerids):
                peerid_s = shortnodeid_b2a(peerid)
                for shnum in self._shares1.get(peerid, {}):
                    if shnum < len(places):
                        which = places[shnum]
                    else:
                        which = "x"
                    s._peers[peerid] = peers = {}
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
            s._sequence = new_sequence
            log.msg("merge done")
        d.addCallback(_merge)
        def _retrieve(res):
            r3 = FakeRetrieve(fn3)
            r3._storage = s
            return r3.retrieve()
        d.addCallback(_retrieve)
        def _retrieved(new_contents):
            # the current specified behavior is "first version recoverable"
            self.failUnlessEqual(new_contents, contents1)
        d.addCallback(_retrieved)
        return d

