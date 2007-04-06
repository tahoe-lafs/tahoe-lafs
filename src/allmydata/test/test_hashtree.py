# -*- test-case-name: allmydata.test.test_hashtree -*-

from twisted.trial import unittest

from allmydata.util.hashutil import tagged_hash
from allmydata import chunk


def make_tree(numleaves):
    leaves = ["%d" % i for i in range(numleaves)]
    leaf_hashes = [tagged_hash("tag", leaf) for leaf in leaves]
    ht = chunk.HashTree(leaf_hashes)
    return ht

class Complete(unittest.TestCase):
    def testCreate(self):
        # try out various sizes
        ht = make_tree(6)
        ht = make_tree(8)
        ht = make_tree(9)
        root = ht[0]
        self.failUnlessEqual(len(root), 32)

class Incomplete(unittest.TestCase):
    def testCheck(self):
        # first create a complete hash tree
        ht = make_tree(6)
        # then create a corresponding incomplete tree
        iht = chunk.IncompleteHashTree(6)

        # suppose we wanted to validate leaf[0]
        #  leaf[0] is the same as node[7]
        self.failUnlessEqual(iht.needed_hashes(0), set([8, 4, 2, 0]))
        self.failUnlessEqual(iht.needed_hashes(1), set([7, 4, 2, 0]))
        iht.set_hash(0, ht[0])
        self.failUnlessEqual(iht.needed_hashes(0), set([8, 4, 2]))
        self.failUnlessEqual(iht.needed_hashes(1), set([7, 4, 2]))
        iht.set_hash(5, ht[5])
        self.failUnlessEqual(iht.needed_hashes(0), set([8, 4, 2]))
        self.failUnlessEqual(iht.needed_hashes(1), set([7, 4, 2]))

        try:
            # this should fail because there aren't enough hashes known
            iht.set_leaf(0, tagged_hash("tag", "0"))
        except chunk.NotEnoughHashesError:
            pass
        else:
            self.fail("didn't catch not enough hashes")

        # provide the missing hashes
        iht.set_hash(2, ht[2])
        iht.set_hash(4, ht[4])
        iht.set_hash(8, ht[8])
        self.failUnlessEqual(iht.needed_hashes(0), set([]))

        try:
            # this should fail because the hash is just plain wrong
            iht.set_leaf(0, tagged_hash("bad tag", "0"))
        except chunk.BadHashError:
            pass
        else:
            self.fail("didn't catch bad hash")

        try:
            # this should succeed
            iht.set_leaf(0, tagged_hash("tag", "0"))
        except chunk.BadHashError, e:
            self.fail("bad hash: %s" % e)

        try:
            # this should succeed too
            iht.set_leaf(1, tagged_hash("tag", "1"))
        except chunk.BadHashError:
            self.fail("bad hash")

        # giving it a bad internal hash should also cause problems
        iht.set_hash(2, tagged_hash("bad tag", "x"))
        try:
            iht.set_leaf(0, tagged_hash("tag", "0"))
        except chunk.BadHashError:
            pass
        else:
            self.fail("didn't catch bad hash")
        # undo our damage
        iht.set_hash(2, ht[2])

        self.failUnlessEqual(iht.needed_hashes(4), set([12, 6]))

        iht.set_hash(6, ht[6])
        iht.set_hash(12, ht[12])
        try:
            # this should succeed
            iht.set_leaf(4, tagged_hash("tag", "4"))
        except chunk.BadHashError, e:
            self.fail("bad hash: %s" % e)

