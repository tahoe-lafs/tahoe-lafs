
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

import sha
import os
#import os.path

from allmydata.util import bencode

__all__ = ['CompleteChunkFile', 'PartialChunkFile']

__version__ = '1.0.0'

BLOCK_SIZE     = 65536
MAX_CHUNK_SIZE = BLOCK_SIZE + 4096

def hash(s):
  """
  Cryptographic hash function used by this module.
  """
  return sha.new(s).digest()


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
    Return a list of nodes that are necessary for the hash chain.
    """
    if i < 0 or i >= len(self):
      raise IndexError('index out of range: ' + repr(i))
    needed = []
    here = i
    while here != 0:
      needed.append(self.sibling(here))
      here = self.parent(here)
    return needed


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

    The augmenting is done so that if the augmented element is at
    index C{i}, then its value is C{hash(bencode.bencode((i, '')))}.
    """
    # Augment the list.
    start = len(L)
    end   = roundup_pow2(len(L))
    L     = L + [None] * (end - start)
    for i in range(start, end):
      L[i] = hash(bencode.bencode((i, '')))
    # Form each row of the tree.
    rows = [L]
    while len(rows[-1]) != 1:
      last = rows[-1]
      rows += [[hash(last[2*i] + last[2*i+1]) for i in xrange(len(last)//2)]]
    # Flatten the list of rows into a single list.
    rows.reverse()
    self[:] = sum(rows, [])


class BlockFile:
  """
  Reads and writes blocks of data to a binary file.

  It is assumed that the binary file does not change in size.

  @ivar file_name:    Full path to file.
  @ivar file_size:    Size of file in bytes.
  @ivar block_size:   Size of each block.
  """
  def __init__(self, file_name, mode, block_size, file_size=None):
    """
    Initialize block reader or writer on given file name.

    If mode is 'r', the file must already exist and it is opened for
    reading only.  If mode is 'w', the file will be created with size
    C{file_size} if it does not exist, and it is opened for reading
    and writing.

    Note that C{file_size} is ignored if the file already exists.
    """
    self.mode = mode
    self.file_name = os.path.abspath(file_name)
    assert self.mode in ['r', 'w']

    if mode == 'r':
      f = open(self.file_name, 'rb')
      f.close()

    # Create file if it doesn't exist.
    created = False
    if mode == 'w' and not os.path.exists(self.file_name):
      created = True
      buf = ' ' * 1024
      f = open(self.file_name, 'wb')
      for i in xrange(file_size // len(buf)):
        f.write(buf)
      f.write(' ' * (file_size % len(buf)))
      f.close()

    self.file_size = os.stat(self.file_name).st_size
    if created:
      assert self.file_size == file_size
    self.block_size = block_size
    self.__block_count = self.file_size // self.block_size
    if self.file_size % self.block_size == 0:
      self.last_block_size = self.block_size
    else:
      self.last_block_size = self.file_size % self.block_size
      self.__block_count += 1

  def __getitem__(self, i):
    """
    Get block i.
    """
    if i < 0 or i >= len(self):
      raise IndexError('block index out of range: ' + repr(i))
    f = open(self.file_name, 'rb')
    try:
      f.seek(i * self.block_size)
      ans = f.read(self.block_size)
    finally:
      f.close()
    return ans

  def __setitem__(self, i, s):
    """
    Set block i.
    """
    if self.mode != 'w':
      raise ValueError('file opened for reading only')
    if i < 0 or i >= len(self):
      raise IndexError('block index out of range: ' + repr(i))
    if i < len(self) - 1:
      if len(s) != self.block_size:
        raise ValueError('length of value must equal block_size')
    else:
      if len(s) != self.last_block_size:
        raise ValueError('length of value must equal last_block_size')
    f = open(self.file_name, 'rb+')
    try:
      f.seek(i * self.block_size)
      f.write(s)
    finally:
      f.close()

  def __len__(self):
    """
    Get number of blocks.
    """
    return int(self.__block_count)


class MetaFile(CompleteBinaryTreeMixin):
  """
  A L{HashTree} stored on disk, with a timestamp.

  The list of hashes can be accessed using subscripting and
  C{__len__}, in the same manner as for L{HashTree}.

  Note that the constructor takes the entire list associated with
  the L{HashTree}, not just the bottom row of the tree.

  @ivar meta_name: Full path to metafile.
  """
  def __init__(self, meta_name, mode, L=None):
    """
    Open an existing meta-file for reading or writing.

    If C{mode} is 'r', the meta-file must already exist and it is
    opened for reading only, and the list C{L} is ignored.  If C{mode}
    is 'w', the file will be created if it does not exist (from the
    list of hashes given in C{L}), and it is opened for reading and
    writing.
    """
    self.meta_name = os.path.abspath(meta_name)
    self.mode = mode
    assert self.mode in ['r', 'w']

    # A timestamp is stored at index 0.  The MetaFile instance
    # offsets all indices passed to __getitem__, __setitem__ by
    # this offset, and pretends it has length equal to
    # self.sublength.
    self.offset = 1

    if self.mode == 'w':
      suggested_length = len(hash('')) * (len(L)+self.offset)
    else:
      suggested_length = None

    created = False
    if self.mode == 'w' and not os.path.exists(self.meta_name):
      created = True

    self.block_file = BlockFile(self.meta_name, self.mode,
                                len(hash('')),
                                suggested_length)
    self.sublength = len(self.block_file) - self.offset

    if created:
      for i in xrange(len(L)):
        self.block_file[i + self.offset] = L[i]

  def __getitem__(self, i):
    if i < 0 or i >= self.sublength:
      raise IndexError('bad meta-file block index')
    return self.block_file[i + self.offset]

  def __setitem__(self, i, value):
    if i < 0 or i >= self.sublength:
      raise IndexError('bad meta-file block index')
    self.block_file[i + self.offset] = value

  def __len__(self):
    return self.sublength

  def set_timestamp(self, file_name):
    """
    Set meta file's timestamp equal to the timestamp for C{file_name}.
    """
    st = os.stat(file_name)
    timestamp = bencode.bencode((st.st_size, st.st_mtime))
    self.block_file[0] = sha.new(timestamp).digest()

  def check_timestamp(self, file_name):
    """
    True if meta file's timestamp equals timestamp for C{file_name}.
    """
    st = os.stat(file_name)
    timestamp = bencode.bencode((st.st_size, st.st_mtime))
    return self.block_file[0] == sha.new(timestamp).digest()


class CompleteChunkFile(BlockFile):
  """
  Reads chunks from a fully-downloaded file.

  A chunk C{i} is created from block C{i}.  Block C{i} is unencoded
  data read from the file by the L{BlockFile}.  Chunk C{i} is
  an encoded string created from block C{i}.

  Chunks can be read using list subscripting.  The total number of
  chunks (equals the total number of blocks) is given by L{__len__}.

  @ivar file_name: Full path to file.
  @ivar file_size: Size of file in bytes.
  @ivar file_hash: Hash of file.
  @ivar meta_name: Full path to metafile, or C{None}.
  @ivar tree:      L{HashTree} or L{MetaFile} instance for the file.
                   One can extract a hash from any node in the hash
                   tree.
  """

  def __init__(self, file_name, meta_name=None, callback=None):
    """
    Initialize reader on the given file name.

    The entire file will be read and the hash will be computed from
    the file.  This may take a long time, so C{callback()} is called
    frequently during this process.  This allows you to reduce CPU
    usage if you wish.

    The C{meta_name} argument is optional.  If it is specified, then the
    hashes for C{file_name} will be stored under the file
    C{meta_name}.  If a C{CompleteChunkFile} is created on the same
    file and metafile in the future, then the hashes will not need to
    be recomputed and the constructor will return instantly.  The
    metafile contains a file and date stamp, so that if the file stored
    in C{file_name} is modified, then the hashes will be recomputed.
    """
    BlockFile.__init__(self, file_name, 'r', block_size=65536)

    # Whether we need to compute the hash tree
    compute_tree = False

    self.meta_name = meta_name
    if self.meta_name != None:
      self.meta_name = os.path.abspath(self.meta_name)
    self.meta = None
    if self.meta_name == None:
      compute_tree = True
    else:
      try:
        meta = MetaFile(self.meta_name, 'r')
        assert meta.check_timestamp(self.file_name)
      except (IOError, AssertionError):
        compute_tree = True

    # Compute the hash tree if needed.
    if compute_tree:
      chunk_hashes = [None] * len(self)
      for i in xrange(len(self)):
        triple = (self.file_size, i, BlockFile.__getitem__(self, i))
        chunk_hashes[i] = hash(bencode.bencode(triple))
        if callback:
          callback()
      self.tree = HashTree(chunk_hashes)
      del chunk_hashes

    # If a meta-file was given, make self.tree be a MetaFile instance.
    if self.meta_name != None:
      if compute_tree:
        # Did we compute the hash tree?  Then store it to disk.
        self.tree = MetaFile(self.meta_name, 'w', self.tree)
        # Update its timestamp to be consistent with the file we
        # just hashed.
        self.tree.set_timestamp(self.file_name)
      else:
        # Read existing file from disk.
        self.tree = MetaFile(self.meta_name, 'r')

    self.file_hash = self.tree[0]

  def __getitem__(self, i):
    """
    Get chunk C{i}.

    Raises C{ValueError} if the file's contents changed since the
    CompleteFileChunkReader was instantiated.
    """
    return encode_chunk(BlockFile.__getitem__(self, i), i,
                        self.file_size, self.tree)


def encode_chunk(block, index, file_size, tree):
  """
  Encode a chunk.

  Given a block at index C{index} in a file with size C{file_size},
  and a L{HashTree} or L{MetaFile} instance C{tree}, computes and
  returns a chunk string for the given block.

  The C{tree} argument needs to have correct hashes only at certain
  indices.  Check out the code for details.  In any case, if a hash
  is wrong an exception will be raised.
  """
  block_count = (len(tree) + 1) // 2
  if index < 0 or index >= block_count:
    raise IndexError('block index out of range: ' + repr(index))

  suffix = bencode.bencode((file_size, index, block))
  current = len(tree) - block_count + index
  prefix = []
  while current > 0:
    sibling = tree.sibling(current)
    prefix += [tree[current], tree[sibling]]
    current = tree.parent(current)      
  prefix = ''.join(prefix)

  # Encode the chunk
  chunk = bencode.bencode((prefix, suffix))

  # Check to make sure it decodes properly.
  decode_chunk(chunk, file_size, tree)
  return chunk


def decode_chunk(chunk, file_size, tree):
  """
  Decode a chunk.

  Given file with size C{file_size} and a L{HashTree} or L{MetaFile}
  instance C{tree}, return C{(index, block, tree_items)}.  Here
  C{index} is the block index where string C{block} should be placed
  in the file.  Also C{tree_items} is a dict mapping indices within
  the L{HashTree} or L{MetaFile} tree object associated with the
  given file to the corresponding hashes at those indices.  These
  have been verified against the file's hash, so it is known that
  they are correct.

  Raises C{ValueError} if chunk verification fails.
  """
  file_hash   = tree[0]
  block_count = (len(tree) + 1) // 2
  try:
    # Decode the chunk
    try:
      (prefix, suffix) = bencode.bdecode(chunk)
    except:
      raise AssertionError()

    assert isinstance(prefix, str)
    assert isinstance(suffix, str)

    # Verify the suffix against the hashes in the prefix.
    hash_len = len(hash(''))
    L = [prefix[hash_len*i:hash_len*(i+1)] for i in range(len(prefix)//hash_len)]
    L += [file_hash]
    assert L[0] == hash(suffix)
    branches = []
    for i in range(0, len(L)-1, 2):
      if hash(L[i] + L[i+1]) == L[i+2]:
        branches += [0]
      elif hash(L[i+1] + L[i]) == L[i+2]:
        branches += [1]
      else:
        raise AssertionError()

    # Decode the suffix
    try:
      (claim_file_size, claim_index, block) = bencode.bdecode(suffix)
    except:
      raise AssertionError()

    assert isinstance(claim_file_size, int) or isinstance(claim_file_size, long)
    assert isinstance(claim_index, int) or isinstance(claim_index, long)
    assert isinstance(block, str)

    assert file_size == claim_file_size

    # Compute the index of the block, and check it.
    found_index = sum([branches[i]*2**i for i in range(len(branches))])
    assert found_index == claim_index

    # Now fill in the tree_items dict.
    tree_items = {}
    current = (len(tree) - block_count) + found_index
    i = 0
    while current > 0 and i + 1 < len(L):
      tree_items[current] = L[i]
      # Next item is our sibling.
      tree_items[tree.sibling(current)] = L[i+1]
      i += 2
      current = tree.parent(current)

    return (found_index, block, tree_items)
  except AssertionError:
    raise ValueError('corrupt chunk')


class PartialChunkFile(BlockFile):
  """
  Reads and writes chunks to a partially downloaded file.

  @ivar file_name: Full path to file.
  @ivar file_size: Size of file in bytes.
  @ivar file_hash: Hash of file.
  @ivar meta_name: Full path to metafile.
  @ivar tree:      L{MetaFile} instance for the file.
                   The hashes in this hash tree are valid only for
                   nodes that we have been sent hashes for.
  """
  def __init__(self, file_name, meta_name, file_hash=None, file_size=None):
    """
    Initialize reader/writer for the given file name and metafile name.

    If neither C{file_name} nor C{meta_file} exist, then both are
    created.  The C{file_hash} and C{file_size} arguments are used to
    initialize the two files.

    If both C{file_name} and C{meta_file} exist, then the hash and
    file size arguments are ignored, and those values are instead read
    from the files.

    If one file exists and the other does not, an C{IOError} is raised.
    """
    self.meta_name = os.path.abspath(meta_name)
    meta_exists = os.path.exists(self.meta_name)
    file_exists = os.path.exists(os.path.abspath(file_name))

    BlockFile.__init__(self, os.path.abspath(file_name), 'w',
                       BLOCK_SIZE, file_size)

    if file_exists and not meta_exists:
      raise IOError('metafile ' + repr(self.meta_name) +
                    ' missing for file ' + repr(self.file_name))
    if meta_exists and not file_exists:
      raise IOError('file ' + repr(self.file_name) +
                    ' missing for metafile ' + repr(self.meta_name))
    tree_count = 2 * roundup_pow2(len(self)) - 1
    self.tree = MetaFile(self.meta_name, 'w', [hash('')] * tree_count)

    if not meta_exists and not file_exists:
      self.tree[0] = file_hash
      
    self.file_hash = self.tree[0]

  def __getitem__(self, i):
    """
    Get chunk C{i}.

    Raises C{ValueError} if chunk has not yet been downloaded or is
    corrupted.
    """
    return encode_chunk(BlockFile.__getitem__(self, i), i,
                        self.file_size, self.tree)

  def __setitem__(self, i, chunk):
    """
    Set chunk C{i}.

    Raises C{ValueError} if the chunk is invalid.
    """
    (index, block, tree_items) = decode_chunk(chunk,
                                 self.file_size, self.tree)
    if index != i:
      raise ValueError('incorrect index for chunk')
    BlockFile.__setitem__(self, index, block)
    for (tree_index, tree_value) in tree_items.items():
      self.tree[tree_index] = tree_value


def test(filename1='temp-out',  metaname1='temp-out.meta',
         filename2='temp-out2', metaname2='temp-out2.meta'):
  """
  Unit tests.
  """
  print 'Testing:'

  import random
  ntests = 100
  max_file_size = 200000

  # Test CompleteChunkFile.

  if os.path.exists(metaname1):
    os.remove(metaname1)

  for i in range(ntests):
    fsize = random.randrange(max_file_size)
    # Make some random string of size 'fsize' to go in the file.
    s = ''.join([sha.new(str(j)).digest() for j in range(fsize//20+1)])
    assert len(s) >= fsize
    s = s[:fsize]
    f = open(filename1, 'wb')
    f.write(s)
    f.close()
    C = CompleteChunkFile(filename1)
    for j in range(len(C)):
      C[j]
    C = CompleteChunkFile(filename1, metaname1)
    for j in range(len(C)):
      C[j]
    C = CompleteChunkFile(filename1, metaname1)
    for j in range(len(C)):
      C[j]
    os.remove(metaname1)

  os.remove(filename1)

  print '  CompleteChunkFile:      OK'

  # Test PartialChunkFile

  for i in range(ntests):
    fsize = random.randrange(max_file_size)
    # Make some random string of size 'fsize' to go in the file.
    s = ''.join([sha.new(str(j)).digest() for j in range(fsize//20+1)])
    assert len(s) >= fsize
    s = s[:fsize]
    f = open(filename1, 'wb')
    f.write(s)
    f.close()
    C1 = CompleteChunkFile(filename1)
    if os.path.exists(filename2):
      os.remove(filename2)

    if os.path.exists(metaname2):
      os.remove(metaname2)
    C2 = PartialChunkFile(filename2, metaname2, C1.file_hash, C1.file_size)
    assert len(C1) == len(C2)
    assert C2.tree[0] == C1.tree[0]
    for j in range(len(C2)):
      try:
        C2[j]
        ok = False
      except ValueError:
        ok = True
      if not ok:
        raise AssertionError()
    for j in range(len(C2)//2):
      k = random.randrange(len(C2))
      if len(C1) > 1:
        assert C1[k] != C1[(k+1)%len(C1)]
        try:
          C2[k] = C1[(k+1)%len(C1)]
          ok = False
        except ValueError:
          ok = True
        if not ok:
          raise AssertionError()
      C2[k] = C1[k]
      assert C2[k] == C1[k]
    for j in range(len(C2)):
      C2[j] = C1[j]
      assert C2[j] == C1[j]

  os.remove(filename1)
  os.remove(filename2)
  os.remove(metaname2)

  print '  PartialChunkFile:       OK'


if __name__ == '__main__':
  test()
