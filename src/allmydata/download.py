
import os, random, sha
from zope.interface import implements
from twisted.python import log
from twisted.internet import defer
from twisted.application import service

from allmydata.util import idlib, mathutil
from allmydata.util.assertutil import _assert
from allmydata import codec, chunk
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
        self._verifierid_hasher = sha.new(netstring("allmydata_v1_verifierid"))
        self._fileid_hasher = sha.new(netstring("allmydata_v1_fileid"))
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


class BlockDownloader:
    def __init__(self, bucket, blocknum, parent):
        self.bucket = bucket
        self.blocknum = blocknum
        self.parent = parent
        
    def start(self, segnum):
        d = self.bucket.callRemote('get_block', segnum)
        d.addCallbacks(self._hold_block, self._got_block_error)
        return d

    def _hold_block(self, data):
        self.parent.hold_block(self.blocknum, data)

    def _got_block_error(self, f):
        log.msg("BlockDownloader[%d] got error: %s" % (self.blocknum, f))
        self.parent.bucket_failed(self.blocknum, self.bucket)

class SegmentDownloader:
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
        while len(self.parent.active_buckets) < self.needed_blocks:
            # need some more
            otherblocknums = list(set(self.parent._share_buckets.keys()) - set(self.parent.active_buckets.keys()))
            if not otherblocknums:
                raise NotEnoughPeersError
            blocknum = random.choice(otherblocknums)
            bucket = random.choice(list(self.parent._share_buckets[blocknum]))
            self.parent.active_buckets[blocknum] = bucket

        # Now we have enough buckets, in self.parent.active_buckets.

        # before we get any blocks of a given share, we need to be able to
        # validate that block and that share. Check to see if we have enough
        # hashes. If we don't, grab them before continuing.
        d = self._grab_needed_hashes()
        d.addCallback(self._download_some_blocks)
        return d

    def _grab_needed_hashes(self):
        # each bucket is holding the hashes necessary to validate their
        # share. So it suffices to ask everybody for all the hashes they know
        # about. Eventually we'll have all that we need, so we can stop
        # asking.

        # for each active share, see what hashes we need
        ht = self.parent.get_share_hashtree()
        needed_hashes = set()
        for shnum in self.parent.active_buckets:
            needed_hashes.update(ht.needed_hashes(shnum))
        if not needed_hashes:
            return defer.succeed(None)

        # for now, just ask everybody for everything
        # TODO: send fewer queries
        dl = []
        for shnum, bucket in self.parent.active_buckets.iteritems():
            d = bucket.callRemote("get_share_hashes")
            d.addCallback(self._got_share_hashes, shnum, bucket)
            dl.append(d)
        d.addCallback(self._validate_root)
        return defer.DeferredList(dl)

    def _got_share_hashes(self, share_hashes, shnum, bucket):
        ht = self.parent.get_share_hashtree()
        for hashnum, sharehash in share_hashes:
            # TODO: we're accumulating these hashes blindly, since we only
            # validate the leaves. This makes it possible for someone to
            # frame another server by giving us bad internal hashes. We pass
            # 'shnum' and 'bucket' in so that if we detected problems with
            # intermediate nodes, we could associate the error with the
            # bucket and stop using them.
            ht.set_hash(hashnum, sharehash)

    def _validate_root(self, res):
        # TODO: I dunno, check that the hash tree looks good so far and that
        # it adds up to the root. The idea is to reject any bad buckets
        # early.
        pass

    def _download_some_blocks(self, res):
        # in test cases, bd.start might mutate active_buckets right away, so
        # we need to put off calling start() until we've iterated all the way
        # through it
        downloaders = []
        for blocknum, bucket in self.parent.active_buckets.iteritems():
            bd = BlockDownloader(bucket, blocknum, self)
            downloaders.append(bd)
        l = [bd.start(self.segmentnumber) for bd in downloaders]
        return defer.DeferredList(l)

    def hold_block(self, blocknum, data):
        self.blocks[blocknum] = data

    def bucket_failed(self, shnum, bucket):
        del self.parent.active_buckets[shnum]
        s = self.parent._share_buckets[shnum]
        s.remove(bucket)
        if not s:
            del self.parent._share_buckets[shnum]
        
class FileDownloader:
    debug = False

    def __init__(self, client, uri, downloadable):
        self._client = client
        self._downloadable = downloadable
        (codec_name, codec_params, tail_codec_params, verifierid, roothash, needed_shares, total_shares, size, segment_size) = unpack_uri(uri)
        assert isinstance(verifierid, str)
        assert len(verifierid) == 20
        self._verifierid = verifierid
        self._roothash = roothash

        self._codec = codec.get_decoder_by_name(codec_name)
        self._codec.set_serialized_params(codec_params)
        self._tail_codec = codec.get_decoder_by_name(codec_name)
        self._tail_codec.set_serialized_params(tail_codec_params)


        self._total_segments = mathutil.div_ceil(size, segment_size)
        self._current_segnum = 0
        self._segment_size = segment_size
        self._size = size
        self._num_needed_shares = self._codec.get_needed_shares()

        key = "\x00" * 16
        self._output = Output(downloadable, key)

        # future:
        # each time we start using a new shnum, we must acquire a share hash
        # from one of the buckets that provides that shnum, then validate it
        # against the rest of the share hash tree that they provide. Then,
        # each time we get a block in that share, we must validate the block
        # against the rest of the subshare hash tree that that bucket will
        # provide.

        self._share_hashtree = chunk.IncompleteHashTree(total_shares)
        #self._block_hashtrees = {} # k: shnum, v: hashtree

    def get_share_hashtree(self):
        return self._share_hashtree

    def start(self):
        log.msg("starting download [%s]" % (idlib.b2a(self._verifierid),))
        if self.debug:
            print "starting download"
        # first step: who should we download from?
        self.active_buckets = {} # k: shnum, v: bucket
        self._share_buckets = {} # k: shnum, v: set of buckets

        d = defer.maybeDeferred(self._get_all_shareholders)
        d.addCallback(self._got_all_shareholders)
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
            self._share_buckets.setdefault(sharenum, set()).add(bucket)
        
    def _got_error(self, f):
        self._client.log("Somebody failed. -- %s" % (f,))

    def _got_all_shareholders(self, res):
        if len(self._share_buckets) < self._num_needed_shares:
            raise NotEnoughPeersError

        self.active_buckets = {}
        self._output.open()

    def _download_all_segments(self, res):
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
            tail_size = self._size % self._segment_size
            self._output.write(data[:tail_size])
        d.addCallback(_done)
        return d

    def _done(self, res):
        self._output.close()
        #print "VERIFIERID: %s" % idlib.b2a(self._output.verifierid)
        #print "FILEID: %s" % idlib.b2a(self._output.fileid)
        #assert self._verifierid == self._output.verifierid
        #assert self._fileid = self._output.fileid
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
        pass

class Downloader(service.MultiService):
    """I am a service that allows file downloading.
    """
    implements(IDownloader)
    name = "downloader"
    debug = False

    def download(self, uri, t):
        assert self.parent
        assert self.running
        t = IDownloadTarget(t)
        assert t.write
        assert t.close
        dl = FileDownloader(self.parent, uri, t)
        if self.debug:
            dl.debug = True
        d = dl.start()
        return d

    # utility functions
    def download_to_data(self, uri):
        return self.download(uri, Data())
    def download_to_filename(self, uri, filename):
        return self.download(uri, FileName(filename))
    def download_to_filehandle(self, uri, filehandle):
        return self.download(uri, FileHandle(filehandle))


