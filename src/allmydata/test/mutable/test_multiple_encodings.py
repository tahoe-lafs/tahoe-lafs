from twisted.trial import unittest
from allmydata.interfaces import SDMF_VERSION
from allmydata.monitor import Monitor
from foolscap.logging import log
from allmydata.mutable.common import MODE_READ
from allmydata.mutable.publish import Publish, MutableData
from allmydata.mutable.servermap import ServerMap, ServermapUpdater
from ..common_util import DevNullDictionary
from .util import FakeStorage, make_nodemaker

class MultipleEncodings(unittest.TestCase):
    def setUp(self):
        self.CONTENTS = "New contents go here"
        self.uploadable = MutableData(self.CONTENTS)
        self._storage = FakeStorage()
        self._nodemaker = make_nodemaker(self._storage, num_peers=20)
        self._storage_broker = self._nodemaker.storage_broker
        d = self._nodemaker.create_mutable_file(self.uploadable)
        def _created(node):
            self._fn = node
        d.addCallback(_created)
        return d

    def _encode(self, k, n, data, version=SDMF_VERSION):
        # encode 'data' into a peerid->shares dict.

        fn = self._fn
        # disable the nodecache, since for these tests we explicitly need
        # multiple nodes pointing at the same file
        self._nodemaker._node_cache = DevNullDictionary()
        fn2 = self._nodemaker.create_from_cap(fn.get_uri())
        # then we copy over other fields that are normally fetched from the
        # existing shares
        fn2._pubkey = fn._pubkey
        fn2._privkey = fn._privkey
        fn2._encprivkey = fn._encprivkey
        # and set the encoding parameters to something completely different
        fn2._required_shares = k
        fn2._total_shares = n

        s = self._storage
        s._peers = {} # clear existing storage
        p2 = Publish(fn2, self._storage_broker, None)
        uploadable = MutableData(data)
        d = p2.publish(uploadable)
        def _published(res):
            shares = s._peers
            s._peers = {}
            return shares
        d.addCallback(_published)
        return d

    def make_servermap(self, mode=MODE_READ, oldmap=None):
        if oldmap is None:
            oldmap = ServerMap()
        smu = ServermapUpdater(self._fn, self._storage_broker, Monitor(),
                               oldmap, mode)
        d = smu.update()
        return d

    def test_multiple_encodings(self):
        # we encode the same file in two different ways (3-of-10 and 4-of-9),
        # then mix up the shares, to make sure that download survives seeing
        # a variety of encodings. This is actually kind of tricky to set up.

        contents1 = "Contents for encoding 1 (3-of-10) go here"*1000
        contents2 = "Contents for encoding 2 (4-of-9) go here"*1000
        contents3 = "Contents for encoding 3 (4-of-7) go here"*1000

        # we make a retrieval object that doesn't know what encoding
        # parameters to use
        fn3 = self._nodemaker.create_from_cap(self._fn.get_uri())

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
            sb = self._storage_broker

            for peerid in sorted(sb.get_all_serverids()):
                for shnum in self._shares1.get(peerid, {}):
                    if shnum < len(places):
                        which = places[shnum]
                    else:
                        which = "x"
                    self._storage._peers[peerid] = peers = {}
                    in_1 = shnum in self._shares1[peerid]
                    in_2 = shnum in self._shares2.get(peerid, {})
                    in_3 = shnum in self._shares3.get(peerid, {})
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
            self._storage._sequence = new_sequence
            log.msg("merge done")
        d.addCallback(_merge)
        d.addCallback(lambda res: fn3.download_best_version())
        def _retrieved(new_contents):
            # the current specified behavior is "first version recoverable"
            self.failUnlessEqual(new_contents, contents1)
        d.addCallback(_retrieved)
        return d
