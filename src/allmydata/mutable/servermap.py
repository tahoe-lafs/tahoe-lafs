
import sys, time
from zope.interface import implements
from itertools import count
from twisted.internet import defer
from twisted.python import failure
from foolscap.api import DeadReferenceError, RemoteException, eventually, \
                         fireEventually
from allmydata.util import base32, hashutil, idlib, log, deferredutil
from allmydata.util.dictutil import DictOfSets
from allmydata.storage.server import si_b2a
from allmydata.interfaces import IServermapUpdaterStatus
from pycryptopp.publickey import rsa

from allmydata.mutable.common import MODE_CHECK, MODE_ANYTHING, MODE_WRITE, MODE_READ, \
     CorruptShareError
from allmydata.mutable.layout import SIGNED_PREFIX_LENGTH, MDMFSlotReadProxy

class UpdateStatus:
    implements(IServermapUpdaterStatus)
    statusid_counter = count(0)
    def __init__(self):
        self.timings = {}
        self.timings["per_server"] = {}
        self.timings["cumulative_verify"] = 0.0
        self.privkey_from = None
        self.problems = {}
        self.active = True
        self.storage_index = None
        self.mode = "?"
        self.status = "Not started"
        self.progress = 0.0
        self.counter = self.statusid_counter.next()
        self.started = time.time()
        self.finished = None

    def add_per_server_time(self, peerid, op, sent, elapsed):
        assert op in ("query", "late", "privkey")
        if peerid not in self.timings["per_server"]:
            self.timings["per_server"][peerid] = []
        self.timings["per_server"][peerid].append((op,sent,elapsed))

    def get_started(self):
        return self.started
    def get_finished(self):
        return self.finished
    def get_storage_index(self):
        return self.storage_index
    def get_mode(self):
        return self.mode
    def get_servermap(self):
        return self.servermap
    def get_privkey_from(self):
        return self.privkey_from
    def using_helper(self):
        return False
    def get_size(self):
        return "-NA-"
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
    def set_mode(self, mode):
        self.mode = mode
    def set_privkey_from(self, peerid):
        self.privkey_from = peerid
    def set_status(self, status):
        self.status = status
    def set_progress(self, value):
        self.progress = value
    def set_active(self, value):
        self.active = value
    def set_finished(self, when):
        self.finished = when

class ServerMap:
    """I record the placement of mutable shares.

    This object records which shares (of various versions) are located on
    which servers.

    One purpose I serve is to inform callers about which versions of the
    mutable file are recoverable and 'current'.

    A second purpose is to serve as a state marker for test-and-set
    operations. I am passed out of retrieval operations and back into publish
    operations, which means 'publish this new version, but only if nothing
    has changed since I last retrieved this data'. This reduces the chances
    of clobbering a simultaneous (uncoordinated) write.

    @ivar servermap: a dictionary, mapping a (peerid, shnum) tuple to a
                     (versionid, timestamp) tuple. Each 'versionid' is a
                     tuple of (seqnum, root_hash, IV, segsize, datalength,
                     k, N, signed_prefix, offsets)

    @ivar connections: maps peerid to a RemoteReference

    @ivar bad_shares: dict with keys of (peerid, shnum) tuples, describing
                      shares that I should ignore (because a previous user of
                      the servermap determined that they were invalid). The
                      updater only locates a certain number of shares: if
                      some of these turn out to have integrity problems and
                      are unusable, the caller will need to mark those shares
                      as bad, then re-update the servermap, then try again.
                      The dict maps (peerid, shnum) tuple to old checkstring.
    """

    def __init__(self):
        self.servermap = {}
        self.connections = {}
        self.unreachable_peers = set() # peerids that didn't respond to queries
        self.reachable_peers = set() # peerids that did respond to queries
        self.problems = [] # mostly for debugging
        self.bad_shares = {} # maps (peerid,shnum) to old checkstring
        self.last_update_mode = None
        self.last_update_time = 0
        self.update_data = {} # (verinfo,shnum) => data

    def copy(self):
        s = ServerMap()
        s.servermap = self.servermap.copy() # tuple->tuple
        s.connections = self.connections.copy() # str->RemoteReference
        s.unreachable_peers = set(self.unreachable_peers)
        s.reachable_peers = set(self.reachable_peers)
        s.problems = self.problems[:]
        s.bad_shares = self.bad_shares.copy() # tuple->str
        s.last_update_mode = self.last_update_mode
        s.last_update_time = self.last_update_time
        return s

    def mark_bad_share(self, peerid, shnum, checkstring):
        """This share was found to be bad, either in the checkstring or
        signature (detected during mapupdate), or deeper in the share
        (detected at retrieve time). Remove it from our list of useful
        shares, and remember that it is bad so we don't add it back again
        later. We record the share's old checkstring (which might be
        corrupted or badly signed) so that a repair operation can do the
        test-and-set using it as a reference.
        """
        key = (peerid, shnum) # record checkstring
        self.bad_shares[key] = checkstring
        self.servermap.pop(key, None)

    def add_new_share(self, peerid, shnum, verinfo, timestamp):
        """We've written a new share out, replacing any that was there
        before."""
        key = (peerid, shnum)
        self.bad_shares.pop(key, None)
        self.servermap[key] = (verinfo, timestamp)

    def dump(self, out=sys.stdout):
        print >>out, "servermap:"

        for ( (peerid, shnum), (verinfo, timestamp) ) in self.servermap.items():
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = verinfo
            print >>out, ("[%s]: sh#%d seq%d-%s %d-of-%d len%d" %
                          (idlib.shortnodeid_b2a(peerid), shnum,
                           seqnum, base32.b2a(root_hash)[:4], k, N,
                           datalength))
        if self.problems:
            print >>out, "%d PROBLEMS" % len(self.problems)
            for f in self.problems:
                print >>out, str(f)
        return out

    def all_peers(self):
        return set([peerid
                    for (peerid, shnum)
                    in self.servermap])

    def all_peers_for_version(self, verinfo):
        """Return a set of peerids that hold shares for the given version."""
        return set([peerid
                    for ( (peerid, shnum), (verinfo2, timestamp) )
                    in self.servermap.items()
                    if verinfo == verinfo2])

    def make_sharemap(self):
        """Return a dict that maps shnum to a set of peerds that hold it."""
        sharemap = DictOfSets()
        for (peerid, shnum) in self.servermap:
            sharemap.add(shnum, peerid)
        return sharemap

    def make_versionmap(self):
        """Return a dict that maps versionid to sets of (shnum, peerid,
        timestamp) tuples."""
        versionmap = DictOfSets()
        for ( (peerid, shnum), (verinfo, timestamp) ) in self.servermap.items():
            versionmap.add(verinfo, (shnum, peerid, timestamp))
        return versionmap

    def shares_on_peer(self, peerid):
        return set([shnum
                    for (s_peerid, shnum)
                    in self.servermap
                    if s_peerid == peerid])

    def version_on_peer(self, peerid, shnum):
        key = (peerid, shnum)
        if key in self.servermap:
            (verinfo, timestamp) = self.servermap[key]
            return verinfo
        return None

    def shares_available(self):
        """Return a dict that maps verinfo to tuples of
        (num_distinct_shares, k, N) tuples."""
        versionmap = self.make_versionmap()
        all_shares = {}
        for verinfo, shares in versionmap.items():
            s = set()
            for (shnum, peerid, timestamp) in shares:
                s.add(shnum)
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = verinfo
            all_shares[verinfo] = (len(s), k, N)
        return all_shares

    def highest_seqnum(self):
        available = self.shares_available()
        seqnums = [verinfo[0]
                   for verinfo in available.keys()]
        seqnums.append(0)
        return max(seqnums)

    def summarize_version(self, verinfo):
        """Take a versionid, return a string that describes it."""
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = verinfo
        return "seq%d-%s" % (seqnum, base32.b2a(root_hash)[:4])

    def summarize_versions(self):
        """Return a string describing which versions we know about."""
        versionmap = self.make_versionmap()
        bits = []
        for (verinfo, shares) in versionmap.items():
            vstr = self.summarize_version(verinfo)
            shnums = set([shnum for (shnum, peerid, timestamp) in shares])
            bits.append("%d*%s" % (len(shnums), vstr))
        return "/".join(bits)

    def recoverable_versions(self):
        """Return a set of versionids, one for each version that is currently
        recoverable."""
        versionmap = self.make_versionmap()
        recoverable_versions = set()
        for (verinfo, shares) in versionmap.items():
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = verinfo
            shnums = set([shnum for (shnum, peerid, timestamp) in shares])
            if len(shnums) >= k:
                # this one is recoverable
                recoverable_versions.add(verinfo)

        return recoverable_versions

    def unrecoverable_versions(self):
        """Return a set of versionids, one for each version that is currently
        unrecoverable."""
        versionmap = self.make_versionmap()

        unrecoverable_versions = set()
        for (verinfo, shares) in versionmap.items():
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = verinfo
            shnums = set([shnum for (shnum, peerid, timestamp) in shares])
            if len(shnums) < k:
                unrecoverable_versions.add(verinfo)

        return unrecoverable_versions

    def best_recoverable_version(self):
        """Return a single versionid, for the so-called 'best' recoverable
        version. Sequence number is the primary sort criteria, followed by
        root hash. Returns None if there are no recoverable versions."""
        recoverable = list(self.recoverable_versions())
        recoverable.sort()
        if recoverable:
            return recoverable[-1]
        return None

    def size_of_version(self, verinfo):
        """Given a versionid (perhaps returned by best_recoverable_version),
        return the size of the file in bytes."""
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = verinfo
        return datalength

    def unrecoverable_newer_versions(self):
        # Return a dict of versionid -> health, for versions that are
        # unrecoverable and have later seqnums than any recoverable versions.
        # These indicate that a write will lose data.
        versionmap = self.make_versionmap()
        healths = {} # maps verinfo to (found,k)
        unrecoverable = set()
        highest_recoverable_seqnum = -1
        for (verinfo, shares) in versionmap.items():
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = verinfo
            shnums = set([shnum for (shnum, peerid, timestamp) in shares])
            healths[verinfo] = (len(shnums),k)
            if len(shnums) < k:
                unrecoverable.add(verinfo)
            else:
                highest_recoverable_seqnum = max(seqnum,
                                                 highest_recoverable_seqnum)

        newversions = {}
        for verinfo in unrecoverable:
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = verinfo
            if seqnum > highest_recoverable_seqnum:
                newversions[verinfo] = healths[verinfo]

        return newversions


    def needs_merge(self):
        # return True if there are multiple recoverable versions with the
        # same seqnum, meaning that MutableFileNode.read_best_version is not
        # giving you the whole story, and that using its data to do a
        # subsequent publish will lose information.
        recoverable_seqnums = [verinfo[0]
                               for verinfo in self.recoverable_versions()]
        for seqnum in recoverable_seqnums:
            if recoverable_seqnums.count(seqnum) > 1:
                return True
        return False


    def get_update_data_for_share_and_verinfo(self, shnum, verinfo):
        """
        I return the update data for the given shnum
        """
        update_data = self.update_data[shnum]
        update_datum = [i[1] for i in update_data if i[0] == verinfo][0]
        return update_datum


    def set_update_data_for_share_and_verinfo(self, shnum, verinfo, data):
        """
        I record the block hash tree for the given shnum.
        """
        self.update_data.setdefault(shnum , []).append((verinfo, data))


class ServermapUpdater:
    def __init__(self, filenode, storage_broker, monitor, servermap,
                 mode=MODE_READ, add_lease=False, update_range=None):
        """I update a servermap, locating a sufficient number of useful
        shares and remembering where they are located.

        """

        self._node = filenode
        self._storage_broker = storage_broker
        self._monitor = monitor
        self._servermap = servermap
        self.mode = mode
        self._add_lease = add_lease
        self._running = True

        self._storage_index = filenode.get_storage_index()
        self._last_failure = None

        self._status = UpdateStatus()
        self._status.set_storage_index(self._storage_index)
        self._status.set_progress(0.0)
        self._status.set_mode(mode)

        self._servers_responded = set()

        # how much data should we read?
        # SDMF:
        #  * if we only need the checkstring, then [0:75]
        #  * if we need to validate the checkstring sig, then [543ish:799ish]
        #  * if we need the verification key, then [107:436ish]
        #   * the offset table at [75:107] tells us about the 'ish'
        #  * if we need the encrypted private key, we want [-1216ish:]
        #   * but we can't read from negative offsets
        #   * the offset table tells us the 'ish', also the positive offset
        # MDMF:
        #  * Checkstring? [0:72]
        #  * If we want to validate the checkstring, then [0:72], [143:?] --
        #    the offset table will tell us for sure.
        #  * If we need the verification key, we have to consult the offset
        #    table as well.
        # At this point, we don't know which we are. Our filenode can
        # tell us, but it might be lying -- in some cases, we're
        # responsible for telling it which kind of file it is.
        self._read_size = 4000
        if mode == MODE_CHECK:
            # we use unpack_prefix_and_signature, so we need 1k
            self._read_size = 1000
        self._need_privkey = False

        if mode == MODE_WRITE and not self._node.get_privkey():
            self._need_privkey = True
        # check+repair: repair requires the privkey, so if we didn't happen
        # to ask for it during the check, we'll have problems doing the
        # publish.

        self.fetch_update_data = False
        if mode == MODE_WRITE and update_range:
            # We're updating the servermap in preparation for an
            # in-place file update, so we need to fetch some additional
            # data from each share that we find.
            assert len(update_range) == 2

            self.start_segment = update_range[0]
            self.end_segment = update_range[1]
            self.fetch_update_data = True

        prefix = si_b2a(self._storage_index)[:5]
        self._log_number = log.msg(format="SharemapUpdater(%(si)s): starting (%(mode)s)",
                                   si=prefix, mode=mode)

    def get_status(self):
        return self._status

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.mutable.mapupdate"
        return log.msg(*args, **kwargs)

    def update(self):
        """Update the servermap to reflect current conditions. Returns a
        Deferred that fires with the servermap once the update has finished."""
        self._started = time.time()
        self._status.set_active(True)

        # self._valid_versions is a set of validated verinfo tuples. We just
        # use it to remember which versions had valid signatures, so we can
        # avoid re-checking the signatures for each share.
        self._valid_versions = set()

        # self.versionmap maps verinfo tuples to sets of (shnum, peerid,
        # timestamp) tuples. This is used to figure out which versions might
        # be retrievable, and to make the eventual data download faster.
        self.versionmap = DictOfSets()

        self._done_deferred = defer.Deferred()

        # first, which peers should be talk to? Any that were in our old
        # servermap, plus "enough" others.

        self._queries_completed = 0

        sb = self._storage_broker
        # All of the peers, permuted by the storage index, as usual.
        full_peerlist = [(s.get_serverid(), s.get_rref())
                         for s in sb.get_servers_for_psi(self._storage_index)]
        self.full_peerlist = full_peerlist # for use later, immutable
        self.extra_peers = full_peerlist[:] # peers are removed as we use them
        self._good_peers = set() # peers who had some shares
        self._empty_peers = set() # peers who don't have any shares
        self._bad_peers = set() # peers to whom our queries failed
        self._readers = {} # peerid -> dict(sharewriters), filled in
                           # after responses come in.

        k = self._node.get_required_shares()
        # For what cases can these conditions work?
        if k is None:
            # make a guess
            k = 3
        N = self._node.get_total_shares()
        if N is None:
            N = 10
        self.EPSILON = k
        # we want to send queries to at least this many peers (although we
        # might not wait for all of their answers to come back)
        self.num_peers_to_query = k + self.EPSILON

        if self.mode == MODE_CHECK:
            # We want to query all of the peers.
            initial_peers_to_query = dict(full_peerlist)
            must_query = set(initial_peers_to_query.keys())
            self.extra_peers = []
        elif self.mode == MODE_WRITE:
            # we're planning to replace all the shares, so we want a good
            # chance of finding them all. We will keep searching until we've
            # seen epsilon that don't have a share.
            # We don't query all of the peers because that could take a while.
            self.num_peers_to_query = N + self.EPSILON
            initial_peers_to_query, must_query = self._build_initial_querylist()
            self.required_num_empty_peers = self.EPSILON

            # TODO: arrange to read lots of data from k-ish servers, to avoid
            # the extra round trip required to read large directories. This
            # might also avoid the round trip required to read the encrypted
            # private key.

        else: # MODE_READ, MODE_ANYTHING
            # 2k peers is good enough.
            initial_peers_to_query, must_query = self._build_initial_querylist()

        # this is a set of peers that we are required to get responses from:
        # they are peers who used to have a share, so we need to know where
        # they currently stand, even if that means we have to wait for a
        # silently-lost TCP connection to time out. We remove peers from this
        # set as we get responses.
        self._must_query = must_query

        # now initial_peers_to_query contains the peers that we should ask,
        # self.must_query contains the peers that we must have heard from
        # before we can consider ourselves finished, and self.extra_peers
        # contains the overflow (peers that we should tap if we don't get
        # enough responses)
        # I guess that self._must_query is a subset of
        # initial_peers_to_query?
        assert set(must_query).issubset(set(initial_peers_to_query))

        self._send_initial_requests(initial_peers_to_query)
        self._status.timings["initial_queries"] = time.time() - self._started
        return self._done_deferred

    def _build_initial_querylist(self):
        initial_peers_to_query = {}
        must_query = set()
        for peerid in self._servermap.all_peers():
            ss = self._servermap.connections[peerid]
            # we send queries to everyone who was already in the sharemap
            initial_peers_to_query[peerid] = ss
            # and we must wait for responses from them
            must_query.add(peerid)

        while ((self.num_peers_to_query > len(initial_peers_to_query))
               and self.extra_peers):
            (peerid, ss) = self.extra_peers.pop(0)
            initial_peers_to_query[peerid] = ss

        return initial_peers_to_query, must_query

    def _send_initial_requests(self, peerlist):
        self._status.set_status("Sending %d initial queries" % len(peerlist))
        self._queries_outstanding = set()
        self._sharemap = DictOfSets() # shnum -> [(peerid, seqnum, R)..]
        for (peerid, ss) in peerlist.items():
            self._queries_outstanding.add(peerid)
            self._do_query(ss, peerid, self._storage_index, self._read_size)

        if not peerlist:
            # there is nobody to ask, so we need to short-circuit the state
            # machine.
            d = defer.maybeDeferred(self._check_for_done, None)
            d.addErrback(self._fatal_error)

        # control flow beyond this point: state machine. Receiving responses
        # from queries is the input. We might send out more queries, or we
        # might produce a result.
        return None

    def _do_query(self, ss, peerid, storage_index, readsize):
        self.log(format="sending query to [%(peerid)s], readsize=%(readsize)d",
                 peerid=idlib.shortnodeid_b2a(peerid),
                 readsize=readsize,
                 level=log.NOISY)
        self._servermap.connections[peerid] = ss
        started = time.time()
        self._queries_outstanding.add(peerid)
        d = self._do_read(ss, peerid, storage_index, [], [(0, readsize)])
        d.addCallback(self._got_results, peerid, readsize, (ss, storage_index),
                      started)
        d.addErrback(self._query_failed, peerid)
        # errors that aren't handled by _query_failed (and errors caused by
        # _query_failed) get logged, but we still want to check for doneness.
        d.addErrback(log.err)
        d.addErrback(self._fatal_error)
        d.addCallback(self._check_for_done)
        return d

    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        if self._add_lease:
            # send an add-lease message in parallel. The results are handled
            # separately. This is sent before the slot_readv() so that we can
            # be sure the add_lease is retired by the time slot_readv comes
            # back (this relies upon our knowledge that the server code for
            # add_lease is synchronous).
            renew_secret = self._node.get_renewal_secret(peerid)
            cancel_secret = self._node.get_cancel_secret(peerid)
            d2 = ss.callRemote("add_lease", storage_index,
                               renew_secret, cancel_secret)
            # we ignore success
            d2.addErrback(self._add_lease_failed, peerid, storage_index)
        d = ss.callRemote("slot_readv", storage_index, shnums, readv)
        return d


    def _got_corrupt_share(self, e, shnum, peerid, data, lp):
        """
        I am called when a remote server returns a corrupt share in
        response to one of our queries. By corrupt, I mean a share
        without a valid signature. I then record the failure, notify the
        server of the corruption, and record the share as bad.
        """
        f = failure.Failure(e)
        self.log(format="bad share: %(f_value)s", f_value=str(f),
                 failure=f, parent=lp, level=log.WEIRD, umid="h5llHg")
        # Notify the server that its share is corrupt.
        self.notify_server_corruption(peerid, shnum, str(e))
        # By flagging this as a bad peer, we won't count any of
        # the other shares on that peer as valid, though if we
        # happen to find a valid version string amongst those
        # shares, we'll keep track of it so that we don't need
        # to validate the signature on those again.
        self._bad_peers.add(peerid)
        self._last_failure = f
        # XXX: Use the reader for this?
        checkstring = data[:SIGNED_PREFIX_LENGTH]
        self._servermap.mark_bad_share(peerid, shnum, checkstring)
        self._servermap.problems.append(f)


    def _cache_good_sharedata(self, verinfo, shnum, now, data):
        """
        If one of my queries returns successfully (which means that we
        were able to and successfully did validate the signature), I
        cache the data that we initially fetched from the storage
        server. This will help reduce the number of roundtrips that need
        to occur when the file is downloaded, or when the file is
        updated.
        """
        if verinfo:
            self._node._add_to_cache(verinfo, shnum, 0, data)


    def _got_results(self, datavs, peerid, readsize, stuff, started):
        lp = self.log(format="got result from [%(peerid)s], %(numshares)d shares",
                      peerid=idlib.shortnodeid_b2a(peerid),
                      numshares=len(datavs))
        now = time.time()
        elapsed = now - started
        def _done_processing(ignored=None):
            self._queries_outstanding.discard(peerid)
            self._servermap.reachable_peers.add(peerid)
            self._must_query.discard(peerid)
            self._queries_completed += 1
        if not self._running:
            self.log("but we're not running, so we'll ignore it", parent=lp)
            _done_processing()
            self._status.add_per_server_time(peerid, "late", started, elapsed)
            return
        self._status.add_per_server_time(peerid, "query", started, elapsed)

        if datavs:
            self._good_peers.add(peerid)
        else:
            self._empty_peers.add(peerid)

        ss, storage_index = stuff
        ds = []

        for shnum,datav in datavs.items():
            data = datav[0]
            reader = MDMFSlotReadProxy(ss,
                                       storage_index,
                                       shnum,
                                       data)
            self._readers.setdefault(peerid, dict())[shnum] = reader
            # our goal, with each response, is to validate the version
            # information and share data as best we can at this point --
            # we do this by validating the signature. To do this, we
            # need to do the following:
            #   - If we don't already have the public key, fetch the
            #     public key. We use this to validate the signature.
            if not self._node.get_pubkey():
                # fetch and set the public key.
                d = reader.get_verification_key()
                d.addCallback(lambda results, shnum=shnum, peerid=peerid:
                    self._try_to_set_pubkey(results, peerid, shnum, lp))
                # XXX: Make self._pubkey_query_failed?
                d.addErrback(lambda error, shnum=shnum, peerid=peerid, data=data:
                    self._got_corrupt_share(error, shnum, peerid, data, lp))
            else:
                # we already have the public key.
                d = defer.succeed(None)

            # Neither of these two branches return anything of
            # consequence, so the first entry in our deferredlist will
            # be None.

            # - Next, we need the version information. We almost
            #   certainly got this by reading the first thousand or so
            #   bytes of the share on the storage server, so we
            #   shouldn't need to fetch anything at this step.
            d2 = reader.get_verinfo()
            d2.addErrback(lambda error, shnum=shnum, peerid=peerid, data=data:
                self._got_corrupt_share(error, shnum, peerid, data, lp))
            # - Next, we need the signature. For an SDMF share, it is
            #   likely that we fetched this when doing our initial fetch
            #   to get the version information. In MDMF, this lives at
            #   the end of the share, so unless the file is quite small,
            #   we'll need to do a remote fetch to get it.
            d3 = reader.get_signature()
            d3.addErrback(lambda error, shnum=shnum, peerid=peerid, data=data:
                self._got_corrupt_share(error, shnum, peerid, data, lp))
            #  Once we have all three of these responses, we can move on
            #  to validating the signature

            # Does the node already have a privkey? If not, we'll try to
            # fetch it here.
            if self._need_privkey:
                d4 = reader.get_encprivkey()
                d4.addCallback(lambda results, shnum=shnum, peerid=peerid:
                    self._try_to_validate_privkey(results, peerid, shnum, lp))
                d4.addErrback(lambda error, shnum=shnum, peerid=peerid, data=data:
                    self._privkey_query_failed(error, shnum, data, lp))
            else:
                d4 = defer.succeed(None)


            if self.fetch_update_data:
                # fetch the block hash tree and first + last segment, as
                # configured earlier.
                # Then set them in wherever we happen to want to set
                # them.
                ds = []
                # XXX: We do this above, too. Is there a good way to
                # make the two routines share the value without
                # introducing more roundtrips?
                ds.append(reader.get_verinfo())
                ds.append(reader.get_blockhashes())
                ds.append(reader.get_block_and_salt(self.start_segment))
                ds.append(reader.get_block_and_salt(self.end_segment))
                d5 = deferredutil.gatherResults(ds)
                d5.addCallback(self._got_update_results_one_share, shnum)
            else:
                d5 = defer.succeed(None)

            dl = defer.DeferredList([d, d2, d3, d4, d5])
            dl.addBoth(self._turn_barrier)
            dl.addCallback(lambda results, shnum=shnum, peerid=peerid:
                self._got_signature_one_share(results, shnum, peerid, lp))
            dl.addErrback(lambda error, shnum=shnum, data=data:
               self._got_corrupt_share(error, shnum, peerid, data, lp))
            dl.addCallback(lambda verinfo, shnum=shnum, peerid=peerid, data=data:
                self._cache_good_sharedata(verinfo, shnum, now, data))
            ds.append(dl)
        # dl is a deferred list that will fire when all of the shares
        # that we found on this peer are done processing. When dl fires,
        # we know that processing is done, so we can decrement the
        # semaphore-like thing that we incremented earlier.
        dl = defer.DeferredList(ds, fireOnOneErrback=True)
        # Are we done? Done means that there are no more queries to
        # send, that there are no outstanding queries, and that we
        # haven't received any queries that are still processing. If we
        # are done, self._check_for_done will cause the done deferred
        # that we returned to our caller to fire, which tells them that
        # they have a complete servermap, and that we won't be touching
        # the servermap anymore.
        dl.addCallback(_done_processing)
        dl.addCallback(self._check_for_done)
        dl.addErrback(self._fatal_error)
        # all done!
        self.log("_got_results done", parent=lp, level=log.NOISY)
        return dl


    def _turn_barrier(self, result):
        """
        I help the servermap updater avoid the recursion limit issues
        discussed in #237.
        """
        return fireEventually(result)


    def _try_to_set_pubkey(self, pubkey_s, peerid, shnum, lp):
        if self._node.get_pubkey():
            return # don't go through this again if we don't have to
        fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
        assert len(fingerprint) == 32
        if fingerprint != self._node.get_fingerprint():
            raise CorruptShareError(peerid, shnum,
                                "pubkey doesn't match fingerprint")
        self._node._populate_pubkey(self._deserialize_pubkey(pubkey_s))
        assert self._node.get_pubkey()


    def notify_server_corruption(self, peerid, shnum, reason):
        ss = self._servermap.connections[peerid]
        ss.callRemoteOnly("advise_corrupt_share",
                          "mutable", self._storage_index, shnum, reason)


    def _got_signature_one_share(self, results, shnum, peerid, lp):
        # It is our job to give versioninfo to our caller. We need to
        # raise CorruptShareError if the share is corrupt for any
        # reason, something that our caller will handle.
        self.log(format="_got_results: got shnum #%(shnum)d from peerid %(peerid)s",
                 shnum=shnum,
                 peerid=idlib.shortnodeid_b2a(peerid),
                 level=log.NOISY,
                 parent=lp)
        if not self._running:
            # We can't process the results, since we can't touch the
            # servermap anymore.
            self.log("but we're not running anymore.")
            return None

        _, verinfo, signature, __, ___ = results
        (seqnum,
         root_hash,
         saltish,
         segsize,
         datalen,
         k,
         n,
         prefix,
         offsets) = verinfo[1]
        offsets_tuple = tuple( [(key,value) for key,value in offsets.items()] )

        # XXX: This should be done for us in the method, so
        # presumably you can go in there and fix it.
        verinfo = (seqnum,
                   root_hash,
                   saltish,
                   segsize,
                   datalen,
                   k,
                   n,
                   prefix,
                   offsets_tuple)
        # This tuple uniquely identifies a share on the grid; we use it
        # to keep track of the ones that we've already seen.

        if verinfo not in self._valid_versions:
            # This is a new version tuple, and we need to validate it
            # against the public key before keeping track of it.
            assert self._node.get_pubkey()
            valid = self._node.get_pubkey().verify(prefix, signature[1])
            if not valid:
                raise CorruptShareError(peerid, shnum,
                                        "signature is invalid")

        # ok, it's a valid verinfo. Add it to the list of validated
        # versions.
        self.log(" found valid version %d-%s from %s-sh%d: %d-%d/%d/%d"
                 % (seqnum, base32.b2a(root_hash)[:4],
                    idlib.shortnodeid_b2a(peerid), shnum,
                    k, n, segsize, datalen),
                    parent=lp)
        self._valid_versions.add(verinfo)
        # We now know that this is a valid candidate verinfo. Whether or
        # not this instance of it is valid is a matter for the next
        # statement; at this point, we just know that if we see this
        # version info again, that its signature checks out and that
        # we're okay to skip the signature-checking step.

        # (peerid, shnum) are bound in the method invocation.
        if (peerid, shnum) in self._servermap.bad_shares:
            # we've been told that the rest of the data in this share is
            # unusable, so don't add it to the servermap.
            self.log("but we've been told this is a bad share",
                     parent=lp, level=log.UNUSUAL)
            return verinfo

        # Add the info to our servermap.
        timestamp = time.time()
        self._servermap.add_new_share(peerid, shnum, verinfo, timestamp)
        # and the versionmap
        self.versionmap.add(verinfo, (shnum, peerid, timestamp))

        return verinfo


    def _got_update_results_one_share(self, results, share):
        """
        I record the update results in results.
        """
        assert len(results) == 4
        verinfo, blockhashes, start, end = results
        (seqnum,
         root_hash,
         saltish,
         segsize,
         datalen,
         k,
         n,
         prefix,
         offsets) = verinfo
        offsets_tuple = tuple( [(key,value) for key,value in offsets.items()] )

        # XXX: This should be done for us in the method, so
        # presumably you can go in there and fix it.
        verinfo = (seqnum,
                   root_hash,
                   saltish,
                   segsize,
                   datalen,
                   k,
                   n,
                   prefix,
                   offsets_tuple)

        update_data = (blockhashes, start, end)
        self._servermap.set_update_data_for_share_and_verinfo(share,
                                                              verinfo,
                                                              update_data)


    def _deserialize_pubkey(self, pubkey_s):
        verifier = rsa.create_verifying_key_from_string(pubkey_s)
        return verifier


    def _try_to_validate_privkey(self, enc_privkey, peerid, shnum, lp):
        """
        Given a writekey from a remote server, I validate it against the
        writekey stored in my node. If it is valid, then I set the
        privkey and encprivkey properties of the node.
        """
        alleged_privkey_s = self._node._decrypt_privkey(enc_privkey)
        alleged_writekey = hashutil.ssk_writekey_hash(alleged_privkey_s)
        if alleged_writekey != self._node.get_writekey():
            self.log("invalid privkey from %s shnum %d" %
                     (idlib.nodeid_b2a(peerid)[:8], shnum),
                     parent=lp, level=log.WEIRD, umid="aJVccw")
            return

        # it's good
        self.log("got valid privkey from shnum %d on peerid %s" %
                 (shnum, idlib.shortnodeid_b2a(peerid)),
                 parent=lp)
        privkey = rsa.create_signing_key_from_string(alleged_privkey_s)
        self._node._populate_encprivkey(enc_privkey)
        self._node._populate_privkey(privkey)
        self._need_privkey = False
        self._status.set_privkey_from(peerid)


    def _add_lease_failed(self, f, peerid, storage_index):
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
            self.log(format="error in add_lease from [%(peerid)s]: %(f_value)s",
                     peerid=idlib.shortnodeid_b2a(peerid),
                     f_value=str(f.value),
                     failure=f,
                     level=log.WEIRD, umid="iqg3mw")
            return
        # local errors are cause for alarm
        log.err(f,
                format="local error in add_lease to [%(peerid)s]: %(f_value)s",
                peerid=idlib.shortnodeid_b2a(peerid),
                f_value=str(f.value),
                level=log.WEIRD, umid="ZWh6HA")

    def _query_failed(self, f, peerid):
        if not self._running:
            return
        level = log.WEIRD
        if f.check(DeadReferenceError):
            level = log.UNUSUAL
        self.log(format="error during query: %(f_value)s",
                 f_value=str(f.value), failure=f,
                 level=level, umid="IHXuQg")
        self._must_query.discard(peerid)
        self._queries_outstanding.discard(peerid)
        self._bad_peers.add(peerid)
        self._servermap.problems.append(f)
        # a peerid could be in both ServerMap.reachable_peers and
        # .unreachable_peers if they responded to our query, but then an
        # exception was raised in _got_results.
        self._servermap.unreachable_peers.add(peerid)
        self._queries_completed += 1
        self._last_failure = f


    def _privkey_query_failed(self, f, peerid, shnum, lp):
        self._queries_outstanding.discard(peerid)
        if not self._running:
            return
        level = log.WEIRD
        if f.check(DeadReferenceError):
            level = log.UNUSUAL
        self.log(format="error during privkey query: %(f_value)s",
                 f_value=str(f.value), failure=f,
                 parent=lp, level=level, umid="McoJ5w")
        self._servermap.problems.append(f)
        self._last_failure = f


    def _check_for_done(self, res):
        # exit paths:
        #  return self._send_more_queries(outstanding) : send some more queries
        #  return self._done() : all done
        #  return : keep waiting, no new queries
        lp = self.log(format=("_check_for_done, mode is '%(mode)s', "
                              "%(outstanding)d queries outstanding, "
                              "%(extra)d extra peers available, "
                              "%(must)d 'must query' peers left, "
                              "need_privkey=%(need_privkey)s"
                              ),
                      mode=self.mode,
                      outstanding=len(self._queries_outstanding),
                      extra=len(self.extra_peers),
                      must=len(self._must_query),
                      need_privkey=self._need_privkey,
                      level=log.NOISY,
                      )

        if not self._running:
            self.log("but we're not running", parent=lp, level=log.NOISY)
            return

        if self._must_query:
            # we are still waiting for responses from peers that used to have
            # a share, so we must continue to wait. No additional queries are
            # required at this time.
            self.log("%d 'must query' peers left" % len(self._must_query),
                     level=log.NOISY, parent=lp)
            return

        if (not self._queries_outstanding and not self.extra_peers):
            # all queries have retired, and we have no peers left to ask. No
            # more progress can be made, therefore we are done.
            self.log("all queries are retired, no extra peers: done",
                     parent=lp)
            return self._done()

        recoverable_versions = self._servermap.recoverable_versions()
        unrecoverable_versions = self._servermap.unrecoverable_versions()

        # what is our completion policy? how hard should we work?

        if self.mode == MODE_ANYTHING:
            if recoverable_versions:
                self.log("%d recoverable versions: done"
                         % len(recoverable_versions),
                         parent=lp)
                return self._done()

        if self.mode == MODE_CHECK:
            # we used self._must_query, and we know there aren't any
            # responses still waiting, so that means we must be done
            self.log("done", parent=lp)
            return self._done()

        MAX_IN_FLIGHT = 5
        if self.mode == MODE_READ:
            # if we've queried k+epsilon servers, and we see a recoverable
            # version, and we haven't seen any unrecoverable higher-seqnum'ed
            # versions, then we're done.

            if self._queries_completed < self.num_peers_to_query:
                self.log(format="%(completed)d completed, %(query)d to query: need more",
                         completed=self._queries_completed,
                         query=self.num_peers_to_query,
                         level=log.NOISY, parent=lp)
                return self._send_more_queries(MAX_IN_FLIGHT)
            if not recoverable_versions:
                self.log("no recoverable versions: need more",
                         level=log.NOISY, parent=lp)
                return self._send_more_queries(MAX_IN_FLIGHT)
            highest_recoverable = max(recoverable_versions)
            highest_recoverable_seqnum = highest_recoverable[0]
            for unrec_verinfo in unrecoverable_versions:
                if unrec_verinfo[0] > highest_recoverable_seqnum:
                    # there is evidence of a higher-seqnum version, but we
                    # don't yet see enough shares to recover it. Try harder.
                    # TODO: consider sending more queries.
                    # TODO: consider limiting the search distance
                    self.log("evidence of higher seqnum: need more",
                             level=log.UNUSUAL, parent=lp)
                    return self._send_more_queries(MAX_IN_FLIGHT)
            # all the unrecoverable versions were old or concurrent with a
            # recoverable version. Good enough.
            self.log("no higher-seqnum: done", parent=lp)
            return self._done()

        if self.mode == MODE_WRITE:
            # we want to keep querying until we've seen a few that don't have
            # any shares, to be sufficiently confident that we've seen all
            # the shares. This is still less work than MODE_CHECK, which asks
            # every server in the world.

            if not recoverable_versions:
                self.log("no recoverable versions: need more", parent=lp,
                         level=log.NOISY)
                return self._send_more_queries(MAX_IN_FLIGHT)

            last_found = -1
            last_not_responded = -1
            num_not_responded = 0
            num_not_found = 0
            states = []
            found_boundary = False

            for i,(peerid,ss) in enumerate(self.full_peerlist):
                if peerid in self._bad_peers:
                    # query failed
                    states.append("x")
                    #self.log("loop [%s]: x" % idlib.shortnodeid_b2a(peerid))
                elif peerid in self._empty_peers:
                    # no shares
                    states.append("0")
                    #self.log("loop [%s]: 0" % idlib.shortnodeid_b2a(peerid))
                    if last_found != -1:
                        num_not_found += 1
                        if num_not_found >= self.EPSILON:
                            self.log("found our boundary, %s" %
                                     "".join(states),
                                     parent=lp, level=log.NOISY)
                            found_boundary = True
                            break

                elif peerid in self._good_peers:
                    # yes shares
                    states.append("1")
                    #self.log("loop [%s]: 1" % idlib.shortnodeid_b2a(peerid))
                    last_found = i
                    num_not_found = 0
                else:
                    # not responded yet
                    states.append("?")
                    #self.log("loop [%s]: ?" % idlib.shortnodeid_b2a(peerid))
                    last_not_responded = i
                    num_not_responded += 1

            if found_boundary:
                # we need to know that we've gotten answers from
                # everybody to the left of here
                if last_not_responded == -1:
                    # we're done
                    self.log("have all our answers",
                             parent=lp, level=log.NOISY)
                    # .. unless we're still waiting on the privkey
                    if self._need_privkey:
                        self.log("but we're still waiting for the privkey",
                                 parent=lp, level=log.NOISY)
                        # if we found the boundary but we haven't yet found
                        # the privkey, we may need to look further. If
                        # somehow all the privkeys were corrupted (but the
                        # shares were readable), then this is likely to do an
                        # exhaustive search.
                        return self._send_more_queries(MAX_IN_FLIGHT)
                    return self._done()
                # still waiting for somebody
                return self._send_more_queries(num_not_responded)

            # if we hit here, we didn't find our boundary, so we're still
            # waiting for peers
            self.log("no boundary yet, %s" % "".join(states), parent=lp,
                     level=log.NOISY)
            return self._send_more_queries(MAX_IN_FLIGHT)

        # otherwise, keep up to 5 queries in flight. TODO: this is pretty
        # arbitrary, really I want this to be something like k -
        # max(known_version_sharecounts) + some extra
        self.log("catchall: need more", parent=lp, level=log.NOISY)
        return self._send_more_queries(MAX_IN_FLIGHT)

    def _send_more_queries(self, num_outstanding):
        more_queries = []

        while True:
            self.log(format=" there are %(outstanding)d queries outstanding",
                     outstanding=len(self._queries_outstanding),
                     level=log.NOISY)
            active_queries = len(self._queries_outstanding) + len(more_queries)
            if active_queries >= num_outstanding:
                break
            if not self.extra_peers:
                break
            more_queries.append(self.extra_peers.pop(0))

        self.log(format="sending %(more)d more queries: %(who)s",
                 more=len(more_queries),
                 who=" ".join(["[%s]" % idlib.shortnodeid_b2a(peerid)
                               for (peerid,ss) in more_queries]),
                 level=log.NOISY)

        for (peerid, ss) in more_queries:
            self._do_query(ss, peerid, self._storage_index, self._read_size)
            # we'll retrigger when those queries come back

    def _done(self):
        if not self._running:
            self.log("not running; we're already done")
            return
        self._running = False
        now = time.time()
        elapsed = now - self._started
        self._status.set_finished(now)
        self._status.timings["total"] = elapsed
        self._status.set_progress(1.0)
        self._status.set_status("Finished")
        self._status.set_active(False)

        self._servermap.last_update_mode = self.mode
        self._servermap.last_update_time = self._started
        # the servermap will not be touched after this
        self.log("servermap: %s" % self._servermap.summarize_versions())

        eventually(self._done_deferred.callback, self._servermap)

    def _fatal_error(self, f):
        self.log("fatal error", failure=f, level=log.WEIRD, umid="1cNvlw")
        self._done_deferred.errback(f)


