
import sys, time
from zope.interface import implements
from itertools import count
from twisted.internet import defer
from twisted.python import failure
from foolscap import DeadReferenceError
from foolscap.eventual import eventually
from allmydata.util import base32, hashutil, idlib, log
from allmydata import storage
from allmydata.interfaces import IServermapUpdaterStatus
from pycryptopp.publickey import rsa

from common import MODE_CHECK, MODE_ANYTHING, MODE_WRITE, MODE_READ, \
     DictOfSets, CorruptShareError, NeedMoreDataError
from layout import unpack_prefix_and_signature, unpack_header, unpack_share, \
     SIGNED_PREFIX_LENGTH

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


class ServermapUpdater:
    def __init__(self, filenode, monitor, servermap, mode=MODE_READ,
                 add_lease=False):
        """I update a servermap, locating a sufficient number of useful
        shares and remembering where they are located.

        """

        self._node = filenode
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
        #  * if we only need the checkstring, then [0:75]
        #  * if we need to validate the checkstring sig, then [543ish:799ish]
        #  * if we need the verification key, then [107:436ish]
        #   * the offset table at [75:107] tells us about the 'ish'
        #  * if we need the encrypted private key, we want [-1216ish:]
        #   * but we can't read from negative offsets
        #   * the offset table tells us the 'ish', also the positive offset
        # A future version of the SMDF slot format should consider using
        # fixed-size slots so we can retrieve less data. For now, we'll just
        # read 2000 bytes, which also happens to read enough actual data to
        # pre-fetch a 9-entry dirnode.
        self._read_size = 2000
        if mode == MODE_CHECK:
            # we use unpack_prefix_and_signature, so we need 1k
            self._read_size = 1000
        self._need_privkey = False
        if mode == MODE_WRITE and not self._node._privkey:
            self._need_privkey = True
        # check+repair: repair requires the privkey, so if we didn't happen
        # to ask for it during the check, we'll have problems doing the
        # publish.

        prefix = storage.si_b2a(self._storage_index)[:5]
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

        client = self._node._client
        full_peerlist = client.get_permuted_peers("storage",
                                                  self._node._storage_index)
        self.full_peerlist = full_peerlist # for use later, immutable
        self.extra_peers = full_peerlist[:] # peers are removed as we use them
        self._good_peers = set() # peers who had some shares
        self._empty_peers = set() # peers who don't have any shares
        self._bad_peers = set() # peers to whom our queries failed

        k = self._node.get_required_shares()
        if k is None:
            # make a guess
            k = 3
        N = self._node.get_required_shares()
        if N is None:
            N = 10
        self.EPSILON = k
        # we want to send queries to at least this many peers (although we
        # might not wait for all of their answers to come back)
        self.num_peers_to_query = k + self.EPSILON

        if self.mode == MODE_CHECK:
            initial_peers_to_query = dict(full_peerlist)
            must_query = set(initial_peers_to_query.keys())
            self.extra_peers = []
        elif self.mode == MODE_WRITE:
            # we're planning to replace all the shares, so we want a good
            # chance of finding them all. We will keep searching until we've
            # seen epsilon that don't have a share.
            self.num_peers_to_query = N + self.EPSILON
            initial_peers_to_query, must_query = self._build_initial_querylist()
            self.required_num_empty_peers = self.EPSILON

            # TODO: arrange to read lots of data from k-ish servers, to avoid
            # the extra round trip required to read large directories. This
            # might also avoid the round trip required to read the encrypted
            # private key.

        else:
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
        dl = []
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
        d.addBoth(self._check_for_done)
        d.addErrback(self._fatal_error)
        return d

    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        d = ss.callRemote("slot_readv", storage_index, shnums, readv)
        if self._add_lease:
            renew_secret = self._node.get_renewal_secret(peerid)
            cancel_secret = self._node.get_cancel_secret(peerid)
            d2 = ss.callRemote("add_lease", storage_index,
                               renew_secret, cancel_secret)
            dl = defer.DeferredList([d, d2])
            def _done(res):
                [(readv_success, readv_result),
                 (addlease_success, addlease_result)] = res
                if (not addlease_success and
                    not addlease_result.check(IndexError)):
                    # tahoe 1.3.0 raised IndexError on non-existant buckets,
                    # which we ignore. Unfortunately tahoe <1.3.0 had a bug
                    # and raised KeyError, which we report.
                    return addlease_result # propagate error
                return readv_result
            dl.addCallback(_done)
            return dl
        return d

    def _got_results(self, datavs, peerid, readsize, stuff, started):
        lp = self.log(format="got result from [%(peerid)s], %(numshares)d shares",
                      peerid=idlib.shortnodeid_b2a(peerid),
                      numshares=len(datavs),
                      level=log.NOISY)
        now = time.time()
        elapsed = now - started
        self._queries_outstanding.discard(peerid)
        self._servermap.reachable_peers.add(peerid)
        self._must_query.discard(peerid)
        self._queries_completed += 1
        if not self._running:
            self.log("but we're not running, so we'll ignore it", parent=lp,
                     level=log.NOISY)
            self._status.add_per_server_time(peerid, "late", started, elapsed)
            return
        self._status.add_per_server_time(peerid, "query", started, elapsed)

        if datavs:
            self._good_peers.add(peerid)
        else:
            self._empty_peers.add(peerid)

        last_verinfo = None
        last_shnum = None
        for shnum,datav in datavs.items():
            data = datav[0]
            try:
                verinfo = self._got_results_one_share(shnum, data, peerid, lp)
                last_verinfo = verinfo
                last_shnum = shnum
                self._node._cache.add(verinfo, shnum, 0, data, now)
            except CorruptShareError, e:
                # log it and give the other shares a chance to be processed
                f = failure.Failure()
                self.log(format="bad share: %(f_value)s", f_value=str(f.value),
                         failure=f, parent=lp, level=log.WEIRD, umid="h5llHg")
                self.notify_server_corruption(peerid, shnum, str(e))
                self._bad_peers.add(peerid)
                self._last_failure = f
                checkstring = data[:SIGNED_PREFIX_LENGTH]
                self._servermap.mark_bad_share(peerid, shnum, checkstring)
                self._servermap.problems.append(f)
                pass

        self._status.timings["cumulative_verify"] += (time.time() - now)

        if self._need_privkey and last_verinfo:
            # send them a request for the privkey. We send one request per
            # server.
            lp2 = self.log("sending privkey request",
                           parent=lp, level=log.NOISY)
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = last_verinfo
            o = dict(offsets_tuple)

            self._queries_outstanding.add(peerid)
            readv = [ (o['enc_privkey'], (o['EOF'] - o['enc_privkey'])) ]
            ss = self._servermap.connections[peerid]
            privkey_started = time.time()
            d = self._do_read(ss, peerid, self._storage_index,
                              [last_shnum], readv)
            d.addCallback(self._got_privkey_results, peerid, last_shnum,
                          privkey_started, lp2)
            d.addErrback(self._privkey_query_failed, peerid, last_shnum, lp2)
            d.addErrback(log.err)
            d.addCallback(self._check_for_done)
            d.addErrback(self._fatal_error)

        # all done!
        self.log("_got_results done", parent=lp, level=log.NOISY)

    def notify_server_corruption(self, peerid, shnum, reason):
        ss = self._servermap.connections[peerid]
        ss.callRemoteOnly("advise_corrupt_share",
                          "mutable", self._storage_index, shnum, reason)

    def _got_results_one_share(self, shnum, data, peerid, lp):
        self.log(format="_got_results: got shnum #%(shnum)d from peerid %(peerid)s",
                 shnum=shnum,
                 peerid=idlib.shortnodeid_b2a(peerid),
                 level=log.NOISY,
                 parent=lp)

        # this might raise NeedMoreDataError, if the pubkey and signature
        # live at some weird offset. That shouldn't happen, so I'm going to
        # treat it as a bad share.
        (seqnum, root_hash, IV, k, N, segsize, datalength,
         pubkey_s, signature, prefix) = unpack_prefix_and_signature(data)

        if not self._node.get_pubkey():
            fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
            assert len(fingerprint) == 32
            if fingerprint != self._node._fingerprint:
                raise CorruptShareError(peerid, shnum,
                                        "pubkey doesn't match fingerprint")
            self._node._populate_pubkey(self._deserialize_pubkey(pubkey_s))

        if self._need_privkey:
            self._try_to_extract_privkey(data, peerid, shnum, lp)

        (ig_version, ig_seqnum, ig_root_hash, ig_IV, ig_k, ig_N,
         ig_segsize, ig_datalen, offsets) = unpack_header(data)
        offsets_tuple = tuple( [(key,value) for key,value in offsets.items()] )

        verinfo = (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
                   offsets_tuple)

        if verinfo not in self._valid_versions:
            # it's a new pair. Verify the signature.
            valid = self._node._pubkey.verify(prefix, signature)
            if not valid:
                raise CorruptShareError(peerid, shnum, "signature is invalid")

            # ok, it's a valid verinfo. Add it to the list of validated
            # versions.
            self.log(" found valid version %d-%s from %s-sh%d: %d-%d/%d/%d"
                     % (seqnum, base32.b2a(root_hash)[:4],
                        idlib.shortnodeid_b2a(peerid), shnum,
                        k, N, segsize, datalength),
                     parent=lp)
            self._valid_versions.add(verinfo)
        # We now know that this is a valid candidate verinfo.

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

    def _deserialize_pubkey(self, pubkey_s):
        verifier = rsa.create_verifying_key_from_string(pubkey_s)
        return verifier

    def _try_to_extract_privkey(self, data, peerid, shnum, lp):
        try:
            r = unpack_share(data)
        except NeedMoreDataError, e:
            # this share won't help us. oh well.
            offset = e.encprivkey_offset
            length = e.encprivkey_length
            self.log("shnum %d on peerid %s: share was too short (%dB) "
                     "to get the encprivkey; [%d:%d] ought to hold it" %
                     (shnum, idlib.shortnodeid_b2a(peerid), len(data),
                      offset, offset+length),
                     parent=lp)
            # NOTE: if uncoordinated writes are taking place, someone might
            # change the share (and most probably move the encprivkey) before
            # we get a chance to do one of these reads and fetch it. This
            # will cause us to see a NotEnoughSharesError(unable to fetch
            # privkey) instead of an UncoordinatedWriteError . This is a
            # nuisance, but it will go away when we move to DSA-based mutable
            # files (since the privkey will be small enough to fit in the
            # write cap).

            return

        (seqnum, root_hash, IV, k, N, segsize, datalen,
         pubkey, signature, share_hash_chain, block_hash_tree,
         share_data, enc_privkey) = r

        return self._try_to_validate_privkey(enc_privkey, peerid, shnum, lp)

    def _try_to_validate_privkey(self, enc_privkey, peerid, shnum, lp):

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

    def _got_privkey_results(self, datavs, peerid, shnum, started, lp):
        now = time.time()
        elapsed = now - started
        self._status.add_per_server_time(peerid, "privkey", started, elapsed)
        self._queries_outstanding.discard(peerid)
        if not self._need_privkey:
            return
        if shnum not in datavs:
            self.log("privkey wasn't there when we asked it",
                     level=log.WEIRD, umid="VA9uDQ")
            return
        datav = datavs[shnum]
        enc_privkey = datav[0]
        self._try_to_validate_privkey(enc_privkey, peerid, shnum, lp)

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
            return
        self._running = False
        now = time.time()
        elapsed = now - self._started
        self._status.set_finished(now)
        self._status.timings["total"] = elapsed
        self._status.set_progress(1.0)
        self._status.set_status("Done")
        self._status.set_active(False)

        self._servermap.last_update_mode = self.mode
        self._servermap.last_update_time = self._started
        # the servermap will not be touched after this
        self.log("servermap: %s" % self._servermap.summarize_versions())
        eventually(self._done_deferred.callback, self._servermap)

    def _fatal_error(self, f):
        self.log("fatal error", failure=f, level=log.WEIRD, umid="1cNvlw")
        self._done_deferred.errback(f)


