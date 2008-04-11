
import os, sys, struct, time, weakref
from itertools import count
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from twisted.application import service
from foolscap.eventual import eventually
from allmydata.interfaces import IMutableFileNode, IMutableFileURI, \
     IPublishStatus, IRetrieveStatus
from allmydata.util import base32, hashutil, mathutil, idlib, log
from allmydata.uri import WriteableSSKFileURI
from allmydata import hashtree, codec, storage
from allmydata.encode import NotEnoughPeersError
from pycryptopp.publickey import rsa
from pycryptopp.cipher.aes import AES


class NotMutableError(Exception):
    pass

class NeedMoreDataError(Exception):
    def __init__(self, needed_bytes, encprivkey_offset, encprivkey_length):
        Exception.__init__(self)
        self.needed_bytes = needed_bytes # up through EOF
        self.encprivkey_offset = encprivkey_offset
        self.encprivkey_length = encprivkey_length
    def __str__(self):
        return "<NeedMoreDataError (%d bytes)>" % self.needed_bytes

class UncoordinatedWriteError(Exception):
    def __repr__(self):
        return "<%s -- You, oh user, tried to change a file or directory at the same time as another process was trying to change it.  To avoid data loss, don't do this.  Please see docs/write_coordination.html for details.>" % (self.__class__.__name__,)

class UnrecoverableFileError(Exception):
    pass

class CorruptShareError(Exception):
    def __init__(self, peerid, shnum, reason):
        self.args = (peerid, shnum, reason)
        self.peerid = peerid
        self.shnum = shnum
        self.reason = reason
    def __str__(self):
        short_peerid = idlib.nodeid_b2a(self.peerid)[:8]
        return "<CorruptShareError peerid=%s shnum[%d]: %s" % (short_peerid,
                                                               self.shnum,
                                                               self.reason)

class DictOfSets(dict):
    def add(self, key, value):
        if key in self:
            self[key].add(value)
        else:
            self[key] = set([value])

    def discard(self, key, value):
        if not key in self:
            return
        self[key].discard(value)
        if not self[key]:
            del self[key]

PREFIX = ">BQ32s16s" # each version has a different prefix
SIGNED_PREFIX = ">BQ32s16s BBQQ" # this is covered by the signature
HEADER = ">BQ32s16s BBQQ LLLLQQ" # includes offsets
HEADER_LENGTH = struct.calcsize(HEADER)

def unpack_header(data):
    o = {}
    (version,
     seqnum,
     root_hash,
     IV,
     k, N, segsize, datalen,
     o['signature'],
     o['share_hash_chain'],
     o['block_hash_tree'],
     o['share_data'],
     o['enc_privkey'],
     o['EOF']) = struct.unpack(HEADER, data[:HEADER_LENGTH])
    return (version, seqnum, root_hash, IV, k, N, segsize, datalen, o)

def unpack_prefix_and_signature(data):
    assert len(data) >= HEADER_LENGTH, len(data)
    prefix = data[:struct.calcsize(SIGNED_PREFIX)]

    (version,
     seqnum,
     root_hash,
     IV,
     k, N, segsize, datalen,
     o) = unpack_header(data)

    assert version == 0
    if len(data) < o['share_hash_chain']:
        raise NeedMoreDataError(o['share_hash_chain'],
                                o['enc_privkey'], o['EOF']-o['enc_privkey'])

    pubkey_s = data[HEADER_LENGTH:o['signature']]
    signature = data[o['signature']:o['share_hash_chain']]

    return (seqnum, root_hash, IV, k, N, segsize, datalen,
            pubkey_s, signature, prefix)

def unpack_share(data):
    assert len(data) >= HEADER_LENGTH
    o = {}
    (version,
     seqnum,
     root_hash,
     IV,
     k, N, segsize, datalen,
     o['signature'],
     o['share_hash_chain'],
     o['block_hash_tree'],
     o['share_data'],
     o['enc_privkey'],
     o['EOF']) = struct.unpack(HEADER, data[:HEADER_LENGTH])

    assert version == 0
    if len(data) < o['EOF']:
        raise NeedMoreDataError(o['EOF'],
                                o['enc_privkey'], o['EOF']-o['enc_privkey'])

    pubkey = data[HEADER_LENGTH:o['signature']]
    signature = data[o['signature']:o['share_hash_chain']]
    share_hash_chain_s = data[o['share_hash_chain']:o['block_hash_tree']]
    share_hash_format = ">H32s"
    hsize = struct.calcsize(share_hash_format)
    assert len(share_hash_chain_s) % hsize == 0, len(share_hash_chain_s)
    share_hash_chain = []
    for i in range(0, len(share_hash_chain_s), hsize):
        chunk = share_hash_chain_s[i:i+hsize]
        (hid, h) = struct.unpack(share_hash_format, chunk)
        share_hash_chain.append( (hid, h) )
    share_hash_chain = dict(share_hash_chain)
    block_hash_tree_s = data[o['block_hash_tree']:o['share_data']]
    assert len(block_hash_tree_s) % 32 == 0, len(block_hash_tree_s)
    block_hash_tree = []
    for i in range(0, len(block_hash_tree_s), 32):
        block_hash_tree.append(block_hash_tree_s[i:i+32])

    share_data = data[o['share_data']:o['enc_privkey']]
    enc_privkey = data[o['enc_privkey']:o['EOF']]

    return (seqnum, root_hash, IV, k, N, segsize, datalen,
            pubkey, signature, share_hash_chain, block_hash_tree,
            share_data, enc_privkey)

def unpack_share_data(verinfo, hash_and_data):
    (seqnum, root_hash, IV, segsize, datalength, k, N, prefix, o_t) = verinfo

    # hash_and_data starts with the share_hash_chain, so figure out what the
    # offsets really are
    o = dict(o_t)
    o_share_hash_chain = 0
    o_block_hash_tree = o['block_hash_tree'] - o['share_hash_chain']
    o_share_data = o['share_data'] - o['share_hash_chain']
    o_enc_privkey = o['enc_privkey'] - o['share_hash_chain']

    share_hash_chain_s = hash_and_data[o_share_hash_chain:o_block_hash_tree]
    share_hash_format = ">H32s"
    hsize = struct.calcsize(share_hash_format)
    assert len(share_hash_chain_s) % hsize == 0, len(share_hash_chain_s)
    share_hash_chain = []
    for i in range(0, len(share_hash_chain_s), hsize):
        chunk = share_hash_chain_s[i:i+hsize]
        (hid, h) = struct.unpack(share_hash_format, chunk)
        share_hash_chain.append( (hid, h) )
    share_hash_chain = dict(share_hash_chain)
    block_hash_tree_s = hash_and_data[o_block_hash_tree:o_share_data]
    assert len(block_hash_tree_s) % 32 == 0, len(block_hash_tree_s)
    block_hash_tree = []
    for i in range(0, len(block_hash_tree_s), 32):
        block_hash_tree.append(block_hash_tree_s[i:i+32])

    share_data = hash_and_data[o_share_data:o_enc_privkey]

    return (share_hash_chain, block_hash_tree, share_data)


def pack_checkstring(seqnum, root_hash, IV):
    return struct.pack(PREFIX,
                       0, # version,
                       seqnum,
                       root_hash,
                       IV)

def unpack_checkstring(checkstring):
    cs_len = struct.calcsize(PREFIX)
    version, seqnum, root_hash, IV = struct.unpack(PREFIX, checkstring[:cs_len])
    assert version == 0 # TODO: just ignore the share
    return (seqnum, root_hash, IV)

def pack_prefix(seqnum, root_hash, IV,
                required_shares, total_shares,
                segment_size, data_length):
    prefix = struct.pack(SIGNED_PREFIX,
                         0, # version,
                         seqnum,
                         root_hash,
                         IV,

                         required_shares,
                         total_shares,
                         segment_size,
                         data_length,
                         )
    return prefix

def pack_offsets(verification_key_length, signature_length,
                 share_hash_chain_length, block_hash_tree_length,
                 share_data_length, encprivkey_length):
    post_offset = HEADER_LENGTH
    offsets = {}
    o1 = offsets['signature'] = post_offset + verification_key_length
    o2 = offsets['share_hash_chain'] = o1 + signature_length
    o3 = offsets['block_hash_tree'] = o2 + share_hash_chain_length
    o4 = offsets['share_data'] = o3 + block_hash_tree_length
    o5 = offsets['enc_privkey'] = o4 + share_data_length
    o6 = offsets['EOF'] = o5 + encprivkey_length

    return struct.pack(">LLLLQQ",
                       offsets['signature'],
                       offsets['share_hash_chain'],
                       offsets['block_hash_tree'],
                       offsets['share_data'],
                       offsets['enc_privkey'],
                       offsets['EOF'])

def pack_share(prefix, verification_key, signature,
               share_hash_chain, block_hash_tree,
               share_data, encprivkey):
    share_hash_chain_s = "".join([struct.pack(">H32s", i, share_hash_chain[i])
                                  for i in sorted(share_hash_chain.keys())])
    for h in block_hash_tree:
        assert len(h) == 32
    block_hash_tree_s = "".join(block_hash_tree)

    offsets = pack_offsets(len(verification_key),
                           len(signature),
                           len(share_hash_chain_s),
                           len(block_hash_tree_s),
                           len(share_data),
                           len(encprivkey))
    final_share = "".join([prefix,
                           offsets,
                           verification_key,
                           signature,
                           share_hash_chain_s,
                           block_hash_tree_s,
                           share_data,
                           encprivkey])
    return final_share

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
    """

    def __init__(self):
        # 'servermap' maps peerid to sets of (shnum, versionid, timestamp)
        # tuples. Each 'versionid' is a (seqnum, root_hash, IV, segsize,
        # datalength, k, N, signed_prefix, offsets) tuple
        self.servermap = DictOfSets()
        self.connections = {} # maps peerid to a RemoteReference
        self.unreachable_peers = set() # peerids that didn't respond to queries
        self.problems = [] # mostly for debugging
        self.last_update_mode = None
        self.last_update_time = 0

    def dump(self, out=sys.stdout):
        print >>out, "servermap:"
        for (peerid, shares) in self.servermap.items():
            for (shnum, versionid, timestamp) in sorted(shares):
                (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
                 offsets_tuple) = versionid
                print >>out, ("[%s]: sh#%d seq%d-%s %d-of-%d len%d" %
                              (idlib.shortnodeid_b2a(peerid), shnum,
                               seqnum, base32.b2a(root_hash)[:4], k, N,
                               datalength))
        return out

    def make_versionmap(self):
        """Return a dict that maps versionid to sets of (shnum, peerid,
        timestamp) tuples."""
        versionmap = DictOfSets()
        for (peerid, shares) in self.servermap.items():
            for (shnum, verinfo, timestamp) in shares:
                versionmap.add(verinfo, (shnum, peerid, timestamp))
        return versionmap

    def shares_on_peer(self, peerid):
        return set([shnum
                    for (shnum, versionid, timestamp)
                    in self.servermap.get(peerid, [])])

    def version_on_peer(self, peerid, shnum):
        shares = self.servermap.get(peerid, [])
        for (sm_shnum, sm_versionid, sm_timestamp) in shares:
            if sm_shnum == shnum:
                return sm_versionid
        return None

    def shares_available(self):
        """Return a dict that maps versionid to tuples of
        (num_distinct_shares, k) tuples."""
        versionmap = self.make_versionmap()
        all_shares = {}
        for versionid, shares in versionmap.items():
            s = set()
            for (shnum, peerid, timestamp) in shares:
                s.add(shnum)
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = versionid
            all_shares[versionid] = (len(s), k)
        return all_shares

    def highest_seqnum(self):
        available = self.shares_available()
        seqnums = [versionid[0]
                   for versionid in available.keys()]
        seqnums.append(0)
        return max(seqnums)

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

    def unrecoverable_newer_versions(self):
        # Return a dict of versionid -> health, for versions that are
        # unrecoverable and have later seqnums than any recoverable versions.
        # These indicate that a write will lose data.
        pass

    def needs_merge(self):
        # return True if there are multiple recoverable versions with the
        # same seqnum, meaning that MutableFileNode.read_best_version is not
        # giving you the whole story, and that using its data to do a
        # subsequent publish will lose information.
        pass


class RetrieveStatus:
    implements(IRetrieveStatus)
    statusid_counter = count(0)
    def __init__(self):
        self.timings = {}
        self.timings["fetch_per_server"] = {}
        self.timings["cumulative_verify"] = 0.0
        self.sharemap = {}
        self.problems = {}
        self.active = True
        self.storage_index = None
        self.helper = False
        self.encoding = ("?","?")
        self.search_distance = None
        self.size = None
        self.status = "Not started"
        self.progress = 0.0
        self.counter = self.statusid_counter.next()
        self.started = time.time()

    def get_started(self):
        return self.started
    def get_storage_index(self):
        return self.storage_index
    def get_encoding(self):
        return self.encoding
    def get_search_distance(self):
        return self.search_distance
    def using_helper(self):
        return self.helper
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
    def set_encoding(self, k, n):
        self.encoding = (k, n)
    def set_search_distance(self, value):
        self.search_distance = value
    def set_size(self, size):
        self.size = size
    def set_status(self, status):
        self.status = status
    def set_progress(self, value):
        self.progress = value
    def set_active(self, value):
        self.active = value

MODE_CHECK = "query all peers"
MODE_ANYTHING = "one recoverable version"
MODE_WRITE = "replace all shares, probably" # not for initial creation
MODE_ENOUGH = "enough"

class ServermapUpdater:
    def __init__(self, filenode, servermap, mode=MODE_ENOUGH):
        self._node = filenode
        self._servermap = servermap
        self.mode = mode
        self._running = True

        self._storage_index = filenode.get_storage_index()
        self._last_failure = None

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

        prefix = storage.si_b2a(self._storage_index)[:5]
        self._log_number = log.msg("SharemapUpdater(%s): starting" % prefix)

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        return log.msg(*args, **kwargs)

    def update(self):
        """Update the servermap to reflect current conditions. Returns a
        Deferred that fires with the servermap once the update has finished."""

        # self._valid_versions is a set of validated verinfo tuples. We just
        # use it to remember which versions had valid signatures, so we can
        # avoid re-checking the signatures for each share.
        self._valid_versions = set()

        # self.versionmap maps verinfo tuples to sets of (shnum, peerid,
        # timestamp) tuples. This is used to figure out which versions might
        # be retrievable, and to make the eventual data download faster.
        self.versionmap = DictOfSets()

        self._started = time.time()
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
        return self._done_deferred

    def _build_initial_querylist(self):
        initial_peers_to_query = {}
        must_query = set()
        for peerid in self._servermap.servermap.keys():
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
        self._queries_outstanding = set()
        self._sharemap = DictOfSets() # shnum -> [(peerid, seqnum, R)..]
        dl = []
        for (peerid, ss) in peerlist.items():
            self._queries_outstanding.add(peerid)
            self._do_query(ss, peerid, self._storage_index, self._read_size)

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
        return d

    def _got_results(self, datavs, peerid, readsize, stuff, started):
        lp = self.log(format="got result from [%(peerid)s], %(numshares)d shares",
                     peerid=idlib.shortnodeid_b2a(peerid),
                     numshares=len(datavs),
                     level=log.NOISY)
        self._queries_outstanding.discard(peerid)
        self._must_query.discard(peerid)
        self._queries_completed += 1
        if not self._running:
            self.log("but we're not running, so we'll ignore it")
            return

        if datavs:
            self._good_peers.add(peerid)
        else:
            self._empty_peers.add(peerid)

        last_verinfo = None
        last_shnum = None
        for shnum,datav in datavs.items():
            data = datav[0]
            try:
                verinfo = self._got_results_one_share(shnum, data, peerid)
                last_verinfo = verinfo
                last_shnum = shnum
            except CorruptShareError, e:
                # log it and give the other shares a chance to be processed
                f = failure.Failure()
                self.log("bad share: %s %s" % (f, f.value), level=log.WEIRD)
                self._bad_peers.add(peerid)
                self._last_failure = f
                self._servermap.problems.append(f)
                pass

        if self._need_privkey and last_verinfo:
            # send them a request for the privkey. We send one request per
            # server.
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = last_verinfo
            o = dict(offsets_tuple)

            self._queries_outstanding.add(peerid)
            readv = [ (o['enc_privkey'], (o['EOF'] - o['enc_privkey'])) ]
            ss = self._servermap.connections[peerid]
            d = self._do_read(ss, peerid, self._storage_index,
                              [last_shnum], readv)
            d.addCallback(self._got_privkey_results, peerid, last_shnum)
            d.addErrback(self._privkey_query_failed, peerid, last_shnum)
            d.addErrback(log.err)
            d.addCallback(self._check_for_done)
            d.addErrback(self._fatal_error)

        # all done!
        self.log("_got_results done", parent=lp)

    def _got_results_one_share(self, shnum, data, peerid):
        self.log(format="_got_results: got shnum #%(shnum)d from peerid %(peerid)s",
                 shnum=shnum,
                 peerid=idlib.shortnodeid_b2a(peerid))

        # this might raise NeedMoreDataError, if the pubkey and signature
        # live at some weird offset. That shouldn't happen, so I'm going to
        # treat it as a bad share.
        (seqnum, root_hash, IV, k, N, segsize, datalength,
         pubkey_s, signature, prefix) = unpack_prefix_and_signature(data)

        if not self._node._pubkey:
            fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
            assert len(fingerprint) == 32
            if fingerprint != self._node._fingerprint:
                raise CorruptShareError(peerid, shnum,
                                        "pubkey doesn't match fingerprint")
            self._node._pubkey = self._deserialize_pubkey(pubkey_s)

        if self._need_privkey:
            self._try_to_extract_privkey(data, peerid, shnum)

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
                        k, N, segsize, datalength))
            self._valid_versions.add(verinfo)
        # We now know that this is a valid candidate verinfo.

        # Add the info to our servermap.
        timestamp = time.time()
        self._servermap.servermap.add(peerid, (shnum, verinfo, timestamp))
        # and the versionmap
        self.versionmap.add(verinfo, (shnum, peerid, timestamp))
        return verinfo

    def _deserialize_pubkey(self, pubkey_s):
        verifier = rsa.create_verifying_key_from_string(pubkey_s)
        return verifier

    def _try_to_extract_privkey(self, data, peerid, shnum):
        try:
            r = unpack_share(data)
        except NeedMoreDataError, e:
            # this share won't help us. oh well.
            offset = e.encprivkey_offset
            length = e.encprivkey_length
            self.log("shnum %d on peerid %s: share was too short (%dB) "
                     "to get the encprivkey; [%d:%d] ought to hold it" %
                     (shnum, idlib.shortnodeid_b2a(peerid), len(data),
                      offset, offset+length))
            # NOTE: if uncoordinated writes are taking place, someone might
            # change the share (and most probably move the encprivkey) before
            # we get a chance to do one of these reads and fetch it. This
            # will cause us to see a NotEnoughPeersError(unable to fetch
            # privkey) instead of an UncoordinatedWriteError . This is a
            # nuisance, but it will go away when we move to DSA-based mutable
            # files (since the privkey will be small enough to fit in the
            # write cap).

            return

        (seqnum, root_hash, IV, k, N, segsize, datalen,
         pubkey, signature, share_hash_chain, block_hash_tree,
         share_data, enc_privkey) = r

        return self._try_to_validate_privkey(self, enc_privkey, peerid, shnum)

    def _try_to_validate_privkey(self, enc_privkey, peerid, shnum):

        alleged_privkey_s = self._node._decrypt_privkey(enc_privkey)
        alleged_writekey = hashutil.ssk_writekey_hash(alleged_privkey_s)
        if alleged_writekey != self._node.get_writekey():
            self.log("invalid privkey from %s shnum %d" %
                     (idlib.nodeid_b2a(peerid)[:8], shnum), level=log.WEIRD)
            return

        # it's good
        self.log("got valid privkey from shnum %d on peerid %s" %
                 (shnum, idlib.shortnodeid_b2a(peerid)))
        privkey = rsa.create_signing_key_from_string(alleged_privkey_s)
        self._node._populate_encprivkey(enc_privkey)
        self._node._populate_privkey(privkey)
        self._need_privkey = False


    def _query_failed(self, f, peerid):
        self.log("error during query: %s %s" % (f, f.value), level=log.WEIRD)
        if not self._running:
            return
        self._must_query.discard(peerid)
        self._queries_outstanding.discard(peerid)
        self._bad_peers.add(peerid)
        self._servermap.problems.append(f)
        self._servermap.unreachable_peers.add(peerid) # TODO: overkill?
        self._queries_completed += 1
        self._last_failure = f

    def _got_privkey_results(self, datavs, peerid, shnum):
        self._queries_outstanding.discard(peerid)
        if not self._need_privkey:
            return
        if shnum not in datavs:
            self.log("privkey wasn't there when we asked it", level=log.WEIRD)
            return
        datav = datavs[shnum]
        enc_privkey = datav[0]
        self._try_to_validate_privkey(enc_privkey, peerid, shnum)

    def _privkey_query_failed(self, f, peerid, shnum):
        self._queries_outstanding.discard(peerid)
        self.log("error during privkey query: %s %s" % (f, f.value),
                 level=log.WEIRD)
        if not self._running:
            return
        self._queries_outstanding.discard(peerid)
        self._servermap.problems.append(f)
        self._last_failure = f

    def _check_for_done(self, res):
        # exit paths:
        #  return self._send_more_queries(outstanding) : send some more queries
        #  return self._done() : all done
        #  return : keep waiting, no new queries

        self.log(format=("_check_for_done, mode is '%(mode)s', "
                         "%(outstanding)d queries outstanding, "
                         "%(extra)d extra peers available, "
                         "%(must)d 'must query' peers left"
                         ),
                 mode=self.mode,
                 outstanding=len(self._queries_outstanding),
                 extra=len(self.extra_peers),
                 must=len(self._must_query),
                 )

        if self._must_query:
            # we are still waiting for responses from peers that used to have
            # a share, so we must continue to wait. No additional queries are
            # required at this time.
            self.log("%d 'must query' peers left" % len(self._must_query))
            return

        if (not self._queries_outstanding and not self.extra_peers):
            # all queries have retired, and we have no peers left to ask. No
            # more progress can be made, therefore we are done.
            self.log("all queries are retired, no extra peers: done")
            return self._done()

        recoverable_versions = self._servermap.recoverable_versions()
        unrecoverable_versions = self._servermap.unrecoverable_versions()

        # what is our completion policy? how hard should we work?

        if self.mode == MODE_ANYTHING:
            if recoverable_versions:
                self.log("MODE_ANYTHING and %d recoverable versions: done"
                         % len(recoverable_versions))
                return self._done()

        if self.mode == MODE_CHECK:
            # we used self._must_query, and we know there aren't any
            # responses still waiting, so that means we must be done
            self.log("MODE_CHECK: done")
            return self._done()

        MAX_IN_FLIGHT = 5
        if self.mode == MODE_ENOUGH:
            # if we've queried k+epsilon servers, and we see a recoverable
            # version, and we haven't seen any unrecoverable higher-seqnum'ed
            # versions, then we're done.

            if self._queries_completed < self.num_peers_to_query:
                self.log(format="ENOUGH, %(completed)d completed, %(query)d to query: need more",
                         completed=self._queries_completed,
                         query=self.num_peers_to_query)
                return self._send_more_queries(MAX_IN_FLIGHT)
            if not recoverable_versions:
                self.log("ENOUGH, no recoverable versions: need more")
                return self._send_more_queries(MAX_IN_FLIGHT)
            highest_recoverable = max(recoverable_versions)
            highest_recoverable_seqnum = highest_recoverable[0]
            for unrec_verinfo in unrecoverable_versions:
                if unrec_verinfo[0] > highest_recoverable_seqnum:
                    # there is evidence of a higher-seqnum version, but we
                    # don't yet see enough shares to recover it. Try harder.
                    # TODO: consider sending more queries.
                    # TODO: consider limiting the search distance
                    self.log("ENOUGH, evidence of higher seqnum: need more")
                    return self._send_more_queries(MAX_IN_FLIGHT)
            # all the unrecoverable versions were old or concurrent with a
            # recoverable version. Good enough.
            self.log("ENOUGH: no higher-seqnum: done")
            return self._done()

        if self.mode == MODE_WRITE:
            # we want to keep querying until we've seen a few that don't have
            # any shares, to be sufficiently confident that we've seen all
            # the shares. This is still less work than MODE_CHECK, which asks
            # every server in the world.

            if not recoverable_versions:
                self.log("WRITE, no recoverable versions: need more")
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
                            self.log("MODE_WRITE: found our boundary, %s" %
                                     "".join(states))
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
                    self.log("have all our answers")
                    # .. unless we're still waiting on the privkey
                    if self._need_privkey:
                        self.log("but we're still waiting for the privkey")
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
            self.log("MODE_WRITE: no boundary yet, %s" % "".join(states))
            return self._send_more_queries(MAX_IN_FLIGHT)

        # otherwise, keep up to 5 queries in flight. TODO: this is pretty
        # arbitrary, really I want this to be something like k -
        # max(known_version_sharecounts) + some extra
        self.log("catchall: need more")
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
        self._servermap.last_update_mode = self.mode
        self._servermap.last_update_time = self._started
        # the servermap will not be touched after this
        eventually(self._done_deferred.callback, self._servermap)

    def _fatal_error(self, f):
        self.log("fatal error", failure=f, level=log.WEIRD)
        self._done_deferred.errback(f)


class Marker:
    pass

class Retrieve:
    # this class is currently single-use. Eventually (in MDMF) we will make
    # it multi-use, in which case you can call download(range) multiple
    # times, and each will have a separate response chain. However the
    # Retrieve object will remain tied to a specific version of the file, and
    # will use a single ServerMap instance.

    def __init__(self, filenode, servermap, verinfo):
        self._node = filenode
        assert self._node._pubkey
        self._storage_index = filenode.get_storage_index()
        assert self._node._readkey
        self._last_failure = None
        prefix = storage.si_b2a(self._storage_index)[:5]
        self._log_number = log.msg("Retrieve(%s): starting" % prefix)
        self._outstanding_queries = {} # maps (peerid,shnum) to start_time
        self._running = True
        self._decoding = False

        self.servermap = servermap
        assert self._node._pubkey
        self.verinfo = verinfo

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        return log.msg(*args, **kwargs)

    def download(self):
        self._done_deferred = defer.Deferred()

        # first, which servers can we use?
        versionmap = self.servermap.make_versionmap()
        shares = versionmap[self.verinfo]
        # this sharemap is consumed as we decide to send requests
        self.remaining_sharemap = DictOfSets()
        for (shnum, peerid, timestamp) in shares:
            self.remaining_sharemap.add(shnum, peerid)

        self.shares = {} # maps shnum to validated blocks

        # how many shares do we need?
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        assert len(self.remaining_sharemap) >= k
        # we start with the lowest shnums we have available, since FEC is
        # faster if we're using "primary shares"
        self.active_shnums = set(sorted(self.remaining_sharemap.keys())[:k])
        for shnum in self.active_shnums:
            # we use an arbitrary peer who has the share. If shares are
            # doubled up (more than one share per peer), we could make this
            # run faster by spreading the load among multiple peers. But the
            # algorithm to do that is more complicated than I want to write
            # right now, and a well-provisioned grid shouldn't have multiple
            # shares per peer.
            peerid = list(self.remaining_sharemap[shnum])[0]
            self.get_data(shnum, peerid)

        # control flow beyond this point: state machine. Receiving responses
        # from queries is the input. We might send out more queries, or we
        # might produce a result.

        return self._done_deferred

    def get_data(self, shnum, peerid):
        self.log(format="sending sh#%(shnum)d request to [%(peerid)s]",
                 shnum=shnum,
                 peerid=idlib.shortnodeid_b2a(peerid),
                 level=log.NOISY)
        ss = self.servermap.connections[peerid]
        started = time.time()
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        offsets = dict(offsets_tuple)
        # we read the checkstring, to make sure that the data we grab is from
        # the right version. We also read the data, and the hashes necessary
        # to validate them (share_hash_chain, block_hash_tree, share_data).
        # We don't read the signature or the pubkey, since that was handled
        # during the servermap phase, and we'll be comparing the share hash
        # chain against the roothash that was validated back then.
        readv = [ (0, struct.calcsize(SIGNED_PREFIX)),
                  (offsets['share_hash_chain'],
                   offsets['enc_privkey'] - offsets['share_hash_chain']),
                  ]

        m = Marker()
        self._outstanding_queries[m] = (peerid, shnum, started)

        # ask the cache first
        datav = []
        #for (offset, length) in readv:
        #    (data, timestamp) = self._node._cache.read(self.verinfo, shnum,
        #                                               offset, length)
        #    if data is not None:
        #        datav.append(data)
        if len(datav) == len(readv):
            self.log("got data from cache")
            d = defer.succeed(datav)
        else:
            self.remaining_sharemap[shnum].remove(peerid)
            d = self._do_read(ss, peerid, self._storage_index, [shnum], readv)
            d.addCallback(self._fill_cache, readv)

        d.addCallback(self._got_results, m, peerid, started)
        d.addErrback(self._query_failed, m, peerid)
        # errors that aren't handled by _query_failed (and errors caused by
        # _query_failed) get logged, but we still want to check for doneness.
        def _oops(f):
            self.log(format="problem in _query_failed for sh#%(shnum)d to %(peerid)s",
                     shnum=shnum,
                     peerid=idlib.shortnodeid_b2a(peerid),
                     failure=f,
                     level=log.WEIRD)
        d.addErrback(_oops)
        d.addBoth(self._check_for_done)
        # any error during _check_for_done means the download fails. If the
        # download is successful, _check_for_done will fire _done by itself.
        d.addErrback(self._done)
        d.addErrback(log.err)
        return d # purely for testing convenience

    def _fill_cache(self, datavs, readv):
        timestamp = time.time()
        for shnum,datav in datavs.items():
            for i, (offset, length) in enumerate(readv):
                data = datav[i]
                self._node._cache.add(self.verinfo, shnum, offset, data,
                                      timestamp)
        return datavs

    def _do_read(self, ss, peerid, storage_index, shnums, readv):
        # isolate the callRemote to a separate method, so tests can subclass
        # Publish and override it
        d = ss.callRemote("slot_readv", storage_index, shnums, readv)
        return d

    def remove_peer(self, peerid):
        for shnum in list(self.remaining_sharemap.keys()):
            self.remaining_sharemap.discard(shnum, peerid)

    def _got_results(self, datavs, marker, peerid, started):
        self.log(format="got results (%(shares)d shares) from [%(peerid)s]",
                 shares=len(datavs),
                 peerid=idlib.shortnodeid_b2a(peerid),
                 level=log.NOISY)
        self._outstanding_queries.pop(marker, None)
        if not self._running:
            return

        # note that we only ask for a single share per query, so we only
        # expect a single share back. On the other hand, we use the extra
        # shares if we get them.. seems better than an assert().

        for shnum,datav in datavs.items():
            (prefix, hash_and_data) = datav
            try:
                self._got_results_one_share(shnum, peerid,
                                            prefix, hash_and_data)
            except CorruptShareError, e:
                # log it and give the other shares a chance to be processed
                f = failure.Failure()
                self.log("bad share: %s %s" % (f, f.value), level=log.WEIRD)
                self.remove_peer(peerid)
                self._last_failure = f
                pass
        # all done!

    def _got_results_one_share(self, shnum, peerid,
                               got_prefix, got_hash_and_data):
        self.log("_got_results: got shnum #%d from peerid %s"
                 % (shnum, idlib.shortnodeid_b2a(peerid)))
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo
        assert len(got_prefix) == len(prefix), (len(got_prefix), len(prefix))
        if got_prefix != prefix:
            msg = "someone wrote to the data since we read the servermap: prefix changed"
            raise UncoordinatedWriteError(msg)
        (share_hash_chain, block_hash_tree,
         share_data) = unpack_share_data(self.verinfo, got_hash_and_data)

        assert isinstance(share_data, str)
        # build the block hash tree. SDMF has only one leaf.
        leaves = [hashutil.block_hash(share_data)]
        t = hashtree.HashTree(leaves)
        if list(t) != block_hash_tree:
            raise CorruptShareError(peerid, shnum, "block hash tree failure")
        share_hash_leaf = t[0]
        t2 = hashtree.IncompleteHashTree(N)
        # root_hash was checked by the signature
        t2.set_hashes({0: root_hash})
        try:
            t2.set_hashes(hashes=share_hash_chain,
                          leaves={shnum: share_hash_leaf})
        except (hashtree.BadHashError, hashtree.NotEnoughHashesError,
                IndexError), e:
            msg = "corrupt hashes: %s" % (e,)
            raise CorruptShareError(peerid, shnum, msg)
        self.log(" data valid! len=%d" % len(share_data))
        # each query comes down to this: placing validated share data into
        # self.shares
        self.shares[shnum] = share_data

    def _query_failed(self, f, marker, peerid):
        self.log(format="query to [%(peerid)s] failed",
                 peerid=idlib.shortnodeid_b2a(peerid),
                 level=log.NOISY)
        self._outstanding_queries.pop(marker, None)
        if not self._running:
            return
        self._last_failure = f
        self.remove_peer(peerid)
        self.log("error during query: %s %s" % (f, f.value), level=log.WEIRD)

    def _check_for_done(self, res):
        # exit paths:
        #  return : keep waiting, no new queries
        #  return self._send_more_queries(outstanding) : send some more queries
        #  fire self._done(plaintext) : download successful
        #  raise exception : download fails

        self.log(format="_check_for_done: running=%(running)s, decoding=%(decoding)s",
                 running=self._running, decoding=self._decoding,
                 level=log.NOISY)
        if not self._running:
            return
        if self._decoding:
            return
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo

        if len(self.shares) < k:
            # we don't have enough shares yet
            return self._maybe_send_more_queries(k)

        # we have enough to finish. All the shares have had their hashes
        # checked, so if something fails at this point, we don't know how
        # to fix it, so the download will fail.

        self._decoding = True # avoid reentrancy

        d = defer.maybeDeferred(self._decode)
        d.addCallback(self._decrypt, IV, self._node._readkey)
        d.addBoth(self._done)
        return d # purely for test convenience

    def _maybe_send_more_queries(self, k):
        # we don't have enough shares yet. Should we send out more queries?
        # There are some number of queries outstanding, each for a single
        # share. If we can generate 'needed_shares' additional queries, we do
        # so. If we can't, then we know this file is a goner, and we raise
        # NotEnoughPeersError.
        self.log(format=("_maybe_send_more_queries, have=%(have)d, k=%(k)d, "
                         "outstanding=%(outstanding)d"),
                 have=len(self.shares), k=k,
                 outstanding=len(self._outstanding_queries),
                 level=log.NOISY)

        remaining_shares = k - len(self.shares)
        needed = remaining_shares - len(self._outstanding_queries)
        if not needed:
            # we have enough queries in flight already

            # TODO: but if they've been in flight for a long time, and we
            # have reason to believe that new queries might respond faster
            # (i.e. we've seen other queries come back faster, then consider
            # sending out new queries. This could help with peers which have
            # silently gone away since the servermap was updated, for which
            # we're still waiting for the 15-minute TCP disconnect to happen.
            self.log("enough queries are in flight, no more are needed",
                     level=log.NOISY)
            return

        outstanding_shnums = set([shnum
                                  for (peerid, shnum, started)
                                  in self._outstanding_queries.values()])
        # prefer low-numbered shares, they are more likely to be primary
        available_shnums = sorted(self.remaining_sharemap.keys())
        for shnum in available_shnums:
            if shnum in outstanding_shnums:
                # skip ones that are already in transit
                continue
            if shnum not in self.remaining_sharemap:
                # no servers for that shnum. note that DictOfSets removes
                # empty sets from the dict for us.
                continue
            peerid = list(self.remaining_sharemap[shnum])[0]
            # get_data will remove that peerid from the sharemap, and add the
            # query to self._outstanding_queries
            self.get_data(shnum, peerid)
            needed -= 1
            if not needed:
                break

        # at this point, we have as many outstanding queries as we can. If
        # needed!=0 then we might not have enough to recover the file.
        if needed:
            format = ("ran out of peers: "
                      "have %(have)d shares (k=%(k)d), "
                      "%(outstanding)d queries in flight, "
                      "need %(need)d more")
            self.log(format=format,
                     have=len(self.shares), k=k,
                     outstanding=len(self._outstanding_queries),
                     need=needed,
                     level=log.WEIRD)
            msg2 = format % {"have": len(self.shares),
                             "k": k,
                             "outstanding": len(self._outstanding_queries),
                             "need": needed,
                             }
            raise NotEnoughPeersError("%s, last failure: %s" %
                                      (msg2, self._last_failure))

        return

    def _decode(self):
        (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
         offsets_tuple) = self.verinfo

        # shares_dict is a dict mapping shnum to share data, but the codec
        # wants two lists.
        shareids = []; shares = []
        for shareid, share in self.shares.items():
            shareids.append(shareid)
            shares.append(share)

        assert len(shareids) >= k, len(shareids)
        # zfec really doesn't want extra shares
        shareids = shareids[:k]
        shares = shares[:k]

        fec = codec.CRSDecoder()
        params = "%d-%d-%d" % (segsize, k, N)
        fec.set_serialized_params(params)

        self.log("params %s, we have %d shares" % (params, len(shares)))
        self.log("about to decode, shareids=%s" % (shareids,))
        d = defer.maybeDeferred(fec.decode, shares, shareids)
        def _done(buffers):
            self.log(" decode done, %d buffers" % len(buffers))
            segment = "".join(buffers)
            self.log(" joined length %d, datalength %d" %
                     (len(segment), datalength))
            segment = segment[:datalength]
            self.log(" segment len=%d" % len(segment))
            return segment
        def _err(f):
            self.log(" decode failed: %s" % f)
            return f
        d.addCallback(_done)
        d.addErrback(_err)
        return d

    def _decrypt(self, crypttext, IV, readkey):
        started = time.time()
        key = hashutil.ssk_readkey_data_hash(IV, readkey)
        decryptor = AES(key)
        plaintext = decryptor.process(crypttext)
        return plaintext

    def _done(self, res):
        if not self._running:
            return
        self._running = False
        # res is either the new contents, or a Failure
        if isinstance(res, failure.Failure):
            self.log("Retrieve done, with failure", failure=res)
        else:
            self.log("Retrieve done, success!: res=%s" % (res,))
            # remember the encoding parameters, use them again next time
            (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
             offsets_tuple) = self.verinfo
            self._node._populate_required_shares(k)
            self._node._populate_total_shares(N)
        eventually(self._done_deferred.callback, res)


class PublishStatus:
    implements(IPublishStatus)
    statusid_counter = count(0)
    def __init__(self):
        self.timings = {}
        self.timings["per_server"] = {}
        self.privkey_from = None
        self.peers_queried = None
        self.sharemap = None # DictOfSets
        self.problems = {}
        self.active = True
        self.storage_index = None
        self.helper = False
        self.encoding = ("?", "?")
        self.initial_read_size = None
        self.size = None
        self.status = "Not started"
        self.progress = 0.0
        self.counter = self.statusid_counter.next()
        self.started = time.time()

    def add_per_server_time(self, peerid, op, elapsed):
        assert op in ("read", "write")
        if peerid not in self.timings["per_server"]:
            self.timings["per_server"][peerid] = []
        self.timings["per_server"][peerid].append((op,elapsed))

    def get_started(self):
        return self.started
    def get_storage_index(self):
        return self.storage_index
    def get_encoding(self):
        return self.encoding
    def using_helper(self):
        return self.helper
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

class Publish:
    """I represent a single act of publishing the mutable file to the grid. I
    will only publish my data if the servermap I am using still represents
    the current state of the world.

    To make the initial publish, set servermap to None.
    """

    # we limit the segment size as usual to constrain our memory footprint.
    # The max segsize is higher for mutable files, because we want to support
    # dirnodes with up to 10k children, and each child uses about 330 bytes.
    # If you actually put that much into a directory you'll be using a
    # footprint of around 14MB, which is higher than we'd like, but it is
    # more important right now to support large directories than to make
    # memory usage small when you use them. Once we implement MDMF (with
    # multiple segments), we will drop this back down, probably to 128KiB.
    MAX_SEGMENT_SIZE = 3500000

    def __init__(self, filenode, servermap):
        self._node = filenode
        self._servermap = servermap
        self._storage_index = self._node.get_storage_index()
        self._log_prefix = prefix = storage.si_b2a(self._storage_index)[:5]
        num = self._node._client.log("Publish(%s): starting" % prefix)
        self._log_number = num
        self._running = True

    def log(self, *args, **kwargs):
        if 'parent' not in kwargs:
            kwargs['parent'] = self._log_number
        return log.msg(*args, **kwargs)

    def log_err(self, *args, **kwargs):
        if 'parent' not in kwargs:
            kwargs['parent'] = self._log_number
        return log.err(*args, **kwargs)

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

        self.done_deferred = defer.Deferred()

        self._writekey = self._node.get_writekey()
        assert self._writekey, "need write capability to publish"

        # first, which servers will we publish to? We require that the
        # servermap was updated in MODE_WRITE, so we can depend upon the
        # peerlist computed by that process instead of computing our own.
        if self._servermap:
            assert self._servermap.last_update_mode == MODE_WRITE
            # we will push a version that is one larger than anything present
            # in the grid, according to the servermap.
            self._new_seqnum = self._servermap.highest_seqnum() + 1
        else:
            # If we don't have a servermap, that's because we're doing the
            # initial publish
            self._new_seqnum = 1
            self._servermap = ServerMap()

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
        self._pubkey = self._node.get_pubkey()
        assert self._pubkey
        self._privkey = self._node.get_privkey()
        assert self._privkey
        self._encprivkey = self._node.get_encprivkey()

        client = self._node._client
        full_peerlist = client.get_permuted_peers("storage",
                                                  self._storage_index)
        self.full_peerlist = full_peerlist # for use later, immutable
        self.bad_peers = set() # peerids who have errbacked/refused requests

        started = time.time()
        dl = []
        for permutedid, (peerid, ss) in enumerate(partial_peerlist):
            self._storage_servers[peerid] = ss
            d = self._do_query(ss, peerid, storage_index)
            d.addCallback(self._got_query_results,
                          peerid, permutedid,
                          reachable_peers, current_share_peers, started)
            dl.append(d)
        d = defer.DeferredList(dl)
        d.addCallback(self._got_all_query_results,
                      total_shares, reachable_peers,
                      current_share_peers)
        # TODO: add an errback too, probably to ignore that peer

        self.setup_encoding_parameters()

        self.surprised = False

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

        # we use the servermap to populate the initial goal: this way we will
        # try to update each existing share in place.
        for (peerid, shares) in self._servermap.servermap.items():
            for (shnum, versionid, timestamp) in shares:
                self.goal.add( (peerid, shnum) )
                self.connections[peerid] = self._servermap.connections[peerid]

        # create the shares. We'll discard these as they are delivered. SMDF:
        # we're allowed to hold everything in memory.

        d = self._encrypt_and_encode()
        d.addCallback(self._generate_shares)
        d.addCallback(self.loop) # trigger delivery
        d.addErrback(self._fatal_error)

        return self.done_deferred

    def setup_encoding_parameters(self):
        segment_size = min(self.MAX_SEGMENT_SIZE, len(self.newdata))
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
        self.log("error during loop", failure=f, level=log.SCARY)
        self._done(f)

    def loop(self, ignored=None):
        self.log("entering loop", level=log.NOISY)
        self.update_goal()
        # how far are we from our goal?
        needed = self.goal - self.placed - self.outstanding

        if needed:
            # we need to send out new shares
            self.log(format="need to send %(needed)d new shares",
                     needed=len(needed), level=log.NOISY)
            d = self._send_shares(needed)
            d.addCallback(self.loop)
            d.addErrback(self._fatal_error)
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
        return self._done(None)

    def log_goal(self, goal):
        logmsg = []
        for (peerid, shnum) in goal:
            logmsg.append("sh%d to [%s]" % (shnum,
                                            idlib.shortnodeid_b2a(peerid)))
        self.log("current goal: %s" % (", ".join(logmsg)), level=log.NOISY)
        self.log("we are planning to push new seqnum=#%d" % self._new_seqnum,
                 level=log.NOISY)

    def update_goal(self):
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

        # if log.recording_noisy
        if False:
            self.log_goal(self.goal)

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
            entry = (len(old_assignments.get(peerid, [])), i, peerid, ss)
            peerlist.append(entry)
        peerlist.sort()

        new_assignments = []
        # we then index this peerlist with an integer, because we may have to
        # wrap. We update the goal as we go.
        i = 0
        for shnum in homeless_shares:
            (ignored1, ignored2, peerid, ss) = peerlist[i]
            self.goal.add( (peerid, shnum) )
            self.connections[peerid] = ss
            i += 1
            if i >= len(peerlist):
                i = 0



    def _encrypt_and_encode(self):
        # this returns a Deferred that fires with a list of (sharedata,
        # sharenum) tuples. TODO: cache the ciphertext, only produce the
        # shares that we care about.
        self.log("_encrypt_and_encode")

        #started = time.time()

        key = hashutil.ssk_readkey_data_hash(self.salt, self.readkey)
        enc = AES(key)
        crypttext = enc.process(self.newdata)
        assert len(crypttext) == len(self.newdata)

        #now = time.time()
        #self._status.timings["encrypt"] = now - started
        #started = now

        # now apply FEC

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
            #elapsed = time.time() - started
            #self._status.timings["encode"] = elapsed
            return res
        d.addCallback(_done_encoding)
        return d

    def _generate_shares(self, shares_and_shareids):
        # this sets self.shares and self.root_hash
        self.log("_generate_shares")
        #started = time.time()

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

        prefix = pack_prefix(self._new_seqnum, root_hash, self.salt,
                             self.required_shares, self.total_shares,
                             self.segment_size, len(self.newdata))

        # now pack the beginning of the share. All shares are the same up
        # to the signature, then they have divergent share hash chains,
        # then completely different block hash trees + salt + share data,
        # then they all share the same encprivkey at the end. The sizes
        # of everything are the same for all shares.

        #sign_started = time.time()
        signature = privkey.sign(prefix)
        #self._status.timings["sign"] = time.time() - sign_started

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
        #elapsed = time.time() - started
        #self._status.timings["pack"] = elapsed
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
        #started = time.time()

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

        for (peerid, shnum) in needed:
            testvs = []
            for (old_shnum, old_versionid, old_timestamp) in sm.get(peerid,[]):
                if old_shnum == shnum:
                    # an old version of that share already exists on the
                    # server, according to our servermap. We will create a
                    # request that attempts to replace it.
                    (old_seqnum, old_root_hash, old_salt, old_segsize,
                     old_datalength, old_k, old_N, old_prefix,
                     old_offsets_tuple) = old_versionid
                    old_checkstring = pack_checkstring(old_seqnum,
                                                       old_root_hash,
                                                       old_salt)
                    testv = (0, len(old_checkstring), "eq", old_checkstring)
                    testvs.append(testv)
                    break
            if not testvs:
                # add a testv that requires the share not exist
                #testv = (0, 1, 'eq', "")

                # Unfortunately, foolscap-0.2.5 has a bug in the way inbound
                # constraints are handled. If the same object is referenced
                # multiple times inside the arguments, foolscap emits a
                # 'reference' token instead of a distinct copy of the
                # argument. The bug is that these 'reference' tokens are not
                # accepted by the inbound constraint code. To work around
                # this, we need to prevent python from interning the
                # (constant) tuple, by creating a new copy of this vector
                # each time. This bug is fixed in later versions of foolscap.
                testv = tuple([0, 1, 'eq', ""])
                testvs.append(testv)

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
        started = time.time()
        dl = []
        for (peerid, tw_vectors) in all_tw_vectors.items():

            write_enabler = self._node.get_write_enabler(peerid)
            renew_secret = self._node.get_renewal_secret(peerid)
            cancel_secret = self._node.get_cancel_secret(peerid)
            secrets = (write_enabler, renew_secret, cancel_secret)
            shnums = tw_vectors.keys()

            d = self._do_testreadwrite(peerid, secrets,
                                       tw_vectors, read_vector)
            d.addCallbacks(self._got_write_answer, self._got_write_error,
                           callbackArgs=(peerid, shnums, started),
                           errbackArgs=(peerid, shnums, started))
            d.addErrback(self._fatal_error)
            dl.append(d)

        d = defer.DeferredList(dl)
        def _done_sending(res):
            elapsed = time.time() - started
            self._status.timings["push"] = elapsed
            self._status.sharemap = dispatch_map
            return res
        d.addCallback(_done_sending)
        d.addCallback(lambda res: (self._surprised, dispatch_map))
        return d

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
        sm = self._servermap.servermap

        wrote, read_data = answer

        if not wrote:
            self.log("our testv failed, so the write did not happen",
                     parent=lp, level=log.WEIRD)
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
                (seqnum, root_hash, IV, segsize, datalength, k, N, prefix,
                 offsets_tuple) = expected_version
                self.log("somebody modified the share on us:"
                         " shnum=%d: I thought they had #%d:R=%s,"
                         " but testv reported #%d:R=%s" %
                         (shnum,
                          seqnum, base32.b2a(root_hash)[:4],
                          other_seqnum, base32.b2a(other_roothash)[:4]),
                         parent=lp, level=log.NOISY)
            # self.loop() will take care of finding new homes
            return

        for shnum in shnums:
            self.placed.add( (peerid, shnum) )
            # and update the servermap. We strip the old entry out..
            newset = set([ t
                           for t in sm.get(peerid, [])
                           if t[0] != shnum ])
            sm[peerid] = newset
            # and add a new one
            sm[peerid].add( (shnum, self.versioninfo, started) )

        surprise_shares = set(read_data.keys()) - set(shnums)
        if surprise_shares:
            self.log("they had shares %s that we didn't know about" %
                     (list(surprise_shares),),
                     parent=lp, level=log.WEIRD)
            self.surprised = True
            return

        # self.loop() will take care of checking to see if we're done
        return

    def _got_write_error(self, f, peerid, shnums, started):
        for shnum in shnums:
            self.outstanding.discard( (peerid, shnum) )
        self.bad_peers.add(peerid)
        self.log(format="error while writing shares %(shnums)s to peerid %(peerid)s",
                 shnums=list(shnums), peerid=idlib.shortnodeid_b2a(peerid),
                 failure=f,
                 level=log.UNUSUAL)
        # self.loop() will take care of checking to see if we're done
        return



    def _log_dispatch_map(self, dispatch_map):
        for shnum, places in dispatch_map.items():
            sent_to = [(idlib.shortnodeid_b2a(peerid),
                        seqnum,
                        base32.b2a(root_hash)[:4])
                       for (peerid,seqnum,root_hash) in places]
            self.log(" share %d sent to: %s" % (shnum, sent_to),
                     level=log.NOISY)

    def _maybe_recover(self, (surprised, dispatch_map)):
        self.log("_maybe_recover, surprised=%s, dispatch_map:" % surprised,
                 level=log.NOISY)
        self._log_dispatch_map(dispatch_map)
        if not surprised:
            self.log(" no recovery needed")
            return
        self.log("We need recovery!", level=log.WEIRD)
        print "RECOVERY NOT YET IMPLEMENTED"
        # but dispatch_map will help us do it
        raise UncoordinatedWriteError("I was surprised!")

    def _done(self, res):
        if not self._running:
            return
        self._running = False
        #now = time.time()
        #self._status.timings["total"] = now - self._started
        #self._status.set_active(False)
        #self._status.set_status("Done")
        #self._status.set_progress(1.0)
        self.done_deferred.callback(res)
        return None

    def get_status(self):
        return self._status



# use client.create_mutable_file() to make one of these

class MutableFileNode:
    implements(IMutableFileNode)
    publish_class = Publish
    retrieve_class = Retrieve
    SIGNATURE_KEY_SIZE = 2048
    DEFAULT_ENCODING = (3, 10)

    def __init__(self, client):
        self._client = client
        self._pubkey = None # filled in upon first read
        self._privkey = None # filled in if we're mutable
        # we keep track of the last encoding parameters that we use. These
        # are updated upon retrieve, and used by publish. If we publish
        # without ever reading (i.e. overwrite()), then we use these values.
        (self._required_shares, self._total_shares) = self.DEFAULT_ENCODING
        self._sharemap = {} # known shares, shnum-to-[nodeids]
        self._cache = ResponseCache()

        self._current_data = None # SDMF: we're allowed to cache the contents
        self._current_roothash = None # ditto
        self._current_seqnum = None # ditto

    def __repr__(self):
        return "<%s %x %s %s>" % (self.__class__.__name__, id(self), self.is_readonly() and 'RO' or 'RW', hasattr(self, '_uri') and self._uri.abbrev())

    def init_from_uri(self, myuri):
        # we have the URI, but we have not yet retrieved the public
        # verification key, nor things like 'k' or 'N'. If and when someone
        # wants to get our contents, we'll pull from shares and fill those
        # in.
        self._uri = IMutableFileURI(myuri)
        if not self._uri.is_readonly():
            self._writekey = self._uri.writekey
        self._readkey = self._uri.readkey
        self._storage_index = self._uri.storage_index
        self._fingerprint = self._uri.fingerprint
        # the following values are learned during Retrieval
        #  self._pubkey
        #  self._required_shares
        #  self._total_shares
        # and these are needed for Publish. They are filled in by Retrieval
        # if possible, otherwise by the first peer that Publish talks to.
        self._privkey = None
        self._encprivkey = None
        return self

    def create(self, initial_contents, keypair_generator=None):
        """Call this when the filenode is first created. This will generate
        the keys, generate the initial shares, wait until at least numpeers
        are connected, allocate shares, and upload the initial
        contents. Returns a Deferred that fires (with the MutableFileNode
        instance you should use) when it completes.
        """
        self._required_shares, self._total_shares = self.DEFAULT_ENCODING

        d = defer.maybeDeferred(self._generate_pubprivkeys, keypair_generator)
        def _generated( (pubkey, privkey) ):
            self._pubkey, self._privkey = pubkey, privkey
            pubkey_s = self._pubkey.serialize()
            privkey_s = self._privkey.serialize()
            self._writekey = hashutil.ssk_writekey_hash(privkey_s)
            self._encprivkey = self._encrypt_privkey(self._writekey, privkey_s)
            self._fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
            self._uri = WriteableSSKFileURI(self._writekey, self._fingerprint)
            self._readkey = self._uri.readkey
            self._storage_index = self._uri.storage_index
            # TODO: seqnum/roothash: really we mean "doesn't matter since
            # nobody knows about us yet"
            self._current_seqnum = 0
            self._current_roothash = "\x00"*32
            return self._publish(initial_contents)
        d.addCallback(_generated)
        return d

    def _generate_pubprivkeys(self, keypair_generator):
        if keypair_generator:
            return keypair_generator(self.SIGNATURE_KEY_SIZE)
        else:
            # RSA key generation for a 2048 bit key takes between 0.8 and 3.2 secs
            signer = rsa.generate(self.SIGNATURE_KEY_SIZE)
            verifier = signer.get_verifying_key()
            return verifier, signer

    def _encrypt_privkey(self, writekey, privkey):
        enc = AES(writekey)
        crypttext = enc.process(privkey)
        return crypttext

    def _decrypt_privkey(self, enc_privkey):
        enc = AES(self._writekey)
        privkey = enc.process(enc_privkey)
        return privkey

    def _populate(self, stuff):
        # the Retrieval object calls this with values it discovers when
        # downloading the slot. This is how a MutableFileNode that was
        # created from a URI learns about its full key.
        pass

    def _populate_pubkey(self, pubkey):
        self._pubkey = pubkey
    def _populate_required_shares(self, required_shares):
        self._required_shares = required_shares
    def _populate_total_shares(self, total_shares):
        self._total_shares = total_shares
    def _populate_seqnum(self, seqnum):
        self._current_seqnum = seqnum
    def _populate_root_hash(self, root_hash):
        self._current_roothash = root_hash

    def _populate_privkey(self, privkey):
        self._privkey = privkey
    def _populate_encprivkey(self, encprivkey):
        self._encprivkey = encprivkey


    def get_write_enabler(self, peerid):
        assert len(peerid) == 20
        return hashutil.ssk_write_enabler_hash(self._writekey, peerid)
    def get_renewal_secret(self, peerid):
        assert len(peerid) == 20
        crs = self._client.get_renewal_secret()
        frs = hashutil.file_renewal_secret_hash(crs, self._storage_index)
        return hashutil.bucket_renewal_secret_hash(frs, peerid)
    def get_cancel_secret(self, peerid):
        assert len(peerid) == 20
        ccs = self._client.get_cancel_secret()
        fcs = hashutil.file_cancel_secret_hash(ccs, self._storage_index)
        return hashutil.bucket_cancel_secret_hash(fcs, peerid)

    def get_writekey(self):
        return self._writekey
    def get_readkey(self):
        return self._readkey
    def get_storage_index(self):
        return self._storage_index
    def get_privkey(self):
        return self._privkey
    def get_encprivkey(self):
        return self._encprivkey
    def get_pubkey(self):
        return self._pubkey

    def get_required_shares(self):
        return self._required_shares
    def get_total_shares(self):
        return self._total_shares


    def get_uri(self):
        return self._uri.to_string()
    def get_size(self):
        return "?" # TODO: this is likely to cause problems, not being an int
    def get_readonly(self):
        if self.is_readonly():
            return self
        ro = MutableFileNode(self._client)
        ro.init_from_uri(self._uri.get_readonly())
        return ro

    def get_readonly_uri(self):
        return self._uri.get_readonly().to_string()

    def is_mutable(self):
        return self._uri.is_mutable()
    def is_readonly(self):
        return self._uri.is_readonly()

    def __hash__(self):
        return hash((self.__class__, self._uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self._uri, them._uri)

    def get_verifier(self):
        return IMutableFileURI(self._uri).get_verifier()

    def obtain_lock(self, res=None):
        # stub, get real version from zooko's #265 patch
        d = defer.Deferred()
        d.callback(res)
        return d

    def release_lock(self, res):
        # stub
        return res

    ############################

    # methods exposed to the higher-layer application

    def update_servermap(self, old_map=None, mode=MODE_ENOUGH):
        servermap = old_map or ServerMap()
        d = self.obtain_lock()
        d.addCallback(lambda res:
                      ServermapUpdater(self, servermap, mode).update())
        d.addBoth(self.release_lock)
        return d

    def download_version(self, servermap, versionid):
        """Returns a Deferred that fires with a string."""
        d = self.obtain_lock()
        d.addCallback(lambda res:
                      Retrieve(self, servermap, versionid).download())
        d.addBoth(self.release_lock)
        return d

    def publish(self, servermap, newdata):
        assert self._pubkey, "update_servermap must be called before publish"
        d = self.obtain_lock()
        d.addCallback(lambda res: Publish(self, servermap).publish(newdata))
        # p = self.publish_class(self)
        # self._client.notify_publish(p)
        d.addBoth(self.release_lock)
        return d

    def modify(self, modifier, *args, **kwargs):
        """I use a modifier callback to apply a change to the mutable file.
        I implement the following pseudocode::

         obtain_mutable_filenode_lock()
         while True:
           update_servermap(MODE_WRITE)
           old = retrieve_best_version()
           new = modifier(old, *args, **kwargs)
           if new == old: break
           try:
             publish(new)
           except UncoordinatedWriteError:
             continue
           break
         release_mutable_filenode_lock()

        The idea is that your modifier function can apply a delta of some
        sort, and it will be re-run as necessary until it succeeds. The
        modifier must inspect the old version to see whether its delta has
        already been applied: if so it should return the contents unmodified.
        """
        NotImplementedError

    #################################

    def check(self):
        verifier = self.get_verifier()
        return self._client.getServiceNamed("checker").check(verifier)

    def download(self, target):
        # fake it. TODO: make this cleaner.
        d = self.download_to_data()
        def _done(data):
            target.open(len(data))
            target.write(data)
            target.close()
            return target.finish()
        d.addCallback(_done)
        return d

    def download_to_data(self):
        d = self.obtain_lock()
        d.addCallback(lambda res: self.update_servermap(mode=MODE_ENOUGH))
        def _updated(smap):
            goal = smap.best_recoverable_version()
            if not goal:
                raise UnrecoverableFileError("no recoverable versions")
            return self.download_version(smap, goal)
        d.addCallback(_updated)
        d.addBoth(self.release_lock)
        return d

    def _publish(self, initial_contents):
        p = Publish(self, None)
        d = p.publish(initial_contents)
        d.addCallback(lambda res: self)
        return d

    def update(self, newdata):
        d = self.obtain_lock()
        d.addCallback(lambda res: self.update_servermap(mode=MODE_WRITE))
        d.addCallback(lambda smap:
                      Publish(self, smap).publish(newdata))
        d.addBoth(self.release_lock)
        return d

    def overwrite(self, newdata):
        return self.update(newdata)


class MutableWatcher(service.MultiService):
    MAX_PUBLISH_STATUSES = 20
    MAX_RETRIEVE_STATUSES = 20
    name = "mutable-watcher"

    def __init__(self, stats_provider=None):
        service.MultiService.__init__(self)
        self.stats_provider = stats_provider
        self._all_publish = weakref.WeakKeyDictionary()
        self._recent_publish_status = []
        self._all_retrieve = weakref.WeakKeyDictionary()
        self._recent_retrieve_status = []

    def notify_publish(self, p):
        self._all_publish[p] = None
        self._recent_publish_status.append(p.get_status())
        if self.stats_provider:
            self.stats_provider.count('mutable.files_published', 1)
            #self.stats_provider.count('mutable.bytes_published', p._node.get_size())
        while len(self._recent_publish_status) > self.MAX_PUBLISH_STATUSES:
            self._recent_publish_status.pop(0)

    def list_all_publish(self):
        return self._all_publish.keys()
    def list_active_publish(self):
        return [p.get_status() for p in self._all_publish.keys()
                if p.get_status().get_active()]
    def list_recent_publish(self):
        return self._recent_publish_status


    def notify_retrieve(self, r):
        self._all_retrieve[r] = None
        self._recent_retrieve_status.append(r.get_status())
        if self.stats_provider:
            self.stats_provider.count('mutable.files_retrieved', 1)
            #self.stats_provider.count('mutable.bytes_retrieved', r._node.get_size())
        while len(self._recent_retrieve_status) > self.MAX_RETRIEVE_STATUSES:
            self._recent_retrieve_status.pop(0)

    def list_all_retrieve(self):
        return self._all_retrieve.keys()
    def list_active_retrieve(self):
        return [p.get_status() for p in self._all_retrieve.keys()
                if p.get_status().get_active()]
    def list_recent_retrieve(self):
        return self._recent_retrieve_status

class ResponseCache:
    """I cache share data, to reduce the number of round trips used during
    mutable file operations. All of the data in my cache is for a single
    storage index, but I will keep information on multiple shares (and
    multiple versions) for that storage index.

    My cache is indexed by a (verinfo, shnum) tuple.

    My cache entries contain a set of non-overlapping byteranges: (start,
    data, timestamp) tuples.
    """

    def __init__(self):
        self.cache = DictOfSets()

    def _does_overlap(self, x_start, x_length, y_start, y_length):
        if x_start < y_start:
            x_start, y_start = y_start, x_start
            x_length, y_length = y_length, x_length
        x_end = x_start + x_length
        y_end = y_start + y_length
        # this just returns a boolean. Eventually we'll want a form that
        # returns a range.
        if not x_length:
            return False
        if not y_length:
            return False
        if x_start >= y_end:
            return False
        if y_start >= x_end:
            return False
        return True


    def _inside(self, x_start, x_length, y_start, y_length):
        x_end = x_start + x_length
        y_end = y_start + y_length
        if x_start < y_start:
            return False
        if x_start >= y_end:
            return False
        if x_end < y_start:
            return False
        if x_end > y_end:
            return False
        return True

    def add(self, verinfo, shnum, offset, data, timestamp):
        index = (verinfo, shnum)
        self.cache.add(index, (offset, data, timestamp) )

    def read(self, verinfo, shnum, offset, length):
        """Try to satisfy a read request from cache.
        Returns (data, timestamp), or (None, None) if the cache did not hold
        the requested data.
        """

        # TODO: join multiple fragments, instead of only returning a hit if
        # we have a fragment that contains the whole request

        index = (verinfo, shnum)
        end = offset+length
        for entry in self.cache.get(index, set()):
            (e_start, e_data, e_timestamp) = entry
            if self._inside(offset, length, e_start, len(e_data)):
                want_start = offset - e_start
                want_end = offset+length - e_start
                return (e_data[want_start:want_end], e_timestamp)
        return None, None

