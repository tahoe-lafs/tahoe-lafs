
import os, random, sha
from zope.interface import implements
from twisted.python import log
from twisted.internet import defer
from twisted.application import service

from allmydata.util import idlib, mathutil
from allmydata.util.assertutil import _assert
from allmydata import codec, hashtree
from allmydata.Crypto.Cipher import AES
from allmydata.uri import unpack_uri
from allmydata.interfaces import IDownloadTarget, IDownloader

class NotEnoughPeersError(Exception):
    pass

class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass


class Output:
    def __init__(self, downloadable, key):
        self.downloadable = downloadable
        self._decryptor = AES.new(key=key, mode=AES.MODE_CTR,
                                  counterstart="\x00"*16)
        self._verifierid_hasher = sha.new(netstring("allmydata_verifierid_v1"))
        self._fileid_hasher = sha.new(netstring("allmydata_fileid_v1"))
        self.length = 0

    def open(self):
        self.downloadable.open()

    def write(self, crypttext):
        self.length += len(crypttext)
        self._verifierid_hasher.update(crypttext)
        plaintext = self._decryptor.decrypt(crypttext)
        self._fileid_hasher.update(plaintext)
        self.downloadable.write(plaintext)

    def close(self):
        self.verifierid = self._verifierid_hasher.digest()
        self.fileid = self._fileid_hasher.digest()
        self.downloadable.close()

    def finish(self):
        return self.downloadable.finish()

class ValidatedBucket:
    """I am a front-end for a remote storage bucket, responsible for
    retrieving and validating data from that bucket.

    My get_block() method is used by BlockDownloaders.
    """

    def __init__(self, sharenum, bucket,
                 share_hash_tree, roothash,
                 num_blocks):
        self.sharenum = sharenum
        self.bucket = bucket
        self._share_hash = None # None means not validated yet
        self.share_hash_tree = share_hash_tree
        self._roothash = roothash
        self.block_hash_tree = hashtree.IncompleteHashTree(num_blocks)

    def get_block(self, blocknum):
        # the first time we use this bucket, we need to fetch enough elements
        # of the share hash tree to validate it from our share hash up to the
        # hashroot.
        if not self._share_hash:
            d1 = self.bucket.callRemote('get_share_hashes')
        else:
            d1 = defer.succeed(None)

        # we might need to grab some elements of our block hash tree, to
        # validate the requested block up to the share hash
        needed = self.block_hash_tree.needed_hashes(blocknum)
        if needed:
            # TODO: get fewer hashes, callRemote('get_block_hashes', needed)
            d2 = self.bucket.callRemote('get_block_hashes')
        else:
            d2 = defer.succeed([])

        d3 = self.bucket.callRemote('get_block', blocknum)

        d = defer.gatherResults([d1, d2, d3])
        d.addCallback(self._got_data, blocknum)
        return d

    def _got_data(self, res, blocknum):
        sharehashes, blockhashes, blockdata = res

        try:
            if not self._share_hash:
                sh = dict(sharehashes)
                sh[0] = self._roothash # always use our own root, from the URI
                sht = self.share_hash_tree
                if sht.get_leaf_index(self.sharenum) not in sh:
                    raise hashtree.NotEnoughHashesError
                sht.set_hashes(sh)
                self._share_hash = sht.get_leaf(self.sharenum)

            #log.msg("checking block_hash(shareid=%d, blocknum=%d) len=%d" %
            #        (self.sharenum, blocknum, len(blockdata)))
            blockhash = hashtree.block_hash(blockdata)
            # we always validate the blockhash
            bh = dict(enumerate(blockhashes))
            # replace blockhash root with validated value
            bh[0] = self._share_hash
            self.block_hash_tree.set_hashes(bh, {blocknum: blockhash})

        except (hashtree.BadHashError, hashtree.NotEnoughHashesError):
            # log.WEIRD: indicates undetected disk/network error, or more
            # likely a programming error
            log.msg("hash failure in block=%d, shnum=%d on %s" %
                    (blocknum, self.sharenum, self.bucket))
            #log.msg(" block length: %d" % len(blockdata))
            #log.msg(" block hash: %s" % idlib.b2a_or_none(blockhash)) # not safe
            #log.msg(" block data: %r" % (blockdata,))
            #log.msg(" root hash: %s" % idlib.b2a(self._roothash))
            #log.msg(" share hash tree:\n" + self.share_hash_tree.dump())
            #log.msg(" block hash tree:\n" + self.block_hash_tree.dump())
            #lines = []
            #for i,h in sorted(sharehashes):
            #    lines.append("%3d: %s" % (i, idlib.b2a_or_none(h)))
            #log.msg(" sharehashes:\n" + "\n".join(lines) + "\n")
            #lines = []
            #for i,h in enumerate(blockhashes):
            #    lines.append("%3d: %s" % (i, idlib.b2a_or_none(h)))
            #log.msg(" blockhashes:\n" + "\n".join(lines) + "\n")
            raise

        # If we made it here, the block is good. If the hash trees didn't
        # like what they saw, they would have raised a BadHashError, causing
        # our caller to see a Failure and thus ignore this block (as well as
        # dropping this bucket).
        return blockdata



class BlockDownloader:
    """I am responsible for downloading a single block (from a single bucket)
    for a single segment.

    I am a child of the SegmentDownloader.
    """

    def __init__(self, vbucket, blocknum, parent):
        self.vbucket = vbucket
        self.blocknum = blocknum
        self.parent = parent
        
    def start(self, segnum):
        d = self.vbucket.get_block(segnum)
        d.addCallbacks(self._hold_block, self._got_block_error)
        return d

    def _hold_block(self, data):
        self.parent.hold_block(self.blocknum, data)

    def _got_block_error(self, f):
        log.msg("BlockDownloader[%d] got error: %s" % (self.blocknum, f))
        self.parent.bucket_failed(self.vbucket)

class SegmentDownloader:
    """I am responsible for downloading all the blocks for a single segment
    of data.

    I am a child of the FileDownloader.
    """

    def __init__(self, parent, segmentnumber, needed_shares):
        self.parent = parent
        self.segmentnumber = segmentnumber
        self.needed_blocks = needed_shares
        self.blocks = {} # k: blocknum, v: data

    def start(self):
        return self._download()

    def _download(self):
        d = self._try()
        def _done(res):
            if len(self.blocks) >= self.needed_blocks:
                # we only need self.needed_blocks blocks
                # we want to get the smallest blockids, because they are
                # more likely to be fast "primary blocks"
                blockids = sorted(self.blocks.keys())[:self.needed_blocks]
                blocks = []
                for blocknum in blockids:
                    blocks.append(self.blocks[blocknum])
                return (blocks, blockids)
            else:
                return self._download()
        d.addCallback(_done)
        return d

    def _try(self):
        # fill our set of active buckets, maybe raising NotEnoughPeersError
        active_buckets = self.parent._activate_enough_buckets()
        # Now we have enough buckets, in self.parent.active_buckets.

        # in test cases, bd.start might mutate active_buckets right away, so
        # we need to put off calling start() until we've iterated all the way
        # through it.
        downloaders = []
        for blocknum, vbucket in active_buckets.iteritems():
            bd = BlockDownloader(vbucket, blocknum, self)
            downloaders.append(bd)
        l = [bd.start(self.segmentnumber) for bd in downloaders]
        return defer.DeferredList(l, fireOnOneErrback=True)

    def hold_block(self, blocknum, data):
        self.blocks[blocknum] = data

    def bucket_failed(self, vbucket):
        self.parent.bucket_failed(vbucket)

class FileDownloader:
    check_verifierid = True
    check_fileid = True

    def __init__(self, client, uri, downloadable):
        self._client = client
        self._downloadable = downloadable

        d = unpack_uri(uri)
        verifierid = d['verifierid']
        size = d['size']
        segment_size = d['segment_size']
        assert isinstance(verifierid, str)
        assert len(verifierid) == 20
        self._verifierid = verifierid
        self._fileid = d['fileid']
        self._roothash = d['roothash']

        self._codec = codec.get_decoder_by_name(d['codec_name'])
        self._codec.set_serialized_params(d['codec_params'])
        self._tail_codec = codec.get_decoder_by_name(d['codec_name'])
        self._tail_codec.set_serialized_params(d['tail_codec_params'])


        self._total_segments = mathutil.div_ceil(size, segment_size)
        self._current_segnum = 0
        self._segment_size = segment_size
        self._size = size
        self._num_needed_shares = self._codec.get_needed_shares()

        self._output = Output(downloadable, d['key'])

        self._share_hashtree = hashtree.IncompleteHashTree(d['total_shares'])
        self._share_hashtree.set_hashes({0: self._roothash})

        self.active_buckets = {} # k: shnum, v: bucket
        self._share_buckets = {} # k: shnum, v: set of buckets

    def start(self):
        log.msg("starting download [%s]" % (idlib.b2a(self._verifierid),))

        # first step: who should we download from?
        d = defer.maybeDeferred(self._get_all_shareholders)
        d.addCallback(self._got_all_shareholders)
        # once we know that, we can download blocks from them
        d.addCallback(self._download_all_segments)
        d.addCallback(self._done)
        return d

    def _get_all_shareholders(self):
        dl = []
        for (permutedpeerid, peerid, connection) in self._client.get_permuted_peers(self._verifierid):
            d = connection.callRemote("get_service", "storageserver")
            d.addCallback(lambda ss: ss.callRemote("get_buckets",
                                                   self._verifierid))
            d.addCallbacks(self._got_response, self._got_error,
                           callbackArgs=(connection,))
            dl.append(d)
        return defer.DeferredList(dl)

    def _got_response(self, buckets, connection):
        _assert(isinstance(buckets, dict), buckets) # soon foolscap will check this for us with its DictOf schema constraint
        for sharenum, bucket in buckets.iteritems():
            self.add_share_bucket(sharenum, bucket)

    def add_share_bucket(self, sharenum, bucket):
        vbucket = ValidatedBucket(sharenum, bucket,
                                  self._share_hashtree,
                                  self._roothash,
                                  self._total_segments)
        self._share_buckets.setdefault(sharenum, set()).add(vbucket)

    def _got_error(self, f):
        self._client.log("Somebody failed. -- %s" % (f,))

    def bucket_failed(self, vbucket):
        shnum = vbucket.sharenum
        del self.active_buckets[shnum]
        s = self._share_buckets[shnum]
        # s is a set of ValidatedBucket instances
        s.remove(vbucket)
        # ... which might now be empty
        if not s:
            # there are no more buckets which can provide this share, so
            # remove the key. This may prompt us to use a different share.
            del self._share_buckets[shnum]

    def _got_all_shareholders(self, res):
        if len(self._share_buckets) < self._num_needed_shares:
            raise NotEnoughPeersError
        for s in self._share_buckets.values():
            for vb in s:
                assert isinstance(vb, ValidatedBucket), \
                       "vb is %s but should be a ValidatedBucket" % (vb,)


    def _activate_enough_buckets(self):
        """either return a mapping from shnum to a ValidatedBucket that can
        provide data for that share, or raise NotEnoughPeersError"""

        while len(self.active_buckets) < self._num_needed_shares:
            # need some more
            handled_shnums = set(self.active_buckets.keys())
            available_shnums = set(self._share_buckets.keys())
            potential_shnums = list(available_shnums - handled_shnums)
            if not potential_shnums:
                raise NotEnoughPeersError
            # choose a random share
            shnum = random.choice(potential_shnums)
            # and a random bucket that will provide it
            validated_bucket = random.choice(list(self._share_buckets[shnum]))
            self.active_buckets[shnum] = validated_bucket
        return self.active_buckets


    def _download_all_segments(self, res):
        # the promise: upon entry to this function, self._share_buckets
        # contains enough buckets to complete the download, and some extra
        # ones to tolerate some buckets dropping out or having errors.
        # self._share_buckets is a dictionary that maps from shnum to a set
        # of ValidatedBuckets, which themselves are wrappers around
        # RIBucketReader references.
        self.active_buckets = {} # k: shnum, v: ValidatedBucket instance
        self._output.open()

        d = defer.succeed(None)
        for segnum in range(self._total_segments-1):
            d.addCallback(self._download_segment, segnum)
        d.addCallback(self._download_tail_segment, self._total_segments-1)
        return d

    def _download_segment(self, res, segnum):
        segmentdler = SegmentDownloader(self, segnum, self._num_needed_shares)
        d = segmentdler.start()
        d.addCallback(lambda (shares, shareids):
                      self._codec.decode(shares, shareids))
        def _done(res):
            for buf in res:
                self._output.write(buf)
        d.addCallback(_done)
        return d

    def _download_tail_segment(self, res, segnum):
        segmentdler = SegmentDownloader(self, segnum, self._num_needed_shares)
        d = segmentdler.start()
        d.addCallback(lambda (shares, shareids):
                      self._tail_codec.decode(shares, shareids))
        def _done(res):
            # trim off any padding added by the upload side
            data = ''.join(res)
            # we never send empty segments. If the data was an exact multiple
            # of the segment size, the last segment will be full.
            pad_size = mathutil.pad_size(self._size, self._segment_size)
            tail_size = self._segment_size - pad_size
            self._output.write(data[:tail_size])
        d.addCallback(_done)
        return d

    def _done(self, res):
        self._output.close()
        log.msg("computed VERIFIERID: %s" % idlib.b2a(self._output.verifierid))
        log.msg("computed FILEID: %s" % idlib.b2a(self._output.fileid))
        if self.check_verifierid:
            _assert(self._verifierid == self._output.verifierid,
                    "bad verifierid: computed=%s, expected=%s" %
                    (idlib.b2a(self._output.verifierid),
                     idlib.b2a(self._verifierid)))
        if self.check_fileid:
            _assert(self._fileid == self._output.fileid,
                    "bad fileid: computed=%s, expected=%s" %
                    (idlib.b2a(self._output.fileid),
                     idlib.b2a(self._fileid)))
        _assert(self._output.length == self._size,
                got=self._output.length, expected=self._size)
        return self._output.finish()


def netstring(s):
    return "%d:%s," % (len(s), s)

class FileName:
    implements(IDownloadTarget)
    def __init__(self, filename):
        self._filename = filename
    def open(self):
        self.f = open(self._filename, "wb")
        return self.f
    def write(self, data):
        self.f.write(data)
    def close(self):
        self.f.close()
    def fail(self):
        self.f.close()
        os.unlink(self._filename)
    def register_canceller(self, cb):
        pass # we won't use it
    def finish(self):
        pass

class Data:
    implements(IDownloadTarget)
    def __init__(self):
        self._data = []
    def open(self):
        pass
    def write(self, data):
        self._data.append(data)
    def close(self):
        self.data = "".join(self._data)
        del self._data
    def fail(self):
        del self._data
    def register_canceller(self, cb):
        pass # we won't use it
    def finish(self):
        return self.data

class FileHandle:
    """Use me to download data to a pre-defined filehandle-like object. I
    will use the target's write() method. I will *not* close the filehandle:
    I leave that up to the originator of the filehandle. The download process
    will return the filehandle when it completes.
    """
    implements(IDownloadTarget)
    def __init__(self, filehandle):
        self._filehandle = filehandle
    def open(self):
        pass
    def write(self, data):
        self._filehandle.write(data)
    def close(self):
        # the originator of the filehandle reserves the right to close it
        pass
    def fail(self):
        pass
    def register_canceller(self, cb):
        pass
    def finish(self):
        return self._filehandle

class Downloader(service.MultiService):
    """I am a service that allows file downloading.
    """
    implements(IDownloader)
    name = "downloader"

    def download(self, uri, t):
        assert self.parent
        assert self.running
        t = IDownloadTarget(t)
        assert t.write
        assert t.close
        dl = FileDownloader(self.parent, uri, t)
        d = dl.start()
        return d

    # utility functions
    def download_to_data(self, uri):
        return self.download(uri, Data())
    def download_to_filename(self, uri, filename):
        return self.download(uri, FileName(filename))
    def download_to_filehandle(self, uri, filehandle):
        return self.download(uri, FileHandle(filehandle))


