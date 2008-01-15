
import os
from zope.interface import implements
from twisted.python import failure
from twisted.internet import defer
from twisted.application import service
from foolscap import Referenceable
from foolscap.logging import log

from allmydata.util.hashutil import file_renewal_secret_hash, \
     file_cancel_secret_hash, bucket_renewal_secret_hash, \
     bucket_cancel_secret_hash, plaintext_hasher, \
     storage_index_chk_hash, plaintext_segment_hasher, key_hasher
from allmydata import encode, storage, hashtree, uri
from allmydata.util import idlib, mathutil
from allmydata.util.assertutil import precondition
from allmydata.interfaces import IUploadable, IUploader, \
     IEncryptedUploadable, RIEncryptedUploadable
from pycryptopp.cipher.aes import AES

from cStringIO import StringIO


class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

# this wants to live in storage, not here
class TooFullError(Exception):
    pass

# our current uri_extension is 846 bytes for small files, a few bytes
# more for larger ones (since the filesize is encoded in decimal in a
# few places). Ask for a little bit more just in case we need it. If
# the extension changes size, we can change EXTENSION_SIZE to
# allocate a more accurate amount of space.
EXTENSION_SIZE = 1000

class PeerTracker:
    def __init__(self, peerid, permutedid, connection,
                 sharesize, blocksize, num_segments, num_share_hashes,
                 storage_index,
                 bucket_renewal_secret, bucket_cancel_secret):
        precondition(isinstance(peerid, str), peerid)
        precondition(len(peerid) == 20, peerid)
        self.peerid = peerid
        self.permutedid = permutedid
        self.connection = connection # to an RIClient
        self.buckets = {} # k: shareid, v: IRemoteBucketWriter
        self.sharesize = sharesize
        #print "PeerTracker", peerid, permutedid, sharesize
        as = storage.allocated_size(sharesize,
                                    num_segments,
                                    num_share_hashes,
                                    EXTENSION_SIZE)
        self.allocated_size = as

        self.blocksize = blocksize
        self.num_segments = num_segments
        self.num_share_hashes = num_share_hashes
        self.storage_index = storage_index
        self._storageserver = None

        self.renew_secret = bucket_renewal_secret
        self.cancel_secret = bucket_cancel_secret

    def __repr__(self):
        return ("<PeerTracker for peer %s and SI %s>"
                % (idlib.shortnodeid_b2a(self.peerid),
                   idlib.b2a(self.storage_index)[:6]))

    def query(self, sharenums):
        if not self._storageserver:
            d = self.connection.callRemote("get_service", "storageserver")
            d.addCallback(self._got_storageserver)
            d.addCallback(lambda res: self._query(sharenums))
            return d
        return self._query(sharenums)
    def _got_storageserver(self, storageserver):
        self._storageserver = storageserver
    def _query(self, sharenums):
        #print " query", self.peerid, len(sharenums)
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
                                          EXTENSION_SIZE)
            b[sharenum] = bp
        self.buckets.update(b)
        return (alreadygot, set(b.keys()))

class Tahoe2PeerSelector:

    def __init__(self, upload_id, logparent=None):
        self.upload_id = upload_id
        self.query_count, self.good_query_count, self.bad_query_count = 0,0,0
        self.error_count = 0
        self.num_peers_contacted = 0
        self.last_failure_msg = None
        self._log_parent = log.msg("%s starting" % self, parent=logparent)

    def __repr__(self):
        return "<Tahoe2PeerSelector for upload %s>" % self.upload_id

    def get_shareholders(self, client,
                         storage_index, share_size, block_size,
                         num_segments, total_shares, shares_of_happiness,
                         push_to_ourselves):
        """
        @return: a set of PeerTracker instances that have agreed to hold some
                 shares for us
        """

        self.total_shares = total_shares
        self.shares_of_happiness = shares_of_happiness

        self.homeless_shares = range(total_shares)
        # self.uncontacted_peers = list() # peers we haven't asked yet
        self.contacted_peers = [] # peers worth asking again
        self.contacted_peers2 = [] # peers that we have asked again
        self._started_second_pass = False
        self.use_peers = set() # PeerTrackers that have shares assigned to them
        self.preexisting_shares = {} # sharenum -> PeerTracker holding the share

        peers = client.get_permuted_peers(storage_index, push_to_ourselves)
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

        trackers = [ PeerTracker(peerid, permutedid, conn,
                                 share_size, block_size,
                                 num_segments, num_share_hashes,
                                 storage_index,
                                 bucket_renewal_secret_hash(file_renewal_secret,
                                                            peerid),
                                 bucket_cancel_secret_hash(file_cancel_secret,
                                                           peerid),
                                 )
                     for permutedid, peerid, conn in peers ]
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

    def __init__(self, original, options={}):
        self.original = original
        self._options = options
        self._encryptor = None
        self._plaintext_hasher = plaintext_hasher()
        self._plaintext_segment_hasher = None
        self._plaintext_segment_hashes = []
        self._params = None

    def get_size(self):
        return self.original.get_size()

    def set_serialized_encoding_parameters(self, params):
        self._params = params

    def _get_encryptor(self):
        if self._encryptor:
            return defer.succeed(self._encryptor)

        if self._params is not None:
            self.original.set_serialized_encoding_parameters(self._params)

        d = self.original.get_encryption_key()
        def _got(key):
            e = AES(key)
            self._encryptor = e

            storage_index = storage_index_chk_hash(key)
            assert isinstance(storage_index, str)
            # There's no point to having the SI be longer than the key, so we
            # specify that it is truncated to the same 128 bits as the AES key.
            assert len(storage_index) == 16  # SHA-256 truncated to 128b
            self._storage_index = storage_index

            return e
        d.addCallback(_got)
        return d

    def get_storage_index(self):
        d = self._get_encryptor()
        d.addCallback(lambda res: self._storage_index)
        return d

    def set_segment_size(self, segsize):
        self._segment_size = segsize

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

            offset += this_segment

    def read_encrypted(self, length):
        d = self._get_encryptor()
        d.addCallback(lambda res: self.original.read(length))
        def _got(data):
            assert isinstance(data, (tuple, list)), type(data)
            data = list(data)
            cryptdata = []
            # we use data.pop(0) instead of 'for chunk in data' to save
            # memory: each chunk is destroyed as soon as we're done with it.
            while data:
                chunk = data.pop(0)
                self._plaintext_hasher.update(chunk)
                self._update_segment_hash(chunk)
                cryptdata.append(self._encryptor.process(chunk))
                del chunk
            return cryptdata
        d.addCallback(_got)
        return d

    def get_plaintext_hashtree_leaves(self, first, last, num_segments):
        if len(self._plaintext_segment_hashes) < num_segments:
            # close out the last one
            assert len(self._plaintext_segment_hashes) == num_segments-1
            p, segment_left = self._get_segment_hasher()
            self._plaintext_segment_hashes.append(p.digest())
            del self._plaintext_segment_hasher
        assert len(self._plaintext_segment_hashes) == num_segments
        return defer.succeed(tuple(self._plaintext_segment_hashes[first:last]))

    def get_plaintext_hash(self):
        h = self._plaintext_hasher.digest()
        return defer.succeed(h)


class CHKUploader:
    peer_selector_class = Tahoe2PeerSelector

    def __init__(self, client, options={}):
        self._client = client
        self._options = options
        self._log_number = self._client.log("CHKUploader starting")

    def set_params(self, encoding_parameters):
        self._encoding_parameters = encoding_parameters

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

        uploadable = IUploadable(uploadable)
        self.log("starting upload of %s" % uploadable)

        eu = EncryptAnUploadable(uploadable)
        d = self.start_encrypted(eu)
        def _uploaded(res):
            d1 = uploadable.get_encryption_key()
            d1.addCallback(lambda key: self._compute_uri(res, key))
            return d1
        d.addCallback(_uploaded)
        return d

    def start_encrypted(self, encrypted):
        eu = IEncryptedUploadable(encrypted)

        e = encode.Encoder(self._options, self)
        e.set_params(self._encoding_parameters)
        d = e.set_encrypted_uploadable(eu)
        d.addCallback(self.locate_all_shareholders)
        d.addCallback(self.set_shareholders, e)
        d.addCallback(lambda res: e.start())
        # this fires with the uri_extension_hash and other data
        return d

    def locate_all_shareholders(self, encoder):
        storage_index = encoder.get_param("storage_index")
        upload_id = idlib.b2a(storage_index)[:6]
        self.log("using storage index %s" % upload_id)
        peer_selector = self.peer_selector_class(upload_id, self._log_number)

        share_size = encoder.get_param("share_size")
        block_size = encoder.get_param("block_size")
        num_segments = encoder.get_param("num_segments")
        k,desired,n = encoder.get_param("share_counts")
        push_to_ourselves = self._options.get("push_to_ourselves", False)

        gs = peer_selector.get_shareholders
        d = gs(self._client, storage_index, share_size, block_size,
               num_segments, n, desired, push_to_ourselves)
        return d

    def set_shareholders(self, used_peers, encoder):
        """
        @param used_peers: a sequence of PeerTracker objects
        """
        self.log("_send_shares, used_peers is %s" % (used_peers,))
        for peer in used_peers:
            assert isinstance(peer, PeerTracker)
        buckets = {}
        for peer in used_peers:
            buckets.update(peer.buckets)
        assert len(buckets) == sum([len(peer.buckets) for peer in used_peers])
        encoder.set_shareholders(buckets)

    def _compute_uri(self, (uri_extension_hash,
                            needed_shares, total_shares, size),
                     key):
        u = uri.CHKFileURI(key=key,
                           uri_extension_hash=uri_extension_hash,
                           needed_shares=needed_shares,
                           total_shares=total_shares,
                           size=size,
                           )
        return u.to_string()


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

    def __init__(self, client, options={}):
        self._client = client
        self._options = options

    def set_params(self, encoding_parameters):
        pass

    def start(self, uploadable):
        uploadable = IUploadable(uploadable)
        d = uploadable.get_size()
        d.addCallback(lambda size: read_this_many_bytes(uploadable, size))
        d.addCallback(lambda data: uri.LiteralFileURI("".join(data)))
        d.addCallback(lambda u: u.to_string())
        return d

    def close(self):
        pass

class RemoteEncryptedUploabable(Referenceable):
    implements(RIEncryptedUploadable)

    def __init__(self, encrypted_uploadable):
        self._eu = IEncryptedUploadable(encrypted_uploadable)
        self._offset = 0

    def remote_get_size(self):
        return self._eu.get_size()
    def remote_set_segment_size(self, segment_size):
        self._eu.set_segment_size(segment_size)
    def remote_read_encrypted(self, offset, length):
        # we don't yet implement seek
        assert offset == self._offset, "%d != %d" % (offset, self._offset)
        d = self._eu.read_encrypted(length)
        def _read(strings):
            self._offset += sum([len(data) for data in strings])
            return strings
        d.addCallback(_read)
        return d
    def remote_get_plaintext_hashtree_leaves(self, first, last, num_segments):
        d = self._eu.get_plaintext_hashtree_leaves(first, last, num_segments)
        d.addCallback(list)
        return d
    def remote_get_plaintext_hash(self):
        return self._eu.get_plaintext_hash()


class AssistedUploader:

    def __init__(self, helper, options={}):
        self._helper = helper
        self._options = options
        self._log_number = log.msg("AssistedUploader starting")

    def log(self, msg, parent=None, **kwargs):
        if parent is None:
            parent = self._log_number
        return log.msg(msg, parent=parent, **kwargs)

    def set_params(self, encoding_parameters):
        self._needed_shares, happy, self._total_shares = encoding_parameters

    def start(self, uploadable):
        u = IUploadable(uploadable)
        eu = IEncryptedUploadable(EncryptAnUploadable(u, self._options))
        self._encuploadable = eu
        d = eu.get_size()
        d.addCallback(self._got_size)
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
        return d

    def _got_size(self, size):
        self._size = size

    def _got_encryption_key(self, key):
        self._key = key

    def _got_storage_index(self, storage_index):
        self._storage_index = storage_index

    def _contact_helper(self, res):
        self.log("contacting helper..")
        d = self._helper.callRemote("upload_chk", self._storage_index)
        d.addCallback(self._contacted_helper)
        return d
    def _contacted_helper(self, (upload_results, upload_helper)):
        if upload_helper:
            self.log("helper says we need to upload")
            # we need to upload the file
            reu = RemoteEncryptedUploabable(self._encuploadable)
            d = upload_helper.callRemote("upload", reu)
            # this Deferred will fire with the upload results
            return d
        self.log("helper says file is already uploaded")
        return upload_results

    def _build_readcap(self, upload_results):
        ur = upload_results
        u = uri.CHKFileURI(key=self._key,
                           uri_extension_hash=ur['uri_extension_hash'],
                           needed_shares=self._needed_shares,
                           total_shares=self._total_shares,
                           size=self._size,
                           )
        return u.to_string()


class ConvergentUploadMixin:
    # to use this, the class it is mixed in to must have a seekable
    # filehandle named self._filehandle
    _params = None
    _key = None

    def set_serialized_encoding_parameters(self, params):
        self._params = params
        # ignored for now

    def get_encryption_key(self):
        if self._key is None:
            f = self._filehandle
            enckey_hasher = key_hasher()
            #enckey_hasher.update(encoding_parameters) # TODO
            f.seek(0)
            BLOCKSIZE = 64*1024
            while True:
                data = f.read(BLOCKSIZE)
                if not data:
                    break
                enckey_hasher.update(data)
            f.seek(0)
            self._key = enckey_hasher.digest()[:16]

        return defer.succeed(self._key)

class NonConvergentUploadMixin:
    _key = None

    def set_serialized_encoding_parameters(self, params):
        pass

    def get_encryption_key(self):
        if self._key is None:
            self._key = os.urandom(16)
        return defer.succeed(self._key)


class FileHandle(ConvergentUploadMixin):
    implements(IUploadable)

    def __init__(self, filehandle):
        self._filehandle = filehandle

    def get_size(self):
        self._filehandle.seek(0,2)
        size = self._filehandle.tell()
        self._filehandle.seek(0)
        return defer.succeed(size)

    def read(self, length):
        return defer.succeed([self._filehandle.read(length)])

    def close(self):
        # the originator of the filehandle reserves the right to close it
        pass

class FileName(FileHandle):
    def __init__(self, filename):
        FileHandle.__init__(self, open(filename, "rb"))
    def close(self):
        FileHandle.close(self)
        self._filehandle.close()

class Data(FileHandle):
    def __init__(self, data):
        FileHandle.__init__(self, StringIO(data))

class Uploader(service.MultiService):
    """I am a service that allows file uploading.
    """
    implements(IUploader)
    name = "uploader"
    uploader_class = CHKUploader
    URI_LIT_SIZE_THRESHOLD = 55

    DEFAULT_ENCODING_PARAMETERS = (25, 75, 100)
    # this is a tuple of (needed, desired, total). 'needed' is the number of
    # shares required to reconstruct a file. 'desired' means that we will
    # abort an upload unless we can allocate space for at least this many.
    # 'total' is the total number of shares created by encoding. If everybody
    # has room then this is is how many we will upload.

    def __init__(self, helper_furl=None):
        self._helper_furl = helper_furl
        self._helper = None
        service.MultiService.__init__(self)

    def startService(self):
        service.MultiService.startService(self)
        if self._helper_furl:
            self.parent.tub.connectTo(self._helper_furl,
                                      self._got_helper)

    def _got_helper(self, helper):
        self._helper = helper

    def upload(self, uploadable, options={}):
        # this returns the URI
        assert self.parent
        assert self.running
        push_to_ourselves = self.parent.get_push_to_ourselves()
        if push_to_ourselves is not None:
            options["push_to_ourselves"] = push_to_ourselves

        uploadable = IUploadable(uploadable)
        d = uploadable.get_size()
        def _got_size(size):
            if size <= self.URI_LIT_SIZE_THRESHOLD:
                uploader = LiteralUploader(self.parent, options)
            elif self._helper:
                uploader = AssistedUploader(self._helper, options)
            else:
                uploader = self.uploader_class(self.parent, options)
            uploader.set_params(self.parent.get_encoding_parameters()
                                or self.DEFAULT_ENCODING_PARAMETERS)
            return uploader.start(uploadable)
        d.addCallback(_got_size)
        def _done(res):
            uploadable.close()
            return res
        d.addBoth(_done)
        return d

    # utility functions
    def upload_data(self, data, options={}):
        return self.upload(Data(data), options)
    def upload_filename(self, filename, options={}):
        return self.upload(FileName(filename), options)
    def upload_filehandle(self, filehandle, options={}):
        return self.upload(FileHandle(filehandle), options)
