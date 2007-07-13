
import os, random
from zope.interface import implements
from twisted.python import log
from twisted.internet import defer
from twisted.application import service

from allmydata.util import idlib, mathutil, hashutil
from allmydata.util.assertutil import _assert
from allmydata import codec, hashtree, storageserver, uri
from allmydata.Crypto.Cipher import AES
from allmydata.interfaces import IDownloadTarget, IDownloader
from allmydata.encode import NotEnoughPeersError

class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

class BadURIExtensionHashValue(Exception):
    pass
class BadPlaintextHashValue(Exception):
    pass
class BadCrypttextHashValue(Exception):
    pass

class Output:
    def __init__(self, downloadable, key, total_length):
        self.downloadable = downloadable
        self._decryptor = AES.new(key=key, mode=AES.MODE_CTR,
                                  counterstart="\x00"*16)
        self._crypttext_hasher = hashutil.crypttext_hasher()
        self._plaintext_hasher = hashutil.plaintext_hasher()
        self.length = 0
        self.total_length = total_length
        self._segment_number = 0
        self._plaintext_hash_tree = None
        self._crypttext_hash_tree = None
        self._opened = False

    def setup_hashtrees(self, plaintext_hashtree, crypttext_hashtree):
        self._plaintext_hash_tree = plaintext_hashtree
        self._crypttext_hash_tree = crypttext_hashtree

    def write_segment(self, crypttext):
        self.length += len(crypttext)

        # memory footprint: 'crypttext' is the only segment_size usage
        # outstanding. While we decrypt it into 'plaintext', we hit
        # 2*segment_size.
        self._crypttext_hasher.update(crypttext)
        if self._crypttext_hash_tree:
            ch = hashutil.crypttext_segment_hasher()
            ch.update(crypttext)
            crypttext_leaves = {self._segment_number: ch.digest()}
            self._crypttext_hash_tree.set_hashes(leaves=crypttext_leaves)

        plaintext = self._decryptor.decrypt(crypttext)
        del crypttext

        # now we're back down to 1*segment_size.

        self._plaintext_hasher.update(plaintext)
        if self._plaintext_hash_tree:
            ph = hashutil.plaintext_segment_hasher()
            ph.update(plaintext)
            plaintext_leaves = {self._segment_number: ph.digest()}
            self._plaintext_hash_tree.set_hashes(leaves=plaintext_leaves)

        self._segment_number += 1
        # We're still at 1*segment_size. The Downloadable is responsible for
        # any memory usage beyond this.
        if not self._opened:
            self._opened = True
            self.downloadable.open(self.total_length)
        self.downloadable.write(plaintext)

    def fail(self, why):
        log.msg("UNUSUAL: download failed: %s" % why)
        self.downloadable.fail(why)

    def close(self):
        self.crypttext_hash = self._crypttext_hasher.digest()
        self.plaintext_hash = self._plaintext_hasher.digest()
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
        self.started = False

    def get_block(self, blocknum):
        if not self.started:
            d = self.bucket.start()
            def _started(res):
                self.started = True
                return self.get_block(blocknum)
            d.addCallback(_started)
            return d

        # the first time we use this bucket, we need to fetch enough elements
        # of the share hash tree to validate it from our share hash up to the
        # hashroot.
        if not self._share_hash:
            d1 = self.bucket.get_share_hashes()
        else:
            d1 = defer.succeed(None)

        # we might need to grab some elements of our block hash tree, to
        # validate the requested block up to the share hash
        needed = self.block_hash_tree.needed_hashes(blocknum)
        if needed:
            # TODO: get fewer hashes, use get_block_hashes(needed)
            d2 = self.bucket.get_block_hashes()
        else:
            d2 = defer.succeed([])

        d3 = self.bucket.get_block(blocknum)

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
            blockhash = hashutil.block_hash(blockdata)
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
    check_crypttext_hash = True
    check_plaintext_hash = True

    def __init__(self, client, u, downloadable):
        self._client = client

        d = uri.unpack_uri(u)
        self._storage_index = d['storage_index']
        self._uri_extension_hash = d['uri_extension_hash']
        self._total_shares = d['total_shares']
        self._size = d['size']
        self._num_needed_shares = d['needed_shares']

        self._output = Output(downloadable, d['key'], self._size)

        self.active_buckets = {} # k: shnum, v: bucket
        self._share_buckets = [] # list of (sharenum, bucket) tuples
        self._share_vbuckets = {} # k: shnum, v: set of ValidatedBuckets
        self._uri_extension_sources = []

        self._uri_extension_data = None

        self._fetch_failures = {"uri_extension": 0,
                                "plaintext_hashroot": 0,
                                "plaintext_hashtree": 0,
                                "crypttext_hashroot": 0,
                                "crypttext_hashtree": 0,
                                }

    def start(self):
        log.msg("starting download [%s]" % idlib.b2a(self._storage_index))

        # first step: who should we download from?
        d = defer.maybeDeferred(self._get_all_shareholders)
        d.addCallback(self._got_all_shareholders)
        # now get the uri_extension block from somebody and validate it
        d.addCallback(self._obtain_uri_extension)
        d.addCallback(self._got_uri_extension)
        d.addCallback(self._get_hashtrees)
        d.addCallback(self._create_validated_buckets)
        # once we know that, we can download blocks from everybody
        d.addCallback(self._download_all_segments)
        def _failed(why):
            self._output.fail(why)
            return why
        d.addErrback(_failed)
        d.addCallback(self._done)
        return d

    def _get_all_shareholders(self):
        dl = []
        for (permutedpeerid, peerid, connection) in self._client.get_permuted_peers(self._storage_index):
            d = connection.callRemote("get_service", "storageserver")
            d.addCallback(lambda ss: ss.callRemote("get_buckets",
                                                   self._storage_index))
            d.addCallbacks(self._got_response, self._got_error,
                           callbackArgs=(connection,))
            dl.append(d)
        return defer.DeferredList(dl)

    def _got_response(self, buckets, connection):
        _assert(isinstance(buckets, dict), buckets) # soon foolscap will check this for us with its DictOf schema constraint
        for sharenum, bucket in buckets.iteritems():
            b = storageserver.ReadBucketProxy(bucket)
            self.add_share_bucket(sharenum, b)
            self._uri_extension_sources.append(b)

    def add_share_bucket(self, sharenum, bucket):
        # this is split out for the benefit of test_encode.py
        self._share_buckets.append( (sharenum, bucket) )

    def _got_error(self, f):
        self._client.log("Somebody failed. -- %s" % (f,))

    def bucket_failed(self, vbucket):
        shnum = vbucket.sharenum
        del self.active_buckets[shnum]
        s = self._share_vbuckets[shnum]
        # s is a set of ValidatedBucket instances
        s.remove(vbucket)
        # ... which might now be empty
        if not s:
            # there are no more buckets which can provide this share, so
            # remove the key. This may prompt us to use a different share.
            del self._share_vbuckets[shnum]

    def _got_all_shareholders(self, res):
        if len(self._share_buckets) < self._num_needed_shares:
            raise NotEnoughPeersError
        #for s in self._share_vbuckets.values():
        #    for vb in s:
        #        assert isinstance(vb, ValidatedBucket), \
        #               "vb is %s but should be a ValidatedBucket" % (vb,)

    def _unpack_uri_extension_data(self, data):
        return uri.unpack_extension(data)

    def _obtain_uri_extension(self, ignored):
        # all shareholders are supposed to have a copy of uri_extension, and
        # all are supposed to be identical. We compute the hash of the data
        # that comes back, and compare it against the version in our URI. If
        # they don't match, ignore their data and try someone else.
        def _validate(proposal, bucket):
            h = hashutil.uri_extension_hash(proposal)
            if h != self._uri_extension_hash:
                self._fetch_failures["uri_extension"] += 1
                msg = ("The copy of uri_extension we received from "
                       "%s was bad" % bucket)
                raise BadURIExtensionHashValue(msg)
            return self._unpack_uri_extension_data(proposal)
        return self._obtain_validated_thing(None,
                                            self._uri_extension_sources,
                                            "uri_extension",
                                            "get_uri_extension", (), _validate)

    def _obtain_validated_thing(self, ignored, sources, name, methname, args,
                                validatorfunc):
        if not sources:
            raise NotEnoughPeersError("started with zero peers while fetching "
                                      "%s" % name)
        bucket = sources[0]
        sources = sources[1:]
        #d = bucket.callRemote(methname, *args)
        d = bucket.startIfNecessary()
        d.addCallback(lambda res: getattr(bucket, methname)(*args))
        d.addCallback(validatorfunc, bucket)
        def _bad(f):
            log.msg("%s from vbucket %s failed: %s" % (name, bucket, f)) # WEIRD
            if not sources:
                raise NotEnoughPeersError("ran out of peers, last error was %s"
                                          % (f,))
            # try again with a different one
            return self._obtain_validated_thing(None, sources, name,
                                                methname, args, validatorfunc)
        d.addErrback(_bad)
        return d

    def _got_uri_extension(self, uri_extension_data):
        d = self._uri_extension_data = uri_extension_data

        self._codec = codec.get_decoder_by_name(d['codec_name'])
        self._codec.set_serialized_params(d['codec_params'])
        self._tail_codec = codec.get_decoder_by_name(d['codec_name'])
        self._tail_codec.set_serialized_params(d['tail_codec_params'])

        crypttext_hash = d['crypttext_hash']
        assert isinstance(crypttext_hash, str)
        assert len(crypttext_hash) == 32
        self._crypttext_hash = crypttext_hash
        self._plaintext_hash = d['plaintext_hash']
        self._roothash = d['share_root_hash']

        self._segment_size = segment_size = d['segment_size']
        self._total_segments = mathutil.div_ceil(self._size, segment_size)
        self._current_segnum = 0

        self._share_hashtree = hashtree.IncompleteHashTree(d['total_shares'])
        self._share_hashtree.set_hashes({0: self._roothash})

    def _get_hashtrees(self, res):
        d = self._get_plaintext_hashtrees()
        d.addCallback(self._get_crypttext_hashtrees)
        d.addCallback(self._setup_hashtrees)
        return d

    def _get_plaintext_hashtrees(self):
        def _validate_plaintext_hashtree(proposal, bucket):
            if proposal[0] != self._uri_extension_data['plaintext_root_hash']:
                self._fetch_failures["plaintext_hashroot"] += 1
                msg = ("The copy of the plaintext_root_hash we received from"
                       " %s was bad" % bucket)
                raise BadPlaintextHashValue(msg)
            pt_hashtree = hashtree.IncompleteHashTree(self._total_segments)
            pt_hashes = dict(list(enumerate(proposal)))
            try:
                pt_hashtree.set_hashes(pt_hashes)
            except hashtree.BadHashError:
                # the hashes they gave us were not self-consistent, even
                # though the root matched what we saw in the uri_extension
                # block
                self._fetch_failures["plaintext_hashtree"] += 1
                raise
            self._plaintext_hashtree = pt_hashtree
        d = self._obtain_validated_thing(None,
                                         self._uri_extension_sources,
                                         "plaintext_hashes",
                                         "get_plaintext_hashes", (),
                                         _validate_plaintext_hashtree)
        return d

    def _get_crypttext_hashtrees(self, res):
        def _validate_crypttext_hashtree(proposal, bucket):
            if proposal[0] != self._uri_extension_data['crypttext_root_hash']:
                self._fetch_failures["crypttext_hashroot"] += 1
                msg = ("The copy of the crypttext_root_hash we received from"
                       " %s was bad" % bucket)
                raise BadCrypttextHashValue(msg)
            ct_hashtree = hashtree.IncompleteHashTree(self._total_segments)
            ct_hashes = dict(list(enumerate(proposal)))
            try:
                ct_hashtree.set_hashes(ct_hashes)
            except hashtree.BadHashError:
                self._fetch_failures["crypttext_hashtree"] += 1
                raise
            ct_hashtree.set_hashes(ct_hashes)
            self._crypttext_hashtree = ct_hashtree
        d = self._obtain_validated_thing(None,
                                         self._uri_extension_sources,
                                         "crypttext_hashes",
                                         "get_crypttext_hashes", (),
                                         _validate_crypttext_hashtree)
        return d

    def _setup_hashtrees(self, res):
        self._output.setup_hashtrees(self._plaintext_hashtree,
                                     self._crypttext_hashtree)


    def _create_validated_buckets(self, ignored=None):
        self._share_vbuckets = {}
        for sharenum, bucket in self._share_buckets:
            vbucket = ValidatedBucket(sharenum, bucket,
                                      self._share_hashtree,
                                      self._roothash,
                                      self._total_segments)
            s = self._share_vbuckets.setdefault(sharenum, set())
            s.add(vbucket)

    def _activate_enough_buckets(self):
        """either return a mapping from shnum to a ValidatedBucket that can
        provide data for that share, or raise NotEnoughPeersError"""

        while len(self.active_buckets) < self._num_needed_shares:
            # need some more
            handled_shnums = set(self.active_buckets.keys())
            available_shnums = set(self._share_vbuckets.keys())
            potential_shnums = list(available_shnums - handled_shnums)
            if not potential_shnums:
                raise NotEnoughPeersError
            # choose a random share
            shnum = random.choice(potential_shnums)
            # and a random bucket that will provide it
            validated_bucket = random.choice(list(self._share_vbuckets[shnum]))
            self.active_buckets[shnum] = validated_bucket
        return self.active_buckets


    def _download_all_segments(self, res):
        # the promise: upon entry to this function, self._share_vbuckets
        # contains enough buckets to complete the download, and some extra
        # ones to tolerate some buckets dropping out or having errors.
        # self._share_vbuckets is a dictionary that maps from shnum to a set
        # of ValidatedBuckets, which themselves are wrappers around
        # RIBucketReader references.
        self.active_buckets = {} # k: shnum, v: ValidatedBucket instance

        d = defer.succeed(None)
        for segnum in range(self._total_segments-1):
            d.addCallback(self._download_segment, segnum)
        d.addCallback(self._download_tail_segment, self._total_segments-1)
        return d

    def _download_segment(self, res, segnum):
        # memory footprint: when the SegmentDownloader finishes pulling down
        # all shares, we have 1*segment_size of usage.
        segmentdler = SegmentDownloader(self, segnum, self._num_needed_shares)
        d = segmentdler.start()
        # while the codec does its job, we hit 2*segment_size
        d.addCallback(lambda (shares, shareids):
                      self._codec.decode(shares, shareids))
        # once the codec is done, we drop back to 1*segment_size, because
        # 'shares' goes out of scope. The memory usage is all in the
        # plaintext now, spread out into a bunch of tiny buffers.
        def _done(buffers):
            # we start by joining all these buffers together into a single
            # string. This makes Output.write easier, since it wants to hash
            # data one segment at a time anyways, and doesn't impact our
            # memory footprint since we're already peaking at 2*segment_size
            # inside the codec a moment ago.
            segment = "".join(buffers)
            del buffers
            # we're down to 1*segment_size right now, but write_segment()
            # will decrypt a copy of the segment internally, which will push
            # us up to 2*segment_size while it runs.
            self._output.write_segment(segment)
        d.addCallback(_done)
        return d

    def _download_tail_segment(self, res, segnum):
        segmentdler = SegmentDownloader(self, segnum, self._num_needed_shares)
        d = segmentdler.start()
        d.addCallback(lambda (shares, shareids):
                      self._tail_codec.decode(shares, shareids))
        def _done(buffers):
            # trim off any padding added by the upload side
            segment = "".join(buffers)
            del buffers
            # we never send empty segments. If the data was an exact multiple
            # of the segment size, the last segment will be full.
            pad_size = mathutil.pad_size(self._size, self._segment_size)
            tail_size = self._segment_size - pad_size
            segment = segment[:tail_size]
            self._output.write_segment(segment)
        d.addCallback(_done)
        return d

    def _done(self, res):
        self._output.close()
        log.msg("computed CRYPTTEXT_HASH: %s" %
                idlib.b2a(self._output.crypttext_hash))
        log.msg("computed PLAINTEXT_HASH: %s" %
                idlib.b2a(self._output.plaintext_hash))
        if self.check_crypttext_hash:
            _assert(self._crypttext_hash == self._output.crypttext_hash,
                    "bad crypttext_hash: computed=%s, expected=%s" %
                    (idlib.b2a(self._output.crypttext_hash),
                     idlib.b2a(self._crypttext_hash)))
        if self.check_plaintext_hash:
            _assert(self._plaintext_hash == self._output.plaintext_hash,
                    "bad plaintext_hash: computed=%s, expected=%s" %
                    (idlib.b2a(self._output.plaintext_hash),
                     idlib.b2a(self._plaintext_hash)))
        _assert(self._output.length == self._size,
                got=self._output.length, expected=self._size)
        return self._output.finish()

class LiteralDownloader:
    def __init__(self, client, uri, downloadable):
        self._uri = uri
        self._downloadable = downloadable

    def start(self):
        data = uri.unpack_lit(self._uri)
        self._downloadable.open(len(data))
        self._downloadable.write(data)
        self._downloadable.close()
        return defer.maybeDeferred(self._downloadable.finish)


class FileName:
    implements(IDownloadTarget)
    def __init__(self, filename):
        self._filename = filename
    def open(self, size):
        self.f = open(self._filename, "wb")
        return self.f
    def write(self, data):
        self.f.write(data)
    def close(self):
        self.f.close()
    def fail(self, why):
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
    def open(self, size):
        pass
    def write(self, data):
        self._data.append(data)
    def close(self):
        self.data = "".join(self._data)
        del self._data
    def fail(self, why):
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
    def open(self, size):
        pass
    def write(self, data):
        self._filehandle.write(data)
    def close(self):
        # the originator of the filehandle reserves the right to close it
        pass
    def fail(self, why):
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

    def download(self, u, t):
        assert self.parent
        assert self.running
        t = IDownloadTarget(t)
        assert t.write
        assert t.close
        utype = uri.get_uri_type(u)
        if utype == "CHK":
            dl = FileDownloader(self.parent, u, t)
        elif utype == "LIT":
            dl = LiteralDownloader(self.parent, u, t)
        d = dl.start()
        return d

    # utility functions
    def download_to_data(self, uri):
        return self.download(uri, Data())
    def download_to_filename(self, uri, filename):
        return self.download(uri, FileName(filename))
    def download_to_filehandle(self, uri, filehandle):
        return self.download(uri, FileHandle(filehandle))


