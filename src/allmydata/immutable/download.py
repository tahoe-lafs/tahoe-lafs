
import os, random, weakref, itertools, time
from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IPushProducer, IConsumer
from twisted.application import service
from foolscap import DeadReferenceError
from foolscap.eventual import eventually

from allmydata.util import base32, mathutil, hashutil, log
from allmydata.util.assertutil import _assert
from allmydata import codec, hashtree, storage, uri
from allmydata.interfaces import IDownloadTarget, IDownloader, IFileURI, \
     IDownloadStatus, IDownloadResults
from allmydata.immutable.encode import NotEnoughSharesError
from pycryptopp.cipher.aes import AES

class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

class IntegrityCheckError(Exception):
    pass

class BadURIExtensionHashValue(IntegrityCheckError):
    pass
class BadURIExtension(IntegrityCheckError):
    pass
class BadPlaintextHashValue(IntegrityCheckError):
    pass
class BadCrypttextHashValue(IntegrityCheckError):
    pass

class DownloadStopped(Exception):
    pass

class DownloadResults:
    implements(IDownloadResults)

    def __init__(self):
        self.servers_used = set()
        self.server_problems = {}
        self.servermap = {}
        self.timings = {}
        self.file_size = None

class Output:
    def __init__(self, downloadable, key, total_length, log_parent,
                 download_status):
        self.downloadable = downloadable
        self._decryptor = AES(key)
        self._crypttext_hasher = hashutil.crypttext_hasher()
        self._plaintext_hasher = hashutil.plaintext_hasher()
        self.length = 0
        self.total_length = total_length
        self._segment_number = 0
        self._plaintext_hash_tree = None
        self._crypttext_hash_tree = None
        self._opened = False
        self._log_parent = log_parent
        self._status = download_status
        self._status.set_progress(0.0)

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_parent
        if "facility" not in kwargs:
            kwargs["facility"] = "download.output"
        return log.msg(*args, **kwargs)

    def setup_hashtrees(self, plaintext_hashtree, crypttext_hashtree):
        self._plaintext_hash_tree = plaintext_hashtree
        self._crypttext_hash_tree = crypttext_hashtree

    def write_segment(self, crypttext):
        self.length += len(crypttext)
        self._status.set_progress( float(self.length) / self.total_length )

        # memory footprint: 'crypttext' is the only segment_size usage
        # outstanding. While we decrypt it into 'plaintext', we hit
        # 2*segment_size.
        self._crypttext_hasher.update(crypttext)
        if self._crypttext_hash_tree:
            ch = hashutil.crypttext_segment_hasher()
            ch.update(crypttext)
            crypttext_leaves = {self._segment_number: ch.digest()}
            self.log(format="crypttext leaf hash (%(bytes)sB) [%(segnum)d] is %(hash)s",
                     bytes=len(crypttext),
                     segnum=self._segment_number, hash=base32.b2a(ch.digest()),
                     level=log.NOISY)
            self._crypttext_hash_tree.set_hashes(leaves=crypttext_leaves)

        plaintext = self._decryptor.process(crypttext)
        del crypttext

        # now we're back down to 1*segment_size.

        self._plaintext_hasher.update(plaintext)
        if self._plaintext_hash_tree:
            ph = hashutil.plaintext_segment_hasher()
            ph.update(plaintext)
            plaintext_leaves = {self._segment_number: ph.digest()}
            self.log(format="plaintext leaf hash (%(bytes)sB) [%(segnum)d] is %(hash)s",
                     bytes=len(plaintext),
                     segnum=self._segment_number, hash=base32.b2a(ph.digest()),
                     level=log.NOISY)
            self._plaintext_hash_tree.set_hashes(leaves=plaintext_leaves)

        self._segment_number += 1
        # We're still at 1*segment_size. The Downloadable is responsible for
        # any memory usage beyond this.
        if not self._opened:
            self._opened = True
            self.downloadable.open(self.total_length)
        self.downloadable.write(plaintext)

    def fail(self, why):
        # this is really unusual, and deserves maximum forensics
        if why.check(DownloadStopped):
            # except DownloadStopped just means the consumer aborted the
            # download, not so scary
            self.log("download stopped", level=log.UNUSUAL)
        else:
            self.log("download failed!", failure=why,
                     level=log.SCARY, umid="lp1vaQ")
        self.downloadable.fail(why)

    def close(self):
        self.crypttext_hash = self._crypttext_hasher.digest()
        self.plaintext_hash = self._plaintext_hasher.digest()
        self.log("download finished, closing IDownloadable", level=log.NOISY)
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
            d1 = defer.succeed([])

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
        blockhash = None # to make logging it safe

        try:
            if not self._share_hash:
                sh = dict(sharehashes)
                sh[0] = self._roothash # always use our own root, from the URI
                sht = self.share_hash_tree
                if sht.get_leaf_index(self.sharenum) not in sh:
                    raise hashtree.NotEnoughHashesError
                sht.set_hashes(sh)
                self._share_hash = sht.get_leaf(self.sharenum)

            blockhash = hashutil.block_hash(blockdata)
            #log.msg("checking block_hash(shareid=%d, blocknum=%d) len=%d "
            #        "%r .. %r: %s" %
            #        (self.sharenum, blocknum, len(blockdata),
            #         blockdata[:50], blockdata[-50:], base32.b2a(blockhash)))

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
            if self._share_hash:
                log.msg(""" failure occurred when checking the block_hash_tree.
                This suggests that either the block data was bad, or that the
                block hashes we received along with it were bad.""")
            else:
                log.msg(""" the failure probably occurred when checking the
                share_hash_tree, which suggests that the share hashes we
                received from the remote peer were bad.""")
            log.msg(" have self._share_hash: %s" % bool(self._share_hash))
            log.msg(" block length: %d" % len(blockdata))
            log.msg(" block hash: %s" % base32.b2a_or_none(blockhash))
            if len(blockdata) < 100:
                log.msg(" block data: %r" % (blockdata,))
            else:
                log.msg(" block data start/end: %r .. %r" %
                        (blockdata[:50], blockdata[-50:]))
            log.msg(" root hash: %s" % base32.b2a(self._roothash))
            log.msg(" share hash tree:\n" + self.share_hash_tree.dump())
            log.msg(" block hash tree:\n" + self.block_hash_tree.dump())
            lines = []
            for i,h in sorted(sharehashes):
                lines.append("%3d: %s" % (i, base32.b2a_or_none(h)))
            log.msg(" sharehashes:\n" + "\n".join(lines) + "\n")
            lines = []
            for i,h in enumerate(blockhashes):
                lines.append("%3d: %s" % (i, base32.b2a_or_none(h)))
            log.msg(" blockhashes:\n" + "\n".join(lines) + "\n")
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

    def __init__(self, vbucket, blocknum, parent, results):
        self.vbucket = vbucket
        self.blocknum = blocknum
        self.parent = parent
        self.results = results
        self._log_number = self.parent.log("starting block %d" % blocknum)

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        return self.parent.log(*args, **kwargs)

    def start(self, segnum):
        lognum = self.log("get_block(segnum=%d)" % segnum)
        started = time.time()
        d = self.vbucket.get_block(segnum)
        d.addCallbacks(self._hold_block, self._got_block_error,
                       callbackArgs=(started, lognum,), errbackArgs=(lognum,))
        return d

    def _hold_block(self, data, started, lognum):
        if self.results:
            elapsed = time.time() - started
            peerid = self.vbucket.bucket.get_peerid()
            if peerid not in self.results.timings["fetch_per_server"]:
                self.results.timings["fetch_per_server"][peerid] = []
            self.results.timings["fetch_per_server"][peerid].append(elapsed)
        self.log("got block", parent=lognum)
        self.parent.hold_block(self.blocknum, data)

    def _got_block_error(self, f, lognum):
        level = log.WEIRD
        if f.check(DeadReferenceError):
            level = log.UNUSUAL
        self.log("BlockDownloader[%d] got error" % self.blocknum,
                 failure=f, level=level, parent=lognum, umid="5Z4uHQ")
        if self.results:
            peerid = self.vbucket.bucket.get_peerid()
            self.results.server_problems[peerid] = str(f)
        self.parent.bucket_failed(self.vbucket)

class SegmentDownloader:
    """I am responsible for downloading all the blocks for a single segment
    of data.

    I am a child of the FileDownloader.
    """

    def __init__(self, parent, segmentnumber, needed_shares, results):
        self.parent = parent
        self.segmentnumber = segmentnumber
        self.needed_blocks = needed_shares
        self.blocks = {} # k: blocknum, v: data
        self.results = results
        self._log_number = self.parent.log("starting segment %d" %
                                           segmentnumber)

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        return self.parent.log(*args, **kwargs)

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
        # fill our set of active buckets, maybe raising NotEnoughSharesError
        active_buckets = self.parent._activate_enough_buckets()
        # Now we have enough buckets, in self.parent.active_buckets.

        # in test cases, bd.start might mutate active_buckets right away, so
        # we need to put off calling start() until we've iterated all the way
        # through it.
        downloaders = []
        for blocknum, vbucket in active_buckets.iteritems():
            bd = BlockDownloader(vbucket, blocknum, self, self.results)
            downloaders.append(bd)
            if self.results:
                self.results.servers_used.add(vbucket.bucket.get_peerid())
        l = [bd.start(self.segmentnumber) for bd in downloaders]
        return defer.DeferredList(l, fireOnOneErrback=True)

    def hold_block(self, blocknum, data):
        self.blocks[blocknum] = data

    def bucket_failed(self, vbucket):
        self.parent.bucket_failed(vbucket)

class DownloadStatus:
    implements(IDownloadStatus)
    statusid_counter = itertools.count(0)

    def __init__(self):
        self.storage_index = None
        self.size = None
        self.helper = False
        self.status = "Not started"
        self.progress = 0.0
        self.paused = False
        self.stopped = False
        self.active = True
        self.results = None
        self.counter = self.statusid_counter.next()
        self.started = time.time()

    def get_started(self):
        return self.started
    def get_storage_index(self):
        return self.storage_index
    def get_size(self):
        return self.size
    def using_helper(self):
        return self.helper
    def get_status(self):
        status = self.status
        if self.paused:
            status += " (output paused)"
        if self.stopped:
            status += " (output stopped)"
        return status
    def get_progress(self):
        return self.progress
    def get_active(self):
        return self.active
    def get_results(self):
        return self.results
    def get_counter(self):
        return self.counter

    def set_storage_index(self, si):
        self.storage_index = si
    def set_size(self, size):
        self.size = size
    def set_helper(self, helper):
        self.helper = helper
    def set_status(self, status):
        self.status = status
    def set_paused(self, paused):
        self.paused = paused
    def set_stopped(self, stopped):
        self.stopped = stopped
    def set_progress(self, value):
        self.progress = value
    def set_active(self, value):
        self.active = value
    def set_results(self, value):
        self.results = value

class FileDownloader:
    implements(IPushProducer)
    check_crypttext_hash = True
    check_plaintext_hash = True
    _status = None

    def __init__(self, client, u, downloadable):
        self._client = client

        u = IFileURI(u)
        self._storage_index = u.storage_index
        self._uri_extension_hash = u.uri_extension_hash
        self._total_shares = u.total_shares
        self._size = u.size
        self._num_needed_shares = u.needed_shares

        self._si_s = storage.si_b2a(self._storage_index)
        self.init_logging()

        self._started = time.time()
        self._status = s = DownloadStatus()
        s.set_status("Starting")
        s.set_storage_index(self._storage_index)
        s.set_size(self._size)
        s.set_helper(False)
        s.set_active(True)

        self._results = DownloadResults()
        s.set_results(self._results)
        self._results.file_size = self._size
        self._results.timings["servers_peer_selection"] = {}
        self._results.timings["fetch_per_server"] = {}
        self._results.timings["cumulative_fetch"] = 0.0
        self._results.timings["cumulative_decode"] = 0.0
        self._results.timings["cumulative_decrypt"] = 0.0
        self._results.timings["paused"] = 0.0

        self._paused = False
        self._stopped = False
        if IConsumer.providedBy(downloadable):
            downloadable.registerProducer(self, True)
        self._downloadable = downloadable
        self._output = Output(downloadable, u.key, self._size, self._log_number,
                              self._status)

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

    def init_logging(self):
        self._log_prefix = prefix = storage.si_b2a(self._storage_index)[:5]
        num = self._client.log(format="FileDownloader(%(si)s): starting",
                               si=storage.si_b2a(self._storage_index))
        self._log_number = num

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.download"
        return log.msg(*args, **kwargs)

    def pauseProducing(self):
        if self._paused:
            return
        self._paused = defer.Deferred()
        self._paused_at = time.time()
        if self._status:
            self._status.set_paused(True)

    def resumeProducing(self):
        if self._paused:
            paused_for = time.time() - self._paused_at
            self._results.timings['paused'] += paused_for
            p = self._paused
            self._paused = None
            eventually(p.callback, None)
            if self._status:
                self._status.set_paused(False)

    def stopProducing(self):
        self.log("Download.stopProducing")
        self._stopped = True
        self.resumeProducing()
        if self._status:
            self._status.set_stopped(True)
            self._status.set_active(False)

    def start(self):
        self.log("starting download")

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
        def _finished(res):
            if self._status:
                self._status.set_status("Finished")
                self._status.set_active(False)
                self._status.set_paused(False)
            if IConsumer.providedBy(self._downloadable):
                self._downloadable.unregisterProducer()
            return res
        d.addBoth(_finished)
        def _failed(why):
            if self._status:
                self._status.set_status("Failed")
                self._status.set_active(False)
            self._output.fail(why)
            return why
        d.addErrback(_failed)
        d.addCallback(self._done)
        return d

    def _get_all_shareholders(self):
        dl = []
        for (peerid,ss) in self._client.get_permuted_peers("storage",
                                                           self._storage_index):
            d = ss.callRemote("get_buckets", self._storage_index)
            d.addCallbacks(self._got_response, self._got_error,
                           callbackArgs=(peerid,))
            dl.append(d)
        self._responses_received = 0
        self._queries_sent = len(dl)
        if self._status:
            self._status.set_status("Locating Shares (%d/%d)" %
                                    (self._responses_received,
                                     self._queries_sent))
        return defer.DeferredList(dl)

    def _got_response(self, buckets, peerid):
        self._responses_received += 1
        if self._results:
            elapsed = time.time() - self._started
            self._results.timings["servers_peer_selection"][peerid] = elapsed
        if self._status:
            self._status.set_status("Locating Shares (%d/%d)" %
                                    (self._responses_received,
                                     self._queries_sent))
        for sharenum, bucket in buckets.iteritems():
            b = storage.ReadBucketProxy(bucket, peerid, self._si_s)
            self.add_share_bucket(sharenum, b)
            self._uri_extension_sources.append(b)
            if self._results:
                if peerid not in self._results.servermap:
                    self._results.servermap[peerid] = set()
                self._results.servermap[peerid].add(sharenum)

    def add_share_bucket(self, sharenum, bucket):
        # this is split out for the benefit of test_encode.py
        self._share_buckets.append( (sharenum, bucket) )

    def _got_error(self, f):
        level = log.WEIRD
        if f.check(DeadReferenceError):
            level = log.UNUSUAL
        self._client.log("Error during get_buckets", failure=f, level=level,
                         umid="3uuBUQ")

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
        if self._results:
            now = time.time()
            self._results.timings["peer_selection"] = now - self._started

        if len(self._share_buckets) < self._num_needed_shares:
            raise NotEnoughSharesError

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
        if self._status:
            self._status.set_status("Obtaining URI Extension")

        self._uri_extension_fetch_started = time.time()
        def _validate(proposal, bucket):
            h = hashutil.uri_extension_hash(proposal)
            if h != self._uri_extension_hash:
                self._fetch_failures["uri_extension"] += 1
                msg = ("The copy of uri_extension we received from "
                       "%s was bad: wanted %s, got %s" %
                       (bucket,
                        base32.b2a(self._uri_extension_hash),
                        base32.b2a(h)))
                self.log(msg, level=log.SCARY, umid="jnkTtQ")
                raise BadURIExtensionHashValue(msg)
            return self._unpack_uri_extension_data(proposal)
        return self._obtain_validated_thing(None,
                                            self._uri_extension_sources,
                                            "uri_extension",
                                            "get_uri_extension", (), _validate)

    def _obtain_validated_thing(self, ignored, sources, name, methname, args,
                                validatorfunc):
        if not sources:
            raise NotEnoughSharesError("started with zero peers while fetching "
                                      "%s" % name)
        bucket = sources[0]
        sources = sources[1:]
        #d = bucket.callRemote(methname, *args)
        d = bucket.startIfNecessary()
        d.addCallback(lambda res: getattr(bucket, methname)(*args))
        d.addCallback(validatorfunc, bucket)
        def _bad(f):
            level = log.WEIRD
            if f.check(DeadReferenceError):
                level = log.UNUSUAL
            self.log(format="operation %(op)s from vbucket %(vbucket)s failed",
                     op=name, vbucket=str(bucket),
                     failure=f, level=level, umid="JGXxBA")
            if not sources:
                raise NotEnoughSharesError("ran out of peers, last error was %s"
                                          % (f,))
            # try again with a different one
            return self._obtain_validated_thing(None, sources, name,
                                                methname, args, validatorfunc)
        d.addErrback(_bad)
        return d

    def _got_uri_extension(self, uri_extension_data):
        if self._results:
            elapsed = time.time() - self._uri_extension_fetch_started
            self._results.timings["uri_extension"] = elapsed

        d = self._uri_extension_data = uri_extension_data

        self._codec = codec.get_decoder_by_name(d['codec_name'])
        self._codec.set_serialized_params(d['codec_params'])
        self._tail_codec = codec.get_decoder_by_name(d['codec_name'])
        self._tail_codec.set_serialized_params(d['tail_codec_params'])

        crypttext_hash = d.get('crypttext_hash', None) # optional
        if crypttext_hash:
            assert isinstance(crypttext_hash, str)
            assert len(crypttext_hash) == 32
        self._crypttext_hash = crypttext_hash
        self._plaintext_hash = d.get('plaintext_hash', None) # optional

        self._roothash = d['share_root_hash']

        self._segment_size = segment_size = d['segment_size']
        self._total_segments = mathutil.div_ceil(self._size, segment_size)
        self._current_segnum = 0

        self._share_hashtree = hashtree.IncompleteHashTree(d['total_shares'])
        self._share_hashtree.set_hashes({0: self._roothash})

    def _get_hashtrees(self, res):
        self._get_hashtrees_started = time.time()
        if self._status:
            self._status.set_status("Retrieving Hash Trees")
        d = defer.maybeDeferred(self._get_plaintext_hashtrees)
        d.addCallback(self._get_crypttext_hashtrees)
        d.addCallback(self._setup_hashtrees)
        return d

    def _get_plaintext_hashtrees(self):
        # plaintext hashes are optional. If the root isn't in the UEB, then
        # the share will be holding an empty list. We don't even bother
        # fetching it.
        if "plaintext_root_hash" not in self._uri_extension_data:
            self._plaintext_hashtree = None
            return
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
        # Ciphertext hash tree root is mandatory, so that there is at
        # most one ciphertext that matches this read-cap or
        # verify-cap.  The integrity check on the shares is not
        # sufficient to prevent the original encoder from creating
        # some shares of file A and other shares of file B.
        if "crypttext_root_hash" not in self._uri_extension_data:
            raise BadURIExtension("URI Extension block did not have the ciphertext hash tree root")
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
        if self._results:
            elapsed = time.time() - self._get_hashtrees_started
            self._results.timings["hashtrees"] = elapsed

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
        provide data for that share, or raise NotEnoughSharesError"""

        while len(self.active_buckets) < self._num_needed_shares:
            # need some more
            handled_shnums = set(self.active_buckets.keys())
            available_shnums = set(self._share_vbuckets.keys())
            potential_shnums = list(available_shnums - handled_shnums)
            if not potential_shnums:
                raise NotEnoughSharesError
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

        self._started_fetching = time.time()

        d = defer.succeed(None)
        for segnum in range(self._total_segments-1):
            d.addCallback(self._download_segment, segnum)
            # this pause, at the end of write, prevents pre-fetch from
            # happening until the consumer is ready for more data.
            d.addCallback(self._check_for_pause)
        d.addCallback(self._download_tail_segment, self._total_segments-1)
        return d

    def _check_for_pause(self, res):
        if self._paused:
            d = defer.Deferred()
            self._paused.addCallback(lambda ignored: d.callback(res))
            return d
        if self._stopped:
            raise DownloadStopped("our Consumer called stopProducing()")
        return res

    def _download_segment(self, res, segnum):
        if self._status:
            self._status.set_status("Downloading segment %d of %d" %
                                    (segnum+1, self._total_segments))
        self.log("downloading seg#%d of %d (%d%%)"
                 % (segnum, self._total_segments,
                    100.0 * segnum / self._total_segments))
        # memory footprint: when the SegmentDownloader finishes pulling down
        # all shares, we have 1*segment_size of usage.
        segmentdler = SegmentDownloader(self, segnum, self._num_needed_shares,
                                        self._results)
        started = time.time()
        d = segmentdler.start()
        def _finished_fetching(res):
            elapsed = time.time() - started
            self._results.timings["cumulative_fetch"] += elapsed
            return res
        if self._results:
            d.addCallback(_finished_fetching)
        # pause before using more memory
        d.addCallback(self._check_for_pause)
        # while the codec does its job, we hit 2*segment_size
        def _started_decode(res):
            self._started_decode = time.time()
            return res
        if self._results:
            d.addCallback(_started_decode)
        d.addCallback(lambda (shares, shareids):
                      self._codec.decode(shares, shareids))
        # once the codec is done, we drop back to 1*segment_size, because
        # 'shares' goes out of scope. The memory usage is all in the
        # plaintext now, spread out into a bunch of tiny buffers.
        def _finished_decode(res):
            elapsed = time.time() - self._started_decode
            self._results.timings["cumulative_decode"] += elapsed
            return res
        if self._results:
            d.addCallback(_finished_decode)

        # pause/check-for-stop just before writing, to honor stopProducing
        d.addCallback(self._check_for_pause)
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
            started_decrypt = time.time()
            self._output.write_segment(segment)
            if self._results:
                elapsed = time.time() - started_decrypt
                self._results.timings["cumulative_decrypt"] += elapsed
        d.addCallback(_done)
        return d

    def _download_tail_segment(self, res, segnum):
        self.log("downloading seg#%d of %d (%d%%)"
                 % (segnum, self._total_segments,
                    100.0 * segnum / self._total_segments))
        segmentdler = SegmentDownloader(self, segnum, self._num_needed_shares,
                                        self._results)
        started = time.time()
        d = segmentdler.start()
        def _finished_fetching(res):
            elapsed = time.time() - started
            self._results.timings["cumulative_fetch"] += elapsed
            return res
        if self._results:
            d.addCallback(_finished_fetching)
        # pause before using more memory
        d.addCallback(self._check_for_pause)
        def _started_decode(res):
            self._started_decode = time.time()
            return res
        if self._results:
            d.addCallback(_started_decode)
        d.addCallback(lambda (shares, shareids):
                      self._tail_codec.decode(shares, shareids))
        def _finished_decode(res):
            elapsed = time.time() - self._started_decode
            self._results.timings["cumulative_decode"] += elapsed
            return res
        if self._results:
            d.addCallback(_finished_decode)
        # pause/check-for-stop just before writing, to honor stopProducing
        d.addCallback(self._check_for_pause)
        def _done(buffers):
            # trim off any padding added by the upload side
            segment = "".join(buffers)
            del buffers
            # we never send empty segments. If the data was an exact multiple
            # of the segment size, the last segment will be full.
            pad_size = mathutil.pad_size(self._size, self._segment_size)
            tail_size = self._segment_size - pad_size
            segment = segment[:tail_size]
            started_decrypt = time.time()
            self._output.write_segment(segment)
            if self._results:
                elapsed = time.time() - started_decrypt
                self._results.timings["cumulative_decrypt"] += elapsed
        d.addCallback(_done)
        return d

    def _done(self, res):
        self.log("download done")
        if self._results:
            now = time.time()
            self._results.timings["total"] = now - self._started
            self._results.timings["segments"] = now - self._started_fetching
        self._output.close()
        if self.check_crypttext_hash and self._crypttext_hash:
            _assert(self._crypttext_hash == self._output.crypttext_hash,
                    "bad crypttext_hash: computed=%s, expected=%s" %
                    (base32.b2a(self._output.crypttext_hash),
                     base32.b2a(self._crypttext_hash)))
        if self.check_plaintext_hash and self._plaintext_hash:
            _assert(self._plaintext_hash == self._output.plaintext_hash,
                    "bad plaintext_hash: computed=%s, expected=%s" %
                    (base32.b2a(self._output.plaintext_hash),
                     base32.b2a(self._plaintext_hash)))
        _assert(self._output.length == self._size,
                got=self._output.length, expected=self._size)
        return self._output.finish()

    def get_download_status(self):
        return self._status


class FileName:
    implements(IDownloadTarget)
    def __init__(self, filename):
        self._filename = filename
        self.f = None
    def open(self, size):
        self.f = open(self._filename, "wb")
        return self.f
    def write(self, data):
        self.f.write(data)
    def close(self):
        if self.f:
            self.f.close()
    def fail(self, why):
        if self.f:
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
    # TODO: in fact, this service only downloads immutable files (URI:CHK:).
    # It is scheduled to go away, to be replaced by filenode.download()
    implements(IDownloader)
    name = "downloader"
    MAX_DOWNLOAD_STATUSES = 10

    def __init__(self, stats_provider=None):
        service.MultiService.__init__(self)
        self.stats_provider = stats_provider
        self._all_downloads = weakref.WeakKeyDictionary() # for debugging
        self._all_download_statuses = weakref.WeakKeyDictionary()
        self._recent_download_statuses = []

    def download(self, u, t):
        assert self.parent
        assert self.running
        u = IFileURI(u)
        t = IDownloadTarget(t)
        assert t.write
        assert t.close

        assert isinstance(u, uri.CHKFileURI)
        if self.stats_provider:
            # these counters are meant for network traffic, and don't
            # include LIT files
            self.stats_provider.count('downloader.files_downloaded', 1)
            self.stats_provider.count('downloader.bytes_downloaded', u.get_size())
        dl = FileDownloader(self.parent, u, t)
        self._add_download(dl)
        d = dl.start()
        return d

    # utility functions
    def download_to_data(self, uri):
        return self.download(uri, Data())
    def download_to_filename(self, uri, filename):
        return self.download(uri, FileName(filename))
    def download_to_filehandle(self, uri, filehandle):
        return self.download(uri, FileHandle(filehandle))

    def _add_download(self, downloader):
        self._all_downloads[downloader] = None
        s = downloader.get_download_status()
        self._all_download_statuses[s] = None
        self._recent_download_statuses.append(s)
        while len(self._recent_download_statuses) > self.MAX_DOWNLOAD_STATUSES:
            self._recent_download_statuses.pop(0)

    def list_all_download_statuses(self):
        for ds in self._all_download_statuses:
            yield ds
