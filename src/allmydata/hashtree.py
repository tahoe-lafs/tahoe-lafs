# -*- test-case-name: allmydata.test.test_hashtree -*-

from allmydata.util import mathutil # from the pyutil library

"""
Read and write chunks from files.

Version 1.0.0.

A file is divided into blocks, each of which has size L{BLOCK_SIZE}
(except for the last block, which may be smaller).  Blocks are encoded
into chunks.  One publishes the hash of the entire file.  Clients
who want to download the file first obtain the hash, then the clients
can receive chunks in any order.  Cryptographic hashing is used to
verify each received chunk before writing to disk.  Thus it is
impossible to download corrupt data if one has the correct file hash.

One obtains the hash of a complete file via
L{CompleteChunkFile.file_hash}.  One can read chunks from a complete
file by the sequence operations of C{len()} and subscripting on a
L{CompleteChunkFile} object.  One can open an empty or partially
downloaded file with L{PartialChunkFile}, and read and write chunks
to this file.  A chunk will fail to write if its contents and index
are not consistent with the overall file hash passed to
L{PartialChunkFile} when the partial chunk file was first created.

The chunks have an overhead of less than 4% for files of size
less than C{10**20} bytes.

Benchmarks:

 - On a 3 GHz Pentium 3, it took 3.4 minutes to first make a
   L{CompleteChunkFile} object for a 4 GB file.  Up to 10 MB of
   memory was used as the constructor ran.  A metafile filename
   was passed to the constructor, and so the hash information was
   written to the metafile.  The object used a negligible amount
   of memory after the constructor was finished.
 - Creation of L{CompleteChunkFile} objects in future runs of the
   program took negligible time, since the hash information was
   already stored in the metafile.

@var BLOCK_SIZE:     Size of a block.  See L{BlockFile}.
@var MAX_CHUNK_SIZE: Upper bound on the size of a chunk.
                     See L{CompleteChunkFile}.

free (adj.): unencumbered; not under the control of others
Written by Connelly Barnes in 2005 and released into the
public domain  with no warranty of any kind, either expressed
or implied.  It probably won't make your computer catch on fire,
or eat  your children, but it might.  Use at your own risk.
"""

from allmydata.util import base32
from allmydata.util.hashutil import tagged_hash, tagged_pair_hash

__version__ = '1.0.0-allmydata'

BLOCK_SIZE     = 65536
MAX_CHUNK_SIZE = BLOCK_SIZE + 4096

def roundup_pow2(x):
    """
    Round integer C{x} up to the nearest power of 2.
    """
    ans = 1
    while ans < x:
        ans *= 2
    return ans


class CompleteBinaryTreeMixin:
    """
    Adds convenience methods to a complete binary tree.

    Assumes the total number of elements in the binary tree may be
    accessed via C{__len__}, and that each element can be retrieved
    using list subscripting.

    Tree is indexed like so::


                        0
                   /        \
                1               2
             /    \          /    \
           3       4       5       6
          / \     / \     / \     / \
         7   8   9   10  11  12  13  14

    """

    def parent(self, i):
        """
        Index of the parent of C{i}.
        """
        if i < 1 or (hasattr(self, '__len__') and i >= len(self)):
            raise IndexError('index out of range: ' + repr(i))
        return (i - 1) // 2

    def lchild(self, i):
        """
        Index of the left child of C{i}.
        """
        ans = 2 * i + 1
        if i < 0 or (hasattr(self, '__len__') and ans >= len(self)):
            raise IndexError('index out of range: ' + repr(i))
        return ans

    def rchild(self, i):
        """
        Index of right child of C{i}.
        """
        ans = 2 * i + 2
        if i < 0 or (hasattr(self, '__len__') and ans >= len(self)):
            raise IndexError('index out of range: ' + repr(i))
        return ans

    def sibling(self, i):
        """
        Index of sibling of C{i}.
        """
        parent = self.parent(i)
        if self.lchild(parent) == i:
            return self.rchild(parent)
        else:
            return self.lchild(parent)

    def needed_for(self, i):
        """
        Return a list of node indices that are necessary for the hash chain.
        """
        if i < 0 or i >= len(self):
            raise IndexError('index out of range: 0 >= %s < %s' % (i, len(self)))
        needed = []
        here = i
        while here != 0:
            needed.append(self.sibling(here))
            here = self.parent(here)
        return needed

    def depth_first(self, i=0):
        yield i, 0
        try:
            for child,childdepth in self.depth_first(self.lchild(i)):
                yield child, childdepth+1
        except IndexError:
            pass
        try:
            for child,childdepth in self.depth_first(self.rchild(i)):
                yield child, childdepth+1
        except IndexError:
            pass

    def dump(self):
        lines = []
        for i,depth in self.depth_first():
            lines.append("%s%3d: %s" % ("  "*depth, i,
                                        base32.b2a_or_none(self[i])))
        return "\n".join(lines) + "\n"

    def get_leaf_index(self, leafnum):
        return self.first_leaf_num + leafnum

    def get_leaf(self, leafnum):
        return self[self.first_leaf_num + leafnum]

def depth_of(i):
    """Return the depth or level of the given node. Level 0 contains node 0
    Level 1 contains nodes 1 and 2. Level 2 contains nodes 3,4,5,6."""
    return mathutil.log_floor(i+1, 2)

def empty_leaf_hash(i):
    return tagged_hash('Merkle tree empty leaf', "%d" % i)
def pair_hash(a, b):
    return tagged_pair_hash('Merkle tree internal node', a, b)

class HashTree(CompleteBinaryTreeMixin, list):
    """
    Compute Merkle hashes at any node in a complete binary tree.

    Tree is indexed like so::


                        0
                   /        \
                1               2
             /    \          /    \
           3       4       5       6
          / \     / \     / \     / \
         7   8   9   10  11  12  13  14  <- List passed to constructor.

    """

    def __init__(self, L):
        """
        Create complete binary tree from list of hash strings.

        The list is augmented by hashes so its length is a power of 2, and
        then this is used as the bottom row of the hash tree.

        The augmenting is done so that if the augmented element is at index
        C{i}, then its value is C{hash(tagged_hash('Merkle tree empty leaf',
        '%d'%i))}.
        """

        # Augment the list.
        start = len(L)
        end   = roundup_pow2(len(L))
        self.first_leaf_num = end - 1
        L     = L + [None] * (end - start)
        for i in range(start, end):
            L[i] = empty_leaf_hash(i)
        # Form each row of the tree.
        rows = [L]
        while len(rows[-1]) != 1:
            last = rows[-1]
            rows += [[pair_hash(last[2*i], last[2*i+1])
                                for i in xrange(len(last)//2)]]
        # Flatten the list of rows into a single list.
        rows.reverse()
        self[:] = sum(rows, [])

    def needed_hashes(self, leafnum, include_leaf=False):
        """Which hashes will someone need to validate a given data block?

        I am used to answer a question: supposing you have the data block
        that is used to form leaf hash N, and you want to validate that it,
        which hashes would you need?

        I accept a leaf number and return a set of 'hash index' values, which
        are integers from 0 to len(self). In the 'hash index' number space,
        hash[0] is the root hash, while hash[len(self)-1] is the last leaf
        hash.

        This method can be used to find out which hashes you should request
        from some untrusted source (usually the same source that provides the
        data block), so you can minimize storage or transmission overhead. It
        can also be used to determine which hashes you should send to a
        remote data store so that it will be able to provide validatable data
        in the future.

        I will not include '0' (the root hash) in the result, since the root
        is generally stored somewhere that is more trusted than the source of
        the remaining hashes. I will include the leaf hash itself only if you
        ask me to, by passing include_leaf=True.
        """

        needed = set(self.needed_for(self.first_leaf_num + leafnum))
        if include_leaf:
            needed.add(self.first_leaf_num + leafnum)
        return needed


class NotEnoughHashesError(Exception):
    pass

class BadHashError(Exception):
    pass

class IncompleteHashTree(CompleteBinaryTreeMixin, list):
    """I am a hash tree which may or may not be complete. I can be used to
    validate inbound data from some untrustworthy provider who has a subset
    of leaves and a sufficient subset of internal nodes.

    Initially I am completely unpopulated. Over time, I will become filled
    with hashes, just enough to validate particular leaf nodes.

    If you desire to validate leaf number N, first find out which hashes I
    need by calling needed_hashes(N). This will return a list of node numbers
    (which will nominally be the sibling chain between the given leaf and the
    root, but if I already have some of those nodes, needed_hashes(N) will
    only return a subset). Obtain these hashes from the data provider, then
    tell me about them with set_hash(i, HASH). Once I have enough hashes, you
    can tell me the hash of the leaf with set_leaf_hash(N, HASH), and I will
    either return None or raise BadHashError.

    The first hash to be set will probably be 0 (the root hash), since this
    is the one that will come from someone more trustworthy than the data
    provider.

    """

    def __init__(self, num_leaves):
        L = [None] * num_leaves
        start = len(L)
        end   = roundup_pow2(len(L))
        self.first_leaf_num = end - 1
        L     = L + [None] * (end - start)
        rows = [L]
        while len(rows[-1]) != 1:
            last = rows[-1]
            rows += [[None for i in xrange(len(last)//2)]]
        # Flatten the list of rows into a single list.
        rows.reverse()
        self[:] = sum(rows, [])


    def needed_hashes(self, leafnum, include_leaf=False):
        """Which new hashes do I need to validate a given data block?

        I am much like HashTree.needed_hashes(), except that I don't include
        hashes that I already know about. When needed_hashes() is called on
        an empty IncompleteHashTree, it will return the same set as a
        HashTree of the same size. But later, once hashes have been added
        with set_hashes(), I will ask for fewer hashes, since some of the
        necessary ones have already been set.
        """

        maybe_needed = set(self.needed_for(self.first_leaf_num + leafnum))
        if include_leaf:
            maybe_needed.add(self.first_leaf_num + leafnum)
        return set([i for i in maybe_needed if self[i] is None])

    def _name_hash(self, i):
        name = "[%d of %d]" % (i, len(self))
        if i >= self.first_leaf_num:
            leafnum = i - self.first_leaf_num
            numleaves = len(self) - self.first_leaf_num
            name += " (leaf [%d] of %d)" % (leafnum, numleaves)
        return name

    def set_hashes(self, hashes={}, leaves={}):
        """Add a bunch of hashes to the tree.

        I will validate these to the best of my ability. If I already have a
        copy of any of the new hashes, the new values must equal the existing
        ones, or I will raise BadHashError. If adding a hash allows me to
        compute a parent hash, those parent hashes must match or I will raise
        BadHashError. If I raise BadHashError, I will forget about all the
        hashes that you tried to add, leaving my state exactly the same as
        before I was called. If I return successfully, I will remember all
        those hashes.

        I insist upon being able to validate all of the hashes that were
        given to me. If I cannot do this because I'm missing some hashes, I
        will raise NotEnoughHashesError (and forget about all the hashes that
        you tried to add). Note that this means that the root hash must
        either be included in 'hashes', or it must have been provided at some
        point in the past.

        'leaves' is a dictionary uses 'leaf index' values, which range from 0
        (the left-most leaf) to num_leaves-1 (the right-most leaf), and form
        the base of the tree. 'hashes' uses 'hash_index' values, which range
        from 0 (the root of the tree) to 2*num_leaves-2 (the right-most
        leaf). leaf[i] is the same as hash[num_leaves-1+i].

        The best way to use me is to start by obtaining the root hash from
        some 'good' channel and populate me with it:

         iht = IncompleteHashTree(numleaves)
         roothash = trusted_channel.get_roothash()
         iht.set_hashes(hashes={0: roothash})

        Then use the 'bad' channel to obtain data block 0 and the
        corresponding hash chain (a dict with the same hashes that
        needed_hashes(0) tells you, e.g. {0:h0, 2:h2, 4:h4, 8:h8} when
        len(L)=8). Hash the data block to create leaf0, then feed everything
        into set_hashes() and see if it raises an exception or not::

         otherhashes = untrusted_channel.get_hashes()
         # otherhashes.keys() should == iht.needed_hashes(leaves=[0])
         datablock0 = untrusted_channel.get_data(0)
         leaf0 = HASH(datablock0)
         # HASH() is probably hashutil.tagged_hash(tag, datablock0)
         iht.set_hashes(otherhashes, leaves={0: leaf0})

        If the set_hashes() call doesn't raise an exception, the data block
        was valid. If it raises BadHashError, then either the data block was
        corrupted or one of the received hashes was corrupted. If it raises
        NotEnoughHashesError, then the otherhashes dictionary was incomplete.
        """

        assert isinstance(hashes, dict)
        for h in hashes.values():
            assert isinstance(h, str)
        assert isinstance(leaves, dict)
        for h in leaves.values():
            assert isinstance(h, str)
        new_hashes = hashes.copy()
        for leafnum,leafhash in leaves.iteritems():
            hashnum = self.first_leaf_num + leafnum
            if hashnum in new_hashes:
                if new_hashes[hashnum] != leafhash:
                    raise BadHashError("got conflicting hashes in my "
                                       "arguments: leaves[%d] != hashes[%d]"
                                       % (leafnum, hashnum))
            new_hashes[hashnum] = leafhash

        remove_upon_failure = set() # we'll remove these if the check fails

        # visualize this method in the following way:
        #  A: start with the empty or partially-populated tree as shown in
        #     the HashTree docstring
        #  B: add all of our input hashes to the tree, filling in some of the
        #     holes. Don't overwrite anything, but new values must equal the
        #     existing ones. Mark everything that was added with a red dot
        #     (meaning "not yet validated")
        #  C: start with the lowest/deepest level. Pick any red-dotted node,
        #     hash it with its sibling to compute the parent hash. Add the
        #     parent to the tree just like in step B (if the parent already
        #     exists, the values must be equal; if not, add our computed
        #     value with a red dot). If we have no sibling, throw
        #     NotEnoughHashesError, since we won't be able to validate this
        #     node. Remove the red dot. If there was a red dot on our
        #     sibling, remove it too.
        #  D: finish all red-dotted nodes in one level before moving up to
        #     the next.
        #  E: if we hit NotEnoughHashesError or BadHashError before getting
        #     to the root, discard every hash we've added.

        try:
            num_levels = depth_of(len(self)-1)
            # hashes_to_check[level] is set(index). This holds the "red dots"
            # described above
            hashes_to_check = [set() for level in range(num_levels+1)]

            # first we provisionally add all hashes to the tree, comparing
            # any duplicates
            for i,h in new_hashes.iteritems():
                level = depth_of(i)

                if self[i]:
                    if self[i] != h:
                        raise BadHashError("new hash %s does not match "
                                           "existing hash %s at %s"
                                           % (base32.b2a(h),
                                              base32.b2a(self[i]),
                                              self._name_hash(i)))
                else:
                    hashes_to_check[level].add(i)
                    self[i] = h
                    remove_upon_failure.add(i)

            for level in reversed(range(len(hashes_to_check))):
                this_level = hashes_to_check[level]
                while this_level:
                    i = this_level.pop()
                    if i == 0:
                        # The root has no sibling. How lonely. You can't
                        # really *check* the root; you either accept it
                        # because the caller told you what it is by including
                        # it in hashes, or you accept it because you
                        # calculated it from its two children.
                        continue
                    siblingnum = self.sibling(i)
                    if self[siblingnum] is None:
                        # without a sibling, we can't compute a parent, and
                        # we can't verify this node
                        raise NotEnoughHashesError("unable to validate [%d]"%i)
                    parentnum = self.parent(i)
                    # make sure we know right from left
                    leftnum, rightnum = sorted([i, siblingnum])
                    new_parent_hash = pair_hash(self[leftnum], self[rightnum])
                    if self[parentnum]:
                        if self[parentnum] != new_parent_hash:
                            raise BadHashError("h([%d]+[%d]) != h[%d]" %
                                               (leftnum, rightnum, parentnum))
                    else:
                        self[parentnum] = new_parent_hash
                        remove_upon_failure.add(parentnum)
                        parent_level = depth_of(parentnum)
                        assert parent_level == level-1
                        hashes_to_check[parent_level].add(parentnum)

                    # our sibling is now as valid as this node
                    this_level.discard(siblingnum)
            # we're done!

        except (BadHashError, NotEnoughHashesError):
            for i in remove_upon_failure:
                self[i] = None
            raise
