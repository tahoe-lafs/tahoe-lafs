from zope.interface import implements
from twisted.internet import defer
from foolscap.api import DeadReferenceError, RemoteException
from allmydata import hashtree, codec, uri
from allmydata.interfaces import IValidatedThingProxy, IVerifierURI
from allmydata.hashtree import IncompleteHashTree
from allmydata.check_results import CheckResults
from allmydata.uri import CHKFileVerifierURI
from allmydata.util.assertutil import precondition
from allmydata.util import base32, deferredutil, dictutil, log, mathutil
from allmydata.util.hashutil import file_renewal_secret_hash, \
     file_cancel_secret_hash, bucket_renewal_secret_hash, \
     bucket_cancel_secret_hash, uri_extension_hash, CRYPTO_VAL_SIZE, \
     block_hash

from allmydata.immutable import layout

class IntegrityCheckReject(Exception):
    pass
class BadURIExtension(IntegrityCheckReject):
    pass
class BadURIExtensionHashValue(IntegrityCheckReject):
    pass
class BadOrMissingHash(IntegrityCheckReject):
    pass
class UnsupportedErasureCodec(BadURIExtension):
    pass

class ValidatedExtendedURIProxy:
    implements(IValidatedThingProxy)
    """ I am a front-end for a remote UEB (using a local ReadBucketProxy),
    responsible for retrieving and validating the elements from the UEB."""

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
        h = uri_extension_hash(data)
        if h != self._verifycap.uri_extension_hash:
            msg = ("The copy of uri_extension we received from %s was bad: wanted %s, got %s" %
                   (self._readbucketproxy,
                    base32.b2a(self._verifycap.uri_extension_hash),
                    base32.b2a(h)))
            if self._fetch_failures is not None:
                self._fetch_failures["uri_extension"] += 1
            raise BadURIExtensionHashValue(msg)
        else:
            return data

    def _parse_and_validate(self, data):
        self.share_size = mathutil.div_ceil(self._verifycap.size,
                                            self._verifycap.needed_shares)

        d = uri.unpack_extension(data)

        # There are several kinds of things that can be found in a UEB.
        # First, things that we really need to learn from the UEB in order to
        # do this download. Next: things which are optional but not redundant
        # -- if they are present in the UEB they will get used. Next, things
        # that are optional and redundant. These things are required to be
        # consistent: they don't have to be in the UEB, but if they are in
        # the UEB then they will be checked for consistency with the
        # already-known facts, and if they are inconsistent then an exception
        # will be raised. These things aren't actually used -- they are just
        # tested for consistency and ignored. Finally: things which are
        # deprecated -- they ought not be in the UEB at all, and if they are
        # present then a warning will be logged but they are otherwise
        # ignored.

        # First, things that we really need to learn from the UEB:
        # segment_size, crypttext_root_hash, and share_root_hash.
        self.segment_size = d['segment_size']

        self.block_size = mathutil.div_ceil(self.segment_size,
                                            self._verifycap.needed_shares)
        self.num_segments = mathutil.div_ceil(self._verifycap.size,
                                              self.segment_size)

        self.tail_data_size = self._verifycap.size % self.segment_size
        if not self.tail_data_size:
            self.tail_data_size = self.segment_size
        # padding for erasure code
        self.tail_segment_size = mathutil.next_multiple(self.tail_data_size,
                                                        self._verifycap.needed_shares)

        # Ciphertext hash tree root is mandatory, so that there is at most
        # one ciphertext that matches this read-cap or verify-cap. The
        # integrity check on the shares is not sufficient to prevent the
        # original encoder from creating some shares of file A and other
        # shares of file B.
        self.crypttext_root_hash = d['crypttext_root_hash']

        self.share_root_hash = d['share_root_hash']


        # Next: things that are optional and not redundant: crypttext_hash
        if d.has_key('crypttext_hash'):
            self.crypttext_hash = d['crypttext_hash']
            if len(self.crypttext_hash) != CRYPTO_VAL_SIZE:
                raise BadURIExtension('crypttext_hash is required to be hashutil.CRYPTO_VAL_SIZE bytes, not %s bytes' % (len(self.crypttext_hash),))


        # Next: things that are optional, redundant, and required to be
        # consistent: codec_name, codec_params, tail_codec_params,
        # num_segments, size, needed_shares, total_shares
        if d.has_key('codec_name'):
            if d['codec_name'] != "crs":
                raise UnsupportedErasureCodec(d['codec_name'])

        if d.has_key('codec_params'):
            ucpss, ucpns, ucpts = codec.parse_params(d['codec_params'])
            if ucpss != self.segment_size:
                raise BadURIExtension("inconsistent erasure code params: "
                                      "ucpss: %s != self.segment_size: %s" %
                                      (ucpss, self.segment_size))
            if ucpns != self._verifycap.needed_shares:
                raise BadURIExtension("inconsistent erasure code params: ucpns: %s != "
                                      "self._verifycap.needed_shares: %s" %
                                      (ucpns, self._verifycap.needed_shares))
            if ucpts != self._verifycap.total_shares:
                raise BadURIExtension("inconsistent erasure code params: ucpts: %s != "
                                      "self._verifycap.total_shares: %s" %
                                      (ucpts, self._verifycap.total_shares))

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

        # Finally, things that are deprecated and ignored: plaintext_hash,
        # plaintext_root_hash
        if d.get('plaintext_hash'):
            log.msg("Found plaintext_hash in UEB. This field is deprecated for security reasons "
                    "and is no longer used.  Ignoring.  %s" % (self,))
        if d.get('plaintext_root_hash'):
            log.msg("Found plaintext_root_hash in UEB. This field is deprecated for security "
                    "reasons and is no longer used.  Ignoring.  %s" % (self,))

        return self

    def start(self):
        """Fetch the UEB from bucket, compare its hash to the hash from
        verifycap, then parse it. Returns a deferred which is called back
        with self once the fetch is successful, or is erred back if it
        fails."""
        d = self._readbucketproxy.get_uri_extension()
        d.addCallback(self._check_integrity)
        d.addCallback(self._parse_and_validate)
        return d

class ValidatedReadBucketProxy(log.PrefixingLogMixin):
    """I am a front-end for a remote storage bucket, responsible for
    retrieving and validating data from that bucket.

    My get_block() method is used by BlockDownloaders.
    """

    def __init__(self, sharenum, bucket, share_hash_tree, num_blocks,
                 block_size, share_size):
        """ share_hash_tree is required to have already been initialized with
        the root hash (the number-0 hash), using the share_root_hash from the
        UEB"""
        precondition(share_hash_tree[0] is not None, share_hash_tree)
        prefix = "%d-%s-%s" % (sharenum, bucket,
                               base32.b2a_l(share_hash_tree[0][:8], 60))
        log.PrefixingLogMixin.__init__(self,
                                       facility="tahoe.immutable.download",
                                       prefix=prefix)
        self.sharenum = sharenum
        self.bucket = bucket
        self.share_hash_tree = share_hash_tree
        self.num_blocks = num_blocks
        self.block_size = block_size
        self.share_size = share_size
        self.block_hash_tree = hashtree.IncompleteHashTree(self.num_blocks)

    def get_all_sharehashes(self):
        """Retrieve and validate all the share-hash-tree nodes that are
        included in this share, regardless of whether we need them to
        validate the share or not. Each share contains a minimal Merkle tree
        chain, but there is lots of overlap, so usually we'll be using hashes
        from other shares and not reading every single hash from this share.
        The Verifier uses this function to read and validate every single
        hash from this share.

        Call this (and wait for the Deferred it returns to fire) before
        calling get_block() for the first time: this lets us check that the
        share share contains enough hashes to validate its own data, and
        avoids downloading any share hash twice.

        I return a Deferred which errbacks upon failure, probably with
        BadOrMissingHash."""

        d = self.bucket.get_share_hashes()
        def _got_share_hashes(sh):
            sharehashes = dict(sh)
            try:
                self.share_hash_tree.set_hashes(sharehashes)
            except IndexError, le:
                raise BadOrMissingHash(le)
            except (hashtree.BadHashError, hashtree.NotEnoughHashesError), le:
                raise BadOrMissingHash(le)
        d.addCallback(_got_share_hashes)
        return d

    def get_all_blockhashes(self):
        """Retrieve and validate all the block-hash-tree nodes that are
        included in this share. Each share contains a full Merkle tree, but
        we usually only fetch the minimal subset necessary for any particular
        block. This function fetches everything at once. The Verifier uses
        this function to validate the block hash tree.

        Call this (and wait for the Deferred it returns to fire) after
        calling get_all_sharehashes() and before calling get_block() for the
        first time: this lets us check that the share contains all block
        hashes and avoids downloading them multiple times.

        I return a Deferred which errbacks upon failure, probably with
        BadOrMissingHash.
        """

        # get_block_hashes(anything) currently always returns everything
        needed = list(range(len(self.block_hash_tree)))
        d = self.bucket.get_block_hashes(needed)
        def _got_block_hashes(blockhashes):
            if len(blockhashes) < len(self.block_hash_tree):
                raise BadOrMissingHash()
            bh = dict(enumerate(blockhashes))

            try:
                self.block_hash_tree.set_hashes(bh)
            except IndexError, le:
                raise BadOrMissingHash(le)
            except (hashtree.BadHashError, hashtree.NotEnoughHashesError), le:
                raise BadOrMissingHash(le)
        d.addCallback(_got_block_hashes)
        return d

    def get_all_crypttext_hashes(self, crypttext_hash_tree):
        """Retrieve and validate all the crypttext-hash-tree nodes that are
        in this share. Normally we don't look at these at all: the download
        process fetches them incrementally as needed to validate each segment
        of ciphertext. But this is a convenient place to give the Verifier a
        function to validate all of these at once.

        Call this with a new hashtree object for each share, initialized with
        the crypttext hash tree root. I return a Deferred which errbacks upon
        failure, probably with BadOrMissingHash.
        """

        # get_crypttext_hashes() always returns everything
        d = self.bucket.get_crypttext_hashes()
        def _got_crypttext_hashes(hashes):
            if len(hashes) < len(crypttext_hash_tree):
                raise BadOrMissingHash()
            ct_hashes = dict(enumerate(hashes))
            try:
                crypttext_hash_tree.set_hashes(ct_hashes)
            except IndexError, le:
                raise BadOrMissingHash(le)
            except (hashtree.BadHashError, hashtree.NotEnoughHashesError), le:
                raise BadOrMissingHash(le)
        d.addCallback(_got_crypttext_hashes)
        return d

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
        # We don't need the root of the block hash tree, as that comes in the
        # share tree.
        blockhashesneeded.discard(0)
        d2 = self.bucket.get_block_hashes(blockhashesneeded)

        if blocknum < self.num_blocks-1:
            thisblocksize = self.block_size
        else:
            thisblocksize = self.share_size % self.block_size
            if thisblocksize == 0:
                thisblocksize = self.block_size
        d3 = self.bucket.get_block_data(blocknum,
                                        self.block_size, thisblocksize)

        dl = deferredutil.gatherResults([d1, d2, d3])
        dl.addCallback(self._got_data, blocknum)
        return dl

    def _got_data(self, results, blocknum):
        precondition(blocknum < self.num_blocks,
                     self, blocknum, self.num_blocks)
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
                # This will raise exception if the values being passed do not
                # match the root node of self.share_hash_tree.
                try:
                    self.share_hash_tree.set_hashes(sharehashes)
                except IndexError, le:
                    # Weird -- sharehashes contained index numbers outside of
                    # the range that fit into this hash tree.
                    raise BadOrMissingHash(le)

            # To validate a block we need the root of the block hash tree,
            # which is also one of the leafs of the share hash tree, and is
            # called "the share hash".
            if not self.block_hash_tree[0]: # empty -- no root node yet
                # Get the share hash from the share hash tree.
                share_hash = self.share_hash_tree.get_leaf(self.sharenum)
                if not share_hash:
                    # No root node in block_hash_tree and also the share hash
                    # wasn't sent by the server.
                    raise hashtree.NotEnoughHashesError
                self.block_hash_tree.set_hashes({0: share_hash})

            if self.block_hash_tree.needed_hashes(blocknum):
                self.block_hash_tree.set_hashes(blockhashes)

            blockhash = block_hash(blockdata)
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


class Checker(log.PrefixingLogMixin):
    """I query all servers to see if M uniquely-numbered shares are
    available.

    If the verify flag was passed to my constructor, then for each share I
    download every data block and all metadata from each server and perform a
    cryptographic integrity check on all of it. If not, I just ask each
    server 'Which shares do you have?' and believe its answer.

    In either case, I wait until I have gotten responses from all servers.
    This fact -- that I wait -- means that an ill-behaved server which fails
    to answer my questions will make me wait indefinitely. If it is
    ill-behaved in a way that triggers the underlying foolscap timeouts, then
    I will wait only as long as those foolscap timeouts, but if it is
    ill-behaved in a way which placates the foolscap timeouts but still
    doesn't answer my question then I will wait indefinitely.

    Before I send any new request to a server, I always ask the 'monitor'
    object that was passed into my constructor whether this task has been
    cancelled (by invoking its raise_if_cancelled() method).
    """
    def __init__(self, verifycap, servers, verify, add_lease, secret_holder,
                 monitor):
        assert precondition(isinstance(verifycap, CHKFileVerifierURI), verifycap, type(verifycap))

        prefix = "%s" % base32.b2a_l(verifycap.get_storage_index()[:8], 60)
        log.PrefixingLogMixin.__init__(self, facility="tahoe.immutable.checker", prefix=prefix)

        self._verifycap = verifycap

        self._monitor = monitor
        self._servers = servers
        self._verify = verify # bool: verify what the servers claim, or not?
        self._add_lease = add_lease

        frs = file_renewal_secret_hash(secret_holder.get_renewal_secret(),
                                       self._verifycap.get_storage_index())
        self.file_renewal_secret = frs
        fcs = file_cancel_secret_hash(secret_holder.get_cancel_secret(),
                                      self._verifycap.get_storage_index())
        self.file_cancel_secret = fcs

    def _get_renewal_secret(self, seed):
        return bucket_renewal_secret_hash(self.file_renewal_secret, seed)
    def _get_cancel_secret(self, seed):
        return bucket_cancel_secret_hash(self.file_cancel_secret, seed)

    def _get_buckets(self, s, storageindex):
        """Return a deferred that eventually fires with ({sharenum: bucket},
        serverid, success). In case the server is disconnected or returns a
        Failure then it fires with ({}, serverid, False) (A server
        disconnecting or returning a Failure when we ask it for buckets is
        the same, for our purposes, as a server that says it has none, except
        that we want to track and report whether or not each server
        responded.)"""

        rref = s.get_rref()
        lease_seed = s.get_lease_seed()
        serverid = s.get_serverid()
        if self._add_lease:
            renew_secret = self._get_renewal_secret(lease_seed)
            cancel_secret = self._get_cancel_secret(lease_seed)
            d2 = rref.callRemote("add_lease", storageindex,
                                 renew_secret, cancel_secret)
            d2.addErrback(self._add_lease_failed, s.name(), storageindex)

        d = rref.callRemote("get_buckets", storageindex)
        def _wrap_results(res):
            return (res, serverid, True)

        def _trap_errs(f):
            level = log.WEIRD
            if f.check(DeadReferenceError):
                level = log.UNUSUAL
            self.log("failure from server on 'get_buckets' the REMOTE failure was:",
                     facility="tahoe.immutable.checker",
                     failure=f, level=level, umid="AX7wZQ")
            return ({}, serverid, False)

        d.addCallbacks(_wrap_results, _trap_errs)
        return d

    def _add_lease_failed(self, f, server_name, storage_index):
        # Older versions of Tahoe didn't handle the add-lease message very
        # well: <=1.1.0 throws a NameError because it doesn't implement
        # remote_add_lease(), 1.2.0/1.3.0 throw IndexError on unknown buckets
        # (which is most of them, since we send add-lease to everybody,
        # before we know whether or not they have any shares for us), and
        # 1.2.0 throws KeyError even on known buckets due to an internal bug
        # in the latency-measuring code.

        # we want to ignore the known-harmless errors and log the others. In
        # particular we want to log any local errors caused by coding
        # problems.

        if f.check(DeadReferenceError):
            return
        if f.check(RemoteException):
            if f.value.failure.check(KeyError, IndexError, NameError):
                # this may ignore a bit too much, but that only hurts us
                # during debugging
                return
            self.log(format="error in add_lease from [%(name)s]: %(f_value)s",
                     name=server_name,
                     f_value=str(f.value),
                     failure=f,
                     level=log.WEIRD, umid="atbAxw")
            return
        # local errors are cause for alarm
        log.err(f,
                format="local error in add_lease to [%(name)s]: %(f_value)s",
                name=server_name,
                f_value=str(f.value),
                level=log.WEIRD, umid="hEGuQg")


    def _download_and_verify(self, serverid, sharenum, bucket):
        """Start an attempt to download and verify every block in this bucket
        and return a deferred that will eventually fire once the attempt
        completes.

        If you download and verify every block then fire with (True,
        sharenum, None), else if the share data couldn't be parsed because it
        was of an unknown version number fire with (False, sharenum,
        'incompatible'), else if any of the blocks were invalid, fire with
        (False, sharenum, 'corrupt'), else if the server disconnected (False,
        sharenum, 'disconnect'), else if the server returned a Failure during
        the process fire with (False, sharenum, 'failure').

        If there is an internal error such as an uncaught exception in this
        code, then the deferred will errback, but if there is a remote error
        such as the server failing or the returned data being incorrect then
        it will not errback -- it will fire normally with the indicated
        results."""

        vcap = self._verifycap
        b = layout.ReadBucketProxy(bucket, serverid, vcap.get_storage_index())
        veup = ValidatedExtendedURIProxy(b, vcap)
        d = veup.start()

        def _got_ueb(vup):
            share_hash_tree = IncompleteHashTree(vcap.total_shares)
            share_hash_tree.set_hashes({0: vup.share_root_hash})

            vrbp = ValidatedReadBucketProxy(sharenum, b,
                                            share_hash_tree,
                                            vup.num_segments,
                                            vup.block_size,
                                            vup.share_size)

            # note: normal download doesn't use get_all_sharehashes(),
            # because it gets more data than necessary. We've discussed the
            # security properties of having verification and download look
            # identical (so the server couldn't, say, provide good responses
            # for one and not the other), but I think that full verification
            # is more important than defending against inconsistent server
            # behavior. Besides, they can't pass the verifier without storing
            # all the data, so there's not so much to be gained by behaving
            # inconsistently.
            d = vrbp.get_all_sharehashes()
            # we fill share_hash_tree before fetching any blocks, so the
            # block fetches won't send redundant share-hash-tree requests, to
            # speed things up. Then we fetch+validate all the blockhashes.
            d.addCallback(lambda ign: vrbp.get_all_blockhashes())

            cht = IncompleteHashTree(vup.num_segments)
            cht.set_hashes({0: vup.crypttext_root_hash})
            d.addCallback(lambda ign: vrbp.get_all_crypttext_hashes(cht))

            d.addCallback(lambda ign: vrbp)
            return d
        d.addCallback(_got_ueb)

        def _discard_result(r):
            assert isinstance(r, str), r
            # to free up the RAM
            return None
        def _get_blocks(vrbp):
            ds = []
            for blocknum in range(veup.num_segments):
                db = vrbp.get_block(blocknum)
                db.addCallback(_discard_result)
                ds.append(db)
            # this gatherResults will fire once every block of this share has
            # been downloaded and verified, or else it will errback.
            return deferredutil.gatherResults(ds)
        d.addCallback(_get_blocks)

        # if none of those errbacked, the blocks (and the hashes above them)
        # are good
        def _all_good(ign):
            return (True, sharenum, None)
        d.addCallback(_all_good)

        # but if anything fails, we'll land here
        def _errb(f):
            # We didn't succeed at fetching and verifying all the blocks of
            # this share. Handle each reason for failure differently.

            if f.check(DeadReferenceError):
                return (False, sharenum, 'disconnect')
            elif f.check(RemoteException):
                return (False, sharenum, 'failure')
            elif f.check(layout.ShareVersionIncompatible):
                return (False, sharenum, 'incompatible')
            elif f.check(layout.LayoutInvalid,
                         layout.RidiculouslyLargeURIExtensionBlock,
                         BadOrMissingHash,
                         BadURIExtensionHashValue):
                return (False, sharenum, 'corrupt')

            # if it wasn't one of those reasons, re-raise the error
            return f
        d.addErrback(_errb)

        return d

    def _verify_server_shares(self, s):
        """ Return a deferred which eventually fires with a tuple of
        (set(sharenum), serverid, set(corruptsharenum),
        set(incompatiblesharenum), success) showing all the shares verified
        to be served by this server, and all the corrupt shares served by the
        server, and all the incompatible shares served by the server. In case
        the server is disconnected or returns a Failure then it fires with
        the last element False.

        A server disconnecting or returning a failure when we ask it for
        shares is the same, for our purposes, as a server that says it has
        none or offers invalid ones, except that we want to track and report
        the server's behavior. Similarly, the presence of corrupt shares is
        mainly of use for diagnostics -- you can typically treat it as just
        like being no share at all by just observing its absence from the
        verified shares dict and ignoring its presence in the corrupt shares
        dict.

        The 'success' argument means whether the server responded to *any*
        queries during this process, so if it responded to some queries and
        then disconnected and ceased responding, or returned a failure, it is
        still marked with the True flag for 'success'.
        """
        d = self._get_buckets(s, self._verifycap.get_storage_index())

        def _got_buckets(result):
            bucketdict, serverid, success = result

            shareverds = []
            for (sharenum, bucket) in bucketdict.items():
                d = self._download_and_verify(serverid, sharenum, bucket)
                shareverds.append(d)

            dl = deferredutil.gatherResults(shareverds)

            def collect(results):
                verified = set()
                corrupt = set()
                incompatible = set()
                for succ, sharenum, whynot in results:
                    if succ:
                        verified.add(sharenum)
                    else:
                        if whynot == 'corrupt':
                            corrupt.add(sharenum)
                        elif whynot == 'incompatible':
                            incompatible.add(sharenum)
                return (verified, serverid, corrupt, incompatible, success)

            dl.addCallback(collect)
            return dl

        def _err(f):
            f.trap(RemoteException, DeadReferenceError)
            return (set(), s.get_serverid(), set(), set(), False)

        d.addCallbacks(_got_buckets, _err)
        return d

    def _check_server_shares(self, s):
        """Return a deferred which eventually fires with a tuple of
        (set(sharenum), serverid, set(), set(), responded) showing all the
        shares claimed to be served by this server. In case the server is
        disconnected then it fires with (set() serverid, set(), set(), False)
        (a server disconnecting when we ask it for buckets is the same, for
        our purposes, as a server that says it has none, except that we want
        to track and report whether or not each server responded.)"""
        def _curry_empty_corrupted(res):
            buckets, serverid, responded = res
            return (set(buckets), serverid, set(), set(), responded)
        d = self._get_buckets(s, self._verifycap.get_storage_index())
        d.addCallback(_curry_empty_corrupted)
        return d

    def _format_results(self, results):
        cr = CheckResults(self._verifycap, self._verifycap.get_storage_index())
        d = {}
        d['count-shares-needed'] = self._verifycap.needed_shares
        d['count-shares-expected'] = self._verifycap.total_shares

        verifiedshares = dictutil.DictOfSets() # {sharenum: set(serverid)}
        servers = {} # {serverid: set(sharenums)}
        corruptsharelocators = [] # (serverid, storageindex, sharenum)
        incompatiblesharelocators = [] # (serverid, storageindex, sharenum)

        for theseverifiedshares, thisserverid, thesecorruptshares, theseincompatibleshares, thisresponded in results:
            servers.setdefault(thisserverid, set()).update(theseverifiedshares)
            for sharenum in theseverifiedshares:
                verifiedshares.setdefault(sharenum, set()).add(thisserverid)
            for sharenum in thesecorruptshares:
                corruptsharelocators.append((thisserverid, self._verifycap.get_storage_index(), sharenum))
            for sharenum in theseincompatibleshares:
                incompatiblesharelocators.append((thisserverid, self._verifycap.get_storage_index(), sharenum))

        d['count-shares-good'] = len(verifiedshares)
        d['count-good-share-hosts'] = len([s for s in servers.keys() if servers[s]])

        assert len(verifiedshares) <= self._verifycap.total_shares, (verifiedshares.keys(), self._verifycap.total_shares)
        if len(verifiedshares) == self._verifycap.total_shares:
            cr.set_healthy(True)
            cr.set_summary("Healthy")
        else:
            cr.set_healthy(False)
            cr.set_summary("Not Healthy: %d shares (enc %d-of-%d)" %
                           (len(verifiedshares),
                            self._verifycap.needed_shares,
                            self._verifycap.total_shares))
        if len(verifiedshares) >= self._verifycap.needed_shares:
            cr.set_recoverable(True)
            d['count-recoverable-versions'] = 1
            d['count-unrecoverable-versions'] = 0
        else:
            cr.set_recoverable(False)
            d['count-recoverable-versions'] = 0
            d['count-unrecoverable-versions'] = 1

        d['servers-responding'] = list(servers)
        d['sharemap'] = verifiedshares
        # no such thing as wrong shares of an immutable file
        d['count-wrong-shares'] = 0
        d['list-corrupt-shares'] = corruptsharelocators
        d['count-corrupt-shares'] = len(corruptsharelocators)
        d['list-incompatible-shares'] = incompatiblesharelocators
        d['count-incompatible-shares'] = len(incompatiblesharelocators)


        # The file needs rebalancing if the set of servers that have at least
        # one share is less than the number of uniquely-numbered shares
        # available.
        cr.set_needs_rebalancing(d['count-good-share-hosts'] < d['count-shares-good'])

        cr.set_data(d)

        return cr

    def start(self):
        ds = []
        if self._verify:
            for s in self._servers:
                ds.append(self._verify_server_shares(s))
        else:
            for s in self._servers:
                ds.append(self._check_server_shares(s))

        return deferredutil.gatherResults(ds).addCallback(self._format_results)
