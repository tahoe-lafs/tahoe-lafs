

import os, time
from StringIO import StringIO
from itertools import count
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from allmydata.interfaces import IPublishStatus, SDMF_VERSION, MDMF_VERSION, \
                                 IMutableUploadable
from allmydata.util import base32, hashutil, mathutil, idlib, log
from allmydata.util.dictutil import DictOfSets
from allmydata import hashtree, codec
from allmydata.storage.server import si_b2a
from pycryptopp.cipher.aes import AES
from foolscap.api import eventually, fireEventually

from allmydata.mutable.common import MODE_WRITE, MODE_CHECK, \
     UncoordinatedWriteError, NotEnoughServersError
from allmydata.mutable.servermap import ServerMap
from allmydata.mutable.layout import get_version_from_checkstring,\
                                     unpack_mdmf_checkstring, \
                                     unpack_sdmf_checkstring, \
                                     MDMFSlotWriteProxy, \
                                     SDMFSlotWriteProxy

KiB = 1024
DEFAULT_MAX_SEGMENT_SIZE = 128 * KiB
PUSHING_BLOCKS_STATE = 0
PUSHING_EVERYTHING_ELSE_STATE = 1
DONE_STATE = 2

class PublishStatus:
    implements(IPublishStatus)
    statusid_counter = count(0)
    def __init__(self):
        self.timings = {}
        self.timings["send_per_server"] = {}
        self.timings["encrypt"] = 0.0
        self.timings["encode"] = 0.0
        self.servermap = None
        self.problems = {}
        self.active = True
        self.storage_index = None
        self.helper = False
        self.encoding = ("?", "?")
        self.size = None
        self.status = "Not started"
        self.progress = 0.0
        self.counter = self.statusid_counter.next()
        self.started = time.time()

    def add_per_server_time(self, peerid, elapsed):
        if peerid not in self.timings["send_per_server"]:
            self.timings["send_per_server"][peerid] = []
        self.timings["send_per_server"][peerid].append(elapsed)
    def accumulate_encode_time(self, elapsed):
        self.timings["encode"] += elapsed
    def accumulate_encrypt_time(self, elapsed):
        self.timings["encrypt"] += elapsed

    def get_started(self):
        return self.started
    def get_storage_index(self):
        return self.storage_index
    def get_encoding(self):
        return self.encoding
    def using_helper(self):
        return self.helper
    def get_servermap(self):
        return self.servermap
    def get_size(self):
        return self.size
    def get_status(self):
        return self.status
    def get_progress(self):
        return self.progress
    def get_active(self):
        return self.active
    def get_counter(self):
        return self.counter

    def set_storage_index(self, si):
        self.storage_index = si
    def set_helper(self, helper):
        self.helper = helper
    def set_servermap(self, servermap):
        self.servermap = servermap
    def set_encoding(self, k, n):
        self.encoding = (k, n)
    def set_size(self, size):
        self.size = size
    def set_status(self, status):
        self.status = status
    def set_progress(self, value):
        self.progress = value
    def set_active(self, value):
        self.active = value

class LoopLimitExceededError(Exception):
    pass

class Publish:
    """I represent a single act of publishing the mutable file to the grid. I
    will only publish my data if the servermap I am using still represents
    the current state of the world.

    To make the initial publish, set servermap to None.
    """

    def __init__(self, filenode, storage_broker, servermap):
        self._node = filenode
        self._storage_broker = storage_broker
        self._servermap = servermap
        self._storage_index = self._node.get_storage_index()
        self._log_prefix = prefix = si_b2a(self._storage_index)[:5]
        num = self.log("Publish(%s): starting" % prefix, parent=None)
        self._log_number = num
        self._running = True
        self._first_write_error = None
        self._last_failure = None

        self._status = PublishStatus()
        self._status.set_storage_index(self._storage_index)
        self._status.set_helper(False)
        self._status.set_progress(0.0)
        self._status.set_active(True)
        self._version = self._node.get_version()
        assert self._version in (SDMF_VERSION, MDMF_VERSION)


    def get_status(self):
        return self._status

    def log(self, *args, **kwargs):
        if 'parent' not in kwargs:
            kwargs['parent'] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.mutable.publish"
        return log.msg(*args, **kwargs)


    def update(self, data, offset, blockhashes, version):
        """
        I replace the contents of this file with the contents of data,
        starting at offset. I return a Deferred that fires with None
        when the replacement has been completed, or with an error if
        something went wrong during the process.

        Note that this process will not upload new shares. If the file
        being updated is in need of repair, callers will have to repair
        it on their own.
        """
        # How this works:
        # 1: Make peer assignments. We'll assign each share that we know
        # about on the grid to that peer that currently holds that
        # share, and will not place any new shares.
        # 2: Setup encoding parameters. Most of these will stay the same
        # -- datalength will change, as will some of the offsets.
        # 3. Upload the new segments.
        # 4. Be done.
        assert IMutableUploadable.providedBy(data)

        self.data = data

        # XXX: Use the MutableFileVersion instead.
        self.datalength = self._node.get_size()
        if data.get_size() > self.datalength:
            self.datalength = data.get_size()

        self.log("starting update")
        self.log("adding new data of length %d at offset %d" % \
                    (data.get_size(), offset))
        self.log("new data length is %d" % self.datalength)
        self._status.set_size(self.datalength)
        self._status.set_status("Started")
        self._started = time.time()

        self.done_deferred = defer.Deferred()

        self._writekey = self._node.get_writekey()
        assert self._writekey, "need write capability to publish"

        # first, which servers will we publish to? We require that the
        # servermap was updated in MODE_WRITE, so we can depend upon the
        # peerlist computed by that process instead of computing our own.
        assert self._servermap
        assert self._servermap.last_update_mode in (MODE_WRITE, MODE_CHECK)
        # we will push a version that is one larger than anything present
        # in the grid, according to the servermap.
        self._new_seqnum = self._servermap.highest_seqnum() + 1
        self._status.set_servermap(self._servermap)

        self.log(format="new seqnum will be %(seqnum)d",
                 seqnum=self._new_seqnum, level=log.NOISY)

        # We're updating an existing file, so all of the following
        # should be available.
        self.readkey = self._node.get_readkey()
        self.required_shares = self._node.get_required_shares()
        assert self.required_shares is not None
        self.total_shares = self._node.get_total_shares()
        assert self.total_shares is not None
        self._status.set_encoding(self.required_shares, self.total_shares)

        self._pubkey = self._node.get_pubkey()
        assert self._pubkey
        self._privkey = self._node.get_privkey()
        assert self._privkey
        self._encprivkey = self._node.get_encprivkey()

        sb = self._storage_broker
        full_peerlist = [(s.get_serverid(), s.get_rref())
                         for s in sb.get_servers_for_psi(self._storage_index)]
        self.full_peerlist = full_peerlist # for use later, immutable
        self.bad_peers = set() # peerids who have errbacked/refused requests

        # This will set self.segment_size, self.num_segments, and
        # self.fec. TODO: Does it know how to do the offset? Probably
        # not. So do that part next.
        self.setup_encoding_parameters(offset=offset)

        # if we experience any surprises (writes which were rejected because
        # our test vector did not match, or shares which we didn't expect to
        # see), we set this flag and report an UncoordinatedWriteError at the
        # end of the publish process.
        self.surprised = False

        # we keep track of three tables. The first is our goal: which share
        # we want to see on which servers. This is initially populated by the
        # existing servermap.
        self.goal = set() # pairs of (peerid, shnum) tuples

        # the number of outstanding queries: those that are in flight and
        # may or may not be delivered, accepted, or acknowledged. This is
        # incremented when a query is sent, and decremented when the response
        # returns or errbacks.
        self.num_outstanding = 0

        # the third is a table of successes: share which have actually been
        # placed. These are populated when responses come back with success.
        # When self.placed == self.goal, we're done.
        self.placed = set() # (peerid, shnum) tuples

        # we also keep a mapping from peerid to RemoteReference. Each time we
        # pull a connection out of the full peerlist, we add it to this for
        # use later.
        self.connections = {}

        self.bad_share_checkstrings = {}

        # This is set at the last step of the publishing process.
        self.versioninfo = ""

        # we use the servermap to populate the initial goal: this way we will
        # try to update each existing share in place. Since we're
        # updating, we ignore damaged and missing shares -- callers must
        # do a repair to repair and recreate these.
        for (peerid, shnum) in self._servermap.servermap:
            self.goal.add( (peerid, shnum) )
            self.connections[peerid] = self._servermap.connections[peerid]
        self.writers = {}

        # SDMF files are updated differently.
        self._version = MDMF_VERSION
        writer_class = MDMFSlotWriteProxy

        # For each (peerid, shnum) in self.goal, we make a
        # write proxy for that peer. We'll use this to write
        # shares to the peer.
        for key in self.goal:
            peerid, shnum = key
            write_enabler = self._node.get_write_enabler(peerid)
            renew_secret = self._node.get_renewal_secret(peerid)
            cancel_secret = self._node.get_cancel_secret(peerid)
            secrets = (write_enabler, renew_secret, cancel_secret)

            self.writers[shnum] =  writer_class(shnum,
                                                self.connections[peerid],
                                                self._storage_index,
                                                secrets,
                                                self._new_seqnum,
                                                self.required_shares,
                                                self.total_shares,
                                                self.segment_size,
                                                self.datalength)
            self.writers[shnum].peerid = peerid
            assert (peerid, shnum) in self._servermap.servermap
            old_versionid, old_timestamp = self._servermap.servermap[key]
            (old_seqnum, old_root_hash, old_salt, old_segsize,
             old_datalength, old_k, old_N, old_prefix,
             old_offsets_tuple) = old_versionid
            self.writers[shnum].set_checkstring(old_seqnum,
                                                old_root_hash,
                                                old_salt)

        # Our remote shares will not have a complete checkstring until
        # after we are done writing share data and have started to write
        # blocks. In the meantime, we need to know what to look for when
        # writing, so that we can detect UncoordinatedWriteErrors.
        self._checkstring = self.writers.values()[0].get_checkstring()

        # Now, we start pushing shares.
        self._status.timings["setup"] = time.time() - self._started
        # First, we encrypt, encode, and publish the shares that we need
        # to encrypt, encode, and publish.

        # Our update process fetched these for us. We need to update
        # them in place as publishing happens.
        self.blockhashes = {} # (shnum, [blochashes])
        for (i, bht) in blockhashes.iteritems():
            # We need to extract the leaves from our old hash tree.
            old_segcount = mathutil.div_ceil(version[4],
                                             version[3])
            h = hashtree.IncompleteHashTree(old_segcount)
            bht = dict(enumerate(bht))
            h.set_hashes(bht)
            leaves = h[h.get_leaf_index(0):]
            for j in xrange(self.num_segments - len(leaves)):
                leaves.append(None)

            assert len(leaves) >= self.num_segments
            self.blockhashes[i] = leaves
            # This list will now be the leaves that were set during the
            # initial upload + enough empty hashes to make it a
            # power-of-two. If we exceed a power of two boundary, we
            # should be encoding the file over again, and should not be
            # here. So, we have
            #assert len(self.blockhashes[i]) == \
            #    hashtree.roundup_pow2(self.num_segments), \
            #        len(self.blockhashes[i])
            # XXX: Except this doesn't work. Figure out why.

        # These are filled in later, after we've modified the block hash
        # tree suitably.
        self.sharehash_leaves = None # eventually [sharehashes]
        self.sharehashes = {} # shnum -> [sharehash leaves necessary to 
                              # validate the share]

        self.log("Starting push")

        self._state = PUSHING_BLOCKS_STATE
        self._push()

        return self.done_deferred


    def publish(self, newdata):
        """Publish the filenode's current contents.  Returns a Deferred that
        fires (with None) when the publish has done as much work as it's ever
        going to do, or errbacks with ConsistencyError if it detects a
        simultaneous write.
        """

        # 0. Setup encoding parameters, encoder, and other such things.
        # 1. Encrypt, encode, and publish segments.
        assert IMutableUploadable.providedBy(newdata)

        self.data = newdata
        self.datalength = newdata.get_size()
        #if self.datalength >= DEFAULT_MAX_SEGMENT_SIZE:
        #    self._version = MDMF_VERSION
        #else:
        #    self._version = SDMF_VERSION

        self.log("starting publish, datalen is %s" % self.datalength)
        self._status.set_size(self.datalength)
        self._status.set_status("Started")
        self._started = time.time()

        self.done_deferred = defer.Deferred()

        self._writekey = self._node.get_writekey()
        assert self._writekey, "need write capability to publish"

        # first, which servers will we publish to? We require that the
        # servermap was updated in MODE_WRITE, so we can depend upon the
        # peerlist computed by that process instead of computing our own.
        if self._servermap:
            assert self._servermap.last_update_mode in (MODE_WRITE, MODE_CHECK)
            # we will push a version that is one larger than anything present
            # in the grid, according to the servermap.
            self._new_seqnum = self._servermap.highest_seqnum() + 1
        else:
            # If we don't have a servermap, that's because we're doing the
            # initial publish
            self._new_seqnum = 1
            self._servermap = ServerMap()
        self._status.set_servermap(self._servermap)

        self.log(format="new seqnum will be %(seqnum)d",
                 seqnum=self._new_seqnum, level=log.NOISY)

        # having an up-to-date servermap (or using a filenode that was just
        # created for the first time) also guarantees that the following
        # fields are available
        self.readkey = self._node.get_readkey()
        self.required_shares = self._node.get_required_shares()
        assert self.required_shares is not None
        self.total_shares = self._node.get_total_shares()
        assert self.total_shares is not None
        self._status.set_encoding(self.required_shares, self.total_shares)

        self._pubkey = self._node.get_pubkey()
        assert self._pubkey
        self._privkey = self._node.get_privkey()
        assert self._privkey
        self._encprivkey = self._node.get_encprivkey()

        sb = self._storage_broker
        full_peerlist = [(s.get_serverid(), s.get_rref())
                         for s in sb.get_servers_for_psi(self._storage_index)]
        self.full_peerlist = full_peerlist # for use later, immutable
        self.bad_peers = set() # peerids who have errbacked/refused requests

        # This will set self.segment_size, self.num_segments, and
        # self.fec.
        self.setup_encoding_parameters()

        # if we experience any surprises (writes which were rejected because
        # our test vector did not match, or shares which we didn't expect to
        # see), we set this flag and report an UncoordinatedWriteError at the
        # end of the publish process.
        self.surprised = False

        # we keep track of three tables. The first is our goal: which share
        # we want to see on which servers. This is initially populated by the
        # existing servermap.
        self.goal = set() # pairs of (peerid, shnum) tuples

        # the number of outstanding queries: those that are in flight and
        # may or may not be delivered, accepted, or acknowledged. This is
        # incremented when a query is sent, and decremented when the response
        # returns or errbacks.
        self.num_outstanding = 0

        # the third is a table of successes: share which have actually been
        # placed. These are populated when responses come back with success.
        # When self.placed == self.goal, we're done.
        self.placed = set() # (peerid, shnum) tuples

        # we also keep a mapping from peerid to RemoteReference. Each time we
        # pull a connection out of the full peerlist, we add it to this for
        # use later.
        self.connections = {}

        self.bad_share_checkstrings = {}

        # This is set at the last step of the publishing process.
        self.versioninfo = ""

        # we use the servermap to populate the initial goal: this way we will
        # try to update each existing share in place.
        for (peerid, shnum) in self._servermap.servermap:
            self.goal.add( (peerid, shnum) )
            self.connections[peerid] = self._servermap.connections[peerid]
        # then we add in all the shares that were bad (corrupted, bad
        # signatures, etc). We want to replace these.
        for key, old_checkstring in self._servermap.bad_shares.items():
            (peerid, shnum) = key
            self.goal.add(key)
            self.bad_share_checkstrings[key] = old_checkstring
            self.connections[peerid] = self._servermap.connections[peerid]

        # TODO: Make this part do peer selection.
        self.update_goal()
        self.writers = {}
        if self._version == MDMF_VERSION:
            writer_class = MDMFSlotWriteProxy
        else:
            writer_class = SDMFSlotWriteProxy

        # For each (peerid, shnum) in self.goal, we make a
        # write proxy for that peer. We'll use this to write
        # shares to the peer.
        for key in self.goal:
            peerid, shnum = key
            write_enabler = self._node.get_write_enabler(peerid)
            renew_secret = self._node.get_renewal_secret(peerid)
            cancel_secret = self._node.get_cancel_secret(peerid)
            secrets = (write_enabler, renew_secret, cancel_secret)

            self.writers[shnum] =  writer_class(shnum,
                                                self.connections[peerid],
                                                self._storage_index,
                                                secrets,
                                                self._new_seqnum,
                                                self.required_shares,
                                                self.total_shares,
                                                self.segment_size,
                                                self.datalength)
            self.writers[shnum].peerid = peerid
            if (peerid, shnum) in self._servermap.servermap:
                old_versionid, old_timestamp = self._servermap.servermap[key]
                (old_seqnum, old_root_hash, old_salt, old_segsize,
                 old_datalength, old_k, old_N, old_prefix,
                 old_offsets_tuple) = old_versionid
                self.writers[shnum].set_checkstring(old_seqnum,
                                                    old_root_hash,
                                                    old_salt)
            elif (peerid, shnum) in self.bad_share_checkstrings:
                old_checkstring = self.bad_share_checkstrings[(peerid, shnum)]
                self.writers[shnum].set_checkstring(old_checkstring)

        # Our remote shares will not have a complete checkstring until
        # after we are done writing share data and have started to write
        # blocks. In the meantime, we need to know what to look for when
        # writing, so that we can detect UncoordinatedWriteErrors.
        self._checkstring = self.writers.values()[0].get_checkstring()

        # Now, we start pushing shares.
        self._status.timings["setup"] = time.time() - self._started
        # First, we encrypt, encode, and publish the shares that we need
        # to encrypt, encode, and publish.

        # This will eventually hold the block hash chain for each share
        # that we publish. We define it this way so that empty publishes
        # will still have something to write to the remote slot.
        self.blockhashes = dict([(i, []) for i in xrange(self.total_shares)])
        for i in xrange(self.total_shares):
            blocks = self.blockhashes[i]
            for j in xrange(self.num_segments):
                blocks.append(None)
        self.sharehash_leaves = None # eventually [sharehashes]
        self.sharehashes = {} # shnum -> [sharehash leaves necessary to 
                              # validate the share]

        self.log("Starting push")

        self._state = PUSHING_BLOCKS_STATE
        self._push()

        return self.done_deferred


    def _update_status(self):
        self._status.set_status("Sending Shares: %d placed out of %d, "
                                "%d messages outstanding" %
                                (len(self.placed),
                                 len(self.goal),
                                 self.num_outstanding))
        self._status.set_progress(1.0 * len(self.placed) / len(self.goal))


    def setup_encoding_parameters(self, offset=0):
        if self._version == MDMF_VERSION:
            segment_size = DEFAULT_MAX_SEGMENT_SIZE # 128 KiB by default
        else:
            segment_size = self.datalength # SDMF is only one segment
        # this must be a multiple of self.required_shares
        segment_size = mathutil.next_multiple(segment_size,
                                              self.required_shares)
        self.segment_size = segment_size

        # Calculate the starting segment for the upload.
        if segment_size:
            # We use div_ceil instead of integer division here because
            # it is semantically correct.
            # If datalength isn't an even multiple of segment_size, but
            # is larger than segment_size, datalength // segment_size
            # will be the largest number such that num <= datalength and
            # num % segment_size == 0. But that's not what we want,
            # because it ignores the extra data. div_ceil will give us
            # the right number of segments for the data that we're
            # given.
            self.num_segments = mathutil.div_ceil(self.datalength,
                                                  segment_size)

            self.starting_segment = offset // segment_size

        else:
            self.num_segments = 0
            self.starting_segment = 0


        self.log("building encoding parameters for file")
        self.log("got segsize %d" % self.segment_size)
        self.log("got %d segments" % self.num_segments)

        if self._version == SDMF_VERSION:
            assert self.num_segments in (0, 1) # SDMF
        # calculate the tail segment size.

        if segment_size and self.datalength:
            self.tail_segment_size = self.datalength % segment_size
            self.log("got tail segment size %d" % self.tail_segment_size)
        else:
            self.tail_segment_size = 0

        if self.tail_segment_size == 0 and segment_size:
            # The tail segment is the same size as the other segments.
            self.tail_segment_size = segment_size

        # Make FEC encoders
        fec = codec.CRSEncoder()
        fec.set_params(self.segment_size,
                       self.required_shares, self.total_shares)
        self.piece_size = fec.get_block_size()
        self.fec = fec

        if self.tail_segment_size == self.segment_size:
            self.tail_fec = self.fec
        else:
            tail_fec = codec.CRSEncoder()
            tail_fec.set_params(self.tail_segment_size,
                                self.required_shares,
                                self.total_shares)
            self.tail_fec = tail_fec

        self._current_segment = self.starting_segment
        self.end_segment = self.num_segments - 1
        # Now figure out where the last segment should be.
        if self.data.get_size() != self.datalength:
            # We're updating a few segments in the middle of a mutable
            # file, so we don't want to republish the whole thing.
            # (we don't have enough data to do that even if we wanted
            # to)
            end = self.data.get_size()
            self.end_segment = end // segment_size
            if end % segment_size == 0:
                self.end_segment -= 1

        self.log("got start segment %d" % self.starting_segment)
        self.log("got end segment %d" % self.end_segment)


    def _push(self, ignored=None):
        """
        I manage state transitions. In particular, I see that we still
        have a good enough number of writers to complete the upload
        successfully.
        """
        # Can we still successfully publish this file?
        # TODO: Keep track of outstanding queries before aborting the
        #       process.
        if len(self.writers) < self.required_shares or self.surprised:
            return self._failure()

        # Figure out what we need to do next. Each of these needs to
        # return a deferred so that we don't block execution when this
        # is first called in the upload method.
        if self._state == PUSHING_BLOCKS_STATE:
            return self.push_segment(self._current_segment)

        elif self._state == PUSHING_EVERYTHING_ELSE_STATE:
            return self.push_everything_else()

        # If we make it to this point, we were successful in placing the
        # file.
        return self._done()


    def push_segment(self, segnum):
        if self.num_segments == 0 and self._version == SDMF_VERSION:
            self._add_dummy_salts()

        if segnum > self.end_segment:
            # We don't have any more segments to push.
            self._state = PUSHING_EVERYTHING_ELSE_STATE
            return self._push()

        d = self._encode_segment(segnum)
        d.addCallback(self._push_segment, segnum)
        def _increment_segnum(ign):
            self._current_segment += 1
        # XXX: I don't think we need to do addBoth here -- any errBacks
        # should be handled within push_segment.
        d.addCallback(_increment_segnum)
        d.addCallback(self._turn_barrier)
        d.addCallback(self._push)
        d.addErrback(self._failure)


    def _turn_barrier(self, result):
        """
        I help the publish process avoid the recursion limit issues
        described in #237.
        """
        return fireEventually(result)


    def _add_dummy_salts(self):
        """
        SDMF files need a salt even if they're empty, or the signature
        won't make sense. This method adds a dummy salt to each of our
        SDMF writers so that they can write the signature later.
        """
        salt = os.urandom(16)
        assert self._version == SDMF_VERSION

        for writer in self.writers.itervalues():
            writer.put_salt(salt)


    def _encode_segment(self, segnum):
        """
        I encrypt and encode the segment segnum.
        """
        started = time.time()

        if segnum + 1 == self.num_segments:
            segsize = self.tail_segment_size
        else:
            segsize = self.segment_size


        self.log("Pushing segment %d of %d" % (segnum + 1, self.num_segments))
        data = self.data.read(segsize)
        # XXX: This is dumb. Why return a list?
        data = "".join(data)

        assert len(data) == segsize, len(data)

        salt = os.urandom(16)

        key = hashutil.ssk_readkey_data_hash(salt, self.readkey)
        self._status.set_status("Encrypting")
        enc = AES(key)
        crypttext = enc.process(data)
        assert len(crypttext) == len(data)

        now = time.time()
        self._status.accumulate_encrypt_time(now - started)
        started = now

        # now apply FEC
        if segnum + 1 == self.num_segments:
            fec = self.tail_fec
        else:
            fec = self.fec

        self._status.set_status("Encoding")
        crypttext_pieces = [None] * self.required_shares
        piece_size = fec.get_block_size()
        for i in range(len(crypttext_pieces)):
            offset = i * piece_size
            piece = crypttext[offset:offset+piece_size]
            piece = piece + "\x00"*(piece_size - len(piece)) # padding
            crypttext_pieces[i] = piece
            assert len(piece) == piece_size
        d = fec.encode(crypttext_pieces)
        def _done_encoding(res):
            elapsed = time.time() - started
            self._status.accumulate_encode_time(elapsed)
            return (res, salt)
        d.addCallback(_done_encoding)
        return d


    def _push_segment(self, encoded_and_salt, segnum):
        """
        I push (data, salt) as segment number segnum.
        """
        results, salt = encoded_and_salt
        shares, shareids = results
        self._status.set_status("Pushing segment")
        for i in xrange(len(shares)):
            sharedata = shares[i]
            shareid = shareids[i]
            if self._version == MDMF_VERSION:
                hashed = salt + sharedata
            else:
                hashed = sharedata
            block_hash = hashutil.block_hash(hashed)
            self.blockhashes[shareid][segnum] = block_hash
            # find the writer for this share
            writer = self.writers[shareid]
            writer.put_block(sharedata, segnum, salt)


    def push_everything_else(self):
        """
        I put everything else associated with a share.
        """
        self._pack_started = time.time()
        self.push_encprivkey()
        self.push_blockhashes()
        self.push_sharehashes()
        self.push_toplevel_hashes_and_signature()
        d = self.finish_publishing()
        def _change_state(ignored):
            self._state = DONE_STATE
        d.addCallback(_change_state)
        d.addCallback(self._push)
        return d


    def push_encprivkey(self):
        encprivkey = self._encprivkey
        self._status.set_status("Pushing encrypted private key")
        for writer in self.writers.itervalues():
            writer.put_encprivkey(encprivkey)


    def push_blockhashes(self):
        self.sharehash_leaves = [None] * len(self.blockhashes)
        self._status.set_status("Building and pushing block hash tree")
        for shnum, blockhashes in self.blockhashes.iteritems():
            t = hashtree.HashTree(blockhashes)
            self.blockhashes[shnum] = list(t)
            # set the leaf for future use.
            self.sharehash_leaves[shnum] = t[0]

            writer = self.writers[shnum]
            writer.put_blockhashes(self.blockhashes[shnum])


    def push_sharehashes(self):
        self._status.set_status("Building and pushing share hash chain")
        share_hash_tree = hashtree.HashTree(self.sharehash_leaves)
        for shnum in xrange(len(self.sharehash_leaves)):
            needed_indices = share_hash_tree.needed_hashes(shnum)
            self.sharehashes[shnum] = dict( [ (i, share_hash_tree[i])
                                             for i in needed_indices] )
            writer = self.writers[shnum]
            writer.put_sharehashes(self.sharehashes[shnum])
        self.root_hash = share_hash_tree[0]


    def push_toplevel_hashes_and_signature(self):
        # We need to to three things here:
        #   - Push the root hash and salt hash
        #   - Get the checkstring of the resulting layout; sign that.
        #   - Push the signature
        self._status.set_status("Pushing root hashes and signature")
        for shnum in xrange(self.total_shares):
            writer = self.writers[shnum]
            writer.put_root_hash(self.root_hash)
        self._update_checkstring()
        self._make_and_place_signature()


    def _update_checkstring(self):
        """
        After putting the root hash, MDMF files will have the
        checkstring written to the storage server. This means that we
        can update our copy of the checkstring so we can detect
        uncoordinated writes. SDMF files will have the same checkstring,
        so we need not do anything.
        """
        self._checkstring = self.writers.values()[0].get_checkstring()


    def _make_and_place_signature(self):
        """
        I create and place the signature.
        """
        started = time.time()
        self._status.set_status("Signing prefix")
        signable = self.writers[0].get_signable()
        self.signature = self._privkey.sign(signable)

        for (shnum, writer) in self.writers.iteritems():
            writer.put_signature(self.signature)
        self._status.timings['sign'] = time.time() - started


    def finish_publishing(self):
        # We're almost done -- we just need to put the verification key
        # and the offsets
        started = time.time()
        self._status.set_status("Pushing shares")
        self._started_pushing = started
        ds = []
        verification_key = self._pubkey.serialize()

        for (shnum, writer) in self.writers.copy().iteritems():
            writer.put_verification_key(verification_key)
            self.num_outstanding += 1
            def _no_longer_outstanding(res):
                self.num_outstanding -= 1
                return res

            d = writer.finish_publishing()
            d.addBoth(_no_longer_outstanding)
            d.addErrback(self._connection_problem, writer)
            d.addCallback(self._got_write_answer, writer, started)
            ds.append(d)
        self._record_verinfo()
        self._status.timings['pack'] = time.time() - started
        return defer.DeferredList(ds)


    def _record_verinfo(self):
        self.versioninfo = self.writers.values()[0].get_verinfo()


    def _connection_problem(self, f, writer):
        """
        We ran into a connection problem while working with writer, and
        need to deal with that.
        """
        self.log("found problem: %s" % str(f))
        self._last_failure = f
        del(self.writers[writer.shnum])


    def log_goal(self, goal, message=""):
        logmsg = [message]
        for (shnum, peerid) in sorted([(s,p) for (p,s) in goal]):
            logmsg.append("sh%d to [%s]" % (shnum,
                                            idlib.shortnodeid_b2a(peerid)))
        self.log("current goal: %s" % (", ".join(logmsg)), level=log.NOISY)
        self.log("we are planning to push new seqnum=#%d" % self._new_seqnum,
                 level=log.NOISY)

    def update_goal(self):
        # if log.recording_noisy
        if True:
            self.log_goal(self.goal, "before update: ")

        # first, remove any bad peers from our goal
        self.goal = set([ (peerid, shnum)
                          for (peerid, shnum) in self.goal
                          if peerid not in self.bad_peers ])

        # find the homeless shares:
        homefull_shares = set([shnum for (peerid, shnum) in self.goal])
        homeless_shares = set(range(self.total_shares)) - homefull_shares
        homeless_shares = sorted(list(homeless_shares))
        # place them somewhere. We prefer unused servers at the beginning of
        # the available peer list.

        if not homeless_shares:
            return

        # if an old share X is on a node, put the new share X there too.
        # TODO: 1: redistribute shares to achieve one-per-peer, by copying
        #       shares from existing peers to new (less-crowded) ones. The
        #       old shares must still be updated.
        # TODO: 2: move those shares instead of copying them, to reduce future
        #       update work

        # this is a bit CPU intensive but easy to analyze. We create a sort
        # order for each peerid. If the peerid is marked as bad, we don't
        # even put them in the list. Then we care about the number of shares
        # which have already been assigned to them. After that we care about
        # their permutation order.
        old_assignments = DictOfSets()
        for (peerid, shnum) in self.goal:
            old_assignments.add(peerid, shnum)

        peerlist = []
        for i, (peerid, ss) in enumerate(self.full_peerlist):
            if peerid in self.bad_peers:
                continue
            entry = (len(old_assignments.get(peerid, [])), i, peerid, ss)
            peerlist.append(entry)
        peerlist.sort()

        if not peerlist:
            raise NotEnoughServersError("Ran out of non-bad servers, "
                                        "first_error=%s" %
                                        str(self._first_write_error),
                                        self._first_write_error)

        # we then index this peerlist with an integer, because we may have to
        # wrap. We update the goal as we go.
        i = 0
        for shnum in homeless_shares:
            (ignored1, ignored2, peerid, ss) = peerlist[i]
            # if we are forced to send a share to a server that already has
            # one, we may have two write requests in flight, and the
            # servermap (which was computed before either request was sent)
            # won't reflect the new shares, so the second response will be
            # surprising. There is code in _got_write_answer() to tolerate
            # this, otherwise it would cause the publish to fail with an
            # UncoordinatedWriteError. See #546 for details of the trouble
            # this used to cause.
            self.goal.add( (peerid, shnum) )
            self.connections[peerid] = ss
            i += 1
            if i >= len(peerlist):
                i = 0
        if True:
            self.log_goal(self.goal, "after update: ")


    def _got_write_answer(self, answer, writer, started):
        if not answer:
            # SDMF writers only pretend to write when readers set their
            # blocks, salts, and so on -- they actually just write once,
            # at the end of the upload process. In fake writes, they
            # return defer.succeed(None). If we see that, we shouldn't
            # bother checking it.
            return

        peerid = writer.peerid
        lp = self.log("_got_write_answer from %s, share %d" %
                      (idlib.shortnodeid_b2a(peerid), writer.shnum))

        now = time.time()
        elapsed = now - started

        self._status.add_per_server_time(peerid, elapsed)

        wrote, read_data = answer

        surprise_shares = set(read_data.keys()) - set([writer.shnum])

        # We need to remove from surprise_shares any shares that we are
        # knowingly also writing to that peer from other writers.

        # TODO: Precompute this.
        known_shnums = [x.shnum for x in self.writers.values()
                        if x.peerid == peerid]
        surprise_shares -= set(known_shnums)
        self.log("found the following surprise shares: %s" %
                 str(surprise_shares))

        # Now surprise shares contains all of the shares that we did not
        # expect to be there.

        surprised = False
        for shnum in surprise_shares:
            # read_data is a dict mapping shnum to checkstring (SIGNED_PREFIX)
            checkstring = read_data[shnum][0]
            # What we want to do here is to see if their (seqnum,
            # roothash, salt) is the same as our (seqnum, roothash,
            # salt), or the equivalent for MDMF. The best way to do this
            # is to store a packed representation of our checkstring
            # somewhere, then not bother unpacking the other
            # checkstring.
            if checkstring == self._checkstring:
                # they have the right share, somehow

                if (peerid,shnum) in self.goal:
                    # and we want them to have it, so we probably sent them a
                    # copy in an earlier write. This is ok, and avoids the
                    # #546 problem.
                    continue

                # They aren't in our goal, but they are still for the right
                # version. Somebody else wrote them, and it's a convergent
                # uncoordinated write. Pretend this is ok (don't be
                # surprised), since I suspect there's a decent chance that
                # we'll hit this in normal operation.
                continue

            else:
                # the new shares are of a different version
                if peerid in self._servermap.reachable_peers:
                    # we asked them about their shares, so we had knowledge
                    # of what they used to have. Any surprising shares must
                    # have come from someone else, so UCW.
                    surprised = True
                else:
                    # we didn't ask them, and now we've discovered that they
                    # have a share we didn't know about. This indicates that
                    # mapupdate should have wokred harder and asked more
                    # servers before concluding that it knew about them all.

                    # signal UCW, but make sure to ask this peer next time,
                    # so we'll remember to update it if/when we retry.
                    surprised = True
                    # TODO: ask this peer next time. I don't yet have a good
                    # way to do this. Two insufficient possibilities are:
                    #
                    # self._servermap.add_new_share(peerid, shnum, verinfo, now)
                    #  but that requires fetching/validating/parsing the whole
                    #  version string, and all we have is the checkstring
                    # self._servermap.mark_bad_share(peerid, shnum, checkstring)
                    #  that will make publish overwrite the share next time,
                    #  but it won't re-query the server, and it won't make
                    #  mapupdate search further

                    # TODO later: when publish starts, do
                    # servermap.get_best_version(), extract the seqnum,
                    # subtract one, and store as highest-replaceable-seqnum.
                    # Then, if this surprise-because-we-didn't-ask share is
                    # of highest-replaceable-seqnum or lower, we're allowed
                    # to replace it: send out a new writev (or rather add it
                    # to self.goal and loop).
                    pass

                surprised = True

        if surprised:
            self.log("they had shares %s that we didn't know about" %
                     (list(surprise_shares),),
                     parent=lp, level=log.WEIRD, umid="un9CSQ")
            self.surprised = True

        if not wrote:
            # TODO: there are two possibilities. The first is that the server
            # is full (or just doesn't want to give us any room), which means
            # we shouldn't ask them again, but is *not* an indication of an
            # uncoordinated write. The second is that our testv failed, which
            # *does* indicate an uncoordinated write. We currently don't have
            # a way to tell these two apart (in fact, the storage server code
            # doesn't have the option of refusing our share).
            #
            # If the server is full, mark the peer as bad (so we don't ask
            # them again), but don't set self.surprised. The loop() will find
            # a new server.
            #
            # If the testv failed, log it, set self.surprised, but don't
            # bother adding to self.bad_peers .

            self.log("our testv failed, so the write did not happen",
                     parent=lp, level=log.WEIRD, umid="8sc26g")
            self.surprised = True
            self.bad_peers.add(writer) # don't ask them again
            # use the checkstring to add information to the log message
            unknown_format = False
            for (shnum,readv) in read_data.items():
                checkstring = readv[0]
                version = get_version_from_checkstring(checkstring)
                if version == MDMF_VERSION:
                    (other_seqnum,
                     other_roothash) = unpack_mdmf_checkstring(checkstring)
                elif version == SDMF_VERSION:
                    (other_seqnum,
                     other_roothash,
                     other_IV) = unpack_sdmf_checkstring(checkstring)
                else:
                    unknown_format = True
                expected_version = self._servermap.version_on_peer(peerid,
                                                                   shnum)
                if expected_version:
                    (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
                     offsets_tuple) = expected_version
                    msg = ("somebody modified the share on us:"
                           " shnum=%d: I thought they had #%d:R=%s," %
                           (shnum,
                            seqnum, base32.b2a(root_hash)[:4]))
                    if unknown_format:
                        msg += (" but I don't know how to read share"
                                " format %d" % version)
                    else:
                        msg += " but testv reported #%d:R=%s" % \
                               (other_seqnum, other_roothash)
                    self.log(msg, parent=lp, level=log.NOISY)
                # if expected_version==None, then we didn't expect to see a
                # share on that peer, and the 'surprise_shares' clause above
                # will have logged it.
            return

        # and update the servermap
        # self.versioninfo is set during the last phase of publishing.
        # If we get there, we know that responses correspond to placed
        # shares, and can safely execute these statements.
        if self.versioninfo:
            self.log("wrote successfully: adding new share to servermap")
            self._servermap.add_new_share(peerid, writer.shnum,
                                          self.versioninfo, started)
            self.placed.add( (peerid, writer.shnum) )
        self._update_status()
        # the next method in the deferred chain will check to see if
        # we're done and successful.
        return


    def _done(self):
        if not self._running:
            return
        self._running = False
        now = time.time()
        self._status.timings["total"] = now - self._started

        elapsed = now - self._started_pushing
        self._status.timings['push'] = elapsed

        self._status.set_active(False)
        self.log("Publish done, success")
        self._status.set_status("Finished")
        self._status.set_progress(1.0)
        # Get k and segsize, then give them to the caller.
        hints = {}
        hints['segsize'] = self.segment_size
        hints['k'] = self.required_shares
        self._node.set_downloader_hints(hints)
        eventually(self.done_deferred.callback, None)

    def _failure(self, f=None):
        if f:
            self._last_failure = f

        if not self.surprised:
            # We ran out of servers
            msg = "Publish ran out of good servers"
            if self._last_failure:
                msg += ", last failure was: %s" % str(self._last_failure)
            self.log(msg)
            e = NotEnoughServersError(msg)

        else:
            # We ran into shares that we didn't recognize, which means
            # that we need to return an UncoordinatedWriteError.
            self.log("Publish failed with UncoordinatedWriteError")
            e = UncoordinatedWriteError()
        f = failure.Failure(e)
        eventually(self.done_deferred.callback, f)


class MutableFileHandle:
    """
    I am a mutable uploadable built around a filehandle-like object,
    usually either a StringIO instance or a handle to an actual file.
    """
    implements(IMutableUploadable)

    def __init__(self, filehandle):
        # The filehandle is defined as a generally file-like object that
        # has these two methods. We don't care beyond that.
        assert hasattr(filehandle, "read")
        assert hasattr(filehandle, "close")

        self._filehandle = filehandle
        # We must start reading at the beginning of the file, or we risk
        # encountering errors when the data read does not match the size
        # reported to the uploader.
        self._filehandle.seek(0)

        # We have not yet read anything, so our position is 0.
        self._marker = 0


    def get_size(self):
        """
        I return the amount of data in my filehandle.
        """
        if not hasattr(self, "_size"):
            old_position = self._filehandle.tell()
            # Seek to the end of the file by seeking 0 bytes from the
            # file's end
            self._filehandle.seek(0, 2) # 2 == os.SEEK_END in 2.5+
            self._size = self._filehandle.tell()
            # Restore the previous position, in case this was called
            # after a read.
            self._filehandle.seek(old_position)
            assert self._filehandle.tell() == old_position

        assert hasattr(self, "_size")
        return self._size


    def pos(self):
        """
        I return the position of my read marker -- i.e., how much data I
        have already read and returned to callers.
        """
        return self._marker


    def read(self, length):
        """
        I return some data (up to length bytes) from my filehandle.

        In most cases, I return length bytes, but sometimes I won't --
        for example, if I am asked to read beyond the end of a file, or
        an error occurs.
        """
        results = self._filehandle.read(length)
        self._marker += len(results)
        return [results]


    def close(self):
        """
        I close the underlying filehandle. Any further operations on the
        filehandle fail at this point.
        """
        self._filehandle.close()


class MutableData(MutableFileHandle):
    """
    I am a mutable uploadable built around a string, which I then cast
    into a StringIO and treat as a filehandle.
    """

    def __init__(self, s):
        # Take a string and return a file-like uploadable.
        assert isinstance(s, str)

        MutableFileHandle.__init__(self, StringIO(s))


class TransformingUploadable:
    """
    I am an IMutableUploadable that wraps another IMutableUploadable,
    and some segments that are already on the grid. When I am called to
    read, I handle merging of boundary segments.
    """
    implements(IMutableUploadable)


    def __init__(self, data, offset, segment_size, start, end):
        assert IMutableUploadable.providedBy(data)

        self._newdata = data
        self._offset = offset
        self._segment_size = segment_size
        self._start = start
        self._end = end

        self._read_marker = 0

        self._first_segment_offset = offset % segment_size

        num = self.log("TransformingUploadable: starting", parent=None)
        self._log_number = num
        self.log("got fso: %d" % self._first_segment_offset)
        self.log("got offset: %d" % self._offset)


    def log(self, *args, **kwargs):
        if 'parent' not in kwargs:
            kwargs['parent'] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.mutable.transforminguploadable"
        return log.msg(*args, **kwargs)


    def get_size(self):
        return self._offset + self._newdata.get_size()


    def read(self, length):
        # We can get data from 3 sources here. 
        #   1. The first of the segments provided to us.
        #   2. The data that we're replacing things with.
        #   3. The last of the segments provided to us.

        # are we in state 0?
        self.log("reading %d bytes" % length)

        old_start_data = ""
        old_data_length = self._first_segment_offset - self._read_marker
        if old_data_length > 0:
            if old_data_length > length:
                old_data_length = length
            self.log("returning %d bytes of old start data" % old_data_length)

            old_data_end = old_data_length + self._read_marker
            old_start_data = self._start[self._read_marker:old_data_end]
            length -= old_data_length
        else:
            # otherwise calculations later get screwed up.
            old_data_length = 0

        # Is there enough new data to satisfy this read? If not, we need
        # to pad the end of the data with data from our last segment.
        old_end_length = length - \
            (self._newdata.get_size() - self._newdata.pos())
        old_end_data = ""
        if old_end_length > 0:
            self.log("reading %d bytes of old end data" % old_end_length)

            # TODO: We're not explicitly checking for tail segment size
            # here. Is that a problem?
            old_data_offset = (length - old_end_length + \
                               old_data_length) % self._segment_size
            self.log("reading at offset %d" % old_data_offset)
            old_end = old_data_offset + old_end_length
            old_end_data = self._end[old_data_offset:old_end]
            length -= old_end_length
            assert length == self._newdata.get_size() - self._newdata.pos()

        self.log("reading %d bytes of new data" % length)
        new_data = self._newdata.read(length)
        new_data = "".join(new_data)

        self._read_marker += len(old_start_data + new_data + old_end_data)

        return old_start_data + new_data + old_end_data

    def close(self):
        pass
