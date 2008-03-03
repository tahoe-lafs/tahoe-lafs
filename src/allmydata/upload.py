
import os, time, weakref, itertools
from zope.interface import implements
from twisted.python import failure
from twisted.internet import defer
from twisted.application import service
from foolscap import Referenceable, Copyable, RemoteCopy
from foolscap import eventual
from foolscap.logging import log

from allmydata.util.hashutil import file_renewal_secret_hash, \
     file_cancel_secret_hash, bucket_renewal_secret_hash, \
     bucket_cancel_secret_hash, plaintext_hasher, \
     storage_index_hash, plaintext_segment_hasher, content_hash_key_hasher
from allmydata import encode, storage, hashtree, uri
from allmydata.util import base32, idlib, mathutil
from allmydata.util.assertutil import precondition
from allmydata.interfaces import IUploadable, IUploader, IUploadResults, \
     IEncryptedUploadable, RIEncryptedUploadable, IUploadStatus
from pycryptopp.cipher.aes import AES

from cStringIO import StringIO


KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

# this wants to live in storage, not here
class TooFullError(Exception):
    pass

class UploadResults(Copyable, RemoteCopy):
    implements(IUploadResults)
    typeToCopy = "allmydata.upload.UploadResults.tahoe.allmydata.com"
    copytype = typeToCopy

    file_size = None
    ciphertext_fetched = None # how much the helper fetched
    uri = None
    sharemap = None # dict of shnum to placement string
    servermap = None # dict of peerid to set(shnums)
    def __init__(self):
        self.timings = {} # dict of name to number of seconds

# our current uri_extension is 846 bytes for small files, a few bytes
# more for larger ones (since the filesize is encoded in decimal in a
# few places). Ask for a little bit more just in case we need it. If
# the extension changes size, we can change EXTENSION_SIZE to
# allocate a more accurate amount of space.
EXTENSION_SIZE = 1000

class PeerTracker:
    def __init__(self, peerid, storage_server,
                 sharesize, blocksize, num_segments, num_share_hashes,
                 storage_index,
                 bucket_renewal_secret, bucket_cancel_secret):
        precondition(isinstance(peerid, str), peerid)
        precondition(len(peerid) == 20, peerid)
        self.peerid = peerid
        self._storageserver = storage_server # to an RIStorageServer
        self.buckets = {} # k: shareid, v: IRemoteBucketWriter
        self.sharesize = sharesize
        as = storage.allocated_size(sharesize,
                                    num_segments,
                                    num_share_hashes,
                                    EXTENSION_SIZE)
        self.allocated_size = as

        self.blocksize = blocksize
        self.num_segments = num_segments
        self.num_share_hashes = num_share_hashes
        self.storage_index = storage_index

        self.renew_secret = bucket_renewal_secret
        self.cancel_secret = bucket_cancel_secret

    def __repr__(self):
        return ("<PeerTracker for peer %s and SI %s>"
                % (idlib.shortnodeid_b2a(self.peerid),
                   storage.si_b2a(self.storage_index)[:5]))

    def query(self, sharenums):
        d = self._storageserver.callRemote("allocate_buckets",
                                           self.storage_index,
                                           self.renew_secret,
                                           self.cancel_secret,
                                           sharenums,
                                           self.allocated_size,
                                           canary=Referenceable())
        d.addCallback(self._got_reply)
        return d

    def _got_reply(self, (alreadygot, buckets)):
        #log.msg("%s._got_reply(%s)" % (self, (alreadygot, buckets)))
        b = {}
        for sharenum, rref in buckets.iteritems():
            bp = storage.WriteBucketProxy(rref, self.sharesize,
                                          self.blocksize,
                                          self.num_segments,
                                          self.num_share_hashes,
                                          EXTENSION_SIZE,
                                          self.peerid)
            b[sharenum] = bp
        self.buckets.update(b)
        return (alreadygot, set(b.keys()))

class Tahoe2PeerSelector:

    def __init__(self, upload_id, logparent=None, upload_status=None):
        self.upload_id = upload_id
        self.query_count, self.good_query_count, self.bad_query_count = 0,0,0
        self.error_count = 0
        self.num_peers_contacted = 0
        self.last_failure_msg = None
        self._status = IUploadStatus(upload_status)
        self._log_parent = log.msg("%s starting" % self, parent=logparent)

    def __repr__(self):
        return "<Tahoe2PeerSelector for upload %s>" % self.upload_id

    def get_shareholders(self, client,
                         storage_index, share_size, block_size,
                         num_segments, total_shares, shares_of_happiness):
        """
        @return: a set of PeerTracker instances that have agreed to hold some
                 shares for us
        """

        if self._status:
            self._status.set_status("Contacting Peers..")

        self.total_shares = total_shares
        self.shares_of_happiness = shares_of_happiness

        self.homeless_shares = range(total_shares)
        # self.uncontacted_peers = list() # peers we haven't asked yet
        self.contacted_peers = [] # peers worth asking again
        self.contacted_peers2 = [] # peers that we have asked again
        self._started_second_pass = False
        self.use_peers = set() # PeerTrackers that have shares assigned to them
        self.preexisting_shares = {} # sharenum -> PeerTracker holding the share

        peers = client.get_permuted_peers("storage", storage_index)
        if not peers:
            raise encode.NotEnoughPeersError("client gave us zero peers")

        # figure out how much space to ask for

        # this needed_hashes computation should mirror
        # Encoder.send_all_share_hash_trees. We use an IncompleteHashTree
        # (instead of a HashTree) because we don't require actual hashing
        # just to count the levels.
        ht = hashtree.IncompleteHashTree(total_shares)
        num_share_hashes = len(ht.needed_hashes(0, include_leaf=True))

        # decide upon the renewal/cancel secrets, to include them in the
        # allocat_buckets query.
        client_renewal_secret = client.get_renewal_secret()
        client_cancel_secret = client.get_cancel_secret()

        file_renewal_secret = file_renewal_secret_hash(client_renewal_secret,
                                                       storage_index)
        file_cancel_secret = file_cancel_secret_hash(client_cancel_secret,
                                                     storage_index)

        trackers = [ PeerTracker(peerid, conn,
                                 share_size, block_size,
                                 num_segments, num_share_hashes,
                                 storage_index,
                                 bucket_renewal_secret_hash(file_renewal_secret,
                                                            peerid),
                                 bucket_cancel_secret_hash(file_cancel_secret,
                                                           peerid),
                                 )
                     for (peerid, conn) in peers ]
        self.uncontacted_peers = trackers

        d = defer.maybeDeferred(self._loop)
        return d

    def _loop(self):
        if not self.homeless_shares:
            # all done
            msg = ("placed all %d shares, "
                   "sent %d queries to %d peers, "
                   "%d queries placed some shares, %d placed none, "
                   "got %d errors" %
                   (self.total_shares,
                    self.query_count, self.num_peers_contacted,
                    self.good_query_count, self.bad_query_count,
                    self.error_count))
            log.msg("peer selection successful for %s: %s" % (self, msg),
                    parent=self._log_parent)
            return self.use_peers

        if self.uncontacted_peers:
            peer = self.uncontacted_peers.pop(0)
            # TODO: don't pre-convert all peerids to PeerTrackers
            assert isinstance(peer, PeerTracker)

            shares_to_ask = set([self.homeless_shares.pop(0)])
            self.query_count += 1
            self.num_peers_contacted += 1
            if self._status:
                self._status.set_status("Contacting Peers [%s] (first query),"
                                        " %d shares left.."
                                        % (idlib.shortnodeid_b2a(peer.peerid),
                                           len(self.homeless_shares)))
            d = peer.query(shares_to_ask)
            d.addBoth(self._got_response, peer, shares_to_ask,
                      self.contacted_peers)
            return d
        elif self.contacted_peers:
            # ask a peer that we've already asked.
            if not self._started_second_pass:
                log.msg("starting second pass", parent=self._log_parent,
                        level=log.NOISY)
                self._started_second_pass = True
            num_shares = mathutil.div_ceil(len(self.homeless_shares),
                                           len(self.contacted_peers))
            peer = self.contacted_peers.pop(0)
            shares_to_ask = set(self.homeless_shares[:num_shares])
            self.homeless_shares[:num_shares] = []
            self.query_count += 1
            if self._status:
                self._status.set_status("Contacting Peers [%s] (second query),"
                                        " %d shares left.."
                                        % (idlib.shortnodeid_b2a(peer.peerid),
                                           len(self.homeless_shares)))
            d = peer.query(shares_to_ask)
            d.addBoth(self._got_response, peer, shares_to_ask,
                      self.contacted_peers2)
            return d
        elif self.contacted_peers2:
            # we've finished the second-or-later pass. Move all the remaining
            # peers back into self.contacted_peers for the next pass.
            self.contacted_peers.extend(self.contacted_peers2)
            self.contacted_peers[:] = []
            return self._loop()
        else:
            # no more peers. If we haven't placed enough shares, we fail.
            placed_shares = self.total_shares - len(self.homeless_shares)
            if placed_shares < self.shares_of_happiness:
                msg = ("placed %d shares out of %d total (%d homeless), "
                       "sent %d queries to %d peers, "
                       "%d queries placed some shares, %d placed none, "
                       "got %d errors" %
                       (self.total_shares - len(self.homeless_shares),
                        self.total_shares, len(self.homeless_shares),
                        self.query_count, self.num_peers_contacted,
                        self.good_query_count, self.bad_query_count,
                        self.error_count))
                msg = "peer selection failed for %s: %s" % (self, msg)
                if self.last_failure_msg:
                    msg += " (%s)" % (self.last_failure_msg,)
                log.msg(msg, level=log.UNUSUAL, parent=self._log_parent)
                raise encode.NotEnoughPeersError(msg)
            else:
                # we placed enough to be happy, so we're done
                if self._status:
                    self._status.set_status("Placed all shares")
                return self.use_peers

    def _got_response(self, res, peer, shares_to_ask, put_peer_here):
        if isinstance(res, failure.Failure):
            # This is unusual, and probably indicates a bug or a network
            # problem.
            log.msg("%s got error during peer selection: %s" % (peer, res),
                    level=log.UNUSUAL, parent=self._log_parent)
            self.error_count += 1
            self.homeless_shares = list(shares_to_ask) + self.homeless_shares
            if (self.uncontacted_peers
                or self.contacted_peers
                or self.contacted_peers2):
                # there is still hope, so just loop
                pass
            else:
                # No more peers, so this upload might fail (it depends upon
                # whether we've hit shares_of_happiness or not). Log the last
                # failure we got: if a coding error causes all peers to fail
                # in the same way, this allows the common failure to be seen
                # by the uploader and should help with debugging
                msg = ("last failure (from %s) was: %s" % (peer, res))
                self.last_failure_msg = msg
        else:
            (alreadygot, allocated) = res
            log.msg("response from peer %s: alreadygot=%s, allocated=%s"
                    % (idlib.shortnodeid_b2a(peer.peerid),
                       tuple(sorted(alreadygot)), tuple(sorted(allocated))),
                    level=log.NOISY, parent=self._log_parent)
            progress = False
            for s in alreadygot:
                self.preexisting_shares[s] = peer
                if s in self.homeless_shares:
                    self.homeless_shares.remove(s)
                    progress = True

            # the PeerTracker will remember which shares were allocated on
            # that peer. We just have to remember to use them.
            if allocated:
                self.use_peers.add(peer)
                progress = True

            not_yet_present = set(shares_to_ask) - set(alreadygot)
            still_homeless = not_yet_present - set(allocated)

            if progress:
                # they accepted or already had at least one share, so
                # progress has been made
                self.good_query_count += 1
            else:
                self.bad_query_count += 1

            if still_homeless:
                # In networks with lots of space, this is very unusual and
                # probably indicates an error. In networks with peers that
                # are full, it is merely unusual. In networks that are very
                # full, it is common, and many uploads will fail. In most
                # cases, this is obviously not fatal, and we'll just use some
                # other peers.

                # some shares are still homeless, keep trying to find them a
                # home. The ones that were rejected get first priority.
                self.homeless_shares = (list(still_homeless)
                                        + self.homeless_shares)
                # Since they were unable to accept all of our requests, so it
                # is safe to assume that asking them again won't help.
            else:
                # if they *were* able to accept everything, they might be
                # willing to accept even more.
                put_peer_here.append(peer)

        # now loop
        return self._loop()


class EncryptAnUploadable:
    """This is a wrapper that takes an IUploadable and provides
    IEncryptedUploadable."""
    implements(IEncryptedUploadable)
    CHUNKSIZE = 50*1024

    def __init__(self, original, log_parent=None):
        self.original = IUploadable(original)
        self._log_number = log_parent
        self._encryptor = None
        self._plaintext_hasher = plaintext_hasher()
        self._plaintext_segment_hasher = None
        self._plaintext_segment_hashes = []
        self._encoding_parameters = None
        self._file_size = None
        self._ciphertext_bytes_read = 0
        self._status = None

    def set_upload_status(self, upload_status):
        self._status = IUploadStatus(upload_status)
        self.original.set_upload_status(upload_status)

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "upload.encryption"
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        return log.msg(*args, **kwargs)

    def get_size(self):
        if self._file_size is not None:
            return defer.succeed(self._file_size)
        d = self.original.get_size()
        def _got_size(size):
            self._file_size = size
            if self._status:
                self._status.set_size(size)
            return size
        d.addCallback(_got_size)
        return d

    def get_all_encoding_parameters(self):
        if self._encoding_parameters is not None:
            return defer.succeed(self._encoding_parameters)
        d = self.original.get_all_encoding_parameters()
        def _got(encoding_parameters):
            (k, happy, n, segsize) = encoding_parameters
            self._segment_size = segsize # used by segment hashers
            self._encoding_parameters = encoding_parameters
            self.log("my encoding parameters: %s" % (encoding_parameters,),
                     level=log.NOISY)
            return encoding_parameters
        d.addCallback(_got)
        return d

    def _get_encryptor(self):
        if self._encryptor:
            return defer.succeed(self._encryptor)

        d = self.original.get_encryption_key()
        def _got(key):
            e = AES(key)
            self._encryptor = e

            storage_index = storage_index_hash(key)
            assert isinstance(storage_index, str)
            # There's no point to having the SI be longer than the key, so we
            # specify that it is truncated to the same 128 bits as the AES key.
            assert len(storage_index) == 16  # SHA-256 truncated to 128b
            self._storage_index = storage_index
            if self._status:
                self._status.set_storage_index(storage_index)
            return e
        d.addCallback(_got)
        return d

    def get_storage_index(self):
        d = self._get_encryptor()
        d.addCallback(lambda res: self._storage_index)
        return d

    def _get_segment_hasher(self):
        p = self._plaintext_segment_hasher
        if p:
            left = self._segment_size - self._plaintext_segment_hashed_bytes
            return p, left
        p = plaintext_segment_hasher()
        self._plaintext_segment_hasher = p
        self._plaintext_segment_hashed_bytes = 0
        return p, self._segment_size

    def _update_segment_hash(self, chunk):
        offset = 0
        while offset < len(chunk):
            p, segment_left = self._get_segment_hasher()
            chunk_left = len(chunk) - offset
            this_segment = min(chunk_left, segment_left)
            p.update(chunk[offset:offset+this_segment])
            self._plaintext_segment_hashed_bytes += this_segment

            if self._plaintext_segment_hashed_bytes == self._segment_size:
                # we've filled this segment
                self._plaintext_segment_hashes.append(p.digest())
                self._plaintext_segment_hasher = None
                self.log("closed hash [%d]: %dB" %
                         (len(self._plaintext_segment_hashes)-1,
                          self._plaintext_segment_hashed_bytes),
                         level=log.NOISY)
                self.log(format="plaintext leaf hash [%(segnum)d] is %(hash)s",
                         segnum=len(self._plaintext_segment_hashes)-1,
                         hash=base32.b2a(p.digest()),
                         level=log.NOISY)

            offset += this_segment


    def read_encrypted(self, length, hash_only):
        # make sure our parameters have been set up first
        d = self.get_all_encoding_parameters()
        # and size
        d.addCallback(lambda ignored: self.get_size())
        d.addCallback(lambda ignored: self._get_encryptor())
        # then fetch and encrypt the plaintext. The unusual structure here
        # (passing a Deferred *into* a function) is needed to avoid
        # overflowing the stack: Deferreds don't optimize out tail recursion.
        # We also pass in a list, to which _read_encrypted will append
        # ciphertext.
        ciphertext = []
        d2 = defer.Deferred()
        d.addCallback(lambda ignored:
                      self._read_encrypted(length, ciphertext, hash_only, d2))
        d.addCallback(lambda ignored: d2)
        return d

    def _read_encrypted(self, remaining, ciphertext, hash_only, fire_when_done):
        if not remaining:
            fire_when_done.callback(ciphertext)
            return None
        # tolerate large length= values without consuming a lot of RAM by
        # reading just a chunk (say 50kB) at a time. This only really matters
        # when hash_only==True (i.e. resuming an interrupted upload), since
        # that's the case where we will be skipping over a lot of data.
        size = min(remaining, self.CHUNKSIZE)
        remaining = remaining - size
        # read a chunk of plaintext..
        d = defer.maybeDeferred(self.original.read, size)
        # N.B.: if read() is synchronous, then since everything else is
        # actually synchronous too, we'd blow the stack unless we stall for a
        # tick. Once you accept a Deferred from IUploadable.read(), you must
        # be prepared to have it fire immediately too.
        d.addCallback(eventual.fireEventually)
        def _good(plaintext):
            # and encrypt it..
            # o/' over the fields we go, hashing all the way, sHA! sHA! sHA! o/'
            ct = self._hash_and_encrypt_plaintext(plaintext, hash_only)
            ciphertext.extend(ct)
            self._read_encrypted(remaining, ciphertext, hash_only,
                                 fire_when_done)
        def _err(why):
            fire_when_done.errback(why)
        d.addCallback(_good)
        d.addErrback(_err)
        return None

    def _hash_and_encrypt_plaintext(self, data, hash_only):
        assert isinstance(data, (tuple, list)), type(data)
        data = list(data)
        cryptdata = []
        # we use data.pop(0) instead of 'for chunk in data' to save
        # memory: each chunk is destroyed as soon as we're done with it.
        bytes_processed = 0
        while data:
            chunk = data.pop(0)
            self.log(" read_encrypted handling %dB-sized chunk" % len(chunk),
                     level=log.NOISY)
            bytes_processed += len(chunk)
            self._plaintext_hasher.update(chunk)
            self._update_segment_hash(chunk)
            # TODO: we have to encrypt the data (even if hash_only==True)
            # because pycryptopp's AES-CTR implementation doesn't offer a
            # way to change the counter value. Once pycryptopp acquires
            # this ability, change this to simply update the counter
            # before each call to (hash_only==False) _encryptor.process()
            ciphertext = self._encryptor.process(chunk)
            if hash_only:
                self.log("  skipping encryption", level=log.NOISY)
            else:
                cryptdata.append(ciphertext)
            del ciphertext
            del chunk
        self._ciphertext_bytes_read += bytes_processed
        if self._status:
            progress = float(self._ciphertext_bytes_read) / self._file_size
            self._status.set_progress(1, progress)
        return cryptdata


    def get_plaintext_hashtree_leaves(self, first, last, num_segments):
        if len(self._plaintext_segment_hashes) < num_segments:
            # close out the last one
            assert len(self._plaintext_segment_hashes) == num_segments-1
            p, segment_left = self._get_segment_hasher()
            self._plaintext_segment_hashes.append(p.digest())
            del self._plaintext_segment_hasher
            self.log("closing plaintext leaf hasher, hashed %d bytes" %
                     self._plaintext_segment_hashed_bytes,
                     level=log.NOISY)
            self.log(format="plaintext leaf hash [%(segnum)d] is %(hash)s",
                     segnum=len(self._plaintext_segment_hashes)-1,
                     hash=base32.b2a(p.digest()),
                     level=log.NOISY)
        assert len(self._plaintext_segment_hashes) == num_segments
        return defer.succeed(tuple(self._plaintext_segment_hashes[first:last]))

    def get_plaintext_hash(self):
        h = self._plaintext_hasher.digest()
        return defer.succeed(h)

    def close(self):
        return self.original.close()

class UploadStatus:
    implements(IUploadStatus)
    statusid_counter = itertools.count(0)

    def __init__(self):
        self.storage_index = None
        self.size = None
        self.helper = False
        self.status = "Not started"
        self.progress = [0.0, 0.0, 0.0]
        self.active = True
        self.results = None
        self.counter = self.statusid_counter.next()

    def get_storage_index(self):
        return self.storage_index
    def get_size(self):
        return self.size
    def using_helper(self):
        return self.helper
    def get_status(self):
        return self.status
    def get_progress(self):
        return tuple(self.progress)
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
    def set_progress(self, which, value):
        # [0]: chk, [1]: ciphertext, [2]: encode+push
        self.progress[which] = value
    def set_active(self, value):
        self.active = value
    def set_results(self, value):
        self.results = value

class CHKUploader:
    peer_selector_class = Tahoe2PeerSelector

    def __init__(self, client):
        self._client = client
        self._log_number = self._client.log("CHKUploader starting")
        self._encoder = None
        self._results = UploadResults()
        self._storage_index = None
        self._upload_status = UploadStatus()
        self._upload_status.set_helper(False)
        self._upload_status.set_active(True)
        self._upload_status.set_results(self._results)

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.upload"
        return self._client.log(*args, **kwargs)

    def start(self, uploadable):
        """Start uploading the file.

        This method returns a Deferred that will fire with the URI (a
        string)."""

        self._started = time.time()
        uploadable = IUploadable(uploadable)
        self.log("starting upload of %s" % uploadable)

        eu = EncryptAnUploadable(uploadable, self._log_number)
        eu.set_upload_status(self._upload_status)
        d = self.start_encrypted(eu)
        def _uploaded(res):
            d1 = uploadable.get_encryption_key()
            d1.addCallback(lambda key: self._compute_uri(res, key))
            return d1
        d.addCallback(_uploaded)
        def _done(res):
            self._upload_status.set_active(False)
            return res
        d.addBoth(_done)
        return d

    def abort(self):
        """Call this is the upload must be abandoned before it completes.
        This will tell the shareholders to delete their partial shares. I
        return a Deferred that fires when these messages have been acked."""
        if not self._encoder:
            # how did you call abort() before calling start() ?
            return defer.succeed(None)
        return self._encoder.abort()

    def start_encrypted(self, encrypted):
        eu = IEncryptedUploadable(encrypted)

        started = time.time()
        self._encoder = e = encode.Encoder(self._log_number,
                                           self._upload_status)
        d = e.set_encrypted_uploadable(eu)
        d.addCallback(self.locate_all_shareholders, started)
        d.addCallback(self.set_shareholders, e)
        d.addCallback(lambda res: e.start())
        d.addCallback(self._encrypted_done)
        # this fires with the uri_extension_hash and other data
        return d

    def locate_all_shareholders(self, encoder, started):
        peer_selection_started = now = time.time()
        self._storage_index_elapsed = now - started
        storage_index = encoder.get_param("storage_index")
        self._storage_index = storage_index
        upload_id = storage.si_b2a(storage_index)[:5]
        self.log("using storage index %s" % upload_id)
        peer_selector = self.peer_selector_class(upload_id, self._log_number,
                                                 self._upload_status)

        share_size = encoder.get_param("share_size")
        block_size = encoder.get_param("block_size")
        num_segments = encoder.get_param("num_segments")
        k,desired,n = encoder.get_param("share_counts")

        self._peer_selection_started = time.time()
        d = peer_selector.get_shareholders(self._client, storage_index,
                                           share_size, block_size,
                                           num_segments, n, desired)
        def _done(res):
            self._peer_selection_elapsed = time.time() - peer_selection_started
            return res
        d.addCallback(_done)
        return d

    def set_shareholders(self, used_peers, encoder):
        """
        @param used_peers: a sequence of PeerTracker objects
        """
        self.log("_send_shares, used_peers is %s" % (used_peers,))
        self._sharemap = {}
        for peer in used_peers:
            assert isinstance(peer, PeerTracker)
        buckets = {}
        for peer in used_peers:
            buckets.update(peer.buckets)
            for shnum in peer.buckets:
                self._sharemap[shnum] = peer
        assert len(buckets) == sum([len(peer.buckets) for peer in used_peers])
        encoder.set_shareholders(buckets)

    def _encrypted_done(self, res):
        r = self._results
        r.sharemap = {}
        r.servermap = {}
        for shnum in self._encoder.get_shares_placed():
            peer_tracker = self._sharemap[shnum]
            peerid = peer_tracker.peerid
            peerid_s = idlib.shortnodeid_b2a(peerid)
            r.sharemap[shnum] = "Placed on [%s]" % peerid_s
            if peerid not in r.servermap:
                r.servermap[peerid] = set()
            r.servermap[peerid].add(shnum)
        now = time.time()
        r.file_size = self._encoder.file_size
        r.timings["total"] = now - self._started
        r.timings["storage_index"] = self._storage_index_elapsed
        r.timings["peer_selection"] = self._peer_selection_elapsed
        r.timings.update(self._encoder.get_times())
        r.uri_extension_data = self._encoder.get_uri_extension_data()
        return res

    def _compute_uri(self, (uri_extension_hash,
                            needed_shares, total_shares, size),
                     key):
        u = uri.CHKFileURI(key=key,
                           uri_extension_hash=uri_extension_hash,
                           needed_shares=needed_shares,
                           total_shares=total_shares,
                           size=size,
                           )
        r = self._results
        r.uri = u.to_string()
        return r

    def get_upload_status(self):
        return self._upload_status

def read_this_many_bytes(uploadable, size, prepend_data=[]):
    if size == 0:
        return defer.succeed([])
    d = uploadable.read(size)
    def _got(data):
        assert isinstance(data, list)
        bytes = sum([len(piece) for piece in data])
        assert bytes > 0
        assert bytes <= size
        remaining = size - bytes
        if remaining:
            return read_this_many_bytes(uploadable, remaining,
                                        prepend_data + data)
        return prepend_data + data
    d.addCallback(_got)
    return d

class LiteralUploader:

    def __init__(self, client):
        self._client = client
        self._results = UploadResults()
        self._status = s = UploadStatus()
        s.set_storage_index(None)
        s.set_helper(False)
        s.set_progress(0, 1.0)
        s.set_active(False)
        s.set_results(self._results)

    def start(self, uploadable):
        uploadable = IUploadable(uploadable)
        d = uploadable.get_size()
        def _got_size(size):
            self._size = size
            self._status.set_size(size)
            self._results.file_size = size
            return read_this_many_bytes(uploadable, size)
        d.addCallback(_got_size)
        d.addCallback(lambda data: uri.LiteralFileURI("".join(data)))
        d.addCallback(lambda u: u.to_string())
        d.addCallback(self._build_results)
        return d

    def _build_results(self, uri):
        self._results.uri = uri
        self._status.set_status("Done")
        self._status.set_progress(1, 1.0)
        self._status.set_progress(2, 1.0)
        return self._results

    def close(self):
        pass

    def get_upload_status(self):
        return self._status

class RemoteEncryptedUploadable(Referenceable):
    implements(RIEncryptedUploadable)

    def __init__(self, encrypted_uploadable, upload_status):
        self._eu = IEncryptedUploadable(encrypted_uploadable)
        self._offset = 0
        self._bytes_sent = 0
        self._status = IUploadStatus(upload_status)
        # we are responsible for updating the status string while we run, and
        # for setting the ciphertext-fetch progress.
        self._size = None

    def get_size(self):
        if self._size is not None:
            return defer.succeed(self._size)
        d = self._eu.get_size()
        def _got_size(size):
            self._size = size
            return size
        d.addCallback(_got_size)
        return d

    def remote_get_size(self):
        return self.get_size()
    def remote_get_all_encoding_parameters(self):
        return self._eu.get_all_encoding_parameters()

    def _read_encrypted(self, length, hash_only):
        d = self._eu.read_encrypted(length, hash_only)
        def _read(strings):
            if hash_only:
                self._offset += length
            else:
                size = sum([len(data) for data in strings])
                self._offset += size
            return strings
        d.addCallback(_read)
        return d

    def remote_read_encrypted(self, offset, length):
        # we don't support seek backwards, but we allow skipping forwards
        precondition(offset >= 0, offset)
        precondition(length >= 0, length)
        lp = log.msg("remote_read_encrypted(%d-%d)" % (offset, offset+length),
                     level=log.NOISY)
        precondition(offset >= self._offset, offset, self._offset)
        if offset > self._offset:
            # read the data from disk anyways, to build up the hash tree
            skip = offset - self._offset
            log.msg("remote_read_encrypted skipping ahead from %d to %d, skip=%d" %
                    (self._offset, offset, skip), level=log.UNUSUAL, parent=lp)
            d = self._read_encrypted(skip, hash_only=True)
        else:
            d = defer.succeed(None)

        def _at_correct_offset(res):
            assert offset == self._offset, "%d != %d" % (offset, self._offset)
            return self._read_encrypted(length, hash_only=False)
        d.addCallback(_at_correct_offset)

        def _read(strings):
            size = sum([len(data) for data in strings])
            self._bytes_sent += size
            return strings
        d.addCallback(_read)
        return d

    def remote_get_plaintext_hashtree_leaves(self, first, last, num_segments):
        log.msg("remote_get_plaintext_hashtree_leaves: %d-%d of %d" %
                (first, last-1, num_segments),
                level=log.NOISY)
        d = self._eu.get_plaintext_hashtree_leaves(first, last, num_segments)
        d.addCallback(list)
        return d
    def remote_get_plaintext_hash(self):
        return self._eu.get_plaintext_hash()
    def remote_close(self):
        return self._eu.close()


class AssistedUploader:

    def __init__(self, helper):
        self._helper = helper
        self._log_number = log.msg("AssistedUploader starting")
        self._storage_index = None
        self._upload_status = s = UploadStatus()
        s.set_helper(True)
        s.set_active(True)

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        return log.msg(*args, **kwargs)

    def start(self, uploadable):
        self._started = time.time()
        u = IUploadable(uploadable)
        eu = EncryptAnUploadable(u, self._log_number)
        eu.set_upload_status(self._upload_status)
        self._encuploadable = eu
        d = eu.get_size()
        d.addCallback(self._got_size)
        d.addCallback(lambda res: eu.get_all_encoding_parameters())
        d.addCallback(self._got_all_encoding_parameters)
        # when we get the encryption key, that will also compute the storage
        # index, so this only takes one pass.
        # TODO: I'm not sure it's cool to switch back and forth between
        # the Uploadable and the IEncryptedUploadable that wraps it.
        d.addCallback(lambda res: u.get_encryption_key())
        d.addCallback(self._got_encryption_key)
        d.addCallback(lambda res: eu.get_storage_index())
        d.addCallback(self._got_storage_index)
        d.addCallback(self._contact_helper)
        d.addCallback(self._build_readcap)
        def _done(res):
            self._upload_status.set_active(False)
            return res
        d.addBoth(_done)
        return d

    def _got_size(self, size):
        self._size = size
        self._upload_status.set_size(size)

    def _got_all_encoding_parameters(self, params):
        k, happy, n, segment_size = params
        # stash these for URI generation later
        self._needed_shares = k
        self._total_shares = n
        self._segment_size = segment_size

    def _got_encryption_key(self, key):
        self._key = key

    def _got_storage_index(self, storage_index):
        self._storage_index = storage_index


    def _contact_helper(self, res):
        now = self._time_contacting_helper_start = time.time()
        self._storage_index_elapsed = now - self._started
        self.log(format="contacting helper for SI %(si)s..",
                 si=storage.si_b2a(self._storage_index))
        self._upload_status.set_status("Contacting Helper")
        d = self._helper.callRemote("upload_chk", self._storage_index)
        d.addCallback(self._contacted_helper)
        return d

    def _contacted_helper(self, (upload_results, upload_helper)):
        now = time.time()
        elapsed = now - self._time_contacting_helper_start
        self._elapsed_time_contacting_helper = elapsed
        if upload_helper:
            self.log("helper says we need to upload")
            self._upload_status.set_status("Uploading Ciphertext")
            # we need to upload the file
            reu = RemoteEncryptedUploadable(self._encuploadable,
                                            self._upload_status)
            # let it pre-compute the size for progress purposes
            d = reu.get_size()
            d.addCallback(lambda ignored:
                          upload_helper.callRemote("upload", reu))
            # this Deferred will fire with the upload results
            return d
        self.log("helper says file is already uploaded")
        self._upload_status.set_progress(1, 1.0)
        self._upload_status.set_results(upload_results)
        return upload_results

    def _build_readcap(self, upload_results):
        self.log("upload finished, building readcap")
        self._upload_status.set_status("Building Readcap")
        r = upload_results
        assert r.uri_extension_data["needed_shares"] == self._needed_shares
        assert r.uri_extension_data["total_shares"] == self._total_shares
        assert r.uri_extension_data["segment_size"] == self._segment_size
        assert r.uri_extension_data["size"] == self._size
        u = uri.CHKFileURI(key=self._key,
                           uri_extension_hash=r.uri_extension_hash,
                           needed_shares=self._needed_shares,
                           total_shares=self._total_shares,
                           size=self._size,
                           )
        r.uri = u.to_string()
        now = time.time()
        r.file_size = self._size
        r.timings["storage_index"] = self._storage_index_elapsed
        r.timings["contacting_helper"] = self._elapsed_time_contacting_helper
        if "total" in r.timings:
            r.timings["helper_total"] = r.timings["total"]
        r.timings["total"] = now - self._started
        self._upload_status.set_status("Done")
        self._upload_status.set_results(r)
        return r

    def get_upload_status(self):
        return self._upload_status

class BaseUploadable:
    default_max_segment_size = 1*MiB # overridden by max_segment_size
    default_encoding_param_k = 3 # overridden by encoding_parameters
    default_encoding_param_happy = 7
    default_encoding_param_n = 10

    max_segment_size = None
    encoding_param_k = None
    encoding_param_happy = None
    encoding_param_n = None

    _all_encoding_parameters = None
    _status = None

    def set_upload_status(self, upload_status):
        self._status = IUploadStatus(upload_status)

    def set_default_encoding_parameters(self, default_params):
        assert isinstance(default_params, dict)
        for k,v in default_params.items():
            precondition(isinstance(k, str), k, v)
            precondition(isinstance(v, int), k, v)
        if "k" in default_params:
            self.default_encoding_param_k = default_params["k"]
        if "happy" in default_params:
            self.default_encoding_param_happy = default_params["happy"]
        if "n" in default_params:
            self.default_encoding_param_n = default_params["n"]
        if "max_segment_size" in default_params:
            self.default_max_segment_size = default_params["max_segment_size"]

    def get_all_encoding_parameters(self):
        if self._all_encoding_parameters:
            return defer.succeed(self._all_encoding_parameters)

        max_segsize = self.max_segment_size or self.default_max_segment_size
        k = self.encoding_param_k or self.default_encoding_param_k
        happy = self.encoding_param_happy or self.default_encoding_param_happy
        n = self.encoding_param_n or self.default_encoding_param_n

        d = self.get_size()
        def _got_size(file_size):
            # for small files, shrink the segment size to avoid wasting space
            segsize = min(max_segsize, file_size)
            # this must be a multiple of 'required_shares'==k
            segsize = mathutil.next_multiple(segsize, k)
            encoding_parameters = (k, happy, n, segsize)
            self._all_encoding_parameters = encoding_parameters
            return encoding_parameters
        d.addCallback(_got_size)
        return d

class FileHandle(BaseUploadable):
    implements(IUploadable)

    def __init__(self, filehandle, contenthashkey=True):
        self._filehandle = filehandle
        self._key = None
        self._contenthashkey = contenthashkey
        self._size = None

    def _get_encryption_key_content_hash(self):
        if self._key is not None:
            return defer.succeed(self._key)

        d = self.get_size()
        # that sets self._size as a side-effect
        d.addCallback(lambda size: self.get_all_encoding_parameters())
        def _got(params):
            k, happy, n, segsize = params
            f = self._filehandle
            enckey_hasher = content_hash_key_hasher(k, n, segsize)
            f.seek(0)
            BLOCKSIZE = 64*1024
            bytes_read = 0
            while True:
                data = f.read(BLOCKSIZE)
                if not data:
                    break
                enckey_hasher.update(data)
                # TODO: setting progress in a non-yielding loop is kind of
                # pointless, but I'm anticipating (perhaps prematurely) the
                # day when we use a slowjob or twisted's CooperatorService to
                # make this yield time to other jobs.
                bytes_read += len(data)
                if self._status:
                    self._status.set_progress(0, float(bytes_read)/self._size)
            f.seek(0)
            self._key = enckey_hasher.digest()
            if self._status:
                self._status.set_progress(0, 1.0)
            assert len(self._key) == 16
            return self._key
        d.addCallback(_got)
        return d

    def _get_encryption_key_random(self):
        if self._key is None:
            self._key = os.urandom(16)
        return defer.succeed(self._key)

    def get_encryption_key(self):
        if self._contenthashkey:
            return self._get_encryption_key_content_hash()
        else:
            return self._get_encryption_key_random()

    def get_size(self):
        if self._size is not None:
            return defer.succeed(self._size)
        self._filehandle.seek(0,2)
        size = self._filehandle.tell()
        self._size = size
        self._filehandle.seek(0)
        return defer.succeed(size)

    def read(self, length):
        return defer.succeed([self._filehandle.read(length)])

    def close(self):
        # the originator of the filehandle reserves the right to close it
        pass

class FileName(FileHandle):
    def __init__(self, filename, contenthashkey=True):
        FileHandle.__init__(self, open(filename, "rb"), contenthashkey=contenthashkey)
    def close(self):
        FileHandle.close(self)
        self._filehandle.close()

class Data(FileHandle):
    def __init__(self, data, contenthashkey=True):
        FileHandle.__init__(self, StringIO(data), contenthashkey=contenthashkey)

class Uploader(service.MultiService):
    """I am a service that allows file uploading. I am a service-child of the
    Client.
    """
    implements(IUploader)
    name = "uploader"
    uploader_class = CHKUploader
    URI_LIT_SIZE_THRESHOLD = 55
    MAX_UPLOAD_STATUSES = 10

    def __init__(self, helper_furl=None):
        self._helper_furl = helper_furl
        self._helper = None
        self._all_uploads = weakref.WeakKeyDictionary()
        self._recent_upload_status = []
        service.MultiService.__init__(self)

    def startService(self):
        service.MultiService.startService(self)
        if self._helper_furl:
            self.parent.tub.connectTo(self._helper_furl,
                                      self._got_helper)

    def _got_helper(self, helper):
        self._helper = helper
        helper.notifyOnDisconnect(self._lost_helper)
    def _lost_helper(self):
        self._helper = None

    def get_helper_info(self):
        # return a tuple of (helper_furl_or_None, connected_bool)
        return (self._helper_furl, bool(self._helper))

    def upload(self, uploadable):
        # this returns the URI
        assert self.parent
        assert self.running

        uploadable = IUploadable(uploadable)
        d = uploadable.get_size()
        def _got_size(size):
            default_params = self.parent.get_encoding_parameters()
            precondition(isinstance(default_params, dict), default_params)
            precondition("max_segment_size" in default_params, default_params)
            uploadable.set_default_encoding_parameters(default_params)
            if size <= self.URI_LIT_SIZE_THRESHOLD:
                uploader = LiteralUploader(self.parent)
            elif self._helper:
                uploader = AssistedUploader(self._helper)
            else:
                uploader = self.uploader_class(self.parent)
            self._all_uploads[uploader] = None
            self._recent_upload_status.append(uploader.get_upload_status())
            while len(self._recent_upload_status) > self.MAX_UPLOAD_STATUSES:
                self._recent_upload_status.pop(0)
            return uploader.start(uploadable)
        d.addCallback(_got_size)
        def _done(res):
            uploadable.close()
            return res
        d.addBoth(_done)
        return d

    def list_all_uploads(self):
        return self._all_uploads.keys()
    def list_active_uploads(self):
        return [u.get_upload_status() for u in self._all_uploads.keys()
                if u.get_upload_status().get_active()]
    def list_recent_uploads(self):
        return self._recent_upload_status
