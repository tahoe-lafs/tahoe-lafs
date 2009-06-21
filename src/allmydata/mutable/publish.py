

import os, struct, time
from itertools import count
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from allmydata.interfaces import IPublishStatus
from allmydata.util import base32, hashutil, mathutil, idlib, log
from allmydata import hashtree, codec
from allmydata.storage.server import si_b2a
from pycryptopp.cipher.aes import AES
from foolscap.api import eventually

from common import MODE_WRITE, MODE_CHECK, DictOfSets, \
     UncoordinatedWriteError, NotEnoughServersError
from servermap import ServerMap
from layout import pack_prefix, pack_share, unpack_header, pack_checkstring, \
     unpack_checkstring, SIGNED_PREFIX

class PublishStatus:
    implements(IPublishStatus)
    statusid_counter = count(0)
    def __init__(self):
        self.timings = {}
        self.timings["send_per_server"] = {}
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

    def __init__(self, filenode, servermap):
        self._node = filenode
        self._servermap = servermap
        self._storage_index = self._node.get_storage_index()
        self._log_prefix = prefix = si_b2a(self._storage_index)[:5]
        num = self._node._client.log("Publish(%s): starting" % prefix)
        self._log_number = num
        self._running = True
        self._first_write_error = None

        self._status = PublishStatus()
        self._status.set_storage_index(self._storage_index)
        self._status.set_helper(False)
        self._status.set_progress(0.0)
        self._status.set_active(True)

    def get_status(self):
        return self._status

    def log(self, *args, **kwargs):
        if 'parent' not in kwargs:
            kwargs['parent'] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.mutable.publish"
        return log.msg(*args, **kwargs)

    def publish(self, newdata):
        """Publish the filenode's current contents.  Returns a Deferred that
        fires (with None) when the publish has done as much work as it's ever
        going to do, or errbacks with ConsistencyError if it detects a
        simultaneous write.
        """

        # 1: generate shares (SDMF: files are small, so we can do it in RAM)
        # 2: perform peer selection, get candidate servers
        #  2a: send queries to n+epsilon servers, to determine current shares
        #  2b: based upon responses, create target map
        # 3: send slot_testv_and_readv_and_writev messages
        # 4: as responses return, update share-dispatch table
        # 4a: may need to run recovery algorithm
        # 5: when enough responses are back, we're done

        self.log("starting publish, datalen is %s" % len(newdata))
        self._status.set_size(len(newdata))
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

        sb = self._node._client.get_storage_broker()
        full_peerlist = sb.get_servers(self._storage_index)
        self.full_peerlist = full_peerlist # for use later, immutable
        self.bad_peers = set() # peerids who have errbacked/refused requests

        self.newdata = newdata
        self.salt = os.urandom(16)

        self.setup_encoding_parameters()

        # if we experience any surprises (writes which were rejected because
        # our test vector did not match, or shares which we didn't expect to
        # see), we set this flag and report an UncoordinatedWriteError at the
        # end of the publish process.
        self.surprised = False

        # as a failsafe, refuse to iterate through self.loop more than a
        # thousand times.
        self.looplimit = 1000

        # we keep track of three tables. The first is our goal: which share
        # we want to see on which servers. This is initially populated by the
        # existing servermap.
        self.goal = set() # pairs of (peerid, shnum) tuples

        # the second table is our list of outstanding queries: those which
        # are in flight and may or may not be delivered, accepted, or
        # acknowledged. Items are added to this table when the request is
        # sent, and removed when the response returns (or errbacks).
        self.outstanding = set() # (peerid, shnum) tuples

        # the third is a table of successes: share which have actually been
        # placed. These are populated when responses come back with success.
        # When self.placed == self.goal, we're done.
        self.placed = set() # (peerid, shnum) tuples

        # we also keep a mapping from peerid to RemoteReference. Each time we
        # pull a connection out of the full peerlist, we add it to this for
        # use later.
        self.connections = {}

        self.bad_share_checkstrings = {}

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

        # create the shares. We'll discard these as they are delivered. SDMF:
        # we're allowed to hold everything in memory.

        self._status.timings["setup"] = time.time() - self._started
        d = self._encrypt_and_encode()
        d.addCallback(self._generate_shares)
        def _start_pushing(res):
            self._started_pushing = time.time()
            return res
        d.addCallback(_start_pushing)
        d.addCallback(self.loop) # trigger delivery
        d.addErrback(self._fatal_error)

        return self.done_deferred

    def setup_encoding_parameters(self):
        segment_size = len(self.newdata)
        # this must be a multiple of self.required_shares
        segment_size = mathutil.next_multiple(segment_size,
                                              self.required_shares)
        self.segment_size = segment_size
        if segment_size:
            self.num_segments = mathutil.div_ceil(len(self.newdata),
                                                  segment_size)
        else:
            self.num_segments = 0
        assert self.num_segments in [0, 1,] # SDMF restrictions

    def _fatal_error(self, f):
        self.log("error during loop", failure=f, level=log.UNUSUAL)
        self._done(f)

    def _update_status(self):
        self._status.set_status("Sending Shares: %d placed out of %d, "
                                "%d messages outstanding" %
                                (len(self.placed),
                                 len(self.goal),
                                 len(self.outstanding)))
        self._status.set_progress(1.0 * len(self.placed) / len(self.goal))

    def loop(self, ignored=None):
        self.log("entering loop", level=log.NOISY)
        if not self._running:
            return

        self.looplimit -= 1
        if self.looplimit <= 0:
            raise LoopLimitExceededError("loop limit exceeded")

        if self.surprised:
            # don't send out any new shares, just wait for the outstanding
            # ones to be retired.
            self.log("currently surprised, so don't send any new shares",
                     level=log.NOISY)
        else:
            self.update_goal()
            # how far are we from our goal?
            needed = self.goal - self.placed - self.outstanding
            self._update_status()

            if needed:
                # we need to send out new shares
                self.log(format="need to send %(needed)d new shares",
                         needed=len(needed), level=log.NOISY)
                self._send_shares(needed)
                return

        if self.outstanding:
            # queries are still pending, keep waiting
            self.log(format="%(outstanding)d queries still outstanding",
                     outstanding=len(self.outstanding),
                     level=log.NOISY)
            return

        # no queries outstanding, no placements needed: we're done
        self.log("no queries outstanding, no placements needed: done",
                 level=log.OPERATIONAL)
        now = time.time()
        elapsed = now - self._started_pushing
        self._status.timings["push"] = elapsed
        return self._done(None)

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

        new_assignments = []
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



    def _encrypt_and_encode(self):
        # this returns a Deferred that fires with a list of (sharedata,
        # sharenum) tuples. TODO: cache the ciphertext, only produce the
        # shares that we care about.
        self.log("_encrypt_and_encode")

        self._status.set_status("Encrypting")
        started = time.time()

        key = hashutil.ssk_readkey_data_hash(self.salt, self.readkey)
        enc = AES(key)
        crypttext = enc.process(self.newdata)
        assert len(crypttext) == len(self.newdata)

        now = time.time()
        self._status.timings["encrypt"] = now - started
        started = now

        # now apply FEC

        self._status.set_status("Encoding")
        fec = codec.CRSEncoder()
        fec.set_params(self.segment_size,
                       self.required_shares, self.total_shares)
        piece_size = fec.get_block_size()
        crypttext_pieces = [None] * self.required_shares
        for i in range(len(crypttext_pieces)):
            offset = i * piece_size
            piece = crypttext[offset:offset+piece_size]
            piece = piece + "\x00"*(piece_size - len(piece)) # padding
            crypttext_pieces[i] = piece
            assert len(piece) == piece_size

        d = fec.encode(crypttext_pieces)
        def _done_encoding(res):
            elapsed = time.time() - started
            self._status.timings["encode"] = elapsed
            return res
        d.addCallback(_done_encoding)
        return d

    def _generate_shares(self, shares_and_shareids):
        # this sets self.shares and self.root_hash
        self.log("_generate_shares")
        self._status.set_status("Generating Shares")
        started = time.time()

        # we should know these by now
        privkey = self._privkey
        encprivkey = self._encprivkey
        pubkey = self._pubkey

        (shares, share_ids) = shares_and_shareids

        assert len(shares) == len(share_ids)
        assert len(shares) == self.total_shares
        all_shares = {}
        block_hash_trees = {}
        share_hash_leaves = [None] * len(shares)
        for i in range(len(shares)):
            share_data = shares[i]
            shnum = share_ids[i]
            all_shares[shnum] = share_data

            # build the block hash tree. SDMF has only one leaf.
            leaves = [hashutil.block_hash(share_data)]
            t = hashtree.HashTree(leaves)
            block_hash_trees[shnum] = block_hash_tree = list(t)
            share_hash_leaves[shnum] = t[0]
        for leaf in share_hash_leaves:
            assert leaf is not None
        share_hash_tree = hashtree.HashTree(share_hash_leaves)
        share_hash_chain = {}
        for shnum in range(self.total_shares):
            needed_hashes = share_hash_tree.needed_hashes(shnum)
            share_hash_chain[shnum] = dict( [ (i, share_hash_tree[i])
                                              for i in needed_hashes ] )
        root_hash = share_hash_tree[0]
        assert len(root_hash) == 32
        self.log("my new root_hash is %s" % base32.b2a(root_hash))
        self._new_version_info = (self._new_seqnum, root_hash, self.salt)

        prefix = pack_prefix(self._new_seqnum, root_hash, self.salt,
                             self.required_shares, self.total_shares,
                             self.segment_size, len(self.newdata))

        # now pack the beginning of the share. All shares are the same up
        # to the signature, then they have divergent share hash chains,
        # then completely different block hash trees + salt + share data,
        # then they all share the same encprivkey at the end. The sizes
        # of everything are the same for all shares.

        sign_started = time.time()
        signature = privkey.sign(prefix)
        self._status.timings["sign"] = time.time() - sign_started

        verification_key = pubkey.serialize()

        final_shares = {}
        for shnum in range(self.total_shares):
            final_share = pack_share(prefix,
                                     verification_key,
                                     signature,
                                     share_hash_chain[shnum],
                                     block_hash_trees[shnum],
                                     all_shares[shnum],
                                     encprivkey)
            final_shares[shnum] = final_share
        elapsed = time.time() - started
        self._status.timings["pack"] = elapsed
        self.shares = final_shares
        self.root_hash = root_hash

        # we also need to build up the version identifier for what we're
        # pushing. Extract the offsets from one of our shares.
        assert final_shares
        offsets = unpack_header(final_shares.values()[0])[-1]
        offsets_tuple = tuple( [(key,value) for key,value in offsets.items()] )
        verinfo = (self._new_seqnum, root_hash, self.salt,
                   self.segment_size, len(self.newdata),
                   self.required_shares, self.total_shares,
                   prefix, offsets_tuple)
        self.versioninfo = verinfo



    def _send_shares(self, needed):
        self.log("_send_shares")

        # we're finally ready to send out our shares. If we encounter any
        # surprises here, it's because somebody else is writing at the same
        # time. (Note: in the future, when we remove the _query_peers() step
        # and instead speculate about [or remember] which shares are where,
        # surprises here are *not* indications of UncoordinatedWriteError,
        # and we'll need to respond to them more gracefully.)

        # needed is a set of (peerid, shnum) tuples. The first thing we do is
        # organize it by peerid.

        peermap = DictOfSets()
        for (peerid, shnum) in needed:
            peermap.add(peerid, shnum)

        # the next thing is to build up a bunch of test vectors. The
        # semantics of Publish are that we perform the operation if the world
        # hasn't changed since the ServerMap was constructed (more or less).
        # For every share we're trying to place, we create a test vector that
        # tests to see if the server*share still corresponds to the
        # map.

        all_tw_vectors = {} # maps peerid to tw_vectors
        sm = self._servermap.servermap

        for key in needed:
            (peerid, shnum) = key

            if key in sm:
                # an old version of that share already exists on the
                # server, according to our servermap. We will create a
                # request that attempts to replace it.
                old_versionid, old_timestamp = sm[key]
                (old_seqnum, old_root_hash, old_salt, old_segsize,
                 old_datalength, old_k, old_N, old_prefix,
                 old_offsets_tuple) = old_versionid
                old_checkstring = pack_checkstring(old_seqnum,
                                                   old_root_hash,
                                                   old_salt)
                testv = (0, len(old_checkstring), "eq", old_checkstring)

            elif key in self.bad_share_checkstrings:
                old_checkstring = self.bad_share_checkstrings[key]
                testv = (0, len(old_checkstring), "eq", old_checkstring)

            else:
                # add a testv that requires the share not exist

                # Unfortunately, foolscap-0.2.5 has a bug in the way inbound
                # constraints are handled. If the same object is referenced
                # multiple times inside the arguments, foolscap emits a
                # 'reference' token instead of a distinct copy of the
                # argument. The bug is that these 'reference' tokens are not
                # accepted by the inbound constraint code. To work around
                # this, we need to prevent python from interning the
                # (constant) tuple, by creating a new copy of this vector
                # each time.

                # This bug is fixed in foolscap-0.2.6, and even though this
                # version of Tahoe requires foolscap-0.3.1 or newer, we are
                # supposed to be able to interoperate with older versions of
                # Tahoe which are allowed to use older versions of foolscap,
                # including foolscap-0.2.5 . In addition, I've seen other
                # foolscap problems triggered by 'reference' tokens (see #541
                # for details). So we must keep this workaround in place.

                #testv = (0, 1, 'eq', "")
                testv = tuple([0, 1, 'eq', ""])

            testvs = [testv]
            # the write vector is simply the share
            writev = [(0, self.shares[shnum])]

            if peerid not in all_tw_vectors:
                all_tw_vectors[peerid] = {}
                # maps shnum to (testvs, writevs, new_length)
            assert shnum not in all_tw_vectors[peerid]

            all_tw_vectors[peerid][shnum] = (testvs, writev, None)

        # we read the checkstring back from each share, however we only use
        # it to detect whether there was a new share that we didn't know
        # about. The success or failure of the write will tell us whether
        # there was a collision or not. If there is a collision, the first
        # thing we'll do is update the servermap, which will find out what
        # happened. We could conceivably reduce a roundtrip by using the
        # readv checkstring to populate the servermap, but really we'd have
        # to read enough data to validate the signatures too, so it wouldn't
        # be an overall win.
        read_vector = [(0, struct.calcsize(SIGNED_PREFIX))]

        # ok, send the messages!
        self.log("sending %d shares" % len(all_tw_vectors), level=log.NOISY)
        started = time.time()
        for (peerid, tw_vectors) in all_tw_vectors.items():

            write_enabler = self._node.get_write_enabler(peerid)
            renew_secret = self._node.get_renewal_secret(peerid)
            cancel_secret = self._node.get_cancel_secret(peerid)
            secrets = (write_enabler, renew_secret, cancel_secret)
            shnums = tw_vectors.keys()

            for shnum in shnums:
                self.outstanding.add( (peerid, shnum) )

            d = self._do_testreadwrite(peerid, secrets,
                                       tw_vectors, read_vector)
            d.addCallbacks(self._got_write_answer, self._got_write_error,
                           callbackArgs=(peerid, shnums, started),
                           errbackArgs=(peerid, shnums, started))
            d.addCallback(self.loop)
            d.addErrback(self._fatal_error)

        self._update_status()
        self.log("%d shares sent" % len(all_tw_vectors), level=log.NOISY)

    def _do_testreadwrite(self, peerid, secrets,
                          tw_vectors, read_vector):
        storage_index = self._storage_index
        ss = self.connections[peerid]

        #print "SS[%s] is %s" % (idlib.shortnodeid_b2a(peerid), ss), ss.tracker.interfaceName
        d = ss.callRemote("slot_testv_and_readv_and_writev",
                          storage_index,
                          secrets,
                          tw_vectors,
                          read_vector)
        return d

    def _got_write_answer(self, answer, peerid, shnums, started):
        lp = self.log("_got_write_answer from %s" %
                      idlib.shortnodeid_b2a(peerid))
        for shnum in shnums:
            self.outstanding.discard( (peerid, shnum) )

        now = time.time()
        elapsed = now - started
        self._status.add_per_server_time(peerid, elapsed)

        wrote, read_data = answer

        surprise_shares = set(read_data.keys()) - set(shnums)

        surprised = False
        for shnum in surprise_shares:
            # read_data is a dict mapping shnum to checkstring (SIGNED_PREFIX)
            checkstring = read_data[shnum][0]
            their_version_info = unpack_checkstring(checkstring)
            if their_version_info == self._new_version_info:
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
            self.bad_peers.add(peerid) # don't ask them again
            # use the checkstring to add information to the log message
            for (shnum,readv) in read_data.items():
                checkstring = readv[0]
                (other_seqnum,
                 other_roothash,
                 other_salt) = unpack_checkstring(checkstring)
                expected_version = self._servermap.version_on_peer(peerid,
                                                                   shnum)
                if expected_version:
                    (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
                     offsets_tuple) = expected_version
                    self.log("somebody modified the share on us:"
                             " shnum=%d: I thought they had #%d:R=%s,"
                             " but testv reported #%d:R=%s" %
                             (shnum,
                              seqnum, base32.b2a(root_hash)[:4],
                              other_seqnum, base32.b2a(other_roothash)[:4]),
                             parent=lp, level=log.NOISY)
                # if expected_version==None, then we didn't expect to see a
                # share on that peer, and the 'surprise_shares' clause above
                # will have logged it.
            # self.loop() will take care of finding new homes
            return

        for shnum in shnums:
            self.placed.add( (peerid, shnum) )
            # and update the servermap
            self._servermap.add_new_share(peerid, shnum,
                                          self.versioninfo, started)

        # self.loop() will take care of checking to see if we're done
        return

    def _got_write_error(self, f, peerid, shnums, started):
        for shnum in shnums:
            self.outstanding.discard( (peerid, shnum) )
        self.bad_peers.add(peerid)
        if self._first_write_error is None:
            self._first_write_error = f
        self.log(format="error while writing shares %(shnums)s to peerid %(peerid)s",
                 shnums=list(shnums), peerid=idlib.shortnodeid_b2a(peerid),
                 failure=f,
                 level=log.UNUSUAL)
        # self.loop() will take care of checking to see if we're done
        return


    def _done(self, res):
        if not self._running:
            return
        self._running = False
        now = time.time()
        self._status.timings["total"] = now - self._started
        self._status.set_active(False)
        if isinstance(res, failure.Failure):
            self.log("Publish done, with failure", failure=res,
                     level=log.WEIRD, umid="nRsR9Q")
            self._status.set_status("Failed")
        elif self.surprised:
            self.log("Publish done, UncoordinatedWriteError", level=log.UNUSUAL)
            self._status.set_status("UncoordinatedWriteError")
            # deliver a failure
            res = failure.Failure(UncoordinatedWriteError())
            # TODO: recovery
        else:
            self.log("Publish done, success")
            self._status.set_status("Done")
            self._status.set_progress(1.0)
        eventually(self.done_deferred.callback, res)


