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
    def testCreate(self):
        # try out various sizes
        ht = make_tree(6)
        ht = make_tree(8)
        ht = make_tree(9)
        root = ht[0]
        self.failUnlessEqual(len(root), 32)

    def testDump(self):
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

    def testCheck(self):
        # first create a complete hash tree
        ht = make_tree(6)
        # then create a corresponding incomplete tree
        iht = hashtree.IncompleteHashTree(6)

        # suppose we wanted to validate leaf[0]
        #  leaf[0] is the same as node[7]
        self.failUnlessEqual(iht.needed_hashes(leaves=[0]), set([8, 4, 2, 0]))
        self.failUnlessEqual(iht.needed_hashes(leaves=[1]), set([7, 4, 2, 0]))
        iht.set_hashes({0: ht[0]}) # set the root
        self.failUnlessEqual(iht.needed_hashes(leaves=[0]), set([8, 4, 2]))
        self.failUnlessEqual(iht.needed_hashes(leaves=[1]), set([7, 4, 2]))
        iht.set_hashes({5: ht[5]})
        self.failUnlessEqual(iht.needed_hashes(leaves=[0]), set([8, 4, 2]))
        self.failUnlessEqual(iht.needed_hashes(leaves=[1]), set([7, 4, 2]))

        current_hashes = list(iht)
        try:
            # this should fail because there aren't enough hashes known
            iht.set_hashes(leaves={0: tagged_hash("tag", "0")},
                           must_validate=True)
        except hashtree.NotEnoughHashesError:
            pass
        else:
            self.fail("didn't catch not enough hashes")

        # and the set of hashes stored in the tree should still be the same
        self.failUnlessEqual(list(iht), current_hashes)

        # provide the missing hashes
        iht.set_hashes({2: ht[2], 4: ht[4], 8: ht[8]})
        self.failUnlessEqual(iht.needed_hashes(leaves=[0]), set())

        try:
            # this should fail because the hash is just plain wrong
            iht.set_hashes(leaves={0: tagged_hash("bad tag", "0")})
        except hashtree.BadHashError:
            pass
        else:
            self.fail("didn't catch bad hash")

        try:
            # this should succeed
            iht.set_hashes(leaves={0: tagged_hash("tag", "0")})
        except hashtree.BadHashError, e:
            self.fail("bad hash: %s" % e)

        try:
            # this should succeed too
            iht.set_hashes(leaves={1: tagged_hash("tag", "1")})
        except hashtree.BadHashError:
            self.fail("bad hash")

        # giving it a bad internal hash should also cause problems
        iht.set_hashes({13: tagged_hash("bad tag", "x")})
        try:
            iht.set_hashes({14: tagged_hash("tag", "14")})
        except hashtree.BadHashError:
            pass
        else:
            self.fail("didn't catch bad hash")
        # undo our damage
        iht[13] = None

        self.failUnlessEqual(iht.needed_hashes(leaves=[4]), set([12, 6]))

        iht.set_hashes({6: ht[6], 12: ht[12]})
        try:
            # this should succeed
            iht.set_hashes(leaves={4: tagged_hash("tag", "4")})
        except hashtree.BadHashError, e:
            self.fail("bad hash: %s" % e)

