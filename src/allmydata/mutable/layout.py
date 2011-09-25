
import struct
from allmydata.mutable.common import NeedMoreDataError, UnknownVersionError
from allmydata.interfaces import HASH_SIZE, SALT_SIZE, SDMF_VERSION, \
                                 MDMF_VERSION, IMutableSlotWriter
from allmydata.util import mathutil
from twisted.python import failure
from twisted.internet import defer
from zope.interface import implements


# These strings describe the format of the packed structs they help process
# Here's what they mean:
#
#  PREFIX:
#    >: Big-endian byte order; the most significant byte is first (leftmost).
#    B: The version information; an 8 bit version identifier. Stored as
#       an unsigned char. This is currently 00 00 00 00; our modifications
#       will turn it into 00 00 00 01.
#    Q: The sequence number; this is sort of like a revision history for
#       mutable files; they start at 1 and increase as they are changed after
#       being uploaded. Stored as an unsigned long long, which is 8 bytes in
#       length.
#  32s: The root hash of the share hash tree. We use sha-256d, so we use 32 
#       characters = 32 bytes to store the value.
#  16s: The salt for the readkey. This is a 16-byte random value, stored as
#       16 characters.
#
#  SIGNED_PREFIX additions, things that are covered by the signature:
#    B: The "k" encoding parameter. We store this as an 8-bit character, 
#       which is convenient because our erasure coding scheme cannot 
#       encode if you ask for more than 255 pieces.
#    B: The "N" encoding parameter. Stored as an 8-bit character for the 
#       same reasons as above.
#    Q: The segment size of the uploaded file. This will essentially be the
#       length of the file in SDMF. An unsigned long long, so we can store 
#       files of quite large size.
#    Q: The data length of the uploaded file. Modulo padding, this will be
#       the same of the data length field. Like the data length field, it is
#       an unsigned long long and can be quite large.
#
#   HEADER additions:
#     L: The offset of the signature of this. An unsigned long.
#     L: The offset of the share hash chain. An unsigned long.
#     L: The offset of the block hash tree. An unsigned long.
#     L: The offset of the share data. An unsigned long.
#     Q: The offset of the encrypted private key. An unsigned long long, to
#        account for the possibility of a lot of share data.
#     Q: The offset of the EOF. An unsigned long long, to account for the
#        possibility of a lot of share data.
# 
#  After all of these, we have the following:
#    - The verification key: Occupies the space between the end of the header
#      and the start of the signature (i.e.: data[HEADER_LENGTH:o['signature']].
#    - The signature, which goes from the signature offset to the share hash
#      chain offset.
#    - The share hash chain, which goes from the share hash chain offset to
#      the block hash tree offset.
#    - The share data, which goes from the share data offset to the encrypted
#      private key offset.
#    - The encrypted private key offset, which goes until the end of the file.
# 
#  The block hash tree in this encoding has only one share, so the offset of
#  the share data will be 32 bits more than the offset of the block hash tree.
#  Given this, we may need to check to see how many bytes a reasonably sized
#  block hash tree will take up.

PREFIX = ">BQ32s16s" # each version has a different prefix
SIGNED_PREFIX = ">BQ32s16s BBQQ" # this is covered by the signature
SIGNED_PREFIX_LENGTH = struct.calcsize(SIGNED_PREFIX)
HEADER = ">BQ32s16s BBQQ LLLLQQ" # includes offsets
HEADER_LENGTH = struct.calcsize(HEADER)
OFFSETS = ">LLLLQQ"
OFFSETS_LENGTH = struct.calcsize(OFFSETS)

# These are still used for some tests.
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

def get_version_from_checkstring(checkstring):
    (t, ) = struct.unpack(">B", checkstring[:1])
    return t

def unpack_sdmf_checkstring(checkstring):
    cs_len = struct.calcsize(PREFIX)
    version, seqnum, root_hash, IV = struct.unpack(PREFIX, checkstring[:cs_len])
    assert version == SDMF_VERSION, version
    return (seqnum, root_hash, IV)

def unpack_mdmf_checkstring(checkstring):
    cs_len = struct.calcsize(MDMFCHECKSTRING)
    version, seqnum, root_hash = struct.unpack(MDMFCHECKSTRING, checkstring[:cs_len])
    assert version == MDMF_VERSION, version
    return (seqnum, root_hash)

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


class SDMFSlotWriteProxy:
    implements(IMutableSlotWriter)
    """
    I represent a remote write slot for an SDMF mutable file. I build a
    share in memory, and then write it in one piece to the remote
    server. This mimics how SDMF shares were built before MDMF (and the
    new MDMF uploader), but provides that functionality in a way that
    allows the MDMF uploader to be built without much special-casing for
    file format, which makes the uploader code more readable.
    """
    def __init__(self,
                 shnum,
                 rref, # a remote reference to a storage server
                 storage_index,
                 secrets, # (write_enabler, renew_secret, cancel_secret)
                 seqnum, # the sequence number of the mutable file
                 required_shares,
                 total_shares,
                 segment_size,
                 data_length): # the length of the original file
        self.shnum = shnum
        self._rref = rref
        self._storage_index = storage_index
        self._secrets = secrets
        self._seqnum = seqnum
        self._required_shares = required_shares
        self._total_shares = total_shares
        self._segment_size = segment_size
        self._data_length = data_length

        # This is an SDMF file, so it should have only one segment, so, 
        # modulo padding of the data length, the segment size and the
        # data length should be the same.
        expected_segment_size = mathutil.next_multiple(data_length,
                                                       self._required_shares)
        assert expected_segment_size == segment_size

        self._block_size = self._segment_size / self._required_shares

        # This is meant to mimic how SDMF files were built before MDMF
        # entered the picture: we generate each share in its entirety,
        # then push it off to the storage server in one write. When
        # callers call set_*, they are just populating this dict.
        # finish_publishing will stitch these pieces together into a
        # coherent share, and then write the coherent share to the
        # storage server.
        self._share_pieces = {}

        # This tells the write logic what checkstring to use when
        # writing remote shares.
        self._testvs = []

        self._readvs = [(0, struct.calcsize(PREFIX))]


    def set_checkstring(self, checkstring_or_seqnum,
                              root_hash=None,
                              salt=None):
        """
        Set the checkstring that I will pass to the remote server when
        writing.

            @param checkstring_or_seqnum: A packed checkstring to use,
                   or a sequence number. I will treat this as a checkstr

        Note that implementations can differ in which semantics they
        wish to support for set_checkstring -- they can, for example,
        build the checkstring themselves from its constituents, or
        some other thing.
        """
        if root_hash and salt:
            checkstring = struct.pack(PREFIX,
                                      0,
                                      checkstring_or_seqnum,
                                      root_hash,
                                      salt)
        else:
            checkstring = checkstring_or_seqnum
        self._testvs = [(0, len(checkstring), "eq", checkstring)]


    def get_checkstring(self):
        """
        Get the checkstring that I think currently exists on the remote
        server.
        """
        if self._testvs:
            return self._testvs[0][3]
        return ""


    def put_block(self, data, segnum, salt):
        """
        Add a block and salt to the share.
        """
        # SDMF files have only one segment
        assert segnum == 0
        assert len(data) == self._block_size
        assert len(salt) == SALT_SIZE

        self._share_pieces['sharedata'] = data
        self._share_pieces['salt'] = salt

        # TODO: Figure out something intelligent to return.
        return defer.succeed(None)


    def put_encprivkey(self, encprivkey):
        """
        Add the encrypted private key to the share.
        """
        self._share_pieces['encprivkey'] = encprivkey

        return defer.succeed(None)


    def put_blockhashes(self, blockhashes):
        """
        Add the block hash tree to the share.
        """
        assert isinstance(blockhashes, list)
        for h in blockhashes:
            assert len(h) == HASH_SIZE

        # serialize the blockhashes, then set them.
        blockhashes_s = "".join(blockhashes)
        self._share_pieces['block_hash_tree'] = blockhashes_s

        return defer.succeed(None)


    def put_sharehashes(self, sharehashes):
        """
        Add the share hash chain to the share.
        """
        assert isinstance(sharehashes, dict)
        for h in sharehashes.itervalues():
            assert len(h) == HASH_SIZE

        # serialize the sharehashes, then set them.
        sharehashes_s = "".join([struct.pack(">H32s", i, sharehashes[i])
                                 for i in sorted(sharehashes.keys())])
        self._share_pieces['share_hash_chain'] = sharehashes_s

        return defer.succeed(None)


    def put_root_hash(self, root_hash):
        """
        Add the root hash to the share.
        """
        assert len(root_hash) == HASH_SIZE

        self._share_pieces['root_hash'] = root_hash

        return defer.succeed(None)


    def put_salt(self, salt):
        """
        Add a salt to an empty SDMF file.
        """
        assert len(salt) == SALT_SIZE

        self._share_pieces['salt'] = salt
        self._share_pieces['sharedata'] = ""


    def get_signable(self):
        """
        Return the part of the share that needs to be signed.

        SDMF writers need to sign the packed representation of the
        first eight fields of the remote share, that is:
            - version number (0)
            - sequence number
            - root of the share hash tree
            - salt
            - k
            - n
            - segsize
            - datalen

        This method is responsible for returning that to callers.
        """
        return struct.pack(SIGNED_PREFIX,
                           0,
                           self._seqnum,
                           self._share_pieces['root_hash'],
                           self._share_pieces['salt'],
                           self._required_shares,
                           self._total_shares,
                           self._segment_size,
                           self._data_length)


    def put_signature(self, signature):
        """
        Add the signature to the share.
        """
        self._share_pieces['signature'] = signature

        return defer.succeed(None)


    def put_verification_key(self, verification_key):
        """
        Add the verification key to the share.
        """
        self._share_pieces['verification_key'] = verification_key

        return defer.succeed(None)


    def get_verinfo(self):
        """
        I return my verinfo tuple. This is used by the ServermapUpdater
        to keep track of versions of mutable files.

        The verinfo tuple for MDMF files contains:
            - seqnum
            - root hash
            - a blank (nothing)
            - segsize
            - datalen
            - k
            - n
            - prefix (the thing that you sign)
            - a tuple of offsets

        We include the nonce in MDMF to simplify processing of version
        information tuples.

        The verinfo tuple for SDMF files is the same, but contains a
        16-byte IV instead of a hash of salts.
        """
        return (self._seqnum,
                self._share_pieces['root_hash'],
                self._share_pieces['salt'],
                self._segment_size,
                self._data_length,
                self._required_shares,
                self._total_shares,
                self.get_signable(),
                self._get_offsets_tuple())

    def _get_offsets_dict(self):
        post_offset = HEADER_LENGTH
        offsets = {}

        verification_key_length = len(self._share_pieces['verification_key'])
        o1 = offsets['signature'] = post_offset + verification_key_length

        signature_length = len(self._share_pieces['signature'])
        o2 = offsets['share_hash_chain'] = o1 + signature_length

        share_hash_chain_length = len(self._share_pieces['share_hash_chain'])
        o3 = offsets['block_hash_tree'] = o2 + share_hash_chain_length

        block_hash_tree_length = len(self._share_pieces['block_hash_tree'])
        o4 = offsets['share_data'] = o3 + block_hash_tree_length

        share_data_length = len(self._share_pieces['sharedata'])
        o5 = offsets['enc_privkey'] = o4 + share_data_length

        encprivkey_length = len(self._share_pieces['encprivkey'])
        offsets['EOF'] = o5 + encprivkey_length
        return offsets


    def _get_offsets_tuple(self):
        offsets = self._get_offsets_dict()
        return tuple([(key, value) for key, value in offsets.items()])


    def _pack_offsets(self):
        offsets = self._get_offsets_dict()
        return struct.pack(">LLLLQQ",
                           offsets['signature'],
                           offsets['share_hash_chain'],
                           offsets['block_hash_tree'],
                           offsets['share_data'],
                           offsets['enc_privkey'],
                           offsets['EOF'])


    def finish_publishing(self):
        """
        Do anything necessary to finish writing the share to a remote
        server. I require that no further publishing needs to take place
        after this method has been called.
        """
        for k in ["sharedata", "encprivkey", "signature", "verification_key",
                  "share_hash_chain", "block_hash_tree"]:
            assert k in self._share_pieces, (self.shnum, k, self._share_pieces.keys())
        # This is the only method that actually writes something to the
        # remote server.
        # First, we need to pack the share into data that we can write
        # to the remote server in one write.
        offsets = self._pack_offsets()
        prefix = self.get_signable()
        final_share = "".join([prefix,
                               offsets,
                               self._share_pieces['verification_key'],
                               self._share_pieces['signature'],
                               self._share_pieces['share_hash_chain'],
                               self._share_pieces['block_hash_tree'],
                               self._share_pieces['sharedata'],
                               self._share_pieces['encprivkey']])

        # Our only data vector is going to be writing the final share,
        # in its entirely.
        datavs = [(0, final_share)]

        if not self._testvs:
            # Our caller has not provided us with another checkstring
            # yet, so we assume that we are writing a new share, and set
            # a test vector that will allow a new share to be written.
            self._testvs = []
            self._testvs.append(tuple([0, 1, "eq", ""]))

        tw_vectors = {}
        tw_vectors[self.shnum] = (self._testvs, datavs, None)
        return self._rref.callRemote("slot_testv_and_readv_and_writev",
                                     self._storage_index,
                                     self._secrets,
                                     tw_vectors,
                                     # TODO is it useful to read something?
                                     self._readvs)


MDMFHEADER = ">BQ32sBBQQ QQQQQQQQ"
MDMFHEADERWITHOUTOFFSETS = ">BQ32sBBQQ"
MDMFHEADERSIZE = struct.calcsize(MDMFHEADER)
MDMFHEADERWITHOUTOFFSETSSIZE = struct.calcsize(MDMFHEADERWITHOUTOFFSETS)
MDMFCHECKSTRING = ">BQ32s"
MDMFSIGNABLEHEADER = ">BQ32sBBQQ"
MDMFOFFSETS = ">QQQQQQQQ"
MDMFOFFSETS_LENGTH = struct.calcsize(MDMFOFFSETS)

PRIVATE_KEY_SIZE = 1220
SIGNATURE_SIZE = 260
VERIFICATION_KEY_SIZE = 292
# We know we won't have more than 256 shares, and we know that we won't need
# to store more than ln2(256) hash-chain nodes to validate, so that's our
# bound. Each node requires 2 bytes of node-number plus 32 bytes of hash.
SHARE_HASH_CHAIN_SIZE = (2+HASH_SIZE)*mathutil.log_ceil(256, 2)

class MDMFSlotWriteProxy:
    implements(IMutableSlotWriter)

    """
    I represent a remote write slot for an MDMF mutable file.

    I abstract away from my caller the details of block and salt
    management, and the implementation of the on-disk format for MDMF
    shares.
    """
    # Expected layout, MDMF:
    # offset:     size:       name:
    #-- signed part --
    # 0           1           version number (01)
    # 1           8           sequence number
    # 9           32          share tree root hash
    # 41          1           The "k" encoding parameter
    # 42          1           The "N" encoding parameter
    # 43          8           The segment size of the uploaded file
    # 51          8           The data length of the original plaintext
    #-- end signed part --
    # 59          8           The offset of the encrypted private key
    # 67          8           The offset of the share hash chain
    # 75          8           The offset of the signature
    # 83          8           The offset of the verification key
    # 91          8           The offset of the end of the v. key.
    # 99          8           The offset of the share data
    # 107         8           The offset of the block hash tree
    # 115         8           The offset of EOF
    # 123         var         encrypted private key
    # var         var         share hash chain
    # var         var         signature
    # var         var         verification key
    # var         large       share data
    # var         var         block hash tree
    #
    # We order the fields that way to make smart downloaders -- downloaders
    # which prempetively read a big part of the share -- possible.
    #
    # The checkstring is the first three fields -- the version number,
    # sequence number, root hash and root salt hash. This is consistent
    # in meaning to what we have with SDMF files, except now instead of
    # using the literal salt, we use a value derived from all of the
    # salts -- the share hash root.
    # 
    # The salt is stored before the block for each segment. The block
    # hash tree is computed over the combination of block and salt for
    # each segment. In this way, we get integrity checking for both
    # block and salt with the current block hash tree arrangement.
    # 
    # The ordering of the offsets is different to reflect the dependencies
    # that we'll run into with an MDMF file. The expected write flow is
    # something like this:
    #
    #   0: Initialize with the sequence number, encoding parameters and
    #      data length. From this, we can deduce the number of segments,
    #      and where they should go.. We can also figure out where the
    #      encrypted private key should go, because we can figure out how
    #      big the share data will be.
    # 
    #   1: Encrypt, encode, and upload the file in chunks. Do something
    #      like 
    #
    #       put_block(data, segnum, salt)
    #
    #      to write a block and a salt to the disk. We can do both of
    #      these operations now because we have enough of the offsets to
    #      know where to put them.
    # 
    #   2: Put the encrypted private key. Use:
    #
    #        put_encprivkey(encprivkey)
    #
    #      Now that we know the length of the private key, we can fill
    #      in the offset for the block hash tree.
    #
    #   3: We're now in a position to upload the block hash tree for
    #      a share. Put that using something like:
    #       
    #        put_blockhashes(block_hash_tree)
    #
    #      Note that block_hash_tree is a list of hashes -- we'll take
    #      care of the details of serializing that appropriately. When
    #      we get the block hash tree, we are also in a position to
    #      calculate the offset for the share hash chain, and fill that
    #      into the offsets table.
    #
    #   4: We're now in a position to upload the share hash chain for
    #      a share. Do that with something like:
    #      
    #        put_sharehashes(share_hash_chain) 
    #
    #      share_hash_chain should be a dictionary mapping shnums to 
    #      32-byte hashes -- the wrapper handles serialization.
    #      We'll know where to put the signature at this point, also.
    #      The root of this tree will be put explicitly in the next
    #      step.
    # 
    #   5: Before putting the signature, we must first put the
    #      root_hash. Do this with:
    # 
    #        put_root_hash(root_hash).
    #      
    #      In terms of knowing where to put this value, it was always
    #      possible to place it, but it makes sense semantically to
    #      place it after the share hash tree, so that's why you do it
    #      in this order.
    #
    #   6: With the root hash put, we can now sign the header. Use:
    #
    #        get_signable()
    #
    #      to get the part of the header that you want to sign, and use:
    #       
    #        put_signature(signature)
    #
    #      to write your signature to the remote server.
    #
    #   6: Add the verification key, and finish. Do:
    #
    #        put_verification_key(key) 
    #
    #      and 
    #
    #        finish_publish()
    #
    # Checkstring management:
    # 
    # To write to a mutable slot, we have to provide test vectors to ensure
    # that we are writing to the same data that we think we are. These
    # vectors allow us to detect uncoordinated writes; that is, writes
    # where both we and some other shareholder are writing to the
    # mutable slot, and to report those back to the parts of the program
    # doing the writing. 
    #
    # With SDMF, this was easy -- all of the share data was written in
    # one go, so it was easy to detect uncoordinated writes, and we only
    # had to do it once. With MDMF, not all of the file is written at
    # once.
    #
    # If a share is new, we write out as much of the header as we can
    # before writing out anything else. This gives other writers a
    # canary that they can use to detect uncoordinated writes, and, if
    # they do the same thing, gives us the same canary. We them update
    # the share. We won't be able to write out two fields of the header
    # -- the share tree hash and the salt hash -- until we finish
    # writing out the share. We only require the writer to provide the
    # initial checkstring, and keep track of what it should be after
    # updates ourselves.
    #
    # If we haven't written anything yet, then on the first write (which
    # will probably be a block + salt of a share), we'll also write out
    # the header. On subsequent passes, we'll expect to see the header.
    # This changes in two places:
    #
    #   - When we write out the salt hash
    #   - When we write out the root of the share hash tree
    #
    # since these values will change the header. It is possible that we 
    # can just make those be written in one operation to minimize
    # disruption.
    def __init__(self,
                 shnum,
                 rref, # a remote reference to a storage server
                 storage_index,
                 secrets, # (write_enabler, renew_secret, cancel_secret)
                 seqnum, # the sequence number of the mutable file
                 required_shares,
                 total_shares,
                 segment_size,
                 data_length): # the length of the original file
        self.shnum = shnum
        self._rref = rref
        self._storage_index = storage_index
        self._seqnum = seqnum
        self._required_shares = required_shares
        assert self.shnum >= 0 and self.shnum < total_shares
        self._total_shares = total_shares
        # We build up the offset table as we write things. It is the
        # last thing we write to the remote server. 
        self._offsets = {}
        self._testvs = []
        # This is a list of write vectors that will be sent to our
        # remote server once we are directed to write things there.
        self._writevs = []
        self._secrets = secrets
        # The segment size needs to be a multiple of the k parameter --
        # any padding should have been carried out by the publisher
        # already.
        assert segment_size % required_shares == 0
        self._segment_size = segment_size
        self._data_length = data_length

        # These are set later -- we define them here so that we can
        # check for their existence easily

        # This is the root of the share hash tree -- the Merkle tree
        # over the roots of the block hash trees computed for shares in
        # this upload.
        self._root_hash = None

        # We haven't yet written anything to the remote bucket. By
        # setting this, we tell the _write method as much. The write
        # method will then know that it also needs to add a write vector
        # for the checkstring (or what we have of it) to the first write
        # request. We'll then record that value for future use.  If
        # we're expecting something to be there already, we need to call
        # set_checkstring before we write anything to tell the first
        # write about that.
        self._written = False

        # When writing data to the storage servers, we get a read vector
        # for free. We'll read the checkstring, which will help us
        # figure out what's gone wrong if a write fails.
        self._readv = [(0, struct.calcsize(MDMFCHECKSTRING))]

        # We calculate the number of segments because it tells us
        # where the salt part of the file ends/share segment begins,
        # and also because it provides a useful amount of bounds checking.
        self._num_segments = mathutil.div_ceil(self._data_length,
                                               self._segment_size)
        self._block_size = self._segment_size / self._required_shares
        # We also calculate the share size, to help us with block
        # constraints later.
        tail_size = self._data_length % self._segment_size
        if not tail_size:
            self._tail_block_size = self._block_size
        else:
            self._tail_block_size = mathutil.next_multiple(tail_size,
                                                           self._required_shares)
            self._tail_block_size /= self._required_shares

        # We already know where the sharedata starts; right after the end
        # of the header (which is defined as the signable part + the offsets)
        # We can also calculate where the encrypted private key begins
        # from what we know know.
        self._actual_block_size = self._block_size + SALT_SIZE
        data_size = self._actual_block_size * (self._num_segments - 1)
        data_size += self._tail_block_size
        data_size += SALT_SIZE
        self._offsets['enc_privkey'] = MDMFHEADERSIZE

        # We don't define offsets for these because we want them to be
        # tightly packed -- this allows us to ignore the responsibility
        # of padding individual values, and of removing that padding
        # later. So nonconstant_start is where we start writing
        # nonconstant data.
        nonconstant_start = self._offsets['enc_privkey']
        nonconstant_start += PRIVATE_KEY_SIZE
        nonconstant_start += SIGNATURE_SIZE
        nonconstant_start += VERIFICATION_KEY_SIZE
        nonconstant_start += SHARE_HASH_CHAIN_SIZE

        self._offsets['share_data'] = nonconstant_start

        # Finally, we know how big the share data will be, so we can
        # figure out where the block hash tree needs to go.
        # XXX: But this will go away if Zooko wants to make it so that
        # you don't need to know the size of the file before you start
        # uploading it.
        self._offsets['block_hash_tree'] = self._offsets['share_data'] + \
                    data_size

        # Done. We can snow start writing.


    def set_checkstring(self,
                        seqnum_or_checkstring,
                        root_hash=None,
                        salt=None):
        """
        Set checkstring checkstring for the given shnum.

        This can be invoked in one of two ways.

        With one argument, I assume that you are giving me a literal
        checkstring -- e.g., the output of get_checkstring. I will then
        set that checkstring as it is. This form is used by unit tests.

        With two arguments, I assume that you are giving me a sequence
        number and root hash to make a checkstring from. In that case, I
        will build a checkstring and set it for you. This form is used
        by the publisher.

        By default, I assume that I am writing new shares to the grid.
        If you don't explcitly set your own checkstring, I will use
        one that requires that the remote share not exist. You will want
        to use this method if you are updating a share in-place;
        otherwise, writes will fail.
        """
        # You're allowed to overwrite checkstrings with this method;
        # I assume that users know what they are doing when they call
        # it.
        if root_hash:
            checkstring = struct.pack(MDMFCHECKSTRING,
                                      1,
                                      seqnum_or_checkstring,
                                      root_hash)
        else:
            checkstring = seqnum_or_checkstring

        if checkstring == "":
            # We special-case this, since len("") = 0, but we need
            # length of 1 for the case of an empty share to work on the
            # storage server, which is what a checkstring that is the
            # empty string means.
            self._testvs = []
        else:
            self._testvs = []
            self._testvs.append((0, len(checkstring), "eq", checkstring))


    def __repr__(self):
        return "MDMFSlotWriteProxy for share %d" % self.shnum


    def get_checkstring(self):
        """
        Given a share number, I return a representation of what the
        checkstring for that share on the server will look like.

        I am mostly used for tests.
        """
        if self._root_hash:
            roothash = self._root_hash
        else:
            roothash = "\x00" * 32
        return struct.pack(MDMFCHECKSTRING,
                           1,
                           self._seqnum,
                           roothash)


    def put_block(self, data, segnum, salt):
        """
        I queue a write vector for the data, salt, and segment number
        provided to me. I return None, as I do not actually cause
        anything to be written yet.
        """
        if segnum >= self._num_segments:
            raise LayoutInvalid("I won't overwrite the block hash tree")
        if len(salt) != SALT_SIZE:
            raise LayoutInvalid("I was given a salt of size %d, but "
                                "I wanted a salt of size %d")
        if segnum + 1 == self._num_segments:
            if len(data) != self._tail_block_size:
                raise LayoutInvalid("I was given the wrong size block to write")
        elif len(data) != self._block_size:
            raise LayoutInvalid("I was given the wrong size block to write")

        # We want to write at len(MDMFHEADER) + segnum * block_size.
        offset = self._offsets['share_data'] + \
            (self._actual_block_size * segnum)
        data = salt + data

        self._writevs.append(tuple([offset, data]))


    def put_encprivkey(self, encprivkey):
        """
        I queue a write vector for the encrypted private key provided to
        me.
        """
        assert self._offsets
        assert self._offsets['enc_privkey']
        # You shouldn't re-write the encprivkey after the block hash
        # tree is written, since that could cause the private key to run
        # into the block hash tree. Before it writes the block hash
        # tree, the block hash tree writing method writes the offset of
        # the share hash chain. So that's a good indicator of whether or
        # not the block hash tree has been written.
        if "signature" in self._offsets:
            raise LayoutInvalid("You can't put the encrypted private key "
                                "after putting the share hash chain")

        self._offsets['share_hash_chain'] = self._offsets['enc_privkey'] + \
                len(encprivkey)

        self._writevs.append(tuple([self._offsets['enc_privkey'], encprivkey]))


    def put_blockhashes(self, blockhashes):
        """
        I queue a write vector to put the block hash tree in blockhashes
        onto the remote server.

        The encrypted private key must be queued before the block hash
        tree, since we need to know how large it is to know where the
        block hash tree should go. The block hash tree must be put
        before the share hash chain, since its size determines the
        offset of the share hash chain.
        """
        assert self._offsets
        assert "block_hash_tree" in self._offsets

        assert isinstance(blockhashes, list)

        blockhashes_s = "".join(blockhashes)
        self._offsets['EOF'] = self._offsets['block_hash_tree'] + len(blockhashes_s)

        self._writevs.append(tuple([self._offsets['block_hash_tree'],
                                  blockhashes_s]))


    def put_sharehashes(self, sharehashes):
        """
        I queue a write vector to put the share hash chain in my
        argument onto the remote server.

        The block hash tree must be queued before the share hash chain,
        since we need to know where the block hash tree ends before we
        can know where the share hash chain starts. The share hash chain
        must be put before the signature, since the length of the packed
        share hash chain determines the offset of the signature. Also,
        semantically, you must know what the root of the block hash tree
        is before you can generate a valid signature.
        """
        assert isinstance(sharehashes, dict)
        assert self._offsets
        if "share_hash_chain" not in self._offsets:
            raise LayoutInvalid("You must put the block hash tree before "
                                "putting the share hash chain")

        # The signature comes after the share hash chain. If the
        # signature has already been written, we must not write another
        # share hash chain. The signature writes the verification key
        # offset when it gets sent to the remote server, so we look for
        # that.
        if "verification_key" in self._offsets:
            raise LayoutInvalid("You must write the share hash chain "
                                "before you write the signature")
        sharehashes_s = "".join([struct.pack(">H32s", i, sharehashes[i])
                                  for i in sorted(sharehashes.keys())])
        self._offsets['signature'] = self._offsets['share_hash_chain'] + \
            len(sharehashes_s)
        self._writevs.append(tuple([self._offsets['share_hash_chain'],
                            sharehashes_s]))


    def put_root_hash(self, roothash):
        """
        Put the root hash (the root of the share hash tree) in the
        remote slot.
        """
        # It does not make sense to be able to put the root 
        # hash without first putting the share hashes, since you need
        # the share hashes to generate the root hash.
        #
        # Signature is defined by the routine that places the share hash
        # chain, so it's a good thing to look for in finding out whether
        # or not the share hash chain exists on the remote server.
        if len(roothash) != HASH_SIZE:
            raise LayoutInvalid("hashes and salts must be exactly %d bytes"
                                 % HASH_SIZE)
        self._root_hash = roothash
        # To write both of these values, we update the checkstring on
        # the remote server, which includes them
        checkstring = self.get_checkstring()
        self._writevs.append(tuple([0, checkstring]))
        # This write, if successful, changes the checkstring, so we need
        # to update our internal checkstring to be consistent with the
        # one on the server.


    def get_signable(self):
        """
        Get the first seven fields of the mutable file; the parts that
        are signed.
        """
        if not self._root_hash:
            raise LayoutInvalid("You need to set the root hash "
                                "before getting something to "
                                "sign")
        return struct.pack(MDMFSIGNABLEHEADER,
                           1,
                           self._seqnum,
                           self._root_hash,
                           self._required_shares,
                           self._total_shares,
                           self._segment_size,
                           self._data_length)


    def put_signature(self, signature):
        """
        I queue a write vector for the signature of the MDMF share.

        I require that the root hash and share hash chain have been put
        to the grid before I will write the signature to the grid.
        """
        if "signature" not in self._offsets:
            raise LayoutInvalid("You must put the share hash chain "
        # It does not make sense to put a signature without first
        # putting the root hash and the salt hash (since otherwise
        # the signature would be incomplete), so we don't allow that.
                       "before putting the signature")
        if not self._root_hash:
            raise LayoutInvalid("You must complete the signed prefix "
                                "before computing a signature")
        # If we put the signature after we put the verification key, we
        # could end up running into the verification key, and will
        # probably screw up the offsets as well. So we don't allow that.
        if "verification_key_end" in self._offsets:
            raise LayoutInvalid("You can't put the signature after the "
                                "verification key")
        # The method that writes the verification key defines the EOF
        # offset before writing the verification key, so look for that.
        self._offsets['verification_key'] = self._offsets['signature'] +\
            len(signature)
        self._writevs.append(tuple([self._offsets['signature'], signature]))


    def put_verification_key(self, verification_key):
        """
        I queue a write vector for the verification key.

        I require that the signature have been written to the storage
        server before I allow the verification key to be written to the
        remote server.
        """
        if "verification_key" not in self._offsets:
            raise LayoutInvalid("You must put the signature before you "
                                "can put the verification key")

        self._offsets['verification_key_end'] = \
            self._offsets['verification_key'] + len(verification_key)
        assert self._offsets['verification_key_end'] <= self._offsets['share_data']
        self._writevs.append(tuple([self._offsets['verification_key'],
                            verification_key]))


    def _get_offsets_tuple(self):
        return tuple([(key, value) for key, value in self._offsets.items()])


    def get_verinfo(self):
        return (self._seqnum,
                self._root_hash,
                self._required_shares,
                self._total_shares,
                self._segment_size,
                self._data_length,
                self.get_signable(),
                self._get_offsets_tuple())


    def finish_publishing(self):
        """
        I add a write vector for the offsets table, and then cause all
        of the write vectors that I've dealt with so far to be published
        to the remote server, ending the write process.
        """
        if "verification_key_end" not in self._offsets:
            raise LayoutInvalid("You must put the verification key before "
                                "you can publish the offsets")
        offsets_offset = struct.calcsize(MDMFHEADERWITHOUTOFFSETS)
        offsets = struct.pack(MDMFOFFSETS,
                              self._offsets['enc_privkey'],
                              self._offsets['share_hash_chain'],
                              self._offsets['signature'],
                              self._offsets['verification_key'],
                              self._offsets['verification_key_end'],
                              self._offsets['share_data'],
                              self._offsets['block_hash_tree'],
                              self._offsets['EOF'])
        self._writevs.append(tuple([offsets_offset, offsets]))
        encoding_parameters_offset = struct.calcsize(MDMFCHECKSTRING)
        params = struct.pack(">BBQQ",
                             self._required_shares,
                             self._total_shares,
                             self._segment_size,
                             self._data_length)
        self._writevs.append(tuple([encoding_parameters_offset, params]))
        return self._write(self._writevs)


    def _write(self, datavs, on_failure=None, on_success=None):
        """I write the data vectors in datavs to the remote slot."""
        tw_vectors = {}
        if not self._testvs:
            self._testvs = []
            self._testvs.append(tuple([0, 1, "eq", ""]))
        if not self._written:
            # Write a new checkstring to the share when we write it, so
            # that we have something to check later.
            new_checkstring = self.get_checkstring()
            datavs.append((0, new_checkstring))
            def _first_write():
                self._written = True
                self._testvs = [(0, len(new_checkstring), "eq", new_checkstring)]
            on_success = _first_write
        tw_vectors[self.shnum] = (self._testvs, datavs, None)
        d = self._rref.callRemote("slot_testv_and_readv_and_writev",
                                  self._storage_index,
                                  self._secrets,
                                  tw_vectors,
                                  self._readv)
        def _result(results):
            if isinstance(results, failure.Failure) or not results[0]:
                # Do nothing; the write was unsuccessful.
                if on_failure: on_failure()
            else:
                if on_success: on_success()
            return results
        d.addCallback(_result)
        return d


class MDMFSlotReadProxy:
    """
    I read from a mutable slot filled with data written in the MDMF data
    format (which is described above).

    I can be initialized with some amount of data, which I will use (if
    it is valid) to eliminate some of the need to fetch it from servers.
    """
    def __init__(self,
                 rref,
                 storage_index,
                 shnum,
                 data=""):
        # Start the initialization process.
        self._rref = rref
        self._storage_index = storage_index
        self.shnum = shnum

        # Before doing anything, the reader is probably going to want to
        # verify that the signature is correct. To do that, they'll need
        # the verification key, and the signature. To get those, we'll
        # need the offset table. So fetch the offset table on the
        # assumption that that will be the first thing that a reader is
        # going to do.

        # The fact that these encoding parameters are None tells us
        # that we haven't yet fetched them from the remote share, so we
        # should. We could just not set them, but the checks will be
        # easier to read if we don't have to use hasattr.
        self._version_number = None
        self._sequence_number = None
        self._root_hash = None
        # Filled in if we're dealing with an SDMF file. Unused
        # otherwise.
        self._salt = None
        self._required_shares = None
        self._total_shares = None
        self._segment_size = None
        self._data_length = None
        self._offsets = None

        # If the user has chosen to initialize us with some data, we'll
        # try to satisfy subsequent data requests with that data before
        # asking the storage server for it. If 
        self._data = data
        # The way callers interact with cache in the filenode returns
        # None if there isn't any cached data, but the way we index the
        # cached data requires a string, so convert None to "".
        if self._data == None:
            self._data = ""


    def _maybe_fetch_offsets_and_header(self, force_remote=False):
        """
        I fetch the offset table and the header from the remote slot if
        I don't already have them. If I do have them, I do nothing and
        return an empty Deferred.
        """
        if self._offsets:
            return defer.succeed(None)
        # At this point, we may be either SDMF or MDMF. Fetching 107 
        # bytes will be enough to get header and offsets for both SDMF and
        # MDMF, though we'll be left with 4 more bytes than we
        # need if this ends up being MDMF. This is probably less
        # expensive than the cost of a second roundtrip.
        readvs = [(0, 123)]
        d = self._read(readvs, force_remote)
        d.addCallback(self._process_encoding_parameters)
        d.addCallback(self._process_offsets)
        return d


    def _process_encoding_parameters(self, encoding_parameters):
        assert self.shnum in encoding_parameters
        encoding_parameters = encoding_parameters[self.shnum][0]
        # The first byte is the version number. It will tell us what
        # to do next.
        (verno,) = struct.unpack(">B", encoding_parameters[:1])
        if verno == MDMF_VERSION:
            read_size = MDMFHEADERWITHOUTOFFSETSSIZE
            (verno,
             seqnum,
             root_hash,
             k,
             n,
             segsize,
             datalen) = struct.unpack(MDMFHEADERWITHOUTOFFSETS,
                                      encoding_parameters[:read_size])
            if segsize == 0 and datalen == 0:
                # Empty file, no segments.
                self._num_segments = 0
            else:
                self._num_segments = mathutil.div_ceil(datalen, segsize)

        elif verno == SDMF_VERSION:
            read_size = SIGNED_PREFIX_LENGTH
            (verno,
             seqnum,
             root_hash,
             salt,
             k,
             n,
             segsize,
             datalen) = struct.unpack(">BQ32s16s BBQQ",
                                encoding_parameters[:SIGNED_PREFIX_LENGTH])
            self._salt = salt
            if segsize == 0 and datalen == 0:
                # empty file
                self._num_segments = 0
            else:
                # non-empty SDMF files have one segment.
                self._num_segments = 1
        else:
            raise UnknownVersionError("You asked me to read mutable file "
                                      "version %d, but I only understand "
                                      "%d and %d" % (verno, SDMF_VERSION,
                                                     MDMF_VERSION))

        self._version_number = verno
        self._sequence_number = seqnum
        self._root_hash = root_hash
        self._required_shares = k
        self._total_shares = n
        self._segment_size = segsize
        self._data_length = datalen

        self._block_size = self._segment_size / self._required_shares
        # We can upload empty files, and need to account for this fact
        # so as to avoid zero-division and zero-modulo errors.
        if datalen > 0:
            tail_size = self._data_length % self._segment_size
        else:
            tail_size = 0
        if not tail_size:
            self._tail_block_size = self._block_size
        else:
            self._tail_block_size = mathutil.next_multiple(tail_size,
                                                    self._required_shares)
            self._tail_block_size /= self._required_shares

        return encoding_parameters


    def _process_offsets(self, offsets):
        if self._version_number == 0:
            read_size = OFFSETS_LENGTH
            read_offset = SIGNED_PREFIX_LENGTH
            end = read_size + read_offset
            (signature,
             share_hash_chain,
             block_hash_tree,
             share_data,
             enc_privkey,
             EOF) = struct.unpack(">LLLLQQ",
                                  offsets[read_offset:end])
            self._offsets = {}
            self._offsets['signature'] = signature
            self._offsets['share_data'] = share_data
            self._offsets['block_hash_tree'] = block_hash_tree
            self._offsets['share_hash_chain'] = share_hash_chain
            self._offsets['enc_privkey'] = enc_privkey
            self._offsets['EOF'] = EOF

        elif self._version_number == 1:
            read_offset = MDMFHEADERWITHOUTOFFSETSSIZE
            read_length = MDMFOFFSETS_LENGTH
            end = read_offset + read_length
            (encprivkey,
             sharehashes,
             signature,
             verification_key,
             verification_key_end,
             sharedata,
             blockhashes,
             eof) = struct.unpack(MDMFOFFSETS,
                                  offsets[read_offset:end])
            self._offsets = {}
            self._offsets['enc_privkey'] = encprivkey
            self._offsets['block_hash_tree'] = blockhashes
            self._offsets['share_hash_chain'] = sharehashes
            self._offsets['signature'] = signature
            self._offsets['verification_key'] = verification_key
            self._offsets['verification_key_end']= \
                verification_key_end
            self._offsets['EOF'] = eof
            self._offsets['share_data'] = sharedata


    def get_block_and_salt(self, segnum):
        """
        I return (block, salt), where block is the block data and
        salt is the salt used to encrypt that segment.
        """
        d = self._maybe_fetch_offsets_and_header()
        def _then(ignored):
            base_share_offset = self._offsets['share_data']

            if segnum + 1 > self._num_segments:
                raise LayoutInvalid("Not a valid segment number")

            if self._version_number == 0:
                share_offset = base_share_offset + self._block_size * segnum
            else:
                share_offset = base_share_offset + (self._block_size + \
                                                    SALT_SIZE) * segnum
            if segnum + 1 == self._num_segments:
                data = self._tail_block_size
            else:
                data = self._block_size

            if self._version_number == 1:
                data += SALT_SIZE

            readvs = [(share_offset, data)]
            return readvs
        d.addCallback(_then)
        d.addCallback(lambda readvs: self._read(readvs))
        def _process_results(results):
            assert self.shnum in results
            if self._version_number == 0:
                # We only read the share data, but we know the salt from
                # when we fetched the header
                data = results[self.shnum]
                if not data:
                    data = ""
                else:
                    assert len(data) == 1
                    data = data[0]
                salt = self._salt
            else:
                data = results[self.shnum]
                if not data:
                    salt = data = ""
                else:
                    salt_and_data = results[self.shnum][0]
                    salt = salt_and_data[:SALT_SIZE]
                    data = salt_and_data[SALT_SIZE:]
            return data, salt
        d.addCallback(_process_results)
        return d


    def get_blockhashes(self, needed=None, force_remote=False):
        """
        I return the block hash tree

        I take an optional argument, needed, which is a set of indices
        correspond to hashes that I should fetch. If this argument is
        missing, I will fetch the entire block hash tree; otherwise, I
        may attempt to fetch fewer hashes, based on what needed says
        that I should do. Note that I may fetch as many hashes as I
        want, so long as the set of hashes that I do fetch is a superset
        of the ones that I am asked for, so callers should be prepared
        to tolerate additional hashes.
        """
        # TODO: Return only the parts of the block hash tree necessary
        # to validate the blocknum provided?
        # This is a good idea, but it is hard to implement correctly. It
        # is bad to fetch any one block hash more than once, so we
        # probably just want to fetch the whole thing at once and then
        # serve it.
        if needed == set([]):
            return defer.succeed([])
        d = self._maybe_fetch_offsets_and_header()
        def _then(ignored):
            blockhashes_offset = self._offsets['block_hash_tree']
            if self._version_number == 1:
                blockhashes_length = self._offsets['EOF'] - blockhashes_offset
            else:
                blockhashes_length = self._offsets['share_data'] - blockhashes_offset
            readvs = [(blockhashes_offset, blockhashes_length)]
            return readvs
        d.addCallback(_then)
        d.addCallback(lambda readvs:
            self._read(readvs, force_remote=force_remote))
        def _build_block_hash_tree(results):
            assert self.shnum in results

            rawhashes = results[self.shnum][0]
            results = [rawhashes[i:i+HASH_SIZE]
                       for i in range(0, len(rawhashes), HASH_SIZE)]
            return results
        d.addCallback(_build_block_hash_tree)
        return d


    def get_sharehashes(self, needed=None, force_remote=False):
        """
        I return the part of the share hash chain placed to validate
        this share.

        I take an optional argument, needed. Needed is a set of indices
        that correspond to the hashes that I should fetch. If needed is
        not present, I will fetch and return the entire share hash
        chain. Otherwise, I may fetch and return any part of the share
        hash chain that is a superset of the part that I am asked to
        fetch. Callers should be prepared to deal with more hashes than
        they've asked for.
        """
        if needed == set([]):
            return defer.succeed([])
        d = self._maybe_fetch_offsets_and_header()

        def _make_readvs(ignored):
            sharehashes_offset = self._offsets['share_hash_chain']
            if self._version_number == 0:
                sharehashes_length = self._offsets['block_hash_tree'] - sharehashes_offset
            else:
                sharehashes_length = self._offsets['signature'] - sharehashes_offset
            readvs = [(sharehashes_offset, sharehashes_length)]
            return readvs
        d.addCallback(_make_readvs)
        d.addCallback(lambda readvs:
            self._read(readvs, force_remote=force_remote))
        def _build_share_hash_chain(results):
            assert self.shnum in results

            sharehashes = results[self.shnum][0]
            results = [sharehashes[i:i+(HASH_SIZE + 2)]
                       for i in range(0, len(sharehashes), HASH_SIZE + 2)]
            results = dict([struct.unpack(">H32s", data)
                            for data in results])
            return results
        d.addCallback(_build_share_hash_chain)
        return d


    def get_encprivkey(self):
        """
        I return the encrypted private key.
        """
        d = self._maybe_fetch_offsets_and_header()

        def _make_readvs(ignored):
            privkey_offset = self._offsets['enc_privkey']
            if self._version_number == 0:
                privkey_length = self._offsets['EOF'] - privkey_offset
            else:
                privkey_length = self._offsets['share_hash_chain'] - privkey_offset
            readvs = [(privkey_offset, privkey_length)]
            return readvs
        d.addCallback(_make_readvs)
        d.addCallback(lambda readvs: self._read(readvs))
        def _process_results(results):
            assert self.shnum in results
            privkey = results[self.shnum][0]
            return privkey
        d.addCallback(_process_results)
        return d


    def get_signature(self):
        """
        I return the signature of my share.
        """
        d = self._maybe_fetch_offsets_and_header()

        def _make_readvs(ignored):
            signature_offset = self._offsets['signature']
            if self._version_number == 1:
                signature_length = self._offsets['verification_key'] - signature_offset
            else:
                signature_length = self._offsets['share_hash_chain'] - signature_offset
            readvs = [(signature_offset, signature_length)]
            return readvs
        d.addCallback(_make_readvs)
        d.addCallback(lambda readvs: self._read(readvs))
        def _process_results(results):
            assert self.shnum in results
            signature = results[self.shnum][0]
            return signature
        d.addCallback(_process_results)
        return d


    def get_verification_key(self):
        """
        I return the verification key.
        """
        d = self._maybe_fetch_offsets_and_header()

        def _make_readvs(ignored):
            if self._version_number == 1:
                vk_offset = self._offsets['verification_key']
                vk_length = self._offsets['verification_key_end'] - vk_offset
            else:
                vk_offset = struct.calcsize(">BQ32s16sBBQQLLLLQQ")
                vk_length = self._offsets['signature'] - vk_offset
            readvs = [(vk_offset, vk_length)]
            return readvs
        d.addCallback(_make_readvs)
        d.addCallback(lambda readvs: self._read(readvs))
        def _process_results(results):
            assert self.shnum in results
            verification_key = results[self.shnum][0]
            return verification_key
        d.addCallback(_process_results)
        return d


    def get_encoding_parameters(self):
        """
        I return (k, n, segsize, datalen)
        """
        d = self._maybe_fetch_offsets_and_header()
        d.addCallback(lambda ignored:
            (self._required_shares,
             self._total_shares,
             self._segment_size,
             self._data_length))
        return d


    def get_seqnum(self):
        """
        I return the sequence number for this share.
        """
        d = self._maybe_fetch_offsets_and_header()
        d.addCallback(lambda ignored:
            self._sequence_number)
        return d


    def get_root_hash(self):
        """
        I return the root of the block hash tree
        """
        d = self._maybe_fetch_offsets_and_header()
        d.addCallback(lambda ignored: self._root_hash)
        return d


    def get_checkstring(self):
        """
        I return the packed representation of the following:

            - version number
            - sequence number
            - root hash
            - salt hash

        which my users use as a checkstring to detect other writers.
        """
        d = self._maybe_fetch_offsets_and_header()
        def _build_checkstring(ignored):
            if self._salt:
                checkstring = struct.pack(PREFIX,
                                          self._version_number,
                                          self._sequence_number,
                                          self._root_hash,
                                          self._salt)
            else:
                checkstring = struct.pack(MDMFCHECKSTRING,
                                          self._version_number,
                                          self._sequence_number,
                                          self._root_hash)

            return checkstring
        d.addCallback(_build_checkstring)
        return d


    def get_prefix(self, force_remote):
        d = self._maybe_fetch_offsets_and_header(force_remote)
        d.addCallback(lambda ignored:
            self._build_prefix())
        return d


    def _build_prefix(self):
        # The prefix is another name for the part of the remote share
        # that gets signed. It consists of everything up to and
        # including the datalength, packed by struct.
        if self._version_number == SDMF_VERSION:
            return struct.pack(SIGNED_PREFIX,
                           self._version_number,
                           self._sequence_number,
                           self._root_hash,
                           self._salt,
                           self._required_shares,
                           self._total_shares,
                           self._segment_size,
                           self._data_length)

        else:
            return struct.pack(MDMFSIGNABLEHEADER,
                           self._version_number,
                           self._sequence_number,
                           self._root_hash,
                           self._required_shares,
                           self._total_shares,
                           self._segment_size,
                           self._data_length)


    def _get_offsets_tuple(self):
        # The offsets tuple is another component of the version
        # information tuple. It is basically our offsets dictionary,
        # itemized and in a tuple.
        return self._offsets.copy()


    def get_verinfo(self):
        """
        I return my verinfo tuple. This is used by the ServermapUpdater
        to keep track of versions of mutable files.

        The verinfo tuple for MDMF files contains:
            - seqnum
            - root hash
            - a blank (nothing)
            - segsize
            - datalen
            - k
            - n
            - prefix (the thing that you sign)
            - a tuple of offsets

        We include the nonce in MDMF to simplify processing of version
        information tuples.

        The verinfo tuple for SDMF files is the same, but contains a
        16-byte IV instead of a hash of salts.
        """
        d = self._maybe_fetch_offsets_and_header()
        def _build_verinfo(ignored):
            if self._version_number == SDMF_VERSION:
                salt_to_use = self._salt
            else:
                salt_to_use = None
            return (self._sequence_number,
                    self._root_hash,
                    salt_to_use,
                    self._segment_size,
                    self._data_length,
                    self._required_shares,
                    self._total_shares,
                    self._build_prefix(),
                    self._get_offsets_tuple())
        d.addCallback(_build_verinfo)
        return d


    def _read(self, readvs, force_remote=False):
        unsatisfiable = filter(lambda x: x[0] + x[1] > len(self._data), readvs)
        # TODO: It's entirely possible to tweak this so that it just
        # fulfills the requests that it can, and not demand that all
        # requests are satisfiable before running it.
        if not unsatisfiable and not force_remote:
            results = [self._data[offset:offset+length]
                       for (offset, length) in readvs]
            results = {self.shnum: results}
            return defer.succeed(results)
        else:
            return self._rref.callRemote("slot_readv",
                                         self._storage_index,
                                         [self.shnum],
                                         readvs)


    def is_sdmf(self):
        """I tell my caller whether or not my remote file is SDMF or MDMF
        """
        d = self._maybe_fetch_offsets_and_header()
        d.addCallback(lambda ignored:
            self._version_number == 0)
        return d


class LayoutInvalid(Exception):
    """
    This isn't a valid MDMF mutable file
    """
