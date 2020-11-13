"""
Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2, native_str
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from past.builtins import long, unicode

import os, time, weakref, itertools
from zope.interface import implementer
from twisted.python import failure
from twisted.internet import defer
from twisted.application import service
from foolscap.api import Referenceable, Copyable, RemoteCopy, fireEventually

from allmydata.crypto import aes
from allmydata.util.hashutil import file_renewal_secret_hash, \
     file_cancel_secret_hash, bucket_renewal_secret_hash, \
     bucket_cancel_secret_hash, plaintext_hasher, \
     storage_index_hash, plaintext_segment_hasher, convergence_hasher
from allmydata.util.deferredutil import timeout_call
from allmydata import hashtree, uri
from allmydata.storage.server import si_b2a
from allmydata.immutable import encode
from allmydata.util import base32, dictutil, idlib, log, mathutil
from allmydata.util.happinessutil import servers_of_happiness, \
    merge_servers, failure_message
from allmydata.util.assertutil import precondition, _assert
from allmydata.util.rrefutil import add_version_to_remote_reference
from allmydata.interfaces import IUploadable, IUploader, IUploadResults, \
     IEncryptedUploadable, RIEncryptedUploadable, IUploadStatus, \
     NoServersError, InsufficientVersionError, UploadUnhappinessError, \
     DEFAULT_MAX_SEGMENT_SIZE, IProgress, IPeerSelector
from allmydata.immutable import layout

from io import BytesIO
from .happiness_upload import share_placement, calculate_happiness

from ..util.eliotutil import (
    log_call_deferred,
    inline_callbacks,
)

from eliot import (
    ActionType,
    MessageType,
    Field,
)

_TOTAL_SHARES = Field.for_types(
    u"total_shares",
    [int, long],
    u"The total number of shares desired.",
)

def _serialize_peers(peers):
    return sorted(base32.b2a(p) for p in peers)

_PEERS = Field(
    u"peers",
    _serialize_peers,
    u"The read/write peers being considered.",
)

_READONLY_PEERS = Field(
    u"readonly_peers",
    _serialize_peers,
    u"The read-only peers being considered.",
)

def _serialize_existing_shares(existing_shares):
    return {
        server: list(shares)
        for (server, shares)
        in existing_shares.items()
    }

_EXISTING_SHARES = Field(
    u"existing_shares",
    _serialize_existing_shares,
    u"The shares that are believed to already have been placed.",
)

def _serialize_happiness_mappings(happiness_mappings):
    return {
        sharenum: base32.b2a(serverid)
        for (sharenum, serverid)
        in happiness_mappings.items()
    }

_HAPPINESS_MAPPINGS = Field(
    u"happiness_mappings",
    _serialize_happiness_mappings,
    u"The computed happiness mapping for a particular upload.",
)

_HAPPINESS = Field.for_types(
    u"happiness",
    [int, long],
    u"The computed happiness of a certain placement.",
)

_UPLOAD_TRACKERS = Field(
    u"upload_trackers",
    lambda trackers: list(
        dict(
            server=tracker.get_name(),
            shareids=sorted(tracker.buckets.keys()),
        )
        for tracker
        in trackers
    ),
    u"Some servers which have agreed to hold some shares for us.",
)

_ALREADY_SERVERIDS = Field(
    u"already_serverids",
    lambda d: d,
    u"Some servers which are already holding some shares that we were interested in uploading.",
)

LOCATE_ALL_SHAREHOLDERS = ActionType(
    u"immutable:upload:locate-all-shareholders",
    [],
    [_UPLOAD_TRACKERS, _ALREADY_SERVERIDS],
    u"Existing shareholders are being identified to plan upload actions.",
)

GET_SHARE_PLACEMENTS = MessageType(
    u"immutable:upload:get-share-placements",
    [_TOTAL_SHARES, _PEERS, _READONLY_PEERS, _EXISTING_SHARES, _HAPPINESS_MAPPINGS, _HAPPINESS],
    u"Share placement is being computed for an upload.",
)

_EFFECTIVE_HAPPINESS = Field.for_types(
    u"effective_happiness",
    [int, long],
    u"The computed happiness value of a share placement map.",
)

CONVERGED_HAPPINESS = MessageType(
    u"immutable:upload:get-shareholders:converged-happiness",
    [_EFFECTIVE_HAPPINESS],
    u"The share placement algorithm has converged and placements efforts are complete.",
)


# this wants to live in storage, not here
class TooFullError(Exception):
    pass

# HelperUploadResults are what we get from the Helper, and to retain
# backwards compatibility with old Helpers we can't change the format. We
# convert them into a local UploadResults upon receipt.
class HelperUploadResults(Copyable, RemoteCopy):
    # note: don't change this string, it needs to match the value used on the
    # helper, and it does *not* need to match the fully-qualified
    # package/module/class name
    #
    # Needs to be native string to make Foolscap happy.
    typeToCopy = native_str("allmydata.upload.UploadResults.tahoe.allmydata.com")
    copytype = typeToCopy

    # also, think twice about changing the shape of any existing attribute,
    # because instances of this class are sent from the helper to its client,
    # so changing this may break compatibility. Consider adding new fields
    # instead of modifying existing ones.

    def __init__(self):
        self.timings = {} # dict of name to number of seconds
        self.sharemap = dictutil.DictOfSets() # {shnum: set(serverid)}
        self.servermap = dictutil.DictOfSets() # {serverid: set(shnum)}
        self.file_size = None
        self.ciphertext_fetched = None # how much the helper fetched
        self.uri = None
        self.preexisting_shares = None # count of shares already present
        self.pushed_shares = None # count of shares we pushed

@implementer(IUploadResults)
class UploadResults(object):

    def __init__(self, file_size,
                 ciphertext_fetched, # how much the helper fetched
                 preexisting_shares, # count of shares already present
                 pushed_shares, # count of shares we pushed
                 sharemap, # {shnum: set(server)}
                 servermap, # {server: set(shnum)}
                 timings, # dict of name to number of seconds
                 uri_extension_data,
                 uri_extension_hash,
                 verifycapstr):
        self._file_size = file_size
        self._ciphertext_fetched = ciphertext_fetched
        self._preexisting_shares = preexisting_shares
        self._pushed_shares = pushed_shares
        self._sharemap = sharemap
        self._servermap = servermap
        self._timings = timings
        self._uri_extension_data = uri_extension_data
        self._uri_extension_hash = uri_extension_hash
        self._verifycapstr = verifycapstr

    def set_uri(self, uri):
        self._uri = uri

    def get_file_size(self):
        return self._file_size
    def get_uri(self):
        return self._uri
    def get_ciphertext_fetched(self):
        return self._ciphertext_fetched
    def get_preexisting_shares(self):
        return self._preexisting_shares
    def get_pushed_shares(self):
        return self._pushed_shares
    def get_sharemap(self):
        return self._sharemap
    def get_servermap(self):
        return self._servermap
    def get_timings(self):
        return self._timings
    def get_uri_extension_data(self):
        return self._uri_extension_data
    def get_verifycapstr(self):
        return self._verifycapstr

# our current uri_extension is 846 bytes for small files, a few bytes
# more for larger ones (since the filesize is encoded in decimal in a
# few places). Ask for a little bit more just in case we need it. If
# the extension changes size, we can change EXTENSION_SIZE to
# allocate a more accurate amount of space.
EXTENSION_SIZE = 1000
# TODO: actual extensions are closer to 419 bytes, so we can probably lower
# this.

def pretty_print_shnum_to_servers(s):
    return ', '.join([ "sh%s: %s" % (k, '+'.join([idlib.shortnodeid_b2a(x) for x in v])) for k, v in s.items() ])

class ServerTracker(object):
    def __init__(self, server,
                 sharesize, blocksize, num_segments, num_share_hashes,
                 storage_index,
                 bucket_renewal_secret, bucket_cancel_secret):
        self._server = server
        self.buckets = {} # k: shareid, v: IRemoteBucketWriter
        self.sharesize = sharesize

        wbp = layout.make_write_bucket_proxy(None, None, sharesize,
                                             blocksize, num_segments,
                                             num_share_hashes,
                                             EXTENSION_SIZE)
        self.wbp_class = wbp.__class__ # to create more of them
        self.allocated_size = wbp.get_allocated_size()
        self.blocksize = blocksize
        self.num_segments = num_segments
        self.num_share_hashes = num_share_hashes
        self.storage_index = storage_index

        self.renew_secret = bucket_renewal_secret
        self.cancel_secret = bucket_cancel_secret

    def __repr__(self):
        return ("<ServerTracker for server %s and SI %s>"
                % (self._server.get_name(), si_b2a(self.storage_index)[:5]))

    def get_server(self):
        return self._server
    def get_serverid(self):
        return self._server.get_serverid()
    def get_name(self):
        return self._server.get_name()

    def query(self, sharenums):
        storage_server = self._server.get_storage_server()
        d = storage_server.allocate_buckets(
            self.storage_index,
            self.renew_secret,
            self.cancel_secret,
            sharenums,
            self.allocated_size,
            canary=Referenceable(),
        )
        d.addCallback(self._buckets_allocated)
        return d

    def ask_about_existing_shares(self):
        storage_server = self._server.get_storage_server()
        return storage_server.get_buckets(self.storage_index)

    def _buckets_allocated(self, alreadygot_and_buckets):
        #log.msg("%s._got_reply(%s)" % (self, (alreadygot, buckets)))
        (alreadygot, buckets) = alreadygot_and_buckets
        b = {}
        for sharenum, rref in list(buckets.items()):
            bp = self.wbp_class(rref, self._server, self.sharesize,
                                self.blocksize,
                                self.num_segments,
                                self.num_share_hashes,
                                EXTENSION_SIZE)
            b[sharenum] = bp
        self.buckets.update(b)
        return (alreadygot, set(b.keys()))


    def abort(self):
        """
        I abort the remote bucket writers for all shares. This is a good idea
        to conserve space on the storage server.
        """
        self.abort_some_buckets(list(self.buckets.keys()))

    def abort_some_buckets(self, sharenums):
        """
        I abort the remote bucket writers for the share numbers in sharenums.
        """
        for sharenum in sharenums:
            if sharenum in self.buckets:
                self.buckets[sharenum].abort()
                del self.buckets[sharenum]


def str_shareloc(shnum, bucketwriter):
    return "%s: %s" % (shnum, bucketwriter.get_servername(),)


@implementer(IPeerSelector)
class PeerSelector(object):

    def __init__(self, num_segments, total_shares, needed_shares, min_happiness):
        self.num_segments = num_segments
        self.total_shares = total_shares
        self.needed_shares = needed_shares
        self.min_happiness = min_happiness

        self.existing_shares = {}
        self.peers = set()
        self.readonly_peers = set()
        self.bad_peers = set()

    def add_peer_with_share(self, peerid, shnum):
        try:
            self.existing_shares[peerid].add(shnum)
        except KeyError:
            self.existing_shares[peerid] = set([shnum])

    def add_peer(self, peerid):
        self.peers.add(peerid)

    def mark_readonly_peer(self, peerid):
        self.readonly_peers.add(peerid)
        self.peers.remove(peerid)

    def mark_bad_peer(self, peerid):
        if peerid in self.peers:
            self.peers.remove(peerid)
            self.bad_peers.add(peerid)
        elif peerid in self.readonly_peers:
            self.readonly_peers.remove(peerid)
            self.bad_peers.add(peerid)

    def get_sharemap_of_preexisting_shares(self):
        preexisting = dictutil.DictOfSets()
        for server, shares in self.existing_shares.items():
            for share in shares:
                preexisting.add(share, server)
        return preexisting

    def get_share_placements(self):
        shares = set(range(self.total_shares))
        self.happiness_mappings = share_placement(self.peers, self.readonly_peers, shares, self.existing_shares)
        self.happiness = calculate_happiness(self.happiness_mappings)
        GET_SHARE_PLACEMENTS.log(
            total_shares=self.total_shares,
            peers=self.peers,
            readonly_peers=self.readonly_peers,
            existing_shares=self.existing_shares,
            happiness_mappings=self.happiness_mappings,
            happiness=self.happiness,
        )
        return self.happiness_mappings


class _QueryStatistics(object):

    def __init__(self):
        self.total = 0
        self.good = 0
        self.bad = 0
        self.full = 0
        self.error = 0
        self.contacted = 0

    def __str__(self):
        return "QueryStatistics(total={} good={} bad={} full={} " \
            "error={} contacted={})".format(
                self.total,
                self.good,
                self.bad,
                self.full,
                self.error,
                self.contacted,
            )


class Tahoe2ServerSelector(log.PrefixingLogMixin):

    def __init__(self, upload_id, logparent=None, upload_status=None, reactor=None):
        self.upload_id = upload_id
        self._query_stats = _QueryStatistics()
        self.last_failure_msg = None
        self._status = IUploadStatus(upload_status)
        log.PrefixingLogMixin.__init__(self, 'tahoe.immutable.upload', logparent, prefix=upload_id)
        self.log("starting", level=log.OPERATIONAL)
        if reactor is None:
            from twisted.internet import reactor
        self._reactor = reactor

    def __repr__(self):
        return "<Tahoe2ServerSelector for upload %s>" % self.upload_id

    def _create_trackers(self, candidate_servers, allocated_size,
                         file_renewal_secret, file_cancel_secret, create_server_tracker):

        # filter the list of servers according to which ones can accomodate
        # this request. This excludes older servers (which used a 4-byte size
        # field) from getting large shares (for files larger than about
        # 12GiB). See #439 for details.
        def _get_maxsize(server):
            v0 = server.get_version()
            v1 = v0[b"http://allmydata.org/tahoe/protocols/storage/v1"]
            return v1[b"maximum-immutable-share-size"]

        for server in candidate_servers:
            self.peer_selector.add_peer(server.get_serverid())
        writeable_servers = [
            server for server in candidate_servers
            if _get_maxsize(server) >= allocated_size
        ]
        readonly_servers = set(candidate_servers) - set(writeable_servers)

        for server in readonly_servers:
            self.peer_selector.mark_readonly_peer(server.get_serverid())

        def _make_trackers(servers):
            trackers = []
            for s in servers:
                seed = s.get_lease_seed()
                renew = bucket_renewal_secret_hash(file_renewal_secret, seed)
                cancel = bucket_cancel_secret_hash(file_cancel_secret, seed)
                st = create_server_tracker(s, renew, cancel)
                trackers.append(st)
            return trackers

        write_trackers = _make_trackers(writeable_servers)

        # We don't try to allocate shares to these servers, since they've
        # said that they're incapable of storing shares of the size that we'd
        # want to store. We ask them about existing shares for this storage
        # index, which we want to know about for accurate
        # servers_of_happiness accounting, then we forget about them.
        readonly_trackers = _make_trackers(readonly_servers)

        return readonly_trackers, write_trackers

    @inline_callbacks
    def get_shareholders(self, storage_broker, secret_holder,
                         storage_index, share_size, block_size,
                         num_segments, total_shares, needed_shares,
                         min_happiness):
        """
        @return: (upload_trackers, already_serverids), where upload_trackers
                 is a set of ServerTracker instances that have agreed to hold
                 some shares for us (the shareids are stashed inside the
                 ServerTracker), and already_serverids is a dict mapping
                 shnum to a set of serverids for servers which claim to
                 already have the share.
        """

        # re-initialize statistics
        self._query_status = _QueryStatistics()

        if self._status:
            self._status.set_status("Contacting Servers..")

        self.peer_selector = PeerSelector(num_segments, total_shares,
                                          needed_shares, min_happiness)

        self.total_shares = total_shares
        self.min_happiness = min_happiness
        self.needed_shares = needed_shares

        self.homeless_shares = set(range(total_shares))
        self.use_trackers = set() # ServerTrackers that have shares assigned
                                  # to them
        self.preexisting_shares = {} # shareid => set(serverids) holding shareid

        # These servers have shares -- any shares -- for our SI. We keep
        # track of these to write an error message with them later.
        self.serverids_with_shares = set()

        # this needed_hashes computation should mirror
        # Encoder.send_all_share_hash_trees. We use an IncompleteHashTree
        # (instead of a HashTree) because we don't require actual hashing
        # just to count the levels.
        ht = hashtree.IncompleteHashTree(total_shares)
        num_share_hashes = len(ht.needed_hashes(0, include_leaf=True))

        # figure out how much space to ask for
        wbp = layout.make_write_bucket_proxy(None, None,
                                             share_size, 0, num_segments,
                                             num_share_hashes, EXTENSION_SIZE)
        allocated_size = wbp.get_allocated_size()

        # decide upon the renewal/cancel secrets, to include them in the
        # allocate_buckets query.
        file_renewal_secret = file_renewal_secret_hash(
            secret_holder.get_renewal_secret(),
            storage_index,
        )
        file_cancel_secret = file_cancel_secret_hash(
            secret_holder.get_cancel_secret(),
            storage_index,
        )

        # see docs/specifications/servers-of-happiness.rst
        # 0. Start with an ordered list of servers. Maybe *2N* of them.
        #

        all_servers = storage_broker.get_servers_for_psi(storage_index, for_upload=True)
        if not all_servers:
            raise NoServersError("client gave us zero servers")

        def _create_server_tracker(server, renew, cancel):
            return ServerTracker(
                server, share_size, block_size, num_segments, num_share_hashes,
                storage_index, renew, cancel,
            )

        readonly_trackers, write_trackers = self._create_trackers(
            all_servers[:(2 * total_shares)],
            allocated_size,
            file_renewal_secret,
            file_cancel_secret,
            _create_server_tracker,
        )

        # see docs/specifications/servers-of-happiness.rst
        # 1. Query all servers for existing shares.
        #
        # The spec doesn't say what to do for timeouts/errors. This
        # adds a timeout to each request, and rejects any that reply
        # with error (i.e. just removed from the list)

        ds = []
        if self._status and readonly_trackers:
            self._status.set_status(
                "Contacting readonly servers to find any existing shares"
            )

        # in the "pre servers-of-happiness" code, it was a little
        # ambigious whether "merely asking" counted as a "query" or
        # not, because "allocate_buckets" with nothing to allocate was
        # used to "ask" a write-able server what it held. Now we count
        # "actual allocation queries" only, because those are the only
        # things that actually affect what the server does.

        for tracker in readonly_trackers:
            assert isinstance(tracker, ServerTracker)
            d = timeout_call(self._reactor, tracker.ask_about_existing_shares(), 15)
            d.addBoth(self._handle_existing_response, tracker)
            ds.append(d)
            self.log("asking server %s for any existing shares" %
                     (tracker.get_name(),), level=log.NOISY)

        for tracker in write_trackers:
            assert isinstance(tracker, ServerTracker)
            d = timeout_call(self._reactor, tracker.ask_about_existing_shares(), 15)

            def timed_out(f, tracker):
                # print("TIMEOUT {}: {}".format(tracker, f))
                write_trackers.remove(tracker)
                readonly_trackers.append(tracker)
                return f
            d.addErrback(timed_out, tracker)
            d.addBoth(self._handle_existing_write_response, tracker, set())
            ds.append(d)
            self.log("asking server %s for any existing shares" %
                     (tracker.get_name(),), level=log.NOISY)

        trackers = set(write_trackers) | set(readonly_trackers)

        # these will always be (True, None) because errors are handled
        # in the _handle_existing_write_response etc callbacks
        yield defer.DeferredList(ds)

        # okay, we've queried the 2N servers, time to get the share
        # placements and attempt to actually place the shares (or
        # renew them on read-only servers). We want to run the loop
        # below *at least once* because even read-only servers won't
        # renew their shares until "allocate_buckets" is called (via
        # tracker.query())

        # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/778#comment:48
        # min_happiness will be 0 for the repairer, so we set current
        # effective_happiness to less than zero so this loop runs at
        # least once for the repairer...

        def _bad_server(fail, tracker):
            self.last_failure_msg = fail
            return False  # will mark it readonly

        def _make_readonly(tracker):
            # print("making {} read-only".format(tracker.get_serverid()))
            try:
                write_trackers.remove(tracker)
            except ValueError:
                pass
            # XXX can we just use a set() or does order matter?
            if tracker not in readonly_trackers:
                readonly_trackers.append(tracker)
            return None

        # so we *always* want to run this loop at least once, even if
        # we only have read-only servers -- because asking them to
        # allocate buckets renews those shares they already have. For
        # subsequent loops, we give up if we've achieved happiness OR
        # if we have zero writable servers left

        last_happiness = None
        effective_happiness = -1
        while effective_happiness < min_happiness and \
              (last_happiness is None or len(write_trackers)):
            errors_before = self._query_stats.bad
            self._share_placements = self.peer_selector.get_share_placements()

            placements = []
            for tracker in trackers:
                shares_to_ask = self._allocation_for(tracker)

                # if we already tried to upload share X to this very
                # same server in a previous iteration, we should *not*
                # ask again. If we *do* ask, there's no real harm, but
                # the server will respond with an empty dict and that
                # confuses our statistics. However, if the server is a
                # readonly sever, we *do* want to ask so it refreshes
                # the share.
                if shares_to_ask != set(tracker.buckets.keys()) or tracker in readonly_trackers:
                    self._query_stats.total += 1
                    self._query_stats.contacted += 1
                    d = timeout_call(self._reactor, tracker.query(shares_to_ask), 15)
                    d.addBoth(self._buckets_allocated, tracker, shares_to_ask)
                    d.addErrback(lambda f, tr: _bad_server(f, tr), tracker)
                    d.addCallback(lambda x, tr: _make_readonly(tr) if not x else x, tracker)
                    placements.append(d)

            yield defer.DeferredList(placements)
            merged = merge_servers(self.peer_selector.get_sharemap_of_preexisting_shares(), self.use_trackers)
            effective_happiness = servers_of_happiness(merged)
            if effective_happiness == last_happiness:
                # print("effective happiness still {}".format(last_happiness))
                # we haven't improved over the last iteration; give up
                break;
            if errors_before == self._query_stats.bad:
                break;
            last_happiness = effective_happiness
            # print("write trackers left: {}".format(len(write_trackers)))

        # note: peer_selector.get_allocations() only maps "things we
        # uploaded in the above loop" and specificaly does *not*
        # include any pre-existing shares on read-only servers .. but
        # we *do* want to count those shares towards total happiness.

        # no more servers. If we haven't placed enough shares, we fail.
        # XXX note sometimes we're not running the loop at least once,
        # and so 'merged' must be (re-)computed here.
        merged = merge_servers(self.peer_selector.get_sharemap_of_preexisting_shares(), self.use_trackers)
        effective_happiness = servers_of_happiness(merged)

        # print("placements completed {} vs {}".format(effective_happiness, min_happiness))
        # for k, v in merged.items():
        #     print("  {} -> {}".format(k, v))

        CONVERGED_HAPPINESS.log(
            effective_happiness=effective_happiness,
        )

        if effective_happiness < min_happiness:
            msg = failure_message(
                peer_count=len(self.serverids_with_shares),
                k=self.needed_shares,
                happy=min_happiness,
                effective_happy=effective_happiness,
            )
            msg = ("server selection failed for %s: %s (%s), merged=%s" %
                   (self, msg, self._get_progress_message(),
                    pretty_print_shnum_to_servers(merged)))
            if self.last_failure_msg:
                msg += " (%s)" % (self.last_failure_msg,)
            self.log(msg, level=log.UNUSUAL)
            self._failed(msg)  # raises UploadUnhappinessError
            return

        # we placed (or already had) enough to be happy, so we're done
        if self._status:
            self._status.set_status("Placed all shares")
        msg = ("server selection successful for %s: %s: pretty_print_merged: %s, "
               "self.use_trackers: %s, self.preexisting_shares: %s") \
               % (self, self._get_progress_message(),
                  pretty_print_shnum_to_servers(merged),
                  [', '.join([str_shareloc(k,v)
                              for k,v in st.buckets.items()])
                   for st in self.use_trackers],
                  pretty_print_shnum_to_servers(self.preexisting_shares))
        self.log(msg, level=log.OPERATIONAL)
        defer.returnValue((self.use_trackers, self.peer_selector.get_sharemap_of_preexisting_shares()))

    def _handle_existing_response(self, res, tracker):
        """
        I handle responses to the queries sent by
        Tahoe2ServerSelector.get_shareholders.
        """
        serverid = tracker.get_serverid()
        if isinstance(res, failure.Failure):
            self.log("%s got error during existing shares check: %s"
                    % (tracker.get_name(), res), level=log.UNUSUAL)
            self.peer_selector.mark_bad_peer(serverid)
        else:
            buckets = res
            if buckets:
                self.serverids_with_shares.add(serverid)
            self.log("response to get_buckets() from server %s: alreadygot=%s"
                    % (tracker.get_name(), tuple(sorted(buckets))),
                    level=log.NOISY)
            for bucket in buckets:
                self.peer_selector.add_peer_with_share(serverid, bucket)
                self.preexisting_shares.setdefault(bucket, set()).add(serverid)
                self.homeless_shares.discard(bucket)

    def _handle_existing_write_response(self, res, tracker, shares_to_ask):
        """
        Function handles the response from the write servers
        when inquiring about what shares each server already has.
        """
        if isinstance(res, failure.Failure):
            self.peer_selector.mark_bad_peer(tracker.get_serverid())
            self.log("%s got error during server selection: %s" % (tracker, res),
                    level=log.UNUSUAL)
            self.homeless_shares |= shares_to_ask
            msg = ("last failure (from %s) was: %s" % (tracker, res))
            self.last_failure_msg = msg
        else:
            for share in res.keys():
                self.peer_selector.add_peer_with_share(tracker.get_serverid(), share)

    def _get_progress_message(self):
        if not self.homeless_shares:
            msg = "placed all %d shares, " % (self.total_shares)
        else:
            msg = ("placed %d shares out of %d total (%d homeless), " %
                   (self.total_shares - len(self.homeless_shares),
                    self.total_shares,
                    len(self.homeless_shares)))
        assert self._query_stats.bad == (self._query_stats.full + self._query_stats.error)
        return (
            msg + "want to place shares on at least {happy} servers such that "
            "any {needed} of them have enough shares to recover the file, "
            "sent {queries} queries to {servers} servers, "
            "{good} queries placed some shares, {bad} placed none "
            "(of which {full} placed none due to the server being"
            " full and {error} placed none due to an error)".format(
                happy=self.min_happiness,
                needed=self.needed_shares,
                queries=self._query_stats.total,
                servers=self._query_stats.contacted,
                good=self._query_stats.good,
                bad=self._query_stats.bad,
                full=self._query_stats.full,
                error=self._query_stats.error
            )
        )

    def _allocation_for(self, tracker):
        """
        Given a ServerTracker, return a list of shares that we should
        store on that server.
        """
        assert isinstance(tracker, ServerTracker)

        shares_to_ask = set()
        servermap = self._share_placements
        for shnum, tracker_id in list(servermap.items()):
            if tracker_id == None:
                continue
            if tracker.get_serverid() == tracker_id:
                shares_to_ask.add(shnum)
                if shnum in self.homeless_shares:
                    self.homeless_shares.remove(shnum)

        if self._status:
            self._status.set_status("Contacting Servers [%s] (first query),"
                                    " %d shares left.."
                                    % (tracker.get_name(),
                                       len(self.homeless_shares)))
        return shares_to_ask

    def _buckets_allocated(self, res, tracker, shares_to_ask):
        """
        Internal helper. If this returns an error or False, the server
        will be considered read-only for any future iterations.
        """
        if isinstance(res, failure.Failure):
            # This is unusual, and probably indicates a bug or a network
            # problem.
            self.log("%s got error during server selection: %s" % (tracker, res),
                    level=log.UNUSUAL)
            self._query_stats.error += 1
            self._query_stats.bad += 1
            self.homeless_shares |= shares_to_ask
            try:
                self.peer_selector.mark_readonly_peer(tracker.get_serverid())
            except KeyError:
                pass
            return res

        else:
            (alreadygot, allocated) = res
            self.log("response to allocate_buckets() from server %s: alreadygot=%s, allocated=%s"
                    % (tracker.get_name(),
                       tuple(sorted(alreadygot)), tuple(sorted(allocated))),
                    level=log.NOISY)
            progress = False
            for s in alreadygot:
                self.preexisting_shares.setdefault(s, set()).add(tracker.get_serverid())
                if s in self.homeless_shares:
                    self.homeless_shares.remove(s)
                    progress = True
                elif s in shares_to_ask:
                    progress = True

            # the ServerTracker will remember which shares were allocated on
            # that peer. We just have to remember to use them.
            if allocated:
                self.use_trackers.add(tracker)
                progress = True

            if allocated or alreadygot:
                self.serverids_with_shares.add(tracker.get_serverid())

            not_yet_present = set(shares_to_ask) - set(alreadygot)
            still_homeless = not_yet_present - set(allocated)

            if still_homeless:
                # In networks with lots of space, this is very unusual and
                # probably indicates an error. In networks with servers that
                # are full, it is merely unusual. In networks that are very
                # full, it is common, and many uploads will fail. In most
                # cases, this is obviously not fatal, and we'll just use some
                # other servers.

                # some shares are still homeless, keep trying to find them a
                # home. The ones that were rejected get first priority.
                self.homeless_shares |= still_homeless
                # Since they were unable to accept all of our requests, so it
                # is safe to assume that asking them again won't help.

            if progress:
                # They accepted at least one of the shares that we asked
                # them to accept, or they had a share that we didn't ask
                # them to accept but that we hadn't placed yet, so this
                # was a productive query
                self._query_stats.good += 1
            else:
                # if we asked for some allocations, but the server
                # didn't return any at all (i.e. empty dict) it must
                # be full
                self._query_stats.full += 1
                self._query_stats.bad += 1
            return progress

    def _failed(self, msg):
        """
        I am called when server selection fails. I first abort all of the
        remote buckets that I allocated during my unsuccessful attempt to
        place shares for this file. I then raise an
        UploadUnhappinessError with my msg argument.
        """
        for tracker in self.use_trackers:
            assert isinstance(tracker, ServerTracker)
            tracker.abort()
        raise UploadUnhappinessError(msg)


@implementer(IEncryptedUploadable)
class EncryptAnUploadable(object):
    """This is a wrapper that takes an IUploadable and provides
    IEncryptedUploadable."""
    CHUNKSIZE = 50*1024

    def __init__(self, original, log_parent=None, progress=None):
        precondition(original.default_params_set,
                     "set_default_encoding_parameters not called on %r before wrapping with EncryptAnUploadable" % (original,))
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
        self._progress = progress

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
            if self._progress:
                self._progress.set_progress_total(size)
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
            self._encryptor = aes.create_encryptor(key)

            storage_index = storage_index_hash(key)
            assert isinstance(storage_index, bytes)
            # There's no point to having the SI be longer than the key, so we
            # specify that it is truncated to the same 128 bits as the AES key.
            assert len(storage_index) == 16  # SHA-256 truncated to 128b
            self._storage_index = storage_index
            if self._status:
                self._status.set_storage_index(storage_index)
            return self._encryptor
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
        d.addCallback(fireEventually)
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
            # because the AES-CTR implementation doesn't offer a
            # way to change the counter value. Once it acquires
            # this ability, change this to simply update the counter
            # before each call to (hash_only==False) encrypt_data
            ciphertext = aes.encrypt_data(self._encryptor, chunk)
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
        # this is currently unused, but will live again when we fix #453
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

@implementer(IUploadStatus)
class UploadStatus(object):
    statusid_counter = itertools.count(0)

    def __init__(self):
        self.storage_index = None
        self.size = None
        self.helper = False
        self.status = "Not started"
        self.progress = [0.0, 0.0, 0.0]
        self.active = True
        self.results = None
        self.counter = next(self.statusid_counter)
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

class CHKUploader(object):

    def __init__(self, storage_broker, secret_holder, progress=None, reactor=None):
        # server_selector needs storage_broker and secret_holder
        self._storage_broker = storage_broker
        self._secret_holder = secret_holder
        self._log_number = self.log("CHKUploader starting", parent=None)
        self._encoder = None
        self._storage_index = None
        self._upload_status = UploadStatus()
        self._upload_status.set_helper(False)
        self._upload_status.set_active(True)
        self._progress = progress
        self._reactor = reactor

        # locate_all_shareholders() will create the following attribute:
        # self._server_trackers = {} # k: shnum, v: instance of ServerTracker

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.upload"
        return log.msg(*args, **kwargs)

    @log_call_deferred(action_type=u"immutable:upload:chk:start")
    def start(self, encrypted_uploadable):
        """Start uploading the file.

        Returns a Deferred that will fire with the UploadResults instance.
        """

        self._started = time.time()
        eu = IEncryptedUploadable(encrypted_uploadable)
        self.log("starting upload of %s" % eu)

        eu.set_upload_status(self._upload_status)
        d = self.start_encrypted(eu)
        def _done(uploadresults):
            self._upload_status.set_active(False)
            return uploadresults
        d.addBoth(_done)
        return d

    def abort(self):
        """Call this if the upload must be abandoned before it completes.
        This will tell the shareholders to delete their partial shares. I
        return a Deferred that fires when these messages have been acked."""
        if not self._encoder:
            # how did you call abort() before calling start() ?
            return defer.succeed(None)
        return self._encoder.abort()

    @log_call_deferred(action_type=u"immutable:upload:chk:start-encrypted")
    @inline_callbacks
    def start_encrypted(self, encrypted):
        """
        Returns a Deferred that will fire with the UploadResults instance.
        """
        eu = IEncryptedUploadable(encrypted)

        started = time.time()
        # would be Really Nice to make Encoder just a local; only
        # abort() really needs self._encoder ...
        self._encoder = encode.Encoder(
            self._log_number,
            self._upload_status,
            progress=self._progress,
        )
        # this just returns itself
        yield self._encoder.set_encrypted_uploadable(eu)
        with LOCATE_ALL_SHAREHOLDERS() as action:
            (upload_trackers, already_serverids) = yield self.locate_all_shareholders(self._encoder, started)
            action.add_success_fields(upload_trackers=upload_trackers, already_serverids=already_serverids)
        self.set_shareholders(upload_trackers, already_serverids, self._encoder)
        verifycap = yield self._encoder.start()
        results = self._encrypted_done(verifycap)
        defer.returnValue(results)

    def locate_all_shareholders(self, encoder, started):
        server_selection_started = now = time.time()
        self._storage_index_elapsed = now - started
        storage_broker = self._storage_broker
        secret_holder = self._secret_holder
        storage_index = encoder.get_param("storage_index")
        self._storage_index = storage_index
        upload_id = si_b2a(storage_index)[:5]
        self.log("using storage index %s" % upload_id)
        server_selector = Tahoe2ServerSelector(
            upload_id,
            self._log_number,
            self._upload_status,
            reactor=self._reactor,
        )

        share_size = encoder.get_param("share_size")
        block_size = encoder.get_param("block_size")
        num_segments = encoder.get_param("num_segments")
        k, desired, n = encoder.get_param("share_counts")

        self._server_selection_started = time.time()
        d = server_selector.get_shareholders(storage_broker, secret_holder,
                                             storage_index,
                                             share_size, block_size,
                                             num_segments, n, k, desired)
        def _done(res):
            self._server_selection_elapsed = time.time() - server_selection_started
            return res
        d.addCallback(_done)
        return d

    def set_shareholders(self, upload_trackers, already_serverids, encoder):
        """
        :param upload_trackers: a sequence of ServerTracker objects that
                                have agreed to hold some shares for us (the
                                shareids are stashed inside the ServerTracker)

        :param already_serverids: a dict mapping sharenum to a set of
                                  serverids for servers that claim to already
                                  have this share
        """
        msgtempl = "set_shareholders; upload_trackers is %s, already_serverids is %s"
        values = ([', '.join([str_shareloc(k,v)
                              for k,v in st.buckets.items()])
                   for st in upload_trackers], already_serverids)
        self.log(msgtempl % values, level=log.OPERATIONAL)
        # record already-present shares in self._results
        self._count_preexisting_shares = len(already_serverids)

        self._server_trackers = {} # k: shnum, v: instance of ServerTracker
        for tracker in upload_trackers:
            assert isinstance(tracker, ServerTracker)
        buckets = {}
        servermap = already_serverids.copy()
        for tracker in upload_trackers:
            buckets.update(tracker.buckets)
            for shnum in tracker.buckets:
                self._server_trackers[shnum] = tracker
                servermap.setdefault(shnum, set()).add(tracker.get_serverid())
        assert len(buckets) == sum([len(tracker.buckets)
                                    for tracker in upload_trackers]), \
            "%s (%s) != %s (%s)" % (
                len(buckets),
                buckets,
                sum([len(tracker.buckets) for tracker in upload_trackers]),
                [(t.buckets, t.get_serverid()) for t in upload_trackers]
                )
        encoder.set_shareholders(buckets, servermap)

    def _encrypted_done(self, verifycap):
        """
        :return UploadResults: A description of the outcome of the upload.
        """
        e = self._encoder
        sharemap = dictutil.DictOfSets()
        servermap = dictutil.DictOfSets()
        for shnum in e.get_shares_placed():
            server = self._server_trackers[shnum].get_server()
            sharemap.add(shnum, server)
            servermap.add(server, shnum)
        now = time.time()
        timings = {}
        timings["total"] = now - self._started
        timings["storage_index"] = self._storage_index_elapsed
        timings["peer_selection"] = self._server_selection_elapsed
        timings.update(e.get_times())
        ur = UploadResults(file_size=e.file_size,
                           ciphertext_fetched=0,
                           preexisting_shares=self._count_preexisting_shares,
                           pushed_shares=len(e.get_shares_placed()),
                           sharemap=sharemap,
                           servermap=servermap,
                           timings=timings,
                           uri_extension_data=e.get_uri_extension_data(),
                           uri_extension_hash=e.get_uri_extension_hash(),
                           verifycapstr=verifycap.to_string())
        self._upload_status.set_results(ur)
        return ur

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

class LiteralUploader(object):

    def __init__(self, progress=None):
        self._status = s = UploadStatus()
        s.set_storage_index(None)
        s.set_helper(False)
        s.set_progress(0, 1.0)
        s.set_active(False)
        self._progress = progress

    def start(self, uploadable):
        uploadable = IUploadable(uploadable)
        d = uploadable.get_size()
        def _got_size(size):
            self._size = size
            self._status.set_size(size)
            if self._progress:
                self._progress.set_progress_total(size)
            return read_this_many_bytes(uploadable, size)
        d.addCallback(_got_size)
        d.addCallback(lambda data: uri.LiteralFileURI(b"".join(data)))
        d.addCallback(lambda u: u.to_string())
        d.addCallback(self._build_results)
        return d

    def _build_results(self, uri):
        ur = UploadResults(file_size=self._size,
                           ciphertext_fetched=0,
                           preexisting_shares=0,
                           pushed_shares=0,
                           sharemap={},
                           servermap={},
                           timings={},
                           uri_extension_data=None,
                           uri_extension_hash=None,
                           verifycapstr=None)
        ur.set_uri(uri)
        self._status.set_status("Finished")
        self._status.set_progress(1, 1.0)
        self._status.set_progress(2, 1.0)
        self._status.set_results(ur)
        if self._progress:
            self._progress.set_progress(self._size)
        return ur

    def close(self):
        pass

    def get_upload_status(self):
        return self._status

@implementer(RIEncryptedUploadable)
class RemoteEncryptedUploadable(Referenceable):

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

    def remote_close(self):
        return self._eu.close()


class AssistedUploader(object):

    def __init__(self, helper, storage_broker):
        self._helper = helper
        self._storage_broker = storage_broker
        self._log_number = log.msg("AssistedUploader starting")
        self._storage_index = None
        self._upload_status = s = UploadStatus()
        s.set_helper(True)
        s.set_active(True)

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        return log.msg(*args, **kwargs)

    def start(self, encrypted_uploadable, storage_index):
        """Start uploading the file.

        Returns a Deferred that will fire with the UploadResults instance.
        """
        precondition(isinstance(storage_index, bytes), storage_index)
        self._started = time.time()
        eu = IEncryptedUploadable(encrypted_uploadable)
        eu.set_upload_status(self._upload_status)
        self._encuploadable = eu
        self._storage_index = storage_index
        d = eu.get_size()
        d.addCallback(self._got_size)
        d.addCallback(lambda res: eu.get_all_encoding_parameters())
        d.addCallback(self._got_all_encoding_parameters)
        d.addCallback(self._contact_helper)
        d.addCallback(self._build_verifycap)
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

    def _contact_helper(self, res):
        now = self._time_contacting_helper_start = time.time()
        self._storage_index_elapsed = now - self._started
        self.log(format="contacting helper for SI %(si)s..",
                 si=si_b2a(self._storage_index), level=log.NOISY)
        self._upload_status.set_status("Contacting Helper")
        d = self._helper.callRemote("upload_chk", self._storage_index)
        d.addCallback(self._contacted_helper)
        return d

    def _contacted_helper(self, helper_upload_results_and_upload_helper):
        (helper_upload_results, upload_helper) = helper_upload_results_and_upload_helper
        now = time.time()
        elapsed = now - self._time_contacting_helper_start
        self._elapsed_time_contacting_helper = elapsed
        if upload_helper:
            self.log("helper says we need to upload", level=log.NOISY)
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
        self.log("helper says file is already uploaded", level=log.OPERATIONAL)
        self._upload_status.set_progress(1, 1.0)
        return helper_upload_results

    def _convert_old_upload_results(self, upload_results):
        # pre-1.3.0 helpers return upload results which contain a mapping
        # from shnum to a single human-readable string, containing things
        # like "Found on [x],[y],[z]" (for healthy files that were already in
        # the grid), "Found on [x]" (for files that needed upload but which
        # discovered pre-existing shares), and "Placed on [x]" (for newly
        # uploaded shares). The 1.3.0 helper returns a mapping from shnum to
        # set of binary serverid strings.

        # the old results are too hard to deal with (they don't even contain
        # as much information as the new results, since the nodeids are
        # abbreviated), so if we detect old results, just clobber them.

        sharemap = upload_results.sharemap
        if any(isinstance(v, (bytes, unicode)) for v in sharemap.values()):
            upload_results.sharemap = None

    def _build_verifycap(self, helper_upload_results):
        self.log("upload finished, building readcap", level=log.OPERATIONAL)
        self._convert_old_upload_results(helper_upload_results)
        self._upload_status.set_status("Building Readcap")
        hur = helper_upload_results
        assert hur.uri_extension_data["needed_shares"] == self._needed_shares
        assert hur.uri_extension_data["total_shares"] == self._total_shares
        assert hur.uri_extension_data["segment_size"] == self._segment_size
        assert hur.uri_extension_data["size"] == self._size

        # hur.verifycap doesn't exist if already found
        v = uri.CHKFileVerifierURI(self._storage_index,
                                   uri_extension_hash=hur.uri_extension_hash,
                                   needed_shares=self._needed_shares,
                                   total_shares=self._total_shares,
                                   size=self._size)
        timings = {}
        timings["storage_index"] = self._storage_index_elapsed
        timings["contacting_helper"] = self._elapsed_time_contacting_helper
        for key,val in hur.timings.items():
            if key == "total":
                key = "helper_total"
            timings[key] = val
        now = time.time()
        timings["total"] = now - self._started

        # Note: older Helpers (<=1.11) sent tubids as serverids. Newer ones
        # send pubkeys. get_stub_server() knows how to map both into
        # IDisplayableServer instances.
        gss = self._storage_broker.get_stub_server
        sharemap = {}
        servermap = {}
        for shnum, serverids in hur.sharemap.items():
            sharemap[shnum] = set([gss(serverid) for serverid in serverids])
        # if the file was already in the grid, hur.servermap is an empty dict
        for serverid, shnums in hur.servermap.items():
            servermap[gss(serverid)] = set(shnums)

        ur = UploadResults(file_size=self._size,
                           # not if already found
                           ciphertext_fetched=hur.ciphertext_fetched,
                           preexisting_shares=hur.preexisting_shares,
                           pushed_shares=hur.pushed_shares,
                           sharemap=sharemap,
                           servermap=servermap,
                           timings=timings,
                           uri_extension_data=hur.uri_extension_data,
                           uri_extension_hash=hur.uri_extension_hash,
                           verifycapstr=v.to_string())

        self._upload_status.set_status("Finished")
        self._upload_status.set_results(ur)
        return ur

    def get_upload_status(self):
        return self._upload_status

class BaseUploadable(object):
    # this is overridden by max_segment_size
    default_max_segment_size = DEFAULT_MAX_SEGMENT_SIZE
    default_params_set = False

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
            precondition(isinstance(k, (bytes, unicode)), k, v)
            precondition(isinstance(v, int), k, v)
        if "k" in default_params:
            self.default_encoding_param_k = default_params["k"]
        if "happy" in default_params:
            self.default_encoding_param_happy = default_params["happy"]
        if "n" in default_params:
            self.default_encoding_param_n = default_params["n"]
        if "max_segment_size" in default_params:
            self.default_max_segment_size = default_params["max_segment_size"]
        self.default_params_set = True

    def get_all_encoding_parameters(self):
        _assert(self.default_params_set, "set_default_encoding_parameters not called on %r" % (self,))
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

@implementer(IUploadable)
class FileHandle(BaseUploadable):

    def __init__(self, filehandle, convergence):
        """
        Upload the data from the filehandle.  If convergence is None then a
        random encryption key will be used, else the plaintext will be hashed,
        then the hash will be hashed together with the string in the
        "convergence" argument to form the encryption key.
        """
        assert convergence is None or isinstance(convergence, bytes), (convergence, type(convergence))
        self._filehandle = filehandle
        self._key = None
        self.convergence = convergence
        self._size = None

    def _get_encryption_key_convergent(self):
        if self._key is not None:
            return defer.succeed(self._key)

        d = self.get_size()
        # that sets self._size as a side-effect
        d.addCallback(lambda size: self.get_all_encoding_parameters())
        def _got(params):
            k, happy, n, segsize = params
            f = self._filehandle
            enckey_hasher = convergence_hasher(k, n, segsize, self.convergence)
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
        if self.convergence is not None:
            return self._get_encryption_key_convergent()
        else:
            return self._get_encryption_key_random()

    def get_size(self):
        if self._size is not None:
            return defer.succeed(self._size)
        self._filehandle.seek(0, os.SEEK_END)
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
    def __init__(self, filename, convergence):
        """
        Upload the data from the filename.  If convergence is None then a
        random encryption key will be used, else the plaintext will be hashed,
        then the hash will be hashed together with the string in the
        "convergence" argument to form the encryption key.
        """
        assert convergence is None or isinstance(convergence, bytes), (convergence, type(convergence))
        FileHandle.__init__(self, open(filename, "rb"), convergence=convergence)
    def close(self):
        FileHandle.close(self)
        self._filehandle.close()

class Data(FileHandle):
    def __init__(self, data, convergence):
        """
        Upload the data from the data argument.  If convergence is None then a
        random encryption key will be used, else the plaintext will be hashed,
        then the hash will be hashed together with the string in the
        "convergence" argument to form the encryption key.
        """
        assert convergence is None or isinstance(convergence, bytes), (convergence, type(convergence))
        FileHandle.__init__(self, BytesIO(data), convergence=convergence)

@implementer(IUploader)
class Uploader(service.MultiService, log.PrefixingLogMixin):
    """I am a service that allows file uploading. I am a service-child of the
    Client.
    """
    name = "uploader"
    URI_LIT_SIZE_THRESHOLD = 55

    def __init__(self, helper_furl=None, stats_provider=None, history=None, progress=None):
        self._helper_furl = helper_furl
        self.stats_provider = stats_provider
        self._history = history
        self._helper = None
        self._all_uploads = weakref.WeakKeyDictionary() # for debugging
        self._progress = progress
        log.PrefixingLogMixin.__init__(self, facility="tahoe.immutable.upload")
        service.MultiService.__init__(self)

    def startService(self):
        service.MultiService.startService(self)
        if self._helper_furl:
            self.parent.tub.connectTo(self._helper_furl,
                                      self._got_helper)

    def _got_helper(self, helper):
        self.log("got helper connection, getting versions")
        default = { b"http://allmydata.org/tahoe/protocols/helper/v1" :
                    { },
                    b"application-version": b"unknown: no get_version()",
                    }
        d = add_version_to_remote_reference(helper, default)
        d.addCallback(self._got_versioned_helper)

    def _got_versioned_helper(self, helper):
        needed = b"http://allmydata.org/tahoe/protocols/helper/v1"
        if needed not in helper.version:
            raise InsufficientVersionError(needed, helper.version)
        self._helper = helper
        helper.notifyOnDisconnect(self._lost_helper)

    def _lost_helper(self):
        self._helper = None

    def get_helper_info(self):
        # return a tuple of (helper_furl_or_None, connected_bool)
        return (self._helper_furl, bool(self._helper))


    def upload(self, uploadable, progress=None, reactor=None):
        """
        Returns a Deferred that will fire with the UploadResults instance.
        """
        assert self.parent
        assert self.running
        assert progress is None or IProgress.providedBy(progress)

        uploadable = IUploadable(uploadable)
        d = uploadable.get_size()
        def _got_size(size):
            default_params = self.parent.get_encoding_parameters()
            precondition(isinstance(default_params, dict), default_params)
            precondition("max_segment_size" in default_params, default_params)
            uploadable.set_default_encoding_parameters(default_params)
            if progress:
                progress.set_progress_total(size)

            if self.stats_provider:
                self.stats_provider.count('uploader.files_uploaded', 1)
                self.stats_provider.count('uploader.bytes_uploaded', size)

            if size <= self.URI_LIT_SIZE_THRESHOLD:
                uploader = LiteralUploader(progress=progress)
                return uploader.start(uploadable)
            else:
                eu = EncryptAnUploadable(uploadable, self._parentmsgid)
                d2 = defer.succeed(None)
                storage_broker = self.parent.get_storage_broker()
                if self._helper:
                    uploader = AssistedUploader(self._helper, storage_broker)
                    d2.addCallback(lambda x: eu.get_storage_index())
                    d2.addCallback(lambda si: uploader.start(eu, si))
                else:
                    storage_broker = self.parent.get_storage_broker()
                    secret_holder = self.parent._secret_holder
                    uploader = CHKUploader(storage_broker, secret_holder, progress=progress, reactor=reactor)
                    d2.addCallback(lambda x: uploader.start(eu))

                self._all_uploads[uploader] = None
                if self._history:
                    self._history.add_upload(uploader.get_upload_status())
                def turn_verifycap_into_read_cap(uploadresults):
                    # Generate the uri from the verifycap plus the key.
                    d3 = uploadable.get_encryption_key()
                    def put_readcap_into_results(key):
                        v = uri.from_string(uploadresults.get_verifycapstr())
                        r = uri.CHKFileURI(key, v.uri_extension_hash, v.needed_shares, v.total_shares, v.size)
                        uploadresults.set_uri(r.to_string())
                        return uploadresults
                    d3.addCallback(put_readcap_into_results)
                    return d3
                d2.addCallback(turn_verifycap_into_read_cap)
                return d2
        d.addCallback(_got_size)
        def _done(res):
            uploadable.close()
            return res
        d.addBoth(_done)
        return d
