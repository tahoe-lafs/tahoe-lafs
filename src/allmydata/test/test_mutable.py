
import itertools, struct
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import failure
from allmydata import mutable, uri, dirnode2
from allmydata.dirnode2 import split_netstring
from allmydata.util.hashutil import netstring, tagged_hash
from allmydata.encode import NotEnoughPeersError

import sha
from allmydata.Crypto.Util.number import bytes_to_long

class Netstring(unittest.TestCase):
    def test_split(self):
        a = netstring("hello") + netstring("world")
        self.failUnlessEqual(split_netstring(a, 2), ("hello", "world"))
        self.failUnlessEqual(split_netstring(a, 2, False), ("hello", "world"))
        self.failUnlessEqual(split_netstring(a, 2, True),
                             ("hello", "world", ""))
        self.failUnlessRaises(ValueError, split_netstring, a+" extra", 2)
        self.failUnlessRaises(ValueError, split_netstring, a+" extra", 2, False)

    def test_extra(self):
        a = netstring("hello")
        self.failUnlessEqual(split_netstring(a, 1, True), ("hello", ""))
        b = netstring("hello") + "extra stuff"
        self.failUnlessEqual(split_netstring(b, 1, True),
                             ("hello", "extra stuff"))

    def test_nested(self):
        a = netstring("hello") + netstring("world") + "extra stuff"
        b = netstring("a") + netstring("is") + netstring(a) + netstring(".")
        top = split_netstring(b, 4)
        self.failUnlessEqual(len(top), 4)
        self.failUnlessEqual(top[0], "a")
        self.failUnlessEqual(top[1], "is")
        self.failUnlessEqual(top[2], a)
        self.failUnlessEqual(top[3], ".")
        self.failUnlessRaises(ValueError, split_netstring, a, 2)
        self.failUnlessRaises(ValueError, split_netstring, a, 2, False)
        bottom = split_netstring(a, 2, True)
        self.failUnlessEqual(bottom, ("hello", "world", "extra stuff"))

class FakeFilenode(mutable.MutableFileNode):
    counter = itertools.count(1)
    all_contents = {}

    def _generate_pubprivkeys(self):
        count = self.counter.next()
        return FakePubKey(count), FakePrivKey(count)
    def _publish(self, initial_contents):
        self.all_contents[self._uri] = initial_contents
        return defer.succeed(self)

    def download_to_data(self):
        return defer.succeed(self.all_contents[self._uri])
    def replace(self, newdata):
        self.all_contents[self._uri] = newdata
        return defer.succeed(None)
    def is_readonly(self):
        return False
    def get_readonly(self):
        return "fake readonly"

class FakePublish(mutable.Publish):
    def _do_query(self, conn, peerid, peer_storage_servers):
        assert conn[0] == peerid
        shares = self._peers[peerid]
        return defer.succeed(shares)

    def _do_testreadwrite(self, peerid, peer_storage_servers, secrets,
                          tw_vectors, read_vector):
        # always-pass: parrot the test vectors back to them.
        readv = {}
        for shnum, (testv, datav, new_length) in tw_vectors.items():
            for (offset, length, op, specimen) in testv:
                assert op in ("le", "eq", "ge")
            readv[shnum] = [ specimen
                             for (offset, length, op, specimen)
                             in testv ]
        answer = (True, readv)
        return defer.succeed(answer)


class FakeNewDirectoryNode(dirnode2.NewDirectoryNode):
    filenode_class = FakeFilenode

class MyClient:
    def __init__(self, num_peers=10):
        self._num_peers = num_peers
        self._peerids = [tagged_hash("peerid", "%d" % i)
                         for i in range(self._num_peers)]

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
    def create_mutable_file_from_uri(self, u):
        return FakeFilenode(self).init_from_uri(u)

    def get_permuted_peers(self, key, include_myself=True):
        """
        @return: list of (permuted-peerid, peerid, connection,)
        """
        peers_and_connections = [(pid, (pid,)) for pid in self._peerids]
        results = []
        for peerid, connection in peers_and_connections:
            assert isinstance(peerid, str)
            permuted = bytes_to_long(sha.new(key + peerid).digest())
            results.append((permuted, peerid, connection))
        results.sort()
        return results

class Filenode(unittest.TestCase):
    def setUp(self):
        self.client = MyClient()

    def test_create(self):
        d = self.client.create_mutable_file()
        def _created(n):
            d = n.replace("contents 1")
            d.addCallback(lambda res: self.failUnlessIdentical(res, None))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.replace("contents 2"))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d

    def test_create_with_initial_contents(self):
        d = self.client.create_mutable_file("contents 1")
        def _created(n):
            d = n.download_to_data()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            d.addCallback(lambda res: n.replace("contents 2"))
            d.addCallback(lambda res: n.download_to_data())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d

class Publish(unittest.TestCase):
    def test_encrypt(self):
        c = MyClient()
        fn = FakeFilenode(c)
        # .create usually returns a Deferred, but we happen to know it's
        # synchronous
        CONTENTS = "some initial contents"
        fn.create(CONTENTS)
        p = mutable.Publish(fn)
        d = defer.maybeDeferred(p._encrypt_and_encode,
                                CONTENTS, "READKEY", "IV"*8, 3, 10)
        def _done( ((shares, share_ids),
                    required_shares, total_shares,
                    segsize, data_length, IV) ):
            self.failUnlessEqual(len(shares), 10)
            for sh in shares:
                self.failUnless(isinstance(sh, str))
                self.failUnlessEqual(len(sh), 7)
            self.failUnlessEqual(len(share_ids), 10)
            self.failUnlessEqual(required_shares, 3)
            self.failUnlessEqual(total_shares, 10)
            self.failUnlessEqual(segsize, 21)
            self.failUnlessEqual(data_length, len(CONTENTS))
            self.failUnlessEqual(len(IV), 16)
        d.addCallback(_done)
        return d

    def test_generate(self):
        c = MyClient()
        fn = FakeFilenode(c)
        # .create usually returns a Deferred, but we happen to know it's
        # synchronous
        CONTENTS = "some initial contents"
        fn.create(CONTENTS)
        p = mutable.Publish(fn)
        r = mutable.Retrieve(fn)
        # make some fake shares
        shares_and_ids = ( ["%07d" % i for i in range(10)], range(10) )
        d = defer.maybeDeferred(p._generate_shares,
                                (shares_and_ids,
                                 3, 10,
                                 21, # segsize
                                 len(CONTENTS),
                                 "IV"*8),
                                3, # seqnum
                                FakePrivKey(0), "encprivkey", FakePubKey(0),
                                )
        def _done( (seqnum, root_hash, final_shares) ):
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
                self.failUnless(isinstance(share_hash_chain, list))
                self.failUnlessEqual(len(share_hash_chain), 4) # ln2(10)++
                for i in share_hash_chain:
                    self.failUnless(isinstance(i, tuple))
                    self.failUnless(isinstance(i[0], int))
                    self.failUnless(isinstance(i[1], str))
                    self.failUnlessEqual(len(i[1]), 32)
                self.failUnless(isinstance(block_hash_tree, list))
                self.failUnlessEqual(len(block_hash_tree), 1) # very small tree
                self.failUnlessEqual(IV, "IV"*8)
                self.failUnlessEqual(len(share_data), len("%07d" % 1))
                self.failUnlessEqual(enc_privkey, "encprivkey")
        d.addCallback(_done)
        return d

    def setup_for_sharemap(self, num_peers):
        c = MyClient(num_peers)
        fn = FakeFilenode(c)
        # .create usually returns a Deferred, but we happen to know it's
        # synchronous
        CONTENTS = "some initial contents"
        fn.create(CONTENTS)
        p = FakePublish(fn)
        #r = mutable.Retrieve(fn)
        p._peers = {}
        for peerid in c._peerids:
            p._peers[peerid] = {}
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

        new_seqnum = 3
        new_root_hash = "Rnew"
        new_shares = None
        total_shares = 10
        d = p._query_peers( (new_seqnum, new_root_hash, new_seqnum),
                            total_shares)
        def _done( (target_map, peer_storage_servers) ):
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

        new_seqnum = 3
        new_root_hash = "Rnew"
        new_shares = None
        total_shares = 10
        d = p._query_peers( (new_seqnum, new_root_hash, new_seqnum),
                            total_shares)
        def _done( (target_map, peer_storage_servers) ):
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

        new_seqnum = 3
        new_root_hash = "Rnew"
        new_shares = None
        total_shares = 10
        d = self.shouldFail(NotEnoughPeersError, "test_sharemap_nopeers",
                            p._query_peers,
                            (new_seqnum, new_root_hash, new_seqnum),
                            total_shares)
        return d

    def setup_for_write(self, num_peers, total_shares):
        c, p = self.setup_for_sharemap(num_peers)
        # make some fake shares
        CONTENTS = "some initial contents"
        shares_and_ids = ( ["%07d" % i for i in range(10)], range(10) )
        d = defer.maybeDeferred(p._generate_shares,
                                (shares_and_ids,
                                 3, total_shares,
                                 21, # segsize
                                 len(CONTENTS),
                                 "IV"*8),
                                3, # seqnum
                                FakePrivKey(0), "encprivkey", FakePubKey(0),
                                )
        return d, p

    def test_write(self):
        total_shares = 10
        d, p = self.setup_for_write(20, total_shares)
        d.addCallback(p._query_peers, total_shares)
        IV = "IV"*8
        d.addCallback(p._send_shares, IV)
        def _done((surprised, dispatch_map)):
            self.failIf(surprised, "surprised!")
        d.addCallback(_done)
        return d

    def setup_for_publish(self, num_peers):
        c = MyClient(num_peers)
        fn = FakeFilenode(c)
        # .create usually returns a Deferred, but we happen to know it's
        # synchronous
        fn.create("")
        p = FakePublish(fn)
        p._peers = {}
        for peerid in c._peerids:
            p._peers[peerid] = {}
        return c, fn, p

    def test_publish(self):
        c, fn, p = self.setup_for_publish(20)
        # make sure the length of our contents string is not a multiple of k,
        # to exercise the padding code.
        d = p.publish("New contents of the mutable filenode.")
        def _done(res):
            # TODO: examine peers and check on their shares
            pass
        d.addCallback(_done)
        return d


class FakePubKey:
    def __init__(self, count):
        self.count = count
    def serialize(self):
        return "PUBKEY-%d" % self.count
class FakePrivKey:
    def __init__(self, count):
        self.count = count
    def serialize(self):
        return "PRIVKEY-%d" % self.count
    def sign(self, data):
        return "SIGN(%s)" % data

class Dirnode(unittest.TestCase):
    def setUp(self):
        self.client = MyClient()

    def test_create(self):
        self.expected_manifest = []

        d = self.client.create_empty_dirnode()
        def _check(n):
            self.failUnless(n.is_mutable())
            u = n.get_uri()
            self.failUnless(u)
            self.failUnless(u.startswith("URI:DIR2:"), u)
            u_ro = n.get_immutable_uri()
            self.failUnless(u_ro.startswith("URI:DIR2-RO:"), u_ro)
            u_v = n.get_verifier()
            self.failUnless(u_v.startswith("URI:DIR2-Verifier:"), u_v)
            self.expected_manifest.append(u_v)

            d = n.list()
            d.addCallback(lambda res: self.failUnlessEqual(res, {}))
            d.addCallback(lambda res: n.has_child("missing"))
            d.addCallback(lambda res: self.failIf(res))
            fake_file_uri = uri.WriteableSSKFileURI("a"*16,"b"*32)
            ffu_v = fake_file_uri.get_verifier().to_string()
            self.expected_manifest.append(ffu_v)
            d.addCallback(lambda res: n.set_uri("child", fake_file_uri))
            d.addCallback(lambda res: self.failUnlessEqual(res, None))

            d.addCallback(lambda res: n.create_empty_directory("subdir"))
            def _created(subdir):
                self.failUnless(isinstance(subdir, FakeNewDirectoryNode))
                self.subdir = subdir
                new_v = subdir.get_verifier()
                self.expected_manifest.append(new_v)
            d.addCallback(_created)

            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted(["child", "subdir"])))

            d.addCallback(lambda res: n.build_manifest())
            def _check_manifest(manifest):
                self.failUnlessEqual(sorted(manifest),
                                     sorted(self.expected_manifest))
            d.addCallback(_check_manifest)

            def _add_subsubdir(res):
                return self.subdir.create_empty_directory("subsubdir")
            d.addCallback(_add_subsubdir)
            d.addCallback(lambda res: n.get_child_at_path("subdir/subsubdir"))
            d.addCallback(lambda subsubdir:
                          self.failUnless(isinstance(subsubdir,
                                                     FakeNewDirectoryNode)))
            d.addCallback(lambda res: n.get_child_at_path(""))
            d.addCallback(lambda res: self.failUnlessEqual(res.get_uri(),
                                                           n.get_uri()))

            d.addCallback(lambda res: n.get_metadata_for("child"))
            d.addCallback(lambda metadata: self.failUnlessEqual(metadata, {}))

            d.addCallback(lambda res: n.delete("subdir"))
            d.addCallback(lambda old_child:
                          self.failUnlessEqual(old_child.get_uri(),
                                               self.subdir.get_uri()))

            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted(["child"])))

            return d

        d.addCallback(_check)

        return d

