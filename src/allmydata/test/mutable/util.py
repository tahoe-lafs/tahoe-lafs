from cStringIO import StringIO
from twisted.internet import defer, reactor
from foolscap.api import eventually, fireEventually
from allmydata import client
from allmydata.nodemaker import NodeMaker
from allmydata.interfaces import SDMF_VERSION, MDMF_VERSION
from allmydata.util import base32
from allmydata.util.hashutil import tagged_hash
from allmydata.storage_client import StorageFarmBroker
from allmydata.mutable.layout import MDMFSlotReadProxy
from allmydata.mutable.publish import MutableData
from ..common import TEST_RSA_KEY_SIZE

def eventuaaaaaly(res=None):
    d = fireEventually(res)
    d.addCallback(fireEventually)
    d.addCallback(fireEventually)
    return d

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

    def read(self, peerid, storage_index):
        shares = self._peers.get(peerid, {})
        if self._sequence is None:
            return eventuaaaaaly(shares)
        d = defer.Deferred()
        if not self._pending:
            self._pending_timer = reactor.callLater(1.0, self._fire_readers)
        if peerid not in self._pending:
            self._pending[peerid] = []
        self._pending[peerid].append( (d, shares) )
        return d

    def _fire_readers(self):
        self._pending_timer = None
        pending = self._pending
        self._pending = {}
        for peerid in self._sequence:
            if peerid in pending:
                for (d, shares) in pending.pop(peerid):
                    eventually(d.callback, shares)
        for peerid in pending:
            for (d, shares) in pending[peerid]:
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
        self.queries += 1
        def _call():
            meth = getattr(self, methname)
            return meth(*args, **kwargs)
        d = fireEventually()
        d.addCallback(lambda res: _call())
        return d

    def callRemoteOnly(self, methname, *args, **kwargs):
        self.queries += 1
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


def flip_bit(original, byte_offset):
    return (original[:byte_offset] +
            chr(ord(original[byte_offset]) ^ 0x01) +
            original[byte_offset+1:])

def add_two(original, byte_offset):
    # It isn't enough to simply flip the bit for the version number,
    # because 1 is a valid version number. So we add two instead.
    return (original[:byte_offset] +
            chr(ord(original[byte_offset]) ^ 0x02) +
            original[byte_offset+1:])

def corrupt(res, s, offset, shnums_to_corrupt=None, offset_offset=0):
    # if shnums_to_corrupt is None, corrupt all shares. Otherwise it is a
    # list of shnums to corrupt.
    ds = []
    for peerid in s._peers:
        shares = s._peers[peerid]
        for shnum in shares:
            if (shnums_to_corrupt is not None
                and shnum not in shnums_to_corrupt):
                continue
            data = shares[shnum]
            # We're feeding the reader all of the share data, so it
            # won't need to use the rref that we didn't provide, nor the
            # storage index that we didn't provide. We do this because
            # the reader will work for both MDMF and SDMF.
            reader = MDMFSlotReadProxy(None, None, shnum, data)
            # We need to get the offsets for the next part.
            d = reader.get_verinfo()
            def _do_corruption(verinfo, data, shnum, shares):
                (seqnum,
                 root_hash,
                 IV,
                 segsize,
                 datalen,
                 k, n, prefix, o) = verinfo
                if isinstance(offset, tuple):
                    offset1, offset2 = offset
                else:
                    offset1 = offset
                    offset2 = 0
                if offset1 == "pubkey" and IV:
                    real_offset = 107
                elif offset1 in o:
                    real_offset = o[offset1]
                else:
                    real_offset = offset1
                real_offset = int(real_offset) + offset2 + offset_offset
                assert isinstance(real_offset, int), offset
                if offset1 == 0: # verbyte
                    f = add_two
                else:
                    f = flip_bit
                shares[shnum] = f(data, real_offset)
            d.addCallback(_do_corruption, data, shnum, shares)
            ds.append(d)
    dl = defer.DeferredList(ds)
    dl.addCallback(lambda ignored: res)
    return dl

def make_storagebroker(s=None, num_peers=10):
    if not s:
        s = FakeStorage()
    peerids = [tagged_hash("peerid", "%d" % i)[:20]
               for i in range(num_peers)]
    storage_broker = StorageFarmBroker(True, None)
    for peerid in peerids:
        fss = FakeStorageServer(peerid, s)
        ann = {"anonymous-storage-FURL": "pb://%s@nowhere/fake" % base32.b2a(peerid),
               "permutation-seed-base32": base32.b2a(peerid) }
        storage_broker.test_add_rref(peerid, fss, ann)
    return storage_broker

def make_nodemaker(s=None, num_peers=10, keysize=TEST_RSA_KEY_SIZE):
    storage_broker = make_storagebroker(s, num_peers)
    sh = client.SecretHolder("lease secret", "convergence secret")
    keygen = client.KeyGenerator()
    if keysize:
        keygen.set_default_keysize(keysize)
    nodemaker = NodeMaker(storage_broker, sh, None,
                          None, None,
                          {"k": 3, "n": 10}, SDMF_VERSION, keygen)
    return nodemaker

class PublishMixin:
    def publish_one(self):
        # publish a file and create shares, which can then be manipulated
        # later.
        self.CONTENTS = "New contents go here" * 1000
        self.uploadable = MutableData(self.CONTENTS)
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage)
        self._storage_broker = self._nodemaker.storage_broker
        d = self._nodemaker.create_mutable_file(self.uploadable)
        def _created(node):
            self._fn = node
            self._fn2 = self._nodemaker.create_from_cap(node.get_uri())
        d.addCallback(_created)
        return d

    def publish_mdmf(self, data=None):
        # like publish_one, except that the result is guaranteed to be
        # an MDMF file.
        # self.CONTENTS should have more than one segment.
        if data is None:
            data = "This is an MDMF file" * 100000
        self.CONTENTS = data
        self.uploadable = MutableData(self.CONTENTS)
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage)
        self._storage_broker = self._nodemaker.storage_broker
        d = self._nodemaker.create_mutable_file(self.uploadable, version=MDMF_VERSION)
        def _created(node):
            self._fn = node
            self._fn2 = self._nodemaker.create_from_cap(node.get_uri())
        d.addCallback(_created)
        return d


    def publish_sdmf(self, data=None):
        # like publish_one, except that the result is guaranteed to be
        # an SDMF file
        if data is None:
            data = "This is an SDMF file" * 1000
        self.CONTENTS = data
        self.uploadable = MutableData(self.CONTENTS)
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage)
        self._storage_broker = self._nodemaker.storage_broker
        d = self._nodemaker.create_mutable_file(self.uploadable, version=SDMF_VERSION)
        def _created(node):
            self._fn = node
            self._fn2 = self._nodemaker.create_from_cap(node.get_uri())
        d.addCallback(_created)
        return d


    def publish_multiple(self, version=0):
        self.CONTENTS = ["Contents 0",
                         "Contents 1",
                         "Contents 2",
                         "Contents 3a",
                         "Contents 3b"]
        self.uploadables = [MutableData(d) for d in self.CONTENTS]
        self._copied_shares = {}
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage)
        d = self._nodemaker.create_mutable_file(self.uploadables[0], version=version) # seqnum=1
        def _created(node):
            self._fn = node
            # now create multiple versions of the same file, and accumulate
            # their shares, so we can mix and match them later.
            d = defer.succeed(None)
            d.addCallback(self._copy_shares, 0)
            d.addCallback(lambda res: node.overwrite(self.uploadables[1])) #s2
            d.addCallback(self._copy_shares, 1)
            d.addCallback(lambda res: node.overwrite(self.uploadables[2])) #s3
            d.addCallback(self._copy_shares, 2)
            d.addCallback(lambda res: node.overwrite(self.uploadables[3])) #s4a
            d.addCallback(self._copy_shares, 3)
            # now we replace all the shares with version s3, and upload a new
            # version to get s4b.
            rollback = dict([(i,2) for i in range(10)])
            d.addCallback(lambda res: self._set_versions(rollback))
            d.addCallback(lambda res: node.overwrite(self.uploadables[4])) #s4b
            d.addCallback(self._copy_shares, 4)
            # we leave the storage in state 4
            return d
        d.addCallback(_created)
        return d


    def _copy_shares(self, ignored, index):
        shares = self._storage._peers
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
        shares = self._storage._peers
        oldshares = self._copied_shares
        for peerid in shares:
            for shnum in shares[peerid]:
                if shnum in versionmap:
                    index = versionmap[shnum]
                    shares[peerid][shnum] = oldshares[index][peerid][shnum]

class CheckerMixin:
    def check_good(self, r, where):
        self.failUnless(r.is_healthy(), where)
        return r

    def check_bad(self, r, where):
        self.failIf(r.is_healthy(), where)
        return r

    def check_expected_failure(self, r, expected_exception, substring, where):
        for (peerid, storage_index, shnum, f) in r.get_share_problems():
            if f.check(expected_exception):
                self.failUnless(substring in str(f),
                                "%s: substring '%s' not in '%s'" %
                                (where, substring, str(f)))
                return
        self.fail("%s: didn't see expected exception %s in problems %s" %
                  (where, expected_exception, r.get_share_problems()))

