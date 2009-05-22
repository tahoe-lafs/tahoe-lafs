import os, random, weakref, itertools, time
from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IPushProducer, IConsumer
from twisted.application import service
from foolscap.api import DeadReferenceError, eventually

from allmydata.util import base32, deferredutil, hashutil, log, mathutil
from allmydata.util.assertutil import _assert, precondition
from allmydata.util.rrefutil import ServerFailure
from allmydata import codec, hashtree, uri
from allmydata.interfaces import IDownloadTarget, IDownloader, IFileURI, IVerifierURI, \
     IDownloadStatus, IDownloadResults, IValidatedThingProxy, NotEnoughSharesError, \
     UnableToFetchCriticalDownloadDataError
from allmydata.immutable import layout
from allmydata.monitor import Monitor
from pycryptopp.cipher.aes import AES

class IntegrityCheckReject(Exception):
    pass

class BadURIExtensionHashValue(IntegrityCheckReject):
    pass
class BadURIExtension(IntegrityCheckReject):
    pass
class UnsupportedErasureCodec(BadURIExtension):
    pass
class BadCrypttextHashValue(IntegrityCheckReject):
    pass
class BadOrMissingHash(IntegrityCheckReject):
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

class DecryptingTarget(log.PrefixingLogMixin):
    implements(IDownloadTarget, IConsumer)
    def __init__(self, target, key, _log_msg_id=None):
        precondition(IDownloadTarget.providedBy(target), target)
        self.target = target
        self._decryptor = AES(key)
        prefix = str(target)
        log.PrefixingLogMixin.__init__(self, "allmydata.immutable.download", _log_msg_id, prefix=prefix)
    # methods to satisfy the IConsumer interface
    def registerProducer(self, producer, streaming):
        if IConsumer.providedBy(self.target):
            self.target.registerProducer(producer, streaming)
    def unregisterProducer(self):
        if IConsumer.providedBy(self.target):
            self.target.unregisterProducer()
    def write(self, ciphertext):
        plaintext = self._decryptor.process(ciphertext)
        self.target.write(plaintext)
    def open(self, size):
        self.target.open(size)
    def close(self):
        self.target.close()
    def finish(self):
        return self.target.finish()
    # The following methods is just to pass through to the next target, and just because that
    # target might be a repairer.DownUpConnector, and just because the current CHKUpload object
    # expects to find the storage index in its Uploadable.
    def set_storageindex(self, storageindex):
        self.target.set_storageindex(storageindex)
    def set_encodingparams(self, encodingparams):
        self.target.set_encodingparams(encodingparams)

class ValidatedThingObtainer:
    def __init__(self, validatedthingproxies, debugname, log_id):
        self._validatedthingproxies = validatedthingproxies
        self._debugname = debugname
        self._log_id = log_id

    def _bad(self, f, validatedthingproxy):
        failtype = f.trap(ServerFailure, IntegrityCheckReject, layout.LayoutInvalid, layout.ShareVersionIncompatible, DeadReferenceError)
        level = log.WEIRD
        if f.check(DeadReferenceError):
            level = log.UNUSUAL
        elif f.check(ServerFailure):
            level = log.WEIRD
        else:
            level = log.SCARY
        log.msg(parent=self._log_id, facility="tahoe.immutable.download",
                format="operation %(op)s from validatedthingproxy %(validatedthingproxy)s failed",
                op=self._debugname, validatedthingproxy=str(validatedthingproxy),
                failure=f, level=level, umid="JGXxBA")
        if not self._validatedthingproxies:
            raise UnableToFetchCriticalDownloadDataError("ran out of peers, last error was %s" % (f,))
        # try again with a different one
        d = self._try_the_next_one()
        return d

    def _try_the_next_one(self):
        vtp = self._validatedthingproxies.pop(0)
        d = vtp.start() # start() obtains, validates, and callsback-with the thing or else errbacks
        d.addErrback(self._bad, vtp)
        return d

    def start(self):
        return self._try_the_next_one()

class ValidatedCrypttextHashTreeProxy:
    implements(IValidatedThingProxy)
    """ I am a front-end for a remote crypttext hash tree using a local ReadBucketProxy -- I use
    its get_crypttext_hashes() method and offer the Validated Thing protocol (i.e., I have a
    start() method that fires with self once I get a valid one). """
    def __init__(self, readbucketproxy, crypttext_hash_tree, num_segments, fetch_failures=None):
        # fetch_failures is for debugging -- see test_encode.py
        self._readbucketproxy = readbucketproxy
        self._num_segments = num_segments
        self._fetch_failures = fetch_failures
        self._crypttext_hash_tree = crypttext_hash_tree

    def _validate(self, proposal):
        ct_hashes = dict(list(enumerate(proposal)))
        try:
            self._crypttext_hash_tree.set_hashes(ct_hashes)
        except (hashtree.BadHashError, hashtree.NotEnoughHashesError), le:
            if self._fetch_failures is not None:
                self._fetch_failures["crypttext_hash_tree"] += 1
            raise BadOrMissingHash(le)
        # If we now have enough of the crypttext hash tree to integrity-check *any* segment of ciphertext, then we are done.
        # TODO: It would have better alacrity if we downloaded only part of the crypttext hash tree at a time.
        for segnum in range(self._num_segments):
            if self._crypttext_hash_tree.needed_hashes(segnum):
                raise BadOrMissingHash("not enough hashes to validate segment number %d" % (segnum,))
        return self

    def start(self):
        d = self._readbucketproxy.get_crypttext_hashes()
        d.addCallback(self._validate)
        return d

class ValidatedExtendedURIProxy:
    implements(IValidatedThingProxy)
    """ I am a front-end for a remote UEB (using a local ReadBucketProxy), responsible for
    retrieving and validating the elements from the UEB. """

    def __init__(self, readbucketproxy, verifycap, fetch_failures=None):
        # fetch_failures is for debugging -- see test_encode.py
        self._fetch_failures = fetch_failures
        self._readbucketproxy = readbucketproxy
        precondition(IVerifierURI.providedBy(verifycap), verifycap)
        self._verifycap = verifycap

        # required
        self.segment_size = None
        self.crypttext_root_hash = None
        self.share_root_hash = None

        # computed
        self.block_size = None
        self.share_size = None
        self.num_segments = None
        self.tail_data_size = None
        self.tail_segment_size = None

        # optional
        self.crypttext_hash = None

    def __str__(self):
        return "<%s %s>" % (self.__class__.__name__, self._verifycap.to_string())

    def _check_integrity(self, data):
        h = hashutil.uri_extension_hash(data)
        if h != self._verifycap.uri_extension_hash:
            msg = ("The copy of uri_extension we received from %s was bad: wanted %s, got %s" %
                   (self._readbucketproxy, base32.b2a(self._verifycap.uri_extension_hash), base32.b2a(h)))
            if self._fetch_failures is not None:
                self._fetch_failures["uri_extension"] += 1
            raise BadURIExtensionHashValue(msg)
        else:
            return data

    def _parse_and_validate(self, data):
        self.share_size = mathutil.div_ceil(self._verifycap.size, self._verifycap.needed_shares)

        d = uri.unpack_extension(data)

        # There are several kinds of things that can be found in a UEB.  First, things that we
        # really need to learn from the UEB in order to do this download. Next: things which are
        # optional but not redundant -- if they are present in the UEB they will get used. Next,
        # things that are optional and redundant. These things are required to be consistent:
        # they don't have to be in the UEB, but if they are in the UEB then they will be checked
        # for consistency with the already-known facts, and if they are inconsistent then an
        # exception will be raised. These things aren't actually used -- they are just tested
        # for consistency and ignored. Finally: things which are deprecated -- they ought not be
        # in the UEB at all, and if they are present then a warning will be logged but they are
        # otherwise ignored.

       # First, things that we really need to learn from the UEB: segment_size,
        # crypttext_root_hash, and share_root_hash.
        self.segment_size = d['segment_size']

        self.block_size = mathutil.div_ceil(self.segment_size, self._verifycap.needed_shares)
        self.num_segments = mathutil.div_ceil(self._verifycap.size, self.segment_size)

        self.tail_data_size = self._verifycap.size % self.segment_size
        if not self.tail_data_size:
            self.tail_data_size = self.segment_size
        # padding for erasure code
        self.tail_segment_size = mathutil.next_multiple(self.tail_data_size, self._verifycap.needed_shares)

        # Ciphertext hash tree root is mandatory, so that there is at most one ciphertext that
        # matches this read-cap or verify-cap.  The integrity check on the shares is not
        # sufficient to prevent the original encoder from creating some shares of file A and
        # other shares of file B.
        self.crypttext_root_hash = d['crypttext_root_hash']

        self.share_root_hash = d['share_root_hash']


        # Next: things that are optional and not redundant: crypttext_hash
        if d.has_key('crypttext_hash'):
            self.crypttext_hash = d['crypttext_hash']
            if len(self.crypttext_hash) != hashutil.CRYPTO_VAL_SIZE:
                raise BadURIExtension('crypttext_hash is required to be hashutil.CRYPTO_VAL_SIZE bytes, not %s bytes' % (len(self.crypttext_hash),))


        # Next: things that are optional, redundant, and required to be consistent: codec_name,
        # codec_params, tail_codec_params, num_segments, size, needed_shares, total_shares
        if d.has_key('codec_name'):
            if d['codec_name'] != "crs":
                raise UnsupportedErasureCodec(d['codec_name'])

        if d.has_key('codec_params'):
            ucpss, ucpns, ucpts = codec.parse_params(d['codec_params'])
            if ucpss != self.segment_size:
                raise BadURIExtension("inconsistent erasure code params: ucpss: %s != "
                                      "self.segment_size: %s" % (ucpss, self.segment_size))
            if ucpns != self._verifycap.needed_shares:
                raise BadURIExtension("inconsistent erasure code params: ucpns: %s != "
                                      "self._verifycap.needed_shares: %s" % (ucpns,
                                                                             self._verifycap.needed_shares))
            if ucpts != self._verifycap.total_shares:
                raise BadURIExtension("inconsistent erasure code params: ucpts: %s != "
                                      "self._verifycap.total_shares: %s" % (ucpts,
                                                                            self._verifycap.total_shares))

        if d.has_key('tail_codec_params'):
            utcpss, utcpns, utcpts = codec.parse_params(d['tail_codec_params'])
            if utcpss != self.tail_segment_size:
                raise BadURIExtension("inconsistent erasure code params: utcpss: %s != "
                                      "self.tail_segment_size: %s, self._verifycap.size: %s, "
                                      "self.segment_size: %s, self._verifycap.needed_shares: %s"
                                      % (utcpss, self.tail_segment_size, self._verifycap.size,
                                         self.segment_size, self._verifycap.needed_shares))
            if utcpns != self._verifycap.needed_shares:
                raise BadURIExtension("inconsistent erasure code params: utcpns: %s != "
                                      "self._verifycap.needed_shares: %s" % (utcpns,
                                                                             self._verifycap.needed_shares))
            if utcpts != self._verifycap.total_shares:
                raise BadURIExtension("inconsistent erasure code params: utcpts: %s != "
                                      "self._verifycap.total_shares: %s" % (utcpts,
                                                                            self._verifycap.total_shares))

        if d.has_key('num_segments'):
            if d['num_segments'] != self.num_segments:
                raise BadURIExtension("inconsistent num_segments: size: %s, "
                                      "segment_size: %s, computed_num_segments: %s, "
                                      "ueb_num_segments: %s" % (self._verifycap.size,
                                                                self.segment_size,
                                                                self.num_segments, d['num_segments']))

        if d.has_key('size'):
            if d['size'] != self._verifycap.size:
                raise BadURIExtension("inconsistent size: URI size: %s, UEB size: %s" %
                                      (self._verifycap.size, d['size']))

        if d.has_key('needed_shares'):
            if d['needed_shares'] != self._verifycap.needed_shares:
                raise BadURIExtension("inconsistent needed shares: URI needed shares: %s, UEB "
                                      "needed shares: %s" % (self._verifycap.total_shares,
                                                             d['needed_shares']))

        if d.has_key('total_shares'):
            if d['total_shares'] != self._verifycap.total_shares:
                raise BadURIExtension("inconsistent total shares: URI total shares: %s, UEB "
                                      "total shares: %s" % (self._verifycap.total_shares,
                                                            d['total_shares']))

        # Finally, things that are deprecated and ignored: plaintext_hash, plaintext_root_hash
        if d.get('plaintext_hash'):
            log.msg("Found plaintext_hash in UEB. This field is deprecated for security reasons "
                    "and is no longer used.  Ignoring.  %s" % (self,))
        if d.get('plaintext_root_hash'):
            log.msg("Found plaintext_root_hash in UEB. This field is deprecated for security "
                    "reasons and is no longer used.  Ignoring.  %s" % (self,))

        return self

    def start(self):
        """ Fetch the UEB from bucket, compare its hash to the hash from verifycap, then parse
        it.  Returns a deferred which is called back with self once the fetch is successful, or
        is erred back if it fails. """
        d = self._readbucketproxy.get_uri_extension()
        d.addCallback(self._check_integrity)
        d.addCallback(self._parse_and_validate)
        return d

class ValidatedReadBucketProxy(log.PrefixingLogMixin):
    """I am a front-end for a remote storage bucket, responsible for retrieving and validating
    data from that bucket.

    My get_block() method is used by BlockDownloaders.
    """

    def __init__(self, sharenum, bucket, share_hash_tree, num_blocks, block_size, share_size):
        """ share_hash_tree is required to have already been initialized with the root hash
        (the number-0 hash), using the share_root_hash from the UEB """
        precondition(share_hash_tree[0] is not None, share_hash_tree)
        prefix = "%d-%s-%s" % (sharenum, bucket, base32.b2a_l(share_hash_tree[0][:8], 60))
        log.PrefixingLogMixin.__init__(self, facility="tahoe.immutable.download", prefix=prefix)
        self.sharenum = sharenum
        self.bucket = bucket
        self.share_hash_tree = share_hash_tree
        self.num_blocks = num_blocks
        self.block_size = block_size
        self.share_size = share_size
        self.block_hash_tree = hashtree.IncompleteHashTree(self.num_blocks)

    def get_block(self, blocknum):
        # the first time we use this bucket, we need to fetch enough elements
        # of the share hash tree to validate it from our share hash up to the
        # hashroot.
        if self.share_hash_tree.needed_hashes(self.sharenum):
            d1 = self.bucket.get_share_hashes()
        else:
            d1 = defer.succeed([])

        # We might need to grab some elements of our block hash tree, to
        # validate the requested block up to the share hash.
        blockhashesneeded = self.block_hash_tree.needed_hashes(blocknum, include_leaf=True)
        # We don't need the root of the block hash tree, as that comes in the share tree.
        blockhashesneeded.discard(0)
        d2 = self.bucket.get_block_hashes(blockhashesneeded)

        if blocknum < self.num_blocks-1:
            thisblocksize = self.block_size
        else:
            thisblocksize = self.share_size % self.block_size
            if thisblocksize == 0:
                thisblocksize = self.block_size
        d3 = self.bucket.get_block_data(blocknum, self.block_size, thisblocksize)

        dl = deferredutil.gatherResults([d1, d2, d3])
        dl.addCallback(self._got_data, blocknum)
        return dl

    def _got_data(self, results, blocknum):
        precondition(blocknum < self.num_blocks, self, blocknum, self.num_blocks)
        sharehashes, blockhashes, blockdata = results
        try:
            sharehashes = dict(sharehashes)
        except ValueError, le:
            le.args = tuple(le.args + (sharehashes,))
            raise
        blockhashes = dict(enumerate(blockhashes))

        candidate_share_hash = None # in case we log it in the except block below
        blockhash = None # in case we log it in the except block below

        try:
            if self.share_hash_tree.needed_hashes(self.sharenum):
                # This will raise exception if the values being passed do not match the root
                # node of self.share_hash_tree.
                try:
                    self.share_hash_tree.set_hashes(sharehashes)
                except IndexError, le:
                    # Weird -- sharehashes contained index numbers outside of the range that fit
                    # into this hash tree.
                    raise BadOrMissingHash(le)

            # To validate a block we need the root of the block hash tree, which is also one of
            # the leafs of the share hash tree, and is called "the share hash".
            if not self.block_hash_tree[0]: # empty -- no root node yet
                # Get the share hash from the share hash tree.
                share_hash = self.share_hash_tree.get_leaf(self.sharenum)
                if not share_hash:
                    raise hashtree.NotEnoughHashesError # No root node in block_hash_tree and also the share hash wasn't sent by the server.
                self.block_hash_tree.set_hashes({0: share_hash})

            if self.block_hash_tree.needed_hashes(blocknum):
                self.block_hash_tree.set_hashes(blockhashes)

            blockhash = hashutil.block_hash(blockdata)
            self.block_hash_tree.set_hashes(leaves={blocknum: blockhash})
            #self.log("checking block_hash(shareid=%d, blocknum=%d) len=%d "
            #        "%r .. %r: %s" %
            #        (self.sharenum, blocknum, len(blockdata),
            #         blockdata[:50], blockdata[-50:], base32.b2a(blockhash)))

        except (hashtree.BadHashError, hashtree.NotEnoughHashesError), le:
            # log.WEIRD: indicates undetected disk/network error, or more
            # likely a programming error
            self.log("hash failure in block=%d, shnum=%d on %s" %
                    (blocknum, self.sharenum, self.bucket))
            if self.block_hash_tree.needed_hashes(blocknum):
                self.log(""" failure occurred when checking the block_hash_tree.
                This suggests that either the block data was bad, or that the
                block hashes we received along with it were bad.""")
            else:
                self.log(""" the failure probably occurred when checking the
                share_hash_tree, which suggests that the share hashes we
                received from the remote peer were bad.""")
            self.log(" have candidate_share_hash: %s" % bool(candidate_share_hash))
            self.log(" block length: %d" % len(blockdata))
            self.log(" block hash: %s" % base32.b2a_or_none(blockhash))
            if len(blockdata) < 100:
                self.log(" block data: %r" % (blockdata,))
            else:
                self.log(" block data start/end: %r .. %r" %
                        (blockdata[:50], blockdata[-50:]))
            self.log(" share hash tree:\n" + self.share_hash_tree.dump())
            self.log(" block hash tree:\n" + self.block_hash_tree.dump())
            lines = []
            for i,h in sorted(sharehashes.items()):
                lines.append("%3d: %s" % (i, base32.b2a_or_none(h)))
            self.log(" sharehashes:\n" + "\n".join(lines) + "\n")
            lines = []
            for i,h in blockhashes.items():
                lines.append("%3d: %s" % (i, base32.b2a_or_none(h)))
            log.msg(" blockhashes:\n" + "\n".join(lines) + "\n")
            raise BadOrMissingHash(le)

        # If we made it here, the block is good. If the hash trees didn't
        # like what they saw, they would have raised a BadHashError, causing
        # our caller to see a Failure and thus ignore this block (as well as
        # dropping this bucket).
        return blockdata



class BlockDownloader(log.PrefixingLogMixin):
    """I am responsible for downloading a single block (from a single bucket)
    for a single segment.

    I am a child of the SegmentDownloader.
    """

    def __init__(self, vbucket, blocknum, parent, results):
        precondition(isinstance(vbucket, ValidatedReadBucketProxy), vbucket)
        prefix = "%s-%d" % (vbucket, blocknum)
        log.PrefixingLogMixin.__init__(self, facility="tahoe.immutable.download", prefix=prefix)
        self.vbucket = vbucket
        self.blocknum = blocknum
        self.parent = parent
        self.results = results

    def start(self, segnum):
        self.log("get_block(segnum=%d)" % segnum)
        started = time.time()
        d = self.vbucket.get_block(segnum)
        d.addCallbacks(self._hold_block, self._got_block_error,
                       callbackArgs=(started,))
        return d

    def _hold_block(self, data, started):
        if self.results:
            elapsed = time.time() - started
            peerid = self.vbucket.bucket.get_peerid()
            if peerid not in self.results.timings["fetch_per_server"]:
                self.results.timings["fetch_per_server"][peerid] = []
            self.results.timings["fetch_per_server"][peerid].append(elapsed)
        self.log("got block")
        self.parent.hold_block(self.blocknum, data)

    def _got_block_error(self, f):
        failtype = f.trap(ServerFailure, IntegrityCheckReject, layout.LayoutInvalid, layout.ShareVersionIncompatible)
        if f.check(ServerFailure):
            level = log.UNUSUAL
        else:
            level = log.WEIRD
        self.log("failure to get block", level=level, umid="5Z4uHQ")
        if self.results:
            peerid = self.vbucket.bucket.get_peerid()
            self.results.server_problems[peerid] = str(f)
        self.parent.bucket_failed(self.vbucket)

class SegmentDownloader:
    """I am responsible for downloading all the blocks for a single segment
    of data.

    I am a child of the CiphertextDownloader.
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
            assert isinstance(vbucket, ValidatedReadBucketProxy), vbucket
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

class CiphertextDownloader(log.PrefixingLogMixin):
    """ I download shares, check their integrity, then decode them, check the integrity of the
    resulting ciphertext, then and write it to my target. Before I send any new request to a
    server, I always ask the "monitor" object that was passed into my constructor whether this
    task has been cancelled (by invoking its raise_if_cancelled() method). """
    implements(IPushProducer)
    _status = None

    def __init__(self, client, v, target, monitor):

        precondition(IVerifierURI.providedBy(v), v)
        precondition(IDownloadTarget.providedBy(target), target)

        prefix=base32.b2a_l(v.storage_index[:8], 60)
        log.PrefixingLogMixin.__init__(self, facility="tahoe.immutable.download", prefix=prefix)
        self._client = client

        self._verifycap = v
        self._storage_index = v.storage_index
        self._uri_extension_hash = v.uri_extension_hash

        self._started = time.time()
        self._status = s = DownloadStatus()
        s.set_status("Starting")
        s.set_storage_index(self._storage_index)
        s.set_size(self._verifycap.size)
        s.set_helper(False)
        s.set_active(True)

        self._results = DownloadResults()
        s.set_results(self._results)
        self._results.file_size = self._verifycap.size
        self._results.timings["servers_peer_selection"] = {}
        self._results.timings["fetch_per_server"] = {}
        self._results.timings["cumulative_fetch"] = 0.0
        self._results.timings["cumulative_decode"] = 0.0
        self._results.timings["cumulative_decrypt"] = 0.0
        self._results.timings["paused"] = 0.0

        self._paused = False
        self._stopped = False
        if IConsumer.providedBy(target):
            target.registerProducer(self, True)
        self._target = target
        self._target.set_storageindex(self._storage_index) # Repairer (uploader) needs the storageindex.
        self._monitor = monitor
        self._opened = False

        self.active_buckets = {} # k: shnum, v: bucket
        self._share_buckets = [] # list of (sharenum, bucket) tuples
        self._share_vbuckets = {} # k: shnum, v: set of ValidatedBuckets

        self._fetch_failures = {"uri_extension": 0, "crypttext_hash_tree": 0, }

        self._ciphertext_hasher = hashutil.crypttext_hasher()

        self._bytes_done = 0
        self._status.set_progress(float(self._bytes_done)/self._verifycap.size)

        # _got_uri_extension() will create the following:
        # self._crypttext_hash_tree
        # self._share_hash_tree
        # self._current_segnum = 0
        # self._vup # ValidatedExtendedURIProxy

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
        # now get the uri_extension block from somebody and integrity check it and parse and validate its contents
        d.addCallback(self._obtain_uri_extension)
        d.addCallback(self._get_crypttext_hash_tree)
        # once we know that, we can download blocks from everybody
        d.addCallback(self._download_all_segments)
        def _finished(res):
            if self._status:
                self._status.set_status("Finished")
                self._status.set_active(False)
                self._status.set_paused(False)
            if IConsumer.providedBy(self._target):
                self._target.unregisterProducer()
            return res
        d.addBoth(_finished)
        def _failed(why):
            if self._status:
                self._status.set_status("Failed")
                self._status.set_active(False)
            if why.check(DownloadStopped):
                # DownloadStopped just means the consumer aborted the download; not so scary.
                self.log("download stopped", level=log.UNUSUAL)
            else:
                # This is really unusual, and deserves maximum forensics.
                self.log("download failed!", failure=why, level=log.SCARY, umid="lp1vaQ")
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
            b = layout.ReadBucketProxy(bucket, peerid, self._storage_index)
            self.add_share_bucket(sharenum, b)

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
        self.log("Error during get_buckets", failure=f, level=level,
                         umid="3uuBUQ")

    def bucket_failed(self, vbucket):
        shnum = vbucket.sharenum
        del self.active_buckets[shnum]
        s = self._share_vbuckets[shnum]
        # s is a set of ValidatedReadBucketProxy instances
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

        if len(self._share_buckets) < self._verifycap.needed_shares:
            raise NotEnoughSharesError("Failed to get enough shareholders",
                                       len(self._share_buckets),
                                       self._verifycap.needed_shares)

        #for s in self._share_vbuckets.values():
        #    for vb in s:
        #        assert isinstance(vb, ValidatedReadBucketProxy), \
        #               "vb is %s but should be a ValidatedReadBucketProxy" % (vb,)

    def _obtain_uri_extension(self, ignored):
        # all shareholders are supposed to have a copy of uri_extension, and
        # all are supposed to be identical. We compute the hash of the data
        # that comes back, and compare it against the version in our URI. If
        # they don't match, ignore their data and try someone else.
        if self._status:
            self._status.set_status("Obtaining URI Extension")

        uri_extension_fetch_started = time.time()

        vups = []
        for sharenum, bucket in self._share_buckets:
            vups.append(ValidatedExtendedURIProxy(bucket, self._verifycap, self._fetch_failures))
        vto = ValidatedThingObtainer(vups, debugname="vups", log_id=self._parentmsgid)
        d = vto.start()

        def _got_uri_extension(vup):
            precondition(isinstance(vup, ValidatedExtendedURIProxy), vup)
            if self._results:
                elapsed = time.time() - uri_extension_fetch_started
                self._results.timings["uri_extension"] = elapsed

            self._vup = vup
            self._codec = codec.CRSDecoder()
            self._codec.set_params(self._vup.segment_size, self._verifycap.needed_shares, self._verifycap.total_shares)
            self._tail_codec = codec.CRSDecoder()
            self._tail_codec.set_params(self._vup.tail_segment_size, self._verifycap.needed_shares, self._verifycap.total_shares)

            self._current_segnum = 0

            self._share_hash_tree = hashtree.IncompleteHashTree(self._verifycap.total_shares)
            self._share_hash_tree.set_hashes({0: vup.share_root_hash})

            self._crypttext_hash_tree = hashtree.IncompleteHashTree(self._vup.num_segments)
            self._crypttext_hash_tree.set_hashes({0: self._vup.crypttext_root_hash})

            # Repairer (uploader) needs the encodingparams.
            self._target.set_encodingparams((
                self._verifycap.needed_shares,
                self._verifycap.total_shares, # I don't think the target actually cares about "happy".
                self._verifycap.total_shares,
                self._vup.segment_size
                ))
        d.addCallback(_got_uri_extension)
        return d

    def _get_crypttext_hash_tree(self, res):
        vchtps = []
        for sharenum, bucket in self._share_buckets:
            vchtp = ValidatedCrypttextHashTreeProxy(bucket, self._crypttext_hash_tree, self._vup.num_segments, self._fetch_failures)
            vchtps.append(vchtp)

        _get_crypttext_hash_tree_started = time.time()
        if self._status:
            self._status.set_status("Retrieving crypttext hash tree")

        vto = ValidatedThingObtainer(vchtps , debugname="vchtps", log_id=self._parentmsgid)
        d = vto.start()

        def _got_crypttext_hash_tree(res):
            # Good -- the self._crypttext_hash_tree that we passed to vchtp is now populated
            # with hashes.
            if self._results:
                elapsed = time.time() - _get_crypttext_hash_tree_started
                self._results.timings["hashtrees"] = elapsed
        d.addCallback(_got_crypttext_hash_tree)
        return d

    def _activate_enough_buckets(self):
        """either return a mapping from shnum to a ValidatedReadBucketProxy that can
        provide data for that share, or raise NotEnoughSharesError"""

        while len(self.active_buckets) < self._verifycap.needed_shares:
            # need some more
            handled_shnums = set(self.active_buckets.keys())
            available_shnums = set(self._share_vbuckets.keys())
            potential_shnums = list(available_shnums - handled_shnums)
            if len(potential_shnums) < (self._verifycap.needed_shares - len(self.active_buckets)):
                have = len(potential_shnums) + len(self.active_buckets)
                raise NotEnoughSharesError("Unable to activate enough shares",
                                           have, self._verifycap.needed_shares)
            # For the next share, choose a primary share if available, else a randomly chosen
            # secondary share.
            potential_shnums.sort()
            if potential_shnums[0] < self._verifycap.needed_shares:
                shnum = potential_shnums[0]
            else:
                shnum = random.choice(potential_shnums)
            # and a random bucket that will provide it
            validated_bucket = random.choice(list(self._share_vbuckets[shnum]))
            self.active_buckets[shnum] = validated_bucket
        return self.active_buckets


    def _download_all_segments(self, res):
        for sharenum, bucket in self._share_buckets:
            vbucket = ValidatedReadBucketProxy(sharenum, bucket, self._share_hash_tree, self._vup.num_segments, self._vup.block_size, self._vup.share_size)
            self._share_vbuckets.setdefault(sharenum, set()).add(vbucket)

        # after the above code, self._share_vbuckets contains enough
        # buckets to complete the download, and some extra ones to
        # tolerate some buckets dropping out or having
        # errors. self._share_vbuckets is a dictionary that maps from
        # shnum to a set of ValidatedBuckets, which themselves are
        # wrappers around RIBucketReader references.
        self.active_buckets = {} # k: shnum, v: ValidatedReadBucketProxy instance

        self._started_fetching = time.time()

        d = defer.succeed(None)
        for segnum in range(self._vup.num_segments):
            d.addCallback(self._download_segment, segnum)
            # this pause, at the end of write, prevents pre-fetch from
            # happening until the consumer is ready for more data.
            d.addCallback(self._check_for_pause)
        return d

    def _check_for_pause(self, res):
        if self._paused:
            d = defer.Deferred()
            self._paused.addCallback(lambda ignored: d.callback(res))
            return d
        if self._stopped:
            raise DownloadStopped("our Consumer called stopProducing()")
        self._monitor.raise_if_cancelled()
        return res

    def _download_segment(self, res, segnum):
        if self._status:
            self._status.set_status("Downloading segment %d of %d" %
                                    (segnum+1, self._vup.num_segments))
        self.log("downloading seg#%d of %d (%d%%)"
                 % (segnum, self._vup.num_segments,
                    100.0 * segnum / self._vup.num_segments))
        # memory footprint: when the SegmentDownloader finishes pulling down
        # all shares, we have 1*segment_size of usage.
        segmentdler = SegmentDownloader(self, segnum, self._verifycap.needed_shares,
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
        if segnum + 1 == self._vup.num_segments:
            codec = self._tail_codec
        else:
            codec = self._codec
        d.addCallback(lambda (shares, shareids): codec.decode(shares, shareids))
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
        d.addCallback(self._got_segment)
        return d

    def _got_segment(self, buffers):
        precondition(self._crypttext_hash_tree)
        started_decrypt = time.time()
        self._status.set_progress(float(self._current_segnum)/self._verifycap.size)

        if self._current_segnum + 1 == self._vup.num_segments:
            # This is the last segment.
            # Trim off any padding added by the upload side.  We never send empty segments. If
            # the data was an exact multiple of the segment size, the last segment will be full.
            tail_buf_size = mathutil.div_ceil(self._vup.tail_segment_size, self._verifycap.needed_shares)
            num_buffers_used = mathutil.div_ceil(self._vup.tail_data_size, tail_buf_size)
            # Remove buffers which don't contain any part of the tail.
            del buffers[num_buffers_used:]
            # Remove the past-the-tail-part of the last buffer.
            tail_in_last_buf = self._vup.tail_data_size % tail_buf_size
            if tail_in_last_buf == 0:
                tail_in_last_buf = tail_buf_size
            buffers[-1] = buffers[-1][:tail_in_last_buf]

        # First compute the hash of this segment and check that it fits.
        ch = hashutil.crypttext_segment_hasher()
        for buffer in buffers:
            self._ciphertext_hasher.update(buffer)
            ch.update(buffer)
        self._crypttext_hash_tree.set_hashes(leaves={self._current_segnum: ch.digest()})

        # Then write this segment to the target.
        if not self._opened:
            self._opened = True
            self._target.open(self._verifycap.size)

        for buffer in buffers:
            self._target.write(buffer)
            self._bytes_done += len(buffer)

        self._status.set_progress(float(self._bytes_done)/self._verifycap.size)
        self._current_segnum += 1

        if self._results:
            elapsed = time.time() - started_decrypt
            self._results.timings["cumulative_decrypt"] += elapsed

    def _done(self, res):
        self.log("download done")
        if self._results:
            now = time.time()
            self._results.timings["total"] = now - self._started
            self._results.timings["segments"] = now - self._started_fetching
        if self._vup.crypttext_hash:
            _assert(self._vup.crypttext_hash == self._ciphertext_hasher.digest(),
                    "bad crypttext_hash: computed=%s, expected=%s" %
                    (base32.b2a(self._ciphertext_hasher.digest()),
                     base32.b2a(self._vup.crypttext_hash)))
        _assert(self._bytes_done == self._verifycap.size, self._bytes_done, self._verifycap.size)
        self._status.set_progress(1)
        self._target.close()
        return self._target.finish()
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
    # The following methods are just because the target might be a repairer.DownUpConnector,
    # and just because the current CHKUpload object expects to find the storage index and
    # encoding parameters in its Uploadable.
    def set_storageindex(self, storageindex):
        pass
    def set_encodingparams(self, encodingparams):
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
    # The following methods are just because the target might be a repairer.DownUpConnector,
    # and just because the current CHKUpload object expects to find the storage index and
    # encoding parameters in its Uploadable.
    def set_storageindex(self, storageindex):
        pass
    def set_encodingparams(self, encodingparams):
        pass

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
    # The following methods are just because the target might be a repairer.DownUpConnector,
    # and just because the current CHKUpload object expects to find the storage index and
    # encoding parameters in its Uploadable.
    def set_storageindex(self, storageindex):
        pass
    def set_encodingparams(self, encodingparams):
        pass

class ConsumerAdapter:
    implements(IDownloadTarget, IConsumer)
    def __init__(self, consumer):
        self._consumer = consumer

    def registerProducer(self, producer, streaming):
        self._consumer.registerProducer(producer, streaming)
    def unregisterProducer(self):
        self._consumer.unregisterProducer()

    def open(self, size):
        pass
    def write(self, data):
        self._consumer.write(data)
    def close(self):
        pass

    def fail(self, why):
        pass
    def register_canceller(self, cb):
        pass
    def finish(self):
        return self._consumer
    # The following methods are just because the target might be a repairer.DownUpConnector,
    # and just because the current CHKUpload object expects to find the storage index and
    # encoding parameters in its Uploadable.
    def set_storageindex(self, storageindex):
        pass
    def set_encodingparams(self, encodingparams):
        pass


class Downloader(service.MultiService):
    """I am a service that allows file downloading.
    """
    # TODO: in fact, this service only downloads immutable files (URI:CHK:).
    # It is scheduled to go away, to be replaced by filenode.download()
    implements(IDownloader)
    name = "downloader"

    def __init__(self, stats_provider=None):
        service.MultiService.__init__(self)
        self.stats_provider = stats_provider
        self._all_downloads = weakref.WeakKeyDictionary() # for debugging

    def download(self, u, t, _log_msg_id=None, monitor=None, history=None):
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

        target = DecryptingTarget(t, u.key, _log_msg_id=_log_msg_id)
        if not monitor:
            monitor=Monitor()
        dl = CiphertextDownloader(self.parent, u.get_verify_cap(), target, monitor=monitor)
        self._all_downloads[dl] = None
        if history:
            history.add_download(dl.get_download_status())
        d = dl.start()
        return d

    # utility functions
    def download_to_data(self, uri, _log_msg_id=None, history=None):
        return self.download(uri, Data(), _log_msg_id=_log_msg_id, history=history)
    def download_to_filename(self, uri, filename, _log_msg_id=None):
        return self.download(uri, FileName(filename), _log_msg_id=_log_msg_id)
    def download_to_filehandle(self, uri, filehandle, _log_msg_id=None):
        return self.download(uri, FileHandle(filehandle), _log_msg_id=_log_msg_id)
