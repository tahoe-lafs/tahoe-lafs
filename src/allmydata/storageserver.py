import os, re

from foolscap import Referenceable
from twisted.application import service

from zope.interface import implements
from allmydata.interfaces import RIStorageServer, RIBucketWriter, \
     RIBucketReader
from allmydata import interfaces
from allmydata.util import bencode, fileutil, idlib
from allmydata.util.assertutil import _assert, precondition

# store/
# store/incoming # temp dirs named $VERIFIERID/$SHARENUM which will be moved to store/$VERIFIERID/$SHARENUM on success
# store/$VERIFIERID
# store/$VERIFIERID/$SHARENUM
# store/$VERIFIERID/$SHARENUM/blocksize
# store/$VERIFIERID/$SHARENUM/data
# store/$VERIFIERID/$SHARENUM/blockhashes
# store/$VERIFIERID/$SHARENUM/sharehashtree

# $SHARENUM matches this regex:
NUM_RE=re.compile("[0-9]*")

class BucketWriter(Referenceable):
    implements(RIBucketWriter)

    def __init__(self, incominghome, finalhome, blocksize):
        self.incominghome = incominghome
        self.finalhome = finalhome
        self.blocksize = blocksize
        self.closed = False
        fileutil.make_dirs(incominghome)
        self._write_file('blocksize', str(blocksize))

    def _write_file(self, fname, data):
        open(os.path.join(self.incominghome, fname), 'wb').write(data)

    def remote_put_block(self, segmentnum, data):
        precondition(not self.closed)
        assert len(data) == self.blocksize
        f = open(os.path.join(self.incominghome, 'data'), 'wb')
        f.seek(self.blocksize*segmentnum)
        f.write(data)

    def remote_put_block_hashes(self, blockhashes):
        precondition(not self.closed)
        # TODO: verify the length of blockhashes.
        # TODO: tighten foolscap schema to require exactly 32 bytes.
        self._write_file('blockhashes', ''.join(blockhashes))

    def remote_put_share_hashes(self, sharehashes):
        precondition(not self.closed)
        self._write_file('sharehashes', bencode.bencode(sharehashes))

    def remote_close(self):
        precondition(not self.closed)
        # TODO assert or check the completeness and consistency of the data that has been written
        fileutil.make_dirs(os.path.dirname(self.finalhome))
        fileutil.rename(self.incominghome, self.finalhome)
        try:
            os.rmdir(os.path.dirname(self.incominghome))
        except OSError:
            # Perhaps the directory wasn't empty.  In any case, ignore the error.
            pass
            
        self.closed = True

def str2l(s):
    """ split string (pulled from storage) into a list of blockids """
    return [ s[i:i+interfaces.HASH_SIZE] for i in range(0, len(s), interfaces.HASH_SIZE) ]

class BucketReader(Referenceable):
    implements(RIBucketReader)

    def __init__(self, home):
        self.home = home
        self.blocksize = int(self._read_file('blocksize'))

    def _read_file(self, fname):
        return open(os.path.join(self.home, fname), 'rb').read()

    def remote_get_block(self, blocknum):
        f = open(os.path.join(self.home, 'data'), 'rb')
        f.seek(self.blocksize * blocknum)
        return f.read(self.blocksize) # this might be short for the last block

    def remote_get_block_hashes(self):
        return str2l(self._read_file('blockhashes'))

    def remote_get_share_hashes(self):
        hashes = bencode.bdecode(self._read_file('sharehashes'))
        # tuples come through bdecode(bencode()) as lists, which violates the
        # schema
        return [tuple(i) for i in hashes]

class StorageServer(service.MultiService, Referenceable):
    implements(RIStorageServer)
    name = 'storageserver'

    def __init__(self, storedir):
        fileutil.make_dirs(storedir)
        self.storedir = storedir
        self.incomingdir = os.path.join(storedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)

        service.MultiService.__init__(self)

    def _clean_incomplete(self):
        fileutil.rm_dir(self.incomingdir)

    def remote_allocate_buckets(self, verifierid, sharenums, sharesize,
                                blocksize, canary):
        alreadygot = set()
        bucketwriters = {} # k: shnum, v: BucketWriter
        for shnum in sharenums:
            incominghome = os.path.join(self.incomingdir, idlib.b2a(verifierid), "%d"%shnum)
            finalhome = os.path.join(self.storedir, idlib.b2a(verifierid), "%d"%shnum)
            if os.path.exists(incominghome) or os.path.exists(finalhome):
                alreadygot.add(shnum)
            else:
                bucketwriters[shnum] = BucketWriter(incominghome, finalhome, blocksize)
            
        return alreadygot, bucketwriters

    def remote_get_buckets(self, verifierid):
        bucketreaders = {} # k: sharenum, v: BucketReader
        verifierdir = os.path.join(self.storedir, idlib.b2a(verifierid))
        try:
            for f in os.listdir(verifierdir):
                if NUM_RE.match(f):
                    bucketreaders[int(f)] = BucketReader(os.path.join(verifierdir, f))
        except OSError:
            # Commonly caused by there being no buckets at all.
            pass

        return bucketreaders
