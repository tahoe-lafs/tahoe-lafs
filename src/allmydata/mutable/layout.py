
import struct
from allmydata.mutable.common import NeedMoreDataError, UnknownVersionError

PREFIX = ">BQ32s16s" # each version has a different prefix
SIGNED_PREFIX = ">BQ32s16s BBQQ" # this is covered by the signature
SIGNED_PREFIX_LENGTH = struct.calcsize(SIGNED_PREFIX)
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
    prefix = data[:SIGNED_PREFIX_LENGTH]

    (version,
     seqnum,
     root_hash,
     IV,
     k, N, segsize, datalen,
     o) = unpack_header(data)

    if version != 0:
        raise UnknownVersionError("got mutable share version %d, but I only understand version 0" % version)

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

    if version != 0:
        raise UnknownVersionError("got mutable share version %d, but I only understand version 0" % version)

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
    if version != 0: # TODO: just ignore the share
        raise UnknownVersionError("got mutable share version %d, but I only understand version 0" % version)
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
    offsets['EOF'] = o5 + encprivkey_length

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

