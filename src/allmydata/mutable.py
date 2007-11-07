
import os, struct
from itertools import islice
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from foolscap.eventual import eventually
from allmydata.interfaces import IMutableFileNode, IMutableFileURI
from allmydata.util import hashutil, mathutil, idlib
from allmydata.uri import WriteableSSKFileURI
from allmydata.Crypto.Cipher import AES
from allmydata import hashtree, codec
from allmydata.encode import NotEnoughPeersError


class NeedMoreDataError(Exception):
    def __init__(self, needed_bytes):
        Exception.__init__(self)
        self.needed_bytes = needed_bytes

class UncoordinatedWriteError(Exception):
    pass

class CorruptShareError(Exception):
    def __init__(self, peerid, shnum, reason):
        self.peerid = peerid
        self.shnum = shnum
        self.reason = reason
    def __repr__(self):
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
        raise NeedMoreDataError(o['share_hash_chain'])

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
        raise NeedMoreDataError(o['EOF'])

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

class Retrieve:
    def __init__(self, filenode):
        self._node = filenode
        self._contents = None
        # if the filenode already has a copy of the pubkey, use it. Otherwise
        # we'll grab a copy from the first peer we talk to.
        self._pubkey = filenode.get_pubkey()
        self._storage_index = filenode.get_storage_index()
        self._readkey = filenode.get_readkey()

    def log(self, msg):
        self._node._client.log(msg)

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

        initial_query_count = 5
        self._read_size = 2000

        # we might not know how many shares we need yet.
        self._required_shares = self._node.get_required_shares()
        self._total_shares = self._node.get_total_shares()
        self._segsize = None
        self._datalength = None

        d = defer.succeed(initial_query_count)
        d.addCallback(self._choose_initial_peers)
        d.addCallback(self._send_initial_requests)
        d.addCallback(lambda res: self._contents)
        return d

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
        self._sharemap = DictOfSets() # shnum -> [(peerid, seqnum, R)..]
        self._peer_storage_servers = {}
        dl = []
        for (permutedid, peerid, conn) in peerlist:
            self._queries_outstanding.add(peerid)
            self._do_query(conn, peerid, self._storage_index, self._read_size,
                           self._peer_storage_servers)

        # control flow beyond this point: state machine. Receiving responses
        # from queries is the input. We might send out more queries, or we
        # might produce a result.

        d = self._done_deferred = defer.Deferred()
        return d

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
        return d

    def _deserialize_pubkey(self, pubkey_s):
        # TODO
        return None

    def _validate_share(self, root_hash, shnum, data):
        if False:
            raise CorruptShareError("explanation")
        pass

    def _got_results(self, datavs, peerid, readsize):
        self._queries_outstanding.discard(peerid)
        self._used_peers.add(peerid)
        if not self._running:
            return

        for shnum,datav in datavs.items():
            data = datav[0]
            (seqnum, root_hash, IV, k, N, segsize, datalength,
             pubkey_s, signature, prefix) = unpack_prefix_and_signature(data)

            if not self._pubkey:
                fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey_s)
                if fingerprint != self._node._fingerprint:
                    # bad share
                    raise CorruptShareError(peerid,
                                            "pubkey doesn't match fingerprint")
                self._pubkey = self._deserialize_pubkey(pubkey_s)

            verinfo = (seqnum, root_hash, IV)
            if verinfo not in self._valid_versions:
                # it's a new pair. Verify the signature.
                valid = self._pubkey.verify(prefix, signature)
                if not valid:
                    raise CorruptShareError(peerid,
                                            "signature is invalid")
                # ok, it's a valid verinfo. Add it to the list of validated
                # versions.
                self._valid_versions[verinfo] = (prefix, DictOfSets())

                # and make a note of the other parameters we've just learned
                if self._required_shares is None:
                    self._required_shares = k
                if self._total_shares is None:
                    self._total_shares = N
                if self._segsize is None:
                    self._segsize = segsize
                if self._datalength is None:
                    self._datalength = datalength

            # we've already seen this pair, and checked the signature so we
            # know it's a valid candidate. Accumulate the share info, if
            # there's enough data present. If not, raise NeedMoreDataError,
            # which will trigger a re-fetch.
            _ignored = unpack_share(data)
            self._valid_versions[verinfo][1].add(shnum, (peerid, data))

        self._check_for_done()


    def _query_failed(self, f, peerid, stuff):
        self._queries_outstanding.discard(peerid)
        self._used_peers.add(peerid)
        if not self._running:
            return
        if f.check(NeedMoreDataError):
            # ah, just re-send the query then.
            self._read_size = max(self._read_size, f.needed_bytes)
            (conn, storage_index, peer_storage_servers) = stuff
            self._do_query(conn, peerid, storage_index, self._read_size,
                           peer_storage_servers)
            return
        self._bad_peerids.add(peerid)
        short_sid = idlib.a2b(self.storage_index)[:6]
        if f.check(CorruptShareError):
            self.log("WEIRD: bad share for %s: %s" % (short_sid, f))
        else:
            self.log("WEIRD: other error for %s: %s" % (short_sid, f))
        self._check_for_done()

    def _check_for_done(self):
        share_prefixes = {}
        versionmap = DictOfSets()
        for verinfo, (prefix, sharemap) in self._valid_versions.items():
            if len(sharemap) >= self._required_shares:
                # this one looks retrievable
                d = defer.maybeDeferred(self._extract_data, verinfo, sharemap)
                def _problem(f):
                    if f.check(CorruptShareError):
                        # log(WEIRD)
                        # _extract_data is responsible for removing the bad
                        # share, so we can just try again
                        eventually(self._check_for_done)
                        return
                    return f
                d.addCallbacks(self._done, _problem)
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
        return self._done(failure.Failure(NotEnoughPeersError()))

    def _extract_data(self, verinfo, sharemap):
        # sharemap is a dict which maps shnum to [(peerid,data)..] sets.
        (seqnum, root_hash, IV) = verinfo

        # first, validate each share that we haven't validated yet. We use
        # self._valid_shares to remember which ones we've already checked.

        self._valid_shares = set()  # set of (peerid,data) sets
        shares = {}
        for shnum, shareinfo in sharemap.items():
            if shareinfo not in self._valid_shares:
                (peerid,data) = shareinfo
                try:
                    # The (seqnum+root_hash+IV) tuple for this share was
                    # already verified: specifically, all shares in the
                    # sharemap have a (seqnum+root_hash+IV) pair that was
                    # present in a validly signed prefix. The remainder of
                    # the prefix for this particular share has *not* been
                    # validated, but we don't care since we don't use it.
                    # self._validate_share() is required to check the hashes
                    # on the share data (and hash chains) to make sure they
                    # match root_hash, but is not required (and is in fact
                    # prohibited, because we don't validate the prefix on all
                    # shares) from using anything else in the share.
                    sharedata = self._validate_share(root_hash, shnum, data)
                except CorruptShareError, e:
                    self.log("WEIRD: share was corrupt: %s" % e)
                    sharemap[shnum].discard(shareinfo)
                    # If there are enough remaining shares, _check_for_done()
                    # will try again
                    raise
                self._valid_shares.add(shareinfo)
                shares[shnum] = sharedata
        # at this point, all shares in the sharemap are valid, and they're
        # all for the same seqnum+root_hash version, so it's now down to
        # doing FEC and decrypt.
        d = defer.maybeDeferred(self._decode, shares)
        d.addCallback(self._decrypt, IV)
        return d

    def _decode(self, shares_dict):
        # shares_dict is a dict mapping shnum to share data, but the codec
        # wants two lists.
        shareids = []; shares = []
        for shareid, share in shares_dict.items():
            shareids.append(shareid)
            shares.append(share)

        fec = codec.CRSDecoder()
        # we ought to know these values by now
        assert self._segsize is not None
        assert self._required_shares is not None
        assert self._total_shares is not None
        params = "%d-%d-%d" % (self._segsize,
                               self._required_shares, self._total_shares)
        fec.set_serialized_params(params)

        d = fec.decode(shares, shareids)
        def _done(buffers):
            segment = "".join(buffers)
            segment = segment[:self._datalength]
            return segment
        d.addCallback(_done)
        return d

    def _decrypt(self, crypttext, IV):
        key = hashutil.ssk_readkey_data_hash(IV, self._readkey)
        decryptor = AES.new(key=key, mode=AES.MODE_CTR, counterstart="\x00"*16)
        plaintext = decryptor.decrypt(crypttext)
        return plaintext

    def _done(self, contents):
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

    def publish(self, newdata):
        """Publish the filenode's current contents. Returns a Deferred that
        fires (with None) when the publish has done as much work as it's ever
        going to do, or errbacks with ConsistencyError if it detects a
        simultaneous write."""

        # 1: generate shares (SDMF: files are small, so we can do it in RAM)
        # 2: perform peer selection, get candidate servers
        #  2a: send queries to n+epsilon servers, to determine current shares
        #  2b: based upon responses, create target map
        # 3: send slot_testv_and_readv_and_writev messages
        # 4: as responses return, update share-dispatch table
        # 4a: may need to run recovery algorithm
        # 5: when enough responses are back, we're done

        old_roothash = self._node._current_roothash
        old_seqnum = self._node._current_seqnum

        readkey = self._node.get_readkey()
        required_shares = self._node.get_required_shares()
        total_shares = self._node.get_total_shares()
        privkey = self._node.get_privkey()
        encprivkey = self._node.get_encprivkey()
        pubkey = self._node.get_pubkey()

        IV = os.urandom(16)

        d = defer.succeed(newdata)
        d.addCallback(self._encrypt_and_encode, readkey, IV,
                      required_shares, total_shares)
        d.addCallback(self._generate_shares, old_seqnum+1,
                      privkey, encprivkey, pubkey)

        d.addCallback(self._query_peers, total_shares)
        d.addCallback(self._send_shares, IV)
        d.addCallback(self._maybe_recover)
        d.addCallback(lambda res: None)
        return d

    def _encrypt_and_encode(self, newdata, readkey, IV,
                            required_shares, total_shares):
        key = hashutil.ssk_readkey_data_hash(IV, readkey)
        enc = AES.new(key=key, mode=AES.MODE_CTR, counterstart="\x00"*16)
        crypttext = enc.encrypt(newdata)

        # now apply FEC
        self.MAX_SEGMENT_SIZE = 1024*1024
        segment_size = min(self.MAX_SEGMENT_SIZE, len(crypttext))
        # this must be a multiple of self.required_shares
        segment_size = mathutil.next_multiple(segment_size,
                                                   required_shares)
        self.num_segments = mathutil.div_ceil(len(crypttext), segment_size)
        assert self.num_segments == 1 # SDMF restrictions
        fec = codec.CRSEncoder()
        fec.set_params(segment_size, required_shares, total_shares)
        piece_size = fec.get_block_size()
        crypttext_pieces = []
        for offset in range(0, len(crypttext), piece_size):
            piece = crypttext[offset:offset+piece_size]
            if len(piece) < piece_size:
                pad_size = piece_size - len(piece)
                piece = piece + "\x00"*pad_size
            crypttext_pieces.append(piece)
            assert len(piece) == piece_size

        d = fec.encode(crypttext_pieces)
        d.addCallback(lambda shares:
                      (shares, required_shares, total_shares,
                       segment_size, len(crypttext), IV) )
        return d

    def _generate_shares(self, (shares_and_shareids,
                                required_shares, total_shares,
                                segment_size, data_length, IV),
                         seqnum, privkey, encprivkey, pubkey):

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
            shc = share_hash_chain[shnum]
            share_hash_chain_s = "".join([struct.pack(">H32s", i, shc[i])
                                          for i in sorted(shc.keys())])
            bht = block_hash_trees[shnum]
            for h in bht:
                assert len(h) == 32
            block_hash_tree_s = "".join(bht)
            share_data = all_shares[shnum]
            offsets = pack_offsets(len(verification_key),
                                   len(signature),
                                   len(share_hash_chain_s),
                                   len(block_hash_tree_s),
                                   len(share_data),
                                   len(encprivkey))

            final_shares[shnum] = "".join([prefix,
                                           offsets,
                                           verification_key,
                                           signature,
                                           share_hash_chain_s,
                                           block_hash_tree_s,
                                           share_data,
                                           encprivkey])
        return (seqnum, root_hash, final_shares)


    def _query_peers(self, (seqnum, root_hash, final_shares), total_shares):
        self._new_seqnum = seqnum
        self._new_root_hash = root_hash
        self._new_shares = final_shares

        storage_index = self._node.get_storage_index()
        peerlist = self._node._client.get_permuted_peers(storage_index,
                                                         include_myself=False)
        # we don't include ourselves in the N peers, but we *do* push an
        # extra copy of share[0] to ourselves so we're more likely to have
        # the signing key around later. This way, even if all the servers die
        # and the directory contents are unrecoverable, at least we can still
        # push out a new copy with brand-new contents.
        # TODO: actually push this copy

        current_share_peers = DictOfSets()
        reachable_peers = {}

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
                      total_shares, reachable_peers, seqnum,
                      current_share_peers, peer_storage_servers)
        # TODO: add an errback to, probably to ignore that peer
        return d

    def _do_query(self, conn, peerid, peer_storage_servers, storage_index):
        d = conn.callRemote("get_service", "storageserver")
        def _got_storageserver(ss):
            peer_storage_servers[peerid] = ss
            return ss.callRemote("slot_readv", storage_index, [], [(0, 2000)])
        d.addCallback(_got_storageserver)
        return d

    def _got_query_results(self, datavs, peerid, permutedid,
                           reachable_peers, current_share_peers):
        assert isinstance(datavs, dict)
        reachable_peers[peerid] = permutedid
        for shnum, datav in datavs.items():
            assert len(datav) == 1
            data = datav[0]
            r = unpack_share(data)
            share = (shnum, r[0], r[1]) # shnum,seqnum,R
            current_share_peers[shnum].add( (peerid, r[0], r[1]) )

    def _got_all_query_results(self, res,
                               total_shares, reachable_peers, new_seqnum,
                               current_share_peers, peer_storage_servers):
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
                if seqnum >= new_seqnum:
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
                raise NotEnoughPeersError("ran out of peers during upload")
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

        return (target_map, peer_storage_servers)

    def _send_shares(self, (target_map, peer_storage_servers), IV ):
        # we're finally ready to send out our shares. If we encounter any
        # surprises here, it's because somebody else is writing at the same
        # time. (Note: in the future, when we remove the _query_peers() step
        # and instead speculate about [or remember] which shares are where,
        # surprises here are *not* indications of UncoordinatedWriteError,
        # and we'll need to respond to them more gracefully.

        my_checkstring = pack_checkstring(self._new_seqnum,
                                          self._new_root_hash, IV)
        peer_messages = {}
        expected_old_shares = {}

        for shnum, peers in target_map.items():
            for (peerid, old_seqnum, old_root_hash) in peers:
                testv = [(0, len(my_checkstring), "le", my_checkstring)]
                new_share = self._new_shares[shnum]
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
        wrote, read_data = answer
        surprised = False

        if not wrote:
            # surprise! our testv failed, so the write did not happen
            surprised = True

        for shnum, (old_cs,) in read_data.items():
            (old_seqnum, old_root_hash, IV) = unpack_checkstring(old_cs)
            if wrote and shnum in tw_vectors:
                cur_cs = my_checkstring
            else:
                cur_cs = old_cs

            (cur_seqnum, cur_root_hash, IV) = unpack_checkstring(cur_cs)
            dispatch_map.add(shnum, (peerid, cur_seqnum, cur_root_hash))

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

    def _maybe_recover(self, (surprised, dispatch_map)):
        if not surprised:
            return
        print "RECOVERY NOT YET IMPLEMENTED"
        # but dispatch_map will help us do it
        raise UncoordinatedWriteError("I was surprised!")


# use client.create_mutable_file() to make one of these

class MutableFileNode:
    implements(IMutableFileNode)
    publish_class = Publish
    retrieve_class = Retrieve

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

    def init_from_uri(self, myuri):
        # we have the URI, but we have not yet retrieved the public
        # verification key, nor things like 'k' or 'N'. If and when someone
        # wants to get our contents, we'll pull from shares and fill those
        # in.
        self._uri = IMutableFileURI(myuri)
        self._writekey = self._uri.writekey
        self._readkey = self._uri.readkey
        self._storage_index = self._uri.storage_index
        return self

    def create(self, initial_contents):
        """Call this when the filenode is first created. This will generate
        the keys, generate the initial shares, allocate shares, and upload
        the initial contents. Returns a Deferred that fires (with the
        MutableFileNode instance you should use) when it completes.
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
            return self._publish(initial_contents)
        d.addCallback(_generated)
        return d

    def _generate_pubprivkeys(self):
        # TODO: wire these up to pycryptopp
        privkey = "very private"
        pubkey = "public"
        from allmydata.test.test_mutable import FakePrivKey, FakePubKey
        pubkey = FakePubKey(0)
        privkey = FakePrivKey(0)
        return pubkey, privkey

    def _publish(self, initial_contents):
        p = self.publish_class(self)
        d = p.publish(initial_contents)
        d.addCallback(lambda res: self)
        return d

    def _encrypt_privkey(self, writekey, privkey):
        enc = AES.new(key=writekey, mode=AES.MODE_CTR, counterstart="\x00"*16)
        crypttext = enc.encrypt(privkey)
        return crypttext

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

    def is_mutable(self):
        return self._uri.is_mutable()

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
        #downloader = self._client.getServiceNamed("downloader")
        #return downloader.download(self.uri, target)
        raise NotImplementedError

    def download_to_data(self):
        #downloader = self._client.getServiceNamed("downloader")
        #return downloader.download_to_data(self.uri)
        return defer.succeed("this isn't going to fool you, is it")

    def replace(self, newdata):
        return defer.succeed(None)
