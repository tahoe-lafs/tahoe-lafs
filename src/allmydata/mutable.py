
import os, struct, itertools
from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IMutableFileNode, IMutableFileURI
from allmydata.util import hashutil, mathutil
from allmydata.uri import WriteableSSKFileURI
from allmydata.Crypto.Cipher import AES
from allmydata import hashtree, codec
from allmydata.encode import NotEnoughPeersError


HEADER_LENGTH = struct.calcsize(">BQ32s BBQQ LLLLLQQ")

class NeedMoreDataError(Exception):
    def __init__(self, needed_bytes):
        Exception.__init__(self)
        self.needed_bytes = needed_bytes

class UncoordinatedWriteError(Exception):
    pass

# use client.create_mutable_file() to make one of these

class MutableFileNode:
    implements(IMutableFileNode)

    def __init__(self, client):
        self._client = client
        self._pubkey = None # filled in upon first read
        self._privkey = None # filled in if we're mutable
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
        return self

    def create(self, initial_contents):
        """Call this when the filenode is first created. This will generate
        the keys, generate the initial shares, allocate shares, and upload
        the initial contents. Returns a Deferred that fires (with the
        MutableFileNode instance you should use) when it completes.
        """
        self._privkey = "very private"
        self._pubkey = "public"
        self._writekey = hashutil.ssk_writekey_hash(self._privkey)
        self._fingerprint = hashutil.ssk_pubkey_fingerprint_hash(self._pubkey)
        self._uri = WriteableSSKFileURI(self._writekey, self._fingerprint)
        d = defer.succeed(None)
        return d


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

class ShareFormattingMixin:

    def _unpack_share(self, data):
        assert len(data) >= HEADER_LENGTH
        o = {}
        (version,
         seqnum,
         root_hash,
         k, N, segsize, datalen,
         o['signature'],
         o['share_hash_chain'],
         o['block_hash_tree'],
         o['IV'],
         o['share_data'],
         o['enc_privkey'],
         o['EOF']) = struct.unpack(">BQ32s" + "BBQQ" + "LLLLLQQ",
                                         data[:HEADER_LENGTH])

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
        block_hash_tree_s = data[o['block_hash_tree']:o['IV']]
        assert len(block_hash_tree_s) % 32 == 0, len(block_hash_tree_s)
        block_hash_tree = []
        for i in range(0, len(block_hash_tree_s), 32):
            block_hash_tree.append(block_hash_tree_s[i:i+32])

        IV = data[o['IV']:o['share_data']]
        share_data = data[o['share_data']:o['enc_privkey']]
        enc_privkey = data[o['enc_privkey']:o['EOF']]

        return (seqnum, root_hash, k, N, segsize, datalen,
                pubkey, signature, share_hash_chain, block_hash_tree,
                IV, share_data, enc_privkey)

class Retrieve(ShareFormattingMixin):
    def __init__(self, filenode):
        self._node = filenode

class DictOfSets(dict):
    def add(self, key, value):
        if key in self:
            self[key].add(value)
        else:
            self[key] = set([value])


class Publish(ShareFormattingMixin):
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

        # 3: pre-allocate some shares to some servers, based upon any existing
        #    self._node._sharemap
        # 4: send allocate/testv_and_writev messages
        # 5: as responses return, update share-dispatch table
        # 5a: may need to run recovery algorithm
        # 6: when enough responses are back, we're done

        old_roothash = self._node._current_roothash
        old_seqnum = self._node._current_seqnum

        readkey = self._node.readkey
        required_shares = self._node.required_shares
        total_shares = self._node.total_shares
        privkey = self._node.privkey
        pubkey = self._node.pubkey

        d = defer.succeed(newdata)
        d.addCallback(self._encrypt_and_encode, readkey,
                      required_shares, total_shares)
        d.addCallback(self._generate_shares, old_seqnum+1,
                      privkey, self._encprivkey, pubkey)

        d.addCallback(self._query_peers, total_shares)
        d.addCallback(self._send_shares)
        d.addCallback(self._wait_for_responses)
        d.addCallback(lambda res: None)
        return d

    def _encrypt_and_encode(self, newdata, readkey,
                            required_shares, total_shares):
        IV = os.urandom(16)
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

        prefix = self._pack_prefix(seqnum, root_hash,
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
            offsets = self._pack_offsets(len(verification_key),
                                         len(signature),
                                         len(share_hash_chain_s),
                                         len(block_hash_tree_s),
                                         len(IV),
                                         len(share_data),
                                         len(encprivkey))

            final_shares[shnum] = "".join([prefix,
                                           offsets,
                                           verification_key,
                                           signature,
                                           share_hash_chain_s,
                                           block_hash_tree_s,
                                           IV,
                                           share_data,
                                           encprivkey])
        return (seqnum, root_hash, final_shares)


    def _pack_checkstring(self, seqnum, root_hash):
        return struct.pack(">BQ32s",
                           0, # version,
                           seqnum,
                           root_hash)

    def _pack_prefix(self, seqnum, root_hash,
                     required_shares, total_shares,
                     segment_size, data_length):
        prefix = struct.pack(">BQ32s" + "BBQQ",
                             0, # version,
                             seqnum,
                             root_hash,

                             required_shares,
                             total_shares,
                             segment_size,
                             data_length,
                             )
        return prefix

    def _pack_offsets(self, verification_key_length, signature_length,
                      share_hash_chain_length, block_hash_tree_length,
                      IV_length, share_data_length, encprivkey_length):
        post_offset = HEADER_LENGTH
        offsets = {}
        o1 = offsets['signature'] = post_offset + verification_key_length
        o2 = offsets['share_hash_chain'] = o1 + signature_length
        o3 = offsets['block_hash_tree'] = o2 + share_hash_chain_length
        assert IV_length == 16
        o4 = offsets['IV'] = o3 + block_hash_tree_length
        o5 = offsets['share_data'] = o4 + IV_length
        o6 = offsets['enc_privkey'] = o5 + share_data_length
        o7 = offsets['EOF'] = o6 + encprivkey_length

        return struct.pack(">LLLLLQQ",
                           offsets['signature'],
                           offsets['share_hash_chain'],
                           offsets['block_hash_tree'],
                           offsets['IV'],
                           offsets['share_data'],
                           offsets['enc_privkey'],
                           offsets['EOF'])

    def _query_peers(self, (seqnum, root_hash, final_shares), total_shares):
        self._new_seqnum = seqnum
        self._new_root_hash = root_hash
        self._new_shares = final_shares

        storage_index = self._node._uri.storage_index
        peerlist = self._node._client.get_permuted_peers(storage_index,
                                                         include_myself=False)
        # we don't include ourselves in the N peers, but we *do* push an
        # extra copy of share[0] to ourselves so we're more likely to have
        # the signing key around later. This way, even if all the servers die
        # and the directory contents are unrecoverable, at least we can still
        # push out a new copy with brand-new contents.

        current_share_peers = DictOfSets()
        reachable_peers = {}

        EPSILON = total_shares / 2
        partial_peerlist = itertools.islice(peerlist, total_shares + EPSILON)
        peer_storage_servers = {}
        dl = []
        for (permutedid, peerid, conn) in partial_peerlist:
            d = self._do_query(conn, peerid, peer_storage_servers)
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

    def _do_query(self, conn, peerid, peer_storage_servers):
        d = conn.callRemote("get_service", "storageserver")
        def _got_storageserver(ss):
            peer_storage_servers[peerid] = ss
            return ss.callRemote("readv_slots", [(0, 2000)])
        d.addCallback(_got_storageserver)
        return d

    def _got_query_results(self, datavs, peerid, permutedid,
                           reachable_peers, current_share_peers):
        assert isinstance(datavs, dict)
        reachable_peers[peerid] = permutedid
        for shnum, datav in datavs.items():
            assert len(datav) == 1
            data = datav[0]
            r = self._unpack_share(data)
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

    def _send_shares(self, (target_map, peer_storage_servers) ):
        # we're finally ready to send out our shares. If we encounter any
        # surprises here, it's because somebody else is writing at the same
        # time. (Note: in the future, when we remove the _query_peers() step
        # and instead speculate about [or remember] which shares are where,
        # surprises here are *not* indications of UncoordinatedWriteError,
        # and we'll need to respond to them more gracefully.

        my_checkstring = self._pack_checkstring(self._new_seqnum,
                                                self._new_root_hash)
        peer_messages = {}
        expected_old_shares = {}

        for shnum, peers in target_map.items():
            for (peerid, old_seqnum, old_root_hash) in peers:
                testv = [(0, len(my_checkstring), "ge", my_checkstring)]
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
        for peerid, tw_vectors in peer_messages.items():
            d = self._do_testreadwrite(peerid, peer_storage_servers,
                                       tw_vectors, read_vector)
            d.addCallback(self._got_write_answer,
                          peerid, expected_old_shares[peerid])
            dl.append(d)

        d = defer.DeferredList(dl)
        d.addCallback(lambda res: self._surprised)
        return d

    def _do_testreadwrite(self, peerid, peer_storage_servers,
                          tw_vectors, read_vector):
        conn = peer_storage_servers[peerid]
        storage_index = self._node._uri.storage_index
        # TOTALLY BOGUS renew/cancel secrets
        write_enabler = hashutil.tagged_hash("WEFOO", storage_index)
        renew_secret = hashutil.tagged_hash("renewFOO", storage_index)
        cancel_secret = hashutil.tagged_hash("cancelFOO", storage_index)

        d = conn.callRemote("slot_testv_and_readv_and_writev",
                            storage_index,
                            (write_enabler, renew_secret, cancel_secret),
                            tw_vectors,
                            read_vector)
        return d

    def _got_write_answer(self, answer, peerid, expected_old_shares):
        wrote, read_data = answer
        surprised = False
        if not wrote:
            # surprise! our testv failed, so the write did not happen
            surprised = True
        for shnum, (old_checkstring,) in read_data.items():
            if shnum not in expected_old_shares:
                # surprise! there was a share we didn't know about
                surprised = True
            else:
                seqnum, root_hash = expected_old_shares[shnum]
                if seqnum is not None:
                    expected_checkstring = self._pack_checkstring(seqnum,
                                                                  root_hash)
                    if old_checkstring != expected_checkstring:
                        # surprise! somebody modified the share
                        surprised = True
        if surprised:
            self._surprised = True

