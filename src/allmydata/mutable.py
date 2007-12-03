
import os, struct
from itertools import islice
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure, log
from foolscap.eventual import eventually
from allmydata.interfaces import IMutableFileNode, IMutableFileURI
from allmydata.util import hashutil, mathutil, idlib
from allmydata.uri import WriteableSSKFileURI
from allmydata.Crypto.Cipher import AES
from allmydata import hashtree, codec
from allmydata.encode import NotEnoughPeersError
from pycryptopp.publickey import rsa


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

PREFIX = ">BQ32s16s" # each version has a different prefix
SIGNED_PREFIX = ">BQ32s16s BBQQ" # this is covered by the signature
HEADER = ">BQ32s16s BBQQ LLLLQQ" # includes offsets
HEADER_LENGTH = struct.calcsize(HEADER)

def unpack_prefix_and_signature(data):
    assert len(data) >= HEADER_LENGTH
    o = {}
    prefix = data[:struct.calcsize(SIGNED_PREFIX)]

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


class Retrieve:
    def __init__(self, filenode):
        self._node = filenode
        self._contents = None
        # if the filenode already has a copy of the pubkey, use it. Otherwise
        # we'll grab a copy from the first peer we talk to.
        self._pubkey = filenode.get_pubkey()
        self._storage_index = filenode.get_storage_index()
        self._readkey = filenode.get_readkey()
        self._last_failure = None

    def log(self, msg):
        prefix = idlib.b2a(self._node.get_storage_index())[:6]
        self._node._client.log("Retrieve(%s): %s" % (prefix, msg))

    def log_err(self, f):
        log.err(f)

    def retrieve(self):
        """Retrieve the filenode's current contents. Returns a Deferred that
        fires with a string when the contents have been retrieved."""

        # 1: make a guess as to how many peers we should send requests to. We
        #    want to hear from k+EPSILON (k because we have to, EPSILON extra
        #    because that helps us resist rollback attacks). [TRADEOFF:
        #    EPSILON>0 means extra work] [TODO: implement EPSILON>0]
        # 2: build the permuted peerlist, taking the first k+E peers
        # 3: send readv requests to all of them in parallel, asking for the
        #    first 2KB of data from all shares
        # 4: when the first of the responses comes back, extract information:
        # 4a: extract the pubkey, hash it, compare against the URI. If this
        #     check fails, log a WEIRD and ignore the peer.
        # 4b: extract the prefix (seqnum, roothash, k, N, segsize, datalength)
        #     and verify the signature on it. If this is wrong, log a WEIRD
        #     and ignore the peer. Save the prefix string in a dict that's
        #     keyed by (seqnum,roothash) and has (prefixstring, sharemap) as
        #     values. We'll use the prefixstring again later to avoid doing
        #     multiple signature checks
        # 4c: extract the share size (offset of the last byte of sharedata).
        #     if it is larger than 2k, send new readv requests to pull down
        #     the extra data
        # 4d: if the extracted 'k' is more than we guessed, rebuild a larger
        #     permuted peerlist and send out more readv requests.
        # 5: as additional responses come back, extract the prefix and compare
        #    against the ones we've already seen. If they match, add the
        #    peerid to the corresponing sharemap dict
        # 6: [TRADEOFF]: if EPSILON==0, when we get k responses for the
        #    same (seqnum,roothash) key, attempt to reconstruct that data.
        #    if EPSILON>0, wait for k+EPSILON responses, then attempt to
        #    reconstruct the most popular version.. If we do not have enough
        #    shares and there are still requests outstanding, wait. If there
        #    are not still requests outstanding (todo: configurable), send
        #    more requests. Never send queries to more than 2*N servers. If
        #    we've run out of servers, fail.
        # 7: if we discover corrupt shares during the reconstruction process,
        #    remove that share from the sharemap.  and start step#6 again.

        self.log("starting retrieval")

        initial_query_count = 5
        self._read_size = 2000

        # we might not know how many shares we need yet.
        self._required_shares = self._node.get_required_shares()
        self._total_shares = self._node.get_total_shares()

        # self._valid_versions is a dictionary in which the keys are
        # 'verinfo' tuples (seqnum, root_hash, IV). Every time we hear about
        # a new potential version of the file, we check its signature, and
        # the valid ones are added to this dictionary. The values of the
        # dictionary are (prefix, sharemap) tuples, where 'prefix' is just
        # the first part of the share (containing the serialized verinfo),
        # for easier comparison. 'sharemap' is a DictOfSets, in which the
        # keys are sharenumbers, and the values are sets of (peerid, data)
        # tuples. There is a (peerid, data) tuple for every instance of a
        # given share that we've seen. The 'data' in this tuple is a full
        # copy of the SDMF share, starting with the \x00 version byte and
        # continuing through the last byte of sharedata.
        self._valid_versions = {}

        # self._valid_shares is a dict mapping (peerid,data) tuples to
        # validated sharedata strings. Each time we examine the hash chains
        # inside a share and validate them against a signed root_hash, we add
        # the share to self._valid_shares . We use this to avoid re-checking
        # the hashes over and over again.
        self._valid_shares = {}

        self._done_deferred = defer.Deferred()

        d = defer.succeed(initial_query_count)
        d.addCallback(self._choose_initial_peers)
        d.addCallback(self._send_initial_requests)
        d.addCallback(self._wait_for_finish)
        return d

    def _wait_for_finish(self, res):
        return self._done_deferred

    def _choose_initial_peers(self, numqueries):
        n = self._node
        full_peerlist = n._client.get_permuted_peers(self._storage_index,
                                                     include_myself=True)
        # _peerlist is a list of (peerid,conn) tuples for peers that are
        # worth talking too. This starts with the first numqueries in the
        # permuted list. If that's not enough to get us a recoverable
        # version, we expand this to include the first 2*total_shares peerids
        # (assuming we learn what total_shares is from one of the first
        # numqueries peers)
        self._peerlist = [(p[1],p[2])
                          for p in islice(full_peerlist, numqueries)]
        # _peerlist_limit is the query limit we used to build this list. If
        # we later increase this limit, it may be useful to re-scan the
        # permuted list.
        self._peerlist_limit = numqueries
        return self._peerlist

    def _send_initial_requests(self, peerlist):
        self._bad_peerids = set()
        self._running = True
        self._queries_outstanding = set()
        self._used_peers = set()
        self._sharemap = DictOfSets() # shnum -> [(peerid, seqnum, R)..]
        self._peer_storage_servers = {}
        dl = []
        for (peerid, conn) in peerlist:
            self._queries_outstanding.add(peerid)
            self._do_query(conn, peerid, self._storage_index, self._read_size,
                           self._peer_storage_servers)

        # control flow beyond this point: state machine. Receiving responses
        # from queries is the input. We might send out more queries, or we
        # might produce a result.
        return None

    def _do_query(self, conn, peerid, storage_index, readsize,
                  peer_storage_servers):
        self._queries_outstanding.add(peerid)
        if peerid in peer_storage_servers:
            d = defer.succeed(peer_storage_servers[peerid])
        else:
            d = conn.callRemote("get_service", "storageserver")
            def _got_storageserver(ss):
                peer_storage_servers[peerid] = ss
                return ss
            d.addCallback(_got_storageserver)
        d.addCallback(lambda ss: ss.callRemote("slot_readv", storage_index,
                                               [], [(0, readsize)]))
        d.addCallback(self._got_results, peerid, readsize)
        d.addErrback(self._query_failed, peerid, (conn, storage_index,
                                                  peer_storage_servers))
        # errors that aren't handled by _query_failed (and errors caused by
        # _query_failed) get logged, but we still want to check for doneness.
        d.addErrback(log.err)
        d.addBoth(self._check_for_done)
        return d

    def _deserialize_pubkey(self, pubkey_s):
        verifier = rsa.create_verifying_key_from_string(pubkey_s)
        return verifier

    def _got_results(self, datavs, peerid, readsize):
        self._queries_outstanding.discard(peerid)
        self._used_peers.add(peerid)
        if not self._running:
            return

        for shnum,datav in datavs.items():
            data = datav[0]
            self.log("_got_results: got shnum #%d from peerid %s"
                     % (shnum, idlib.shortnodeid_b2a(peerid)))
            (seqnum, root_hash, IV, k, N, segsize, datalength,
             pubkey_s, signature, prefix) = unpack_prefix_and_signature(data)

            if not self._pubkey:
                fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
                if fingerprint != self._node._fingerprint:
                    # bad share
                    raise CorruptShareError(peerid, shnum,
                                            "pubkey doesn't match fingerprint")
                self._pubkey = self._deserialize_pubkey(pubkey_s)
                self._node._populate_pubkey(self._pubkey)

            verinfo = (seqnum, root_hash, IV, segsize, datalength)
            if verinfo not in self._valid_versions:
                # it's a new pair. Verify the signature.
                valid = self._pubkey.verify(prefix, signature)
                if not valid:
                    raise CorruptShareError(peerid, shnum,
                                            "signature is invalid")
                # ok, it's a valid verinfo. Add it to the list of validated
                # versions.
                self.log(" found valid version %d-%s from %s-sh%d: %d-%d/%d/%d"
                         % (seqnum, idlib.b2a(root_hash)[:4],
                            idlib.shortnodeid_b2a(peerid), shnum,
                            k, N, segsize, datalength))
                self._valid_versions[verinfo] = (prefix, DictOfSets())

                # and make a note of the other parameters we've just learned
                if self._required_shares is None:
                    self._required_shares = k
                    self._node._populate_required_shares(k)
                if self._total_shares is None:
                    self._total_shares = N
                    self._node._populate_total_shares(N)

            # we've already seen this pair, and checked the signature so we
            # know it's a valid candidate. Accumulate the share info, if
            # there's enough data present. If not, raise NeedMoreDataError,
            # which will trigger a re-fetch.
            _ignored = unpack_share(data)
            self.log(" found enough data to add share contents")
            self._valid_versions[verinfo][1].add(shnum, (peerid, data))


    def _query_failed(self, f, peerid, stuff):
        self._queries_outstanding.discard(peerid)
        self._used_peers.add(peerid)
        if not self._running:
            return
        if f.check(NeedMoreDataError):
            # ah, just re-send the query then.
            self._read_size = max(self._read_size, f.value.needed_bytes)
            (conn, storage_index, peer_storage_servers) = stuff
            self._do_query(conn, peerid, storage_index, self._read_size,
                           peer_storage_servers)
            return
        self._last_failure = f
        self._bad_peerids.add(peerid)
        short_sid = idlib.b2a(self._storage_index)[:6]
        if f.check(CorruptShareError):
            self.log("WEIRD: bad share for %s: %s %s" % (short_sid, f,
                                                         f.value))
        else:
            self.log("WEIRD: other error for %s: %s %s" % (short_sid, f,
                                                           f.value))

    def _check_for_done(self, res):
        if not self._running:
            return
        share_prefixes = {}
        versionmap = DictOfSets()
        for verinfo, (prefix, sharemap) in self._valid_versions.items():
            # sharemap is a dict that maps shnums to sets of (peerid,data).
            # len(sharemap) is the number of distinct shares that appear to
            # be available.
            if len(sharemap) >= self._required_shares:
                # this one looks retrievable. TODO: our policy of decoding
                # the first version that we can get is a bit troublesome: in
                # a small grid with a large expansion factor, a single
                # out-of-date server can cause us to retrieve an older
                # version. Fixing this is equivalent to protecting ourselves
                # against a rollback attack, and the best approach is
                # probably to say that we won't do _attempt_decode until:
                #  (we've received at least k+EPSILON shares or
                #   we've received at least k shares and ran out of servers)
                # in that case, identify the verinfos that are decodeable and
                # attempt the one with the highest (seqnum,R) value. If the
                # highest seqnum can't be recovered, only then might we fall
                # back to an older version.
                d = defer.maybeDeferred(self._attempt_decode, verinfo, sharemap)
                def _problem(f):
                    self._last_failure = f
                    if f.check(CorruptShareError):
                        self.log("WEIRD: saw corrupt share, rescheduling")
                        # _attempt_decode is responsible for removing the bad
                        # share, so we can just try again
                        eventually(self._check_for_done, None)
                        return
                    return f
                d.addCallbacks(self._done, _problem)
                # TODO: create an errback-routing mechanism to make sure that
                # weird coding errors will cause the retrieval to fail rather
                # than hanging forever. Any otherwise-unhandled exceptions
                # should follow this path.
                return

        # we don't have enough shares yet. Should we send out more queries?
        if self._queries_outstanding:
            # there are some running, so just wait for them to come back.
            # TODO: if our initial guess at k was too low, waiting for these
            # responses before sending new queries will increase our latency,
            # so we could speed things up by sending new requests earlier.
            return

        # no more queries are outstanding. Can we send out more? First,
        # should we be looking at more peers?
        if self._total_shares is not None:
            search_distance = self._total_shares * 2
        else:
            search_distance = 20
        if self._peerlist_limit < search_distance:
            # we might be able to get some more peers from the list
            peers = self._node._client.get_permuted_peers(self._storage_index,
                                                          include_myself=True)
            self._peerlist = [(p[1],p[2])
                              for p in islice(peers, search_distance)]
            self._peerlist_limit = search_distance
        # are there any peers on the list that we haven't used?
        new_query_peers = []
        for (peerid, conn) in self._peerlist:
            if peerid not in self._used_peers:
                new_query_peers.append( (peerid, conn) )
                if len(new_query_peers) > 5:
                    # only query in batches of 5. TODO: this is pretty
                    # arbitrary, really I want this to be something like
                    # k - max(known_version_sharecounts) + some extra
                    break
        if new_query_peers:
            for (peerid, conn) in new_query_peers:
                self._do_query(conn, peerid,
                               self._storage_index, self._read_size,
                               self._peer_storage_servers)
            # we'll retrigger when those queries come back
            return

        # we've used up all the peers we're allowed to search. Failure.
        e = NotEnoughPeersError("last failure: %s" % self._last_failure)
        return self._done(failure.Failure(e))

    def _attempt_decode(self, verinfo, sharemap):
        # sharemap is a dict which maps shnum to [(peerid,data)..] sets.
        (seqnum, root_hash, IV, segsize, datalength) = verinfo

        assert len(sharemap) >= self._required_shares, len(sharemap)

        shares_s = []
        for shnum in sorted(sharemap.keys()):
            for shareinfo in sharemap[shnum]:
                shares_s.append("#%d" % shnum)
        shares_s = ",".join(shares_s)
        self.log("_attempt_decode: version %d-%s, shares: %s" %
                 (seqnum, idlib.b2a(root_hash)[:4], shares_s))

        # first, validate each share that we haven't validated yet. We use
        # self._valid_shares to remember which ones we've already checked.

        shares = {}
        for shnum, shareinfos in sharemap.items():
            assert len(shareinfos) > 0
            for shareinfo in shareinfos:
                # have we already validated the hashes on this share?
                if shareinfo not in self._valid_shares:
                    # nope: must check the hashes and extract the actual data
                    (peerid,data) = shareinfo
                    try:
                        # The (seqnum+root_hash+IV) tuple for this share was
                        # already verified: specifically, all shares in the
                        # sharemap have a (seqnum+root_hash+IV) pair that was
                        # present in a validly signed prefix. The remainder
                        # of the prefix for this particular share has *not*
                        # been validated, but we don't care since we don't
                        # use it. self._validate_share() is required to check
                        # the hashes on the share data (and hash chains) to
                        # make sure they match root_hash, but is not required
                        # (and is in fact prohibited, because we don't
                        # validate the prefix on all shares) from using
                        # anything else in the share.
                        validator = self._validate_share_and_extract_data
                        sharedata = validator(peerid, root_hash, shnum, data)
                        assert isinstance(sharedata, str)
                    except CorruptShareError, e:
                        self.log("WEIRD: share was corrupt: %s" % e)
                        sharemap[shnum].discard(shareinfo)
                        if not sharemap[shnum]:
                            # remove the key so the test in _check_for_done
                            # can accurately decide that we don't have enough
                            # shares to try again right now.
                            del sharemap[shnum]
                        # If there are enough remaining shares,
                        # _check_for_done() will try again
                        raise
                    # share is valid: remember it so we won't need to check
                    # (or extract) it again
                    self._valid_shares[shareinfo] = sharedata

                # the share is now in _valid_shares, so just copy over the
                # sharedata
                shares[shnum] = self._valid_shares[shareinfo]

        # now that the big loop is done, all shares in the sharemap are
        # valid, and they're all for the same seqnum+root_hash version, so
        # it's now down to doing FEC and decrypt.
        assert len(shares) >= self._required_shares, len(shares)
        d = defer.maybeDeferred(self._decode, shares, segsize, datalength)
        d.addCallback(self._decrypt, IV, seqnum, root_hash)
        return d

    def _validate_share_and_extract_data(self, peerid, root_hash, shnum, data):
        # 'data' is the whole SMDF share
        self.log("_validate_share_and_extract_data[%d]" % shnum)
        assert data[0] == "\x00"
        pieces = unpack_share(data)
        (seqnum, root_hash, IV, k, N, segsize, datalen,
         pubkey, signature, share_hash_chain, block_hash_tree,
         share_data, enc_privkey) = pieces

        assert isinstance(share_data, str)
        # build the block hash tree. SDMF has only one leaf.
        leaves = [hashutil.block_hash(share_data)]
        t = hashtree.HashTree(leaves)
        if list(t) != block_hash_tree:
            raise CorruptShareError(peerid, shnum, "block hash tree failure")
        share_hash_leaf = t[0]
        # t2 = hashtree.IncompleteHashTree()
        # TODO: use shnum, share_hash_leaf, share_hash_chain to compare against
        # root_hash
        #if False:
        #    raise CorruptShareError("explanation")
        self.log(" data valid! len=%d" % len(share_data))
        return share_data

    def _decode(self, shares_dict, segsize, datalength):
        # we ought to know these values by now
        assert self._required_shares is not None
        assert self._total_shares is not None

        # shares_dict is a dict mapping shnum to share data, but the codec
        # wants two lists.
        shareids = []; shares = []
        for shareid, share in shares_dict.items():
            shareids.append(shareid)
            shares.append(share)

        assert len(shareids) >= self._required_shares, len(shareids)
        # zfec really doesn't want extra shares
        shareids = shareids[:self._required_shares]
        shares = shares[:self._required_shares]

        fec = codec.CRSDecoder()
        params = "%d-%d-%d" % (segsize,
                               self._required_shares, self._total_shares)
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

    def _decrypt(self, crypttext, IV, seqnum, root_hash):
        key = hashutil.ssk_readkey_data_hash(IV, self._readkey)
        decryptor = AES.new(key=key, mode=AES.MODE_CTR, counterstart="\x00"*16)
        plaintext = decryptor.decrypt(crypttext)
        # it worked, so record the seqnum and root_hash for next time
        self._node._populate_seqnum(seqnum)
        self._node._populate_root_hash(root_hash)
        return plaintext

    def _done(self, contents):
        self.log("DONE, contents: %r" % contents)
        self._running = False
        eventually(self._done_deferred.callback, contents)



class DictOfSets(dict):
    def add(self, key, value):
        if key in self:
            self[key].add(value)
        else:
            self[key] = set([value])

class Publish:
    """I represent a single act of publishing the mutable file to the grid."""

    def __init__(self, filenode):
        self._node = filenode

    def log(self, msg):
        prefix = idlib.b2a(self._node.get_storage_index())[:6]
        self._node._client.log("Publish(%s): %s" % (prefix, msg))

    def log_err(self, f):
        log.err(f)

    def publish(self, newdata, wait_for_numpeers=None):
        """Publish the filenode's current contents.  Returns a Deferred that
        fires (with None) when the publish has done as much work as it's ever
        going to do, or errbacks with ConsistencyError if it detects a
        simultaneous write.

        It will wait until at least wait_for_numpeers peers are connected
        before it starts uploading

        If wait_for_numpeers is None, then wait_for_numpeers is set to the
        number of shares total (M).
        """

        self.log("starting publish")

        if wait_for_numpeers is None:
            # TODO: perhaps the default should be something like:
            # wait_for_numpeers = self._node.get_total_shares()
            wait_for_numpeers = 1

        d = self._node._client.introducer_client.when_enough_peers(wait_for_numpeers)
        d.addCallback(lambda dummy: self._after_enough_peers(newdata))
        return d

    def _after_enough_peers(self, newdata):
        # 1: generate shares (SDMF: files are small, so we can do it in RAM)
        # 2: perform peer selection, get candidate servers
        #  2a: send queries to n+epsilon servers, to determine current shares
        #  2b: based upon responses, create target map
        # 3: send slot_testv_and_readv_and_writev messages
        # 4: as responses return, update share-dispatch table
        # 4a: may need to run recovery algorithm
        # 5: when enough responses are back, we're done

        self.log("got enough peers, datalen is %s" % len(newdata))

        self._storage_index = self._node.get_storage_index()
        self._writekey = self._node.get_writekey()
        assert self._writekey, "need write capability to publish"

        old_roothash = self._node._current_roothash
        old_seqnum = self._node._current_seqnum
        assert old_seqnum is not None, "must read before replace"
        self._new_seqnum = old_seqnum + 1

        # read-before-replace also guarantees these fields are available
        readkey = self._node.get_readkey()
        required_shares = self._node.get_required_shares()
        total_shares = self._node.get_total_shares()
        self._pubkey = self._node.get_pubkey()

        # these two may not be, we might have to get them from the first peer
        self._privkey = self._node.get_privkey()
        self._encprivkey = self._node.get_encprivkey()

        IV = os.urandom(16)

        # we read only 1KB because all we generally care about is the seqnum
        # ("prefix") info, so we know which shares are where. We need to get
        # the privkey from somebody, which means reading more like 3KB, but
        # the code in _obtain_privkey will ensure that we manage that even if
        # we need an extra roundtrip. TODO: arrange to read 3KB from one peer
        # who is likely to hold a share (like, say, ourselves), so we can
        # avoid the latency of that extra roundtrip.
        self._read_size = 1000

        d = defer.succeed(total_shares)
        d.addCallback(self._query_peers)
        d.addCallback(self._obtain_privkey)

        d.addCallback(self._encrypt_and_encode, newdata, readkey, IV,
                      required_shares, total_shares)
        d.addCallback(self._generate_shares, self._new_seqnum, IV)

        d.addCallback(self._send_shares, IV)
        d.addCallback(self._maybe_recover)
        d.addCallback(lambda res: None)
        return d

    def _query_peers(self, total_shares):
        self.log("_query_peers")

        storage_index = self._storage_index

        # we need to include ourselves in the list for two reasons. The most
        # important is so that any shares which already exist on our own
        # server get updated. The second is to ensure that we leave a share
        # on our own server, so we're more likely to have the signing key
        # around later. This way, even if all the servers die and the
        # directory contents are unrecoverable, at least we can still push
        # out a new copy with brand-new contents. TODO: it would be nice if
        # the share we use for ourselves didn't count against the N total..
        # maybe use N+1 if we find ourselves in the permuted list?

        peerlist = self._node._client.get_permuted_peers(storage_index,
                                                         include_myself=True)

        current_share_peers = DictOfSets()
        reachable_peers = {}
        # list of (peerid, offset, length) where the encprivkey might be found
        self._encprivkey_shares = []

        EPSILON = total_shares / 2
        partial_peerlist = islice(peerlist, total_shares + EPSILON)
        peer_storage_servers = {}
        dl = []
        for (permutedid, peerid, conn) in partial_peerlist:
            d = self._do_query(conn, peerid, peer_storage_servers,
                               storage_index)
            d.addCallback(self._got_query_results,
                          peerid, permutedid,
                          reachable_peers, current_share_peers)
            dl.append(d)
        d = defer.DeferredList(dl)
        d.addCallback(self._got_all_query_results,
                      total_shares, reachable_peers,
                      current_share_peers, peer_storage_servers)
        # TODO: add an errback to, probably to ignore that peer
        return d

    def _do_query(self, conn, peerid, peer_storage_servers, storage_index):
        d = conn.callRemote("get_service", "storageserver")
        def _got_storageserver(ss):
            peer_storage_servers[peerid] = ss
            return ss.callRemote("slot_readv",
                                 storage_index, [], [(0, self._read_size)])
        d.addCallback(_got_storageserver)
        return d

    def _got_query_results(self, datavs, peerid, permutedid,
                           reachable_peers, current_share_peers):

        self.log("_got_query_results from %s" % idlib.shortnodeid_b2a(peerid))
        assert isinstance(datavs, dict)
        reachable_peers[peerid] = permutedid
        for shnum, datav in datavs.items():
            self.log(" peer has shnum %d" % shnum)
            assert len(datav) == 1
            data = datav[0]
            # We want (seqnum, root_hash, IV) from all servers to know what
            # versions we are replacing. We want the encprivkey from one
            # server (assuming it's valid) so we know our own private key, so
            # we can sign our update. SMDF: read the whole share from each
            # server. TODO: later we can optimize this to transfer less data.

            # we assume that we have enough data to extract the signature.
            # TODO: if this raises NeedMoreDataError, arrange to do another
            # read pass.
            r = unpack_prefix_and_signature(data)
            (seqnum, root_hash, IV, k, N, segsize, datalen,
             pubkey_s, signature, prefix) = r

            # self._pubkey is present because we require read-before-replace
            valid = self._pubkey.verify(prefix, signature)
            if not valid:
                self.log("WEIRD: bad signature from %s shnum %d" %
                         (shnum, idlib.shortnodeid_b2a(peerid)))
                continue

            share = (shnum, seqnum, root_hash)
            current_share_peers.add(shnum, (peerid, seqnum, root_hash) )

            if not self._privkey:
                self._try_to_extract_privkey(data, peerid, shnum)


    def _try_to_extract_privkey(self, data, peerid, shnum):
        try:
            r = unpack_share(data)
        except NeedMoreDataError, e:
            # this share won't help us. oh well.
            offset = e.encprivkey_offset
            length = e.encprivkey_length
            self.log("shnum %d on peerid %s: share was too short "
                     "to get the encprivkey, but [%d:%d] ought to hold it" %
                     (shnum, idlib.shortnodeid_b2a(peerid),
                      offset, offset+length))

            self._encprivkey_shares.append( (peerid, shnum, offset, length) )
            return

        (seqnum, root_hash, IV, k, N, segsize, datalen,
         pubkey, signature, share_hash_chain, block_hash_tree,
         share_data, enc_privkey) = r

        return self._try_to_validate_privkey(enc_privkey, peerid, shnum)

    def _try_to_validate_privkey(self, enc_privkey, peerid, shnum):
        alleged_privkey_s = self._node._decrypt_privkey(enc_privkey)
        alleged_writekey = hashutil.ssk_writekey_hash(alleged_privkey_s)
        if alleged_writekey != self._writekey:
            self.log("WEIRD: invalid privkey from %s shnum %d" %
                     (idlib.nodeid_b2a(peerid)[:8], shnum))
            return

        # it's good
        self.log("got valid privkey from shnum %d on peerid %s" %
                 (shnum, idlib.shortnodeid_b2a(peerid)))
        self._privkey = rsa.create_signing_key_from_string(alleged_privkey_s)
        self._encprivkey = enc_privkey
        self._node._populate_encprivkey(self._encprivkey)
        self._node._populate_privkey(self._privkey)

    def _got_all_query_results(self, res,
                               total_shares, reachable_peers,
                               current_share_peers, peer_storage_servers):
        self.log("_got_all_query_results")
        # now that we know everything about the shares currently out there,
        # decide where to place the new shares.

        # if an old share X is on a node, put the new share X there too.
        # TODO: 1: redistribute shares to achieve one-per-peer, by copying
        #       shares from existing peers to new (less-crowded) ones. The
        #       old shares must still be updated.
        # TODO: 2: move those shares instead of copying them, to reduce future
        #       update work

        shares_needing_homes = range(total_shares)
        target_map = DictOfSets() # maps shnum to set((peerid,oldseqnum,oldR))
        shares_per_peer = DictOfSets()
        for shnum in range(total_shares):
            for oldplace in current_share_peers.get(shnum, []):
                (peerid, seqnum, R) = oldplace
                if seqnum >= self._new_seqnum:
                    raise UncoordinatedWriteError()
                target_map.add(shnum, oldplace)
                shares_per_peer.add(peerid, shnum)
                if shnum in shares_needing_homes:
                    shares_needing_homes.remove(shnum)

        # now choose homes for the remaining shares. We prefer peers with the
        # fewest target shares, then peers with the lowest permuted index. If
        # there are no shares already in place, this will assign them
        # one-per-peer in the normal permuted order.
        while shares_needing_homes:
            if not reachable_peers:
                prefix = idlib.b2a(self._node.get_storage_index())[:6]
                raise NotEnoughPeersError("ran out of peers during upload of (%s); shares_needing_homes: %s, reachable_peers: %s" % (prefix, shares_needing_homes, reachable_peers,))
            shnum = shares_needing_homes.pop(0)
            possible_homes = reachable_peers.keys()
            possible_homes.sort(lambda a,b:
                                cmp( (len(shares_per_peer.get(a, [])),
                                      reachable_peers[a]),
                                     (len(shares_per_peer.get(b, [])),
                                      reachable_peers[b]) ))
            target_peerid = possible_homes[0]
            target_map.add(shnum, (target_peerid, None, None) )
            shares_per_peer.add(target_peerid, shnum)

        assert not shares_needing_homes

        target_info = (target_map, shares_per_peer, peer_storage_servers)
        return target_info

    def _obtain_privkey(self, target_info):
        # make sure we've got a copy of our private key.
        if self._privkey:
            # Must have picked it up during _query_peers. We're good to go.
            return target_info

        # Nope, we haven't managed to grab a copy, and we still need it. Ask
        # peers one at a time until we get a copy. Only bother asking peers
        # who've admitted to holding a share.

        target_map, shares_per_peer, peer_storage_servers = target_info
        # pull shares from self._encprivkey_shares
        if not self._encprivkey_shares:
            raise NotEnoughPeersError("Unable to find a copy of the privkey")

        (peerid, shnum, offset, length) = self._encprivkey_shares.pop(0)
        self.log("trying to obtain privkey from %s shnum %d" %
                 (idlib.shortnodeid_b2a(peerid), shnum))
        d = self._do_privkey_query(peer_storage_servers[peerid], peerid,
                                   shnum, offset, length)
        d.addErrback(self.log_err)
        d.addCallback(lambda res: self._obtain_privkey(target_info))
        return d

    def _do_privkey_query(self, rref, peerid, shnum, offset, length):
        d = rref.callRemote("slot_readv", self._storage_index,
                            [shnum], [(offset, length)] )
        d.addCallback(self._privkey_query_response, peerid, shnum)
        return d

    def _privkey_query_response(self, datav, peerid, shnum):
        data = datav[shnum][0]
        self._try_to_validate_privkey(data, peerid, shnum)

    def _encrypt_and_encode(self, target_info,
                            newdata, readkey, IV,
                            required_shares, total_shares):
        self.log("_encrypt_and_encode")

        key = hashutil.ssk_readkey_data_hash(IV, readkey)
        enc = AES.new(key=key, mode=AES.MODE_CTR, counterstart="\x00"*16)
        crypttext = enc.encrypt(newdata)
        assert len(crypttext) == len(newdata)

        # now apply FEC
        self.MAX_SEGMENT_SIZE = 1024*1024
        data_length = len(crypttext)

        segment_size = min(self.MAX_SEGMENT_SIZE, len(crypttext))
        # this must be a multiple of self.required_shares
        segment_size = mathutil.next_multiple(segment_size, required_shares)
        if segment_size:
            self.num_segments = mathutil.div_ceil(len(crypttext), segment_size)
        else:
            self.num_segments = 0
        assert self.num_segments in [0, 1,] # SDMF restrictions
        fec = codec.CRSEncoder()
        fec.set_params(segment_size, required_shares, total_shares)
        piece_size = fec.get_block_size()
        crypttext_pieces = [None] * required_shares
        for i in range(len(crypttext_pieces)):
            offset = i * piece_size
            piece = crypttext[offset:offset+piece_size]
            piece = piece + "\x00"*(piece_size - len(piece)) # padding
            crypttext_pieces[i] = piece
            assert len(piece) == piece_size

        d = fec.encode(crypttext_pieces)
        d.addCallback(lambda shares_and_shareids:
                      (shares_and_shareids,
                       required_shares, total_shares,
                       segment_size, data_length,
                       target_info) )
        return d

    def _generate_shares(self, (shares_and_shareids,
                                required_shares, total_shares,
                                segment_size, data_length,
                                target_info),
                         seqnum, IV):
        self.log("_generate_shares")

        # we should know these by now
        privkey = self._privkey
        encprivkey = self._encprivkey
        pubkey = self._pubkey

        (shares, share_ids) = shares_and_shareids

        assert len(shares) == len(share_ids)
        assert len(shares) == total_shares
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
        for shnum in range(total_shares):
            needed_hashes = share_hash_tree.needed_hashes(shnum)
            share_hash_chain[shnum] = dict( [ (i, share_hash_tree[i])
                                              for i in needed_hashes ] )
        root_hash = share_hash_tree[0]
        assert len(root_hash) == 32
        self.log("my new root_hash is %s" % idlib.b2a(root_hash))

        prefix = pack_prefix(seqnum, root_hash, IV,
                             required_shares, total_shares,
                             segment_size, data_length)

        # now pack the beginning of the share. All shares are the same up
        # to the signature, then they have divergent share hash chains,
        # then completely different block hash trees + IV + share data,
        # then they all share the same encprivkey at the end. The sizes
        # of everything are the same for all shares.

        signature = privkey.sign(prefix)

        verification_key = pubkey.serialize()

        final_shares = {}
        for shnum in range(total_shares):
            final_share = pack_share(prefix,
                                     verification_key,
                                     signature,
                                     share_hash_chain[shnum],
                                     block_hash_trees[shnum],
                                     all_shares[shnum],
                                     encprivkey)
            final_shares[shnum] = final_share
        return (seqnum, root_hash, final_shares, target_info)


    def _send_shares(self, (seqnum, root_hash, final_shares, target_info), IV):
        self.log("_send_shares")
        # we're finally ready to send out our shares. If we encounter any
        # surprises here, it's because somebody else is writing at the same
        # time. (Note: in the future, when we remove the _query_peers() step
        # and instead speculate about [or remember] which shares are where,
        # surprises here are *not* indications of UncoordinatedWriteError,
        # and we'll need to respond to them more gracefully.

        target_map, shares_per_peer, peer_storage_servers = target_info

        my_checkstring = pack_checkstring(seqnum, root_hash, IV)
        peer_messages = {}
        expected_old_shares = {}

        for shnum, peers in target_map.items():
            for (peerid, old_seqnum, old_root_hash) in peers:
                testv = [(0, len(my_checkstring), "le", my_checkstring)]
                new_share = final_shares[shnum]
                writev = [(0, new_share)]
                if peerid not in peer_messages:
                    peer_messages[peerid] = {}
                peer_messages[peerid][shnum] = (testv, writev, None)
                if peerid not in expected_old_shares:
                    expected_old_shares[peerid] = {}
                expected_old_shares[peerid][shnum] = (old_seqnum, old_root_hash)

        read_vector = [(0, len(my_checkstring))]

        dl = []
        # ok, send the messages!
        self._surprised = False
        dispatch_map = DictOfSets()

        for peerid, tw_vectors in peer_messages.items():

            write_enabler = self._node.get_write_enabler(peerid)
            renew_secret = self._node.get_renewal_secret(peerid)
            cancel_secret = self._node.get_cancel_secret(peerid)
            secrets = (write_enabler, renew_secret, cancel_secret)

            d = self._do_testreadwrite(peerid, peer_storage_servers, secrets,
                                       tw_vectors, read_vector)
            d.addCallback(self._got_write_answer, tw_vectors, my_checkstring,
                          peerid, expected_old_shares[peerid], dispatch_map)
            dl.append(d)

        d = defer.DeferredList(dl)
        d.addCallback(lambda res: (self._surprised, dispatch_map))
        return d

    def _do_testreadwrite(self, peerid, peer_storage_servers, secrets,
                          tw_vectors, read_vector):
        conn = peer_storage_servers[peerid]
        storage_index = self._node._uri.storage_index

        d = conn.callRemote("slot_testv_and_readv_and_writev",
                            storage_index,
                            secrets,
                            tw_vectors,
                            read_vector)
        return d

    def _got_write_answer(self, answer, tw_vectors, my_checkstring,
                          peerid, expected_old_shares,
                          dispatch_map):
        self.log("_got_write_answer from %s" % idlib.shortnodeid_b2a(peerid))
        wrote, read_data = answer
        surprised = False

        (new_seqnum,new_root_hash,new_IV) = unpack_checkstring(my_checkstring)

        if wrote:
            for shnum in tw_vectors:
                dispatch_map.add(shnum, (peerid, new_seqnum, new_root_hash))
        else:
            # surprise! our testv failed, so the write did not happen
            surprised = True

        for shnum, (old_cs,) in read_data.items():
            (old_seqnum, old_root_hash, IV) = unpack_checkstring(old_cs)

            if not wrote:
                dispatch_map.add(shnum, (peerid, old_seqnum, old_root_hash))

            if shnum not in expected_old_shares:
                # surprise! there was a share we didn't know about
                surprised = True
            else:
                seqnum, root_hash = expected_old_shares[shnum]
                if seqnum is not None:
                    if seqnum != old_seqnum or root_hash != old_root_hash:
                        # surprise! somebody modified the share on us
                        surprised = True
        if surprised:
            self._surprised = True

    def _log_dispatch_map(self, dispatch_map):
        for shnum, places in dispatch_map.items():
            sent_to = [(idlib.shortnodeid_b2a(peerid),
                        seqnum,
                        idlib.b2a(root_hash)[:4])
                       for (peerid,seqnum,root_hash) in places]
            self.log(" share %d sent to: %s" % (shnum, sent_to))

    def _maybe_recover(self, (surprised, dispatch_map)):
        self.log("_maybe_recover, surprised=%s, dispatch_map:" % surprised)
        self._log_dispatch_map(dispatch_map)
        if not surprised:
            self.log(" no recovery needed")
            return
        print "RECOVERY NOT YET IMPLEMENTED"
        # but dispatch_map will help us do it
        raise UncoordinatedWriteError("I was surprised!")


# use client.create_mutable_file() to make one of these

class MutableFileNode:
    implements(IMutableFileNode)
    publish_class = Publish
    retrieve_class = Retrieve
    SIGNATURE_KEY_SIZE = 2048

    def __init__(self, client):
        self._client = client
        self._pubkey = None # filled in upon first read
        self._privkey = None # filled in if we're mutable
        self._required_shares = None # ditto
        self._total_shares = None # ditto
        self._sharemap = {} # known shares, shnum-to-[nodeids]

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

    def create(self, initial_contents, wait_for_numpeers=None):
        """Call this when the filenode is first created. This will generate
        the keys, generate the initial shares, wait until at least numpeers
        are connected, allocate shares, and upload the initial
        contents. Returns a Deferred that fires (with the MutableFileNode
        instance you should use) when it completes.
        """
        self._required_shares = 3
        self._total_shares = 10
        d = defer.maybeDeferred(self._generate_pubprivkeys)
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
            return self._publish(initial_contents, wait_for_numpeers=wait_for_numpeers)
        d.addCallback(_generated)
        return d

    def _generate_pubprivkeys(self):
        # RSA key generation for a 2048 bit key takes between 0.8 and 3.2 secs
        signer = rsa.generate(self.SIGNATURE_KEY_SIZE)
        verifier = signer.get_verifying_key()
        return verifier, signer

    def _publish(self, initial_contents, wait_for_numpeers):
        p = self.publish_class(self)
        d = p.publish(initial_contents, wait_for_numpeers=wait_for_numpeers)
        d.addCallback(lambda res: self)
        return d

    def _encrypt_privkey(self, writekey, privkey):
        enc = AES.new(key=writekey, mode=AES.MODE_CTR, counterstart="\x00"*16)
        crypttext = enc.encrypt(privkey)
        return crypttext

    def _decrypt_privkey(self, enc_privkey):
        enc = AES.new(key=self._writekey, mode=AES.MODE_CTR, counterstart="\x00"*16)
        privkey = enc.decrypt(enc_privkey)
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
        return hash((self.__class__, self.uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.uri, them.uri)

    def get_verifier(self):
        return IMutableFileURI(self._uri).get_verifier()

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
        r = Retrieve(self)
        return r.retrieve()

    def replace(self, newdata, wait_for_numpeers=None):
        r = Retrieve(self)
        d = r.retrieve()
        d.addCallback(lambda res: self._publish(newdata, wait_for_numpeers=wait_for_numpeers))
        return d
