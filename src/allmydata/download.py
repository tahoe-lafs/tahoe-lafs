
import os, random, sha
from zope.interface import implements
from twisted.python import log
from twisted.internet import defer
from twisted.application import service

from allmydata.util import idlib, bencode, mathutil
from allmydata.util.deferredutil import DeferredListShouldSucceed
from allmydata import codec
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
    def write(self, crypttext):
        self._verifierid_hasher.update(crypttext)
        plaintext = self._decryptor.decrypt(crypttext)
        self._fileid_hasher.update(plaintext)
        self.downloadable.write(plaintext)
    def finish(self):
        self.downloadable.close()
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
        self.parent.bucket_failed(self.blocknum, self.bucket)

class SegmentDownloader:
    def __init__(self, segmentnumber, needed_shares):
        self.segmentnumber = segmentnumber
        self.needed_blocks = needed_shares
        self.blocks = {} # k: blocknum, v: data

    def start(self):
        return self._download()

    def _download(self):
        d = self._try()
        def _done(res):
            if len(self.blocks) >= self.needed_blocks:
                return self.blocks
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
            self.parent.active_buckets[blocknum] = random.choice(self.parent._share_buckets[blocknum])

        # Now we have enough buckets, in self.parent.active_buckets.
        l = []
        for blocknum, bucket in self.parent.active_buckets.iteritems():
            bd = BlockDownloader(bucket, blocknum, self)
            d = bd.start(self.segmentnumber)
            l.append(d)
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
        (codec_name, codec_params, verifierid, roothash, needed_shares, total_shares, size, segment_size) = unpack_uri(uri)
        assert isinstance(verifierid, str)
        assert len(verifierid) == 20
        self._verifierid = verifierid
        self._roothash = roothash
        self._decoder = codec.get_decoder_by_name(codec_name)
        self._decoder.set_serialized_params(codec_params)
        self._total_segments = mathutil.div_ceil(size, segment_size)
        self._current_segnum = 0
        self._segment_size = segment_size
        self._needed_shares = self._decoder.get_needed_shares()

        # future:
        # self._share_hash_tree = ??
        # self._subshare_hash_trees = {} # k:shnum, v: hashtree
        # each time we start using a new shnum, we must acquire a share hash
        # from one of the buckets that provides that shnum, then validate it against
        # the rest of the share hash tree that they provide. Then, each time we
        # get a block in that share, we must validate the block against the rest
        # of the subshare hash tree that that bucket will provide.

    def start(self):
        log.msg("starting download [%s]" % (idlib.b2a(self._verifierid),))
        if self.debug:
            print "starting download"
        # first step: who should we download from?
        self.active_buckets = {} # k: shnum, v: bucket
        self._share_buckets = {} # k: shnum, v: set of buckets

        key = "\x00" * 16
        self._output = Output(self._downloadable, key)
 
        d = defer.maybeDeferred(self._get_all_shareholders)
        d.addCallback(self._got_all_shareholders)
        d.addCallback(self._download_all_segments)
        d.addCallback(self._done)
        return d

    def _get_all_shareholders(self):
        dl = []
        for (permutedpeerid, peerid, connection) in self._client.get_permuted_peers(self._verifierid):
            d = connection.callRemote("get_buckets", self._verifierid)
            d.addCallbacks(self._got_response, self._got_error,
                           callbackArgs=(connection,))
            dl.append(d)
        return defer.DeferredList(dl)

    def _got_response(self, buckets, connection):
        for sharenum, bucket in buckets:
            self._share_buckets.setdefault(sharenum, set()).add(bucket)
        
    def _got_error(self, f):
        self._client.log("Somebody failed. -- %s" % (f,))

    def _got_all_shareholders(self, res):
        if len(self._share_buckets) < self._needed_shares:
            raise NotEnoughPeersError

        self.active_buckets = {}
        
    def _download_all_segments(self):
        d = self._download_segment(self._current_segnum)
        def _done(res):
            if self._current_segnum == self._total_segments:
                return None
            return self._download_segment(self._current_segnum)
        d.addCallback(_done)
        return d

    def _download_segment(self, segnum):
        segmentdler = SegmentDownloader(segnum, self._needed_shares)
        d = segmentdler.start()
        d.addCallback(self._decoder.decode)
        def _done(res):
            self._current_segnum += 1
            if self._current_segnum == self._total_segments:
                data = ''.join(res)
                padsize = mathutil.pad_size(self._size, self._segment_size)
                data = data[:-padsize]
                self.output.write(data)
            else:
                for buf in res:
                    self.output.write(buf)
        d.addCallback(_done)
        return d

    def _done(self, res):
        return self._output.finish()
        
    def _write_data(self, data):
        self._verifierid_hasher.update(data)
        
        

# old stuff
    def _got_all_peers(self, res):
        all_buckets = []
        for peerid, buckets in self.landlords:
            all_buckets.extend(buckets)
        # TODO: try to avoid pulling multiple shares from the same peer
        all_buckets = all_buckets[:self.needed_shares]
        # retrieve all shares
        dl = []
        shares = []
        shareids = []
        for (bucket_num, bucket) in all_buckets:
            d0 = bucket.callRemote("get_metadata")
            d1 = bucket.callRemote("read")
            d2 = DeferredListShouldSucceed([d0, d1])
            def _got(res):
                shareid_s, sharedata = res
                shareid = bencode.bdecode(shareid_s)
                shares.append(sharedata)
                shareids.append(shareid)
            d2.addCallback(_got)
            dl.append(d2)
        d = DeferredListShouldSucceed(dl)

        d.addCallback(lambda res: self._decoder.decode(shares, shareids))

        def _write(decoded_shares):
            data = "".join(decoded_shares)
            self._target.open()
            hasher = sha.new(netstring("allmydata_v1_verifierid"))
            hasher.update(data)
            vid = hasher.digest()
            assert self._verifierid == vid, "%s != %s" % (idlib.b2a(self._verifierid), idlib.b2a(vid))
            self._target.write(data)
        d.addCallback(_write)

        def _done(res):
            self._target.close()
            return self._target.finish()
        def _fail(res):
            self._target.fail()
            return res
        d.addCallbacks(_done, _fail)
        return d

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
        dl = FileDownloader(self.parent, uri)
        dl.set_download_target(t)
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


