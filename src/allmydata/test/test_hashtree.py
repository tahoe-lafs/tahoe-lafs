# -*- test-case-name: allmydata.test.test_hashtree -*-

from twisted.trial import unittest

from allmydata.util.hashutil import tagged_hash
from allmydata import hashtree


def make_tree(numleaves):
    leaves = ["%d" % i for i in range(numleaves)]
    leaf_hashes = [tagged_hash("tag", leaf) for leaf in leaves]
    ht = hashtree.HashTree(leaf_hashes)
    return ht

class Complete(unittest.TestCase):
    def test_create(self):
        # try out various sizes, since we pad to a power of two
        ht = make_tree(6)
        ht = make_tree(9)
        ht = make_tree(8)
        root = ht[0]
        self.failUnlessEqual(len(root), 32)
        self.failUnlessEqual(ht.get_leaf(0), tagged_hash("tag", "0"))
        self.failUnlessRaises(IndexError, ht.get_leaf, 8)
        self.failUnlessEqual(ht.get_leaf_index(0), 7)
        self.failUnlessRaises(IndexError, ht.parent, 0)
        self.failUnlessRaises(IndexError, ht.needed_for, -1)

    def test_needed_hashes(self):
        ht = make_tree(8)
        self.failUnlessEqual(ht.needed_hashes(0), set([8, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(0, True), set([7, 8, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(1), set([7, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(7), set([13, 5, 1]))
        self.failUnlessEqual(ht.needed_hashes(7, False), set([13, 5, 1]))
        self.failUnlessEqual(ht.needed_hashes(7, True), set([14, 13, 5, 1]))

    def test_dump(self):
        ht = make_tree(6)
        expected = [(0,0),
                    (1,1), (3,2), (7,3), (8,3), (4,2), (9,3), (10,3),
                    (2,1), (5,2), (11,3), (12,3), (6,2), (13,3), (14,3),
                    ]
        self.failUnlessEqual(list(ht.depth_first()), expected)
        d = "\n" + ht.dump()
        #print d
        self.failUnless("\n  0:" in d)
        self.failUnless("\n    1:" in d)
        self.failUnless("\n      3:" in d)
        self.failUnless("\n        7:" in d)
        self.failUnless("\n        8:" in d)
        self.failUnless("\n      4:" in d)

class Incomplete(unittest.TestCase):

    def test_create(self):
        ht = hashtree.IncompleteHashTree(6)
        ht = hashtree.IncompleteHashTree(9)
        ht = hashtree.IncompleteHashTree(8)
        self.failUnlessEqual(ht[0], None)
        self.failUnlessEqual(ht.get_leaf(0), None)
        self.failUnlessRaises(IndexError, ht.get_leaf, 8)
        self.failUnlessEqual(ht.get_leaf_index(0), 7)

    def test_needed_hashes(self):
        ht = hashtree.IncompleteHashTree(8)
        self.failUnlessEqual(ht.needed_hashes(0), set([8, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(0, True), set([7, 8, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(1), set([7, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(7), set([13, 5, 1]))
        self.failUnlessEqual(ht.needed_hashes(7, False), set([13, 5, 1]))
        self.failUnlessEqual(ht.needed_hashes(7, True), set([14, 13, 5, 1]))
        ht = hashtree.IncompleteHashTree(1)
        self.failUnlessEqual(ht.needed_hashes(0), set([]))
        ht = hashtree.IncompleteHashTree(6)
        self.failUnlessEqual(ht.needed_hashes(0), set([8, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(0, True), set([7, 8, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(1), set([7, 4, 2]))
        self.failUnlessEqual(ht.needed_hashes(5), set([11, 6, 1]))
        self.failUnlessEqual(ht.needed_hashes(5, False), set([11, 6, 1]))
        self.failUnlessEqual(ht.needed_hashes(5, True), set([12, 11, 6, 1]))

    def test_depth_of(self):
        hashtree.IncompleteHashTree(8)
        self.failUnlessEqual(hashtree.depth_of(0), 0)
        for i in [1,2]:
            self.failUnlessEqual(hashtree.depth_of(i), 1, "i=%d"%i)
        for i in [3,4,5,6]:
            self.failUnlessEqual(hashtree.depth_of(i), 2, "i=%d"%i)
        for i in [7,8,9,10,11,12,13,14]:
            self.failUnlessEqual(hashtree.depth_of(i), 3, "i=%d"%i)

    def test_large(self):
        # IncompleteHashTree.set_hashes() used to take O(N**2). This test is
        # meant to show that it now takes O(N) or maybe O(N*ln(N)). I wish
        # there were a good way to assert this (like counting VM operations
        # or something): the problem was inside list.sort(), so there's no
        # good way to instrument set_hashes() to count what we care about. On
        # my laptop, 10k leaves takes 1.1s in this fixed version, and 11.6s
        # in the old broken version. An 80k-leaf test (corresponding to a
        # 10GB file with a 128KiB segsize) 10s in the fixed version, and
        # several hours in the broken version, but 10s on my laptop (plus the
        # 20s of setup code) probably means 200s on our dapper buildslave,
        # which is painfully long for a unit test.
        self.do_test_speed(10000)

    def do_test_speed(self, SIZE):
        # on my laptop, SIZE=80k (corresponding to a 10GB file with a 128KiB
        # segsize) takes:
        #  7s to build the (complete) HashTree
        #  13s to set up the dictionary
        #  10s to run set_hashes()
        ht = make_tree(SIZE)
        iht = hashtree.IncompleteHashTree(SIZE)

        needed = set()
        for i in range(SIZE):
            needed.update(ht.needed_hashes(i, True))
        all = dict([ (i, ht[i]) for i in needed])
        iht.set_hashes(hashes=all)

    def test_check(self):
        # first create a complete hash tree
        ht = make_tree(6)
        # then create a corresponding incomplete tree
        iht = hashtree.IncompleteHashTree(6)

        # suppose we wanted to validate leaf[0]
        #  leaf[0] is the same as node[7]
        self.failUnlessEqual(iht.needed_hashes(0), set([8, 4, 2]))
        self.failUnlessEqual(iht.needed_hashes(0, True), set([7, 8, 4, 2]))
        self.failUnlessEqual(iht.needed_hashes(1), set([7, 4, 2]))
        iht[0] = ht[0] # set the root
        self.failUnlessEqual(iht.needed_hashes(0), set([8, 4, 2]))
        self.failUnlessEqual(iht.needed_hashes(1), set([7, 4, 2]))
        iht[5] = ht[5]
        self.failUnlessEqual(iht.needed_hashes(0), set([8, 4, 2]))
        self.failUnlessEqual(iht.needed_hashes(1), set([7, 4, 2]))

        # reset
        iht = hashtree.IncompleteHashTree(6)

        current_hashes = list(iht)
        # this should fail because there aren't enough hashes known
        try:
            iht.set_hashes(leaves={0: tagged_hash("tag", "0")})
        except hashtree.NotEnoughHashesError:
            pass
        else:
            self.fail("didn't catch not enough hashes")

        # and the set of hashes stored in the tree should still be the same
        self.failUnlessEqual(list(iht), current_hashes)
        # and we should still need the same
        self.failUnlessEqual(iht.needed_hashes(0), set([8, 4, 2]))

        chain = {0: ht[0], 2: ht[2], 4: ht[4], 8: ht[8]}
        # this should fail because the leaf hash is just plain wrong
        try:
            iht.set_hashes(chain, leaves={0: tagged_hash("bad tag", "0")})
        except hashtree.BadHashError:
            pass
        else:
            self.fail("didn't catch bad hash")

        # this should fail because we give it conflicting hashes: one as an
        # internal node, another as a leaf
        try:
            iht.set_hashes(chain, leaves={1: tagged_hash("bad tag", "1")})
        except hashtree.BadHashError:
            pass
        else:
            self.fail("didn't catch bad hash")

        bad_chain = chain.copy()
        bad_chain[2] = ht[2] + "BOGUS"

        # this should fail because the internal hash is wrong
        try:
            iht.set_hashes(bad_chain, leaves={0: tagged_hash("tag", "0")})
        except hashtree.BadHashError:
            pass
        else:
            self.fail("didn't catch bad hash")

        # this should succeed
        try:
            iht.set_hashes(chain, leaves={0: tagged_hash("tag", "0")})
        except hashtree.BadHashError, e:
            self.fail("bad hash: %s" % e)

        self.failUnlessEqual(ht.get_leaf(0), tagged_hash("tag", "0"))
        self.failUnlessRaises(IndexError, ht.get_leaf, 8)

        # this should succeed too
        try:
            iht.set_hashes(leaves={1: tagged_hash("tag", "1")})
        except hashtree.BadHashError:
            self.fail("bad hash")

        # this should fail because we give it hashes that conflict with some
        # that we added successfully before
        try:
            iht.set_hashes(leaves={1: tagged_hash("bad tag", "1")})
        except hashtree.BadHashError:
            pass
        else:
            self.fail("didn't catch bad hash")

        # now that leaves 0 and 1 are known, some of the internal nodes are
        # known
        self.failUnlessEqual(iht.needed_hashes(4), set([12, 6]))
        chain = {6: ht[6], 12: ht[12]}

        # this should succeed
        try:
            iht.set_hashes(chain, leaves={4: tagged_hash("tag", "4")})
        except hashtree.BadHashError, e:
            self.fail("bad hash: %s" % e)
