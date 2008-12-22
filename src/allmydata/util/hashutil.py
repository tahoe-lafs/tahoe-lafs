from pycryptopp.hash.sha256 import SHA256
import os
from allmydata.util.netstring import netstring

# Be very very cautious when modifying this file. Almost any change will
# cause a compatibility break, invalidating all outstanding URIs and making
# any previously uploaded files become inaccessible. BE CONSERVATIVE AND TEST
# AGAINST OLD DATA!

# Various crypto values are this size: hash outputs (from SHA-256d),
# randomly-generated secrets such as the lease secret, and symmetric encryption
# keys.  In the near future we will add DSA private keys, and salts of various
# kinds.
CRYPTO_VAL_SIZE=32

class _SHA256d_Hasher:
    # use SHA-256d, as defined by Ferguson and Schneier: hash the output
    # again to prevent length-extension attacks
    def __init__(self, truncate_to=None):
        self.h = SHA256()
        self.truncate_to = truncate_to
        self._digest = None
    def update(self, data):
        assert isinstance(data, str) # no unicode
        self.h.update(data)
    def digest(self):
        if self._digest is None:
            h1 = self.h.digest()
            del self.h
            h2 = SHA256(h1).digest()
            if self.truncate_to:
                h2 = h2[:self.truncate_to]
            self._digest = h2
        return self._digest



def tagged_hasher(tag, truncate_to=None):
    hasher = _SHA256d_Hasher(truncate_to)
    hasher.update(netstring(tag))
    return hasher

def tagged_hash(tag, val, truncate_to=None):
    hasher = tagged_hasher(tag, truncate_to)
    hasher.update(val)
    return hasher.digest()

def tagged_pair_hash(tag, val1, val2, truncate_to=None):
    s = _SHA256d_Hasher(truncate_to)
    s.update(netstring(tag))
    s.update(netstring(val1))
    s.update(netstring(val2))
    return s.digest()

## specific hash tags that we use

# immutable
STORAGE_INDEX_TAG = "allmydata_immutable_key_to_storage_index_v1"
BLOCK_TAG = "allmydata_encoded_subshare_v1"
UEB_TAG = "allmydata_uri_extension_v1"
PLAINTEXT_TAG = "allmydata_plaintext_v1"
CIPHERTEXT_TAG = "allmydata_crypttext_v1"
CIPHERTEXT_SEGMENT_TAG = "allmydata_crypttext_segment_v1"
PLAINTEXT_SEGMENT_TAG = "allmydata_plaintext_segment_v1"
CONVERGENT_ENCRYPTION_TAG = "allmydata_immutable_content_to_key_with_added_secret_v1+"

CLIENT_RENEWAL_TAG = "allmydata_client_renewal_secret_v1"
CLIENT_CANCEL_TAG = "allmydata_client_cancel_secret_v1"
FILE_RENEWAL_TAG = "allmydata_file_renewal_secret_v1"
FILE_CANCEL_TAG = "allmydata_file_cancel_secret_v1"
BUCKET_RENEWAL_TAG = "allmydata_bucket_renewal_secret_v1"
BUCKET_CANCEL_TAG = "allmydata_bucket_cancel_secret_v1"

# mutable
MUTABLE_WRITEKEY_TAG = "allmydata_mutable_privkey_to_writekey_v1"
MUTABLE_WRITE_ENABLER_MASTER_TAG = "allmydata_mutable_writekey_to_write_enabler_master_v1"
MUTABLE_WRITE_ENABLER_TAG = "allmydata_mutable_write_enabler_master_and_nodeid_to_write_enabler_v1"
MUTABLE_PUBKEY_TAG = "allmydata_mutable_pubkey_to_fingerprint_v1"
MUTABLE_READKEY_TAG = "allmydata_mutable_writekey_to_readkey_v1"
MUTABLE_DATAKEY_TAG = "allmydata_mutable_readkey_to_datakey_v1"
MUTABLE_STORAGEINDEX_TAG = "allmydata_mutable_readkey_to_storage_index_v1"

# dirnodes
DIRNODE_CHILD_WRITECAP_TAG = "allmydata_mutable_writekey_and_salt_to_dirnode_child_capkey_v1"

def storage_index_hash(key):
    # storage index is truncated to 128 bits (16 bytes). We're only hashing a
    # 16-byte value to get it, so there's no point in using a larger value.  We
    # use this same tagged hash to go from encryption key to storage index for
    # random-keyed immutable files and convergent-encryption immutabie
    # files. Mutable files use ssk_storage_index_hash().
    return tagged_hash(STORAGE_INDEX_TAG, key, 16)

def block_hash(data):
    return tagged_hash(BLOCK_TAG, data)
def block_hasher():
    return tagged_hasher(BLOCK_TAG)

def uri_extension_hash(data):
    return tagged_hash(UEB_TAG, data)
def uri_extension_hasher():
    return tagged_hasher(UEB_TAG)

def plaintext_hash(data):
    return tagged_hash(PLAINTEXT_TAG, data)
def plaintext_hasher():
    return tagged_hasher(PLAINTEXT_TAG)

def crypttext_hash(data):
    return tagged_hash(CIPHERTEXT_TAG, data)
def crypttext_hasher():
    return tagged_hasher(CIPHERTEXT_TAG)

def crypttext_segment_hash(data):
    return tagged_hash(CIPHERTEXT_SEGMENT_TAG, data)
def crypttext_segment_hasher():
    return tagged_hasher(CIPHERTEXT_SEGMENT_TAG)

def plaintext_segment_hash(data):
    return tagged_hash(PLAINTEXT_SEGMENT_TAG, data)
def plaintext_segment_hasher():
    return tagged_hasher(PLAINTEXT_SEGMENT_TAG)

KEYLEN = 16

def convergence_hash(k, n, segsize, data, convergence):
    h = convergence_hasher(k, n, segsize, convergence)
    h.update(data)
    return h.digest()
def convergence_hasher(k, n, segsize, convergence):
    assert isinstance(convergence, str)
    param_tag = netstring("%d,%d,%d" % (k, n, segsize))
    tag = CONVERGENT_ENCRYPTION_TAG + netstring(convergence) + param_tag
    return tagged_hasher(tag, KEYLEN)

def random_key():
    return os.urandom(KEYLEN)

def my_renewal_secret_hash(my_secret):
    return tagged_hash(my_secret, CLIENT_RENEWAL_TAG)
def my_cancel_secret_hash(my_secret):
    return tagged_hash(my_secret, CLIENT_CANCEL_TAG)

def file_renewal_secret_hash(client_renewal_secret, storage_index):
    return tagged_pair_hash(FILE_RENEWAL_TAG,
                            client_renewal_secret, storage_index)

def file_cancel_secret_hash(client_cancel_secret, storage_index):
    return tagged_pair_hash(FILE_CANCEL_TAG,
                            client_cancel_secret, storage_index)

def bucket_renewal_secret_hash(file_renewal_secret, peerid):
    assert len(peerid) == 20, "%s: %r" % (len(peerid), peerid) # binary!
    return tagged_pair_hash(BUCKET_RENEWAL_TAG, file_renewal_secret, peerid)

def bucket_cancel_secret_hash(file_cancel_secret, peerid):
    assert len(peerid) == 20, "%s: %r" % (len(peerid), peerid) # binary!
    return tagged_pair_hash(BUCKET_CANCEL_TAG, file_cancel_secret, peerid)


def _xor(a, b):
    return "".join([chr(ord(c) ^ ord(b)) for c in a])

def hmac(tag, data):
    ikey = _xor(tag, "\x36")
    okey = _xor(tag, "\x5c")
    h1 = SHA256(ikey + data).digest()
    h2 = SHA256(okey + h1).digest()
    return h2

def mutable_rwcap_key_hash(iv, writekey):
    return tagged_pair_hash(DIRNODE_CHILD_WRITECAP_TAG, iv, writekey, KEYLEN)

def ssk_writekey_hash(privkey):
    return tagged_hash(MUTABLE_WRITEKEY_TAG, privkey, KEYLEN)
def ssk_write_enabler_master_hash(writekey):
    return tagged_hash(MUTABLE_WRITE_ENABLER_MASTER_TAG, writekey)
def ssk_write_enabler_hash(writekey, peerid):
    assert len(peerid) == 20, "%s: %r" % (len(peerid), peerid) # binary!
    wem = ssk_write_enabler_master_hash(writekey)
    return tagged_pair_hash(MUTABLE_WRITE_ENABLER_TAG, wem, peerid)

def ssk_pubkey_fingerprint_hash(pubkey):
    return tagged_hash(MUTABLE_PUBKEY_TAG, pubkey)

def ssk_readkey_hash(writekey):
    return tagged_hash(MUTABLE_READKEY_TAG, writekey, KEYLEN)
def ssk_readkey_data_hash(IV, readkey):
    return tagged_pair_hash(MUTABLE_DATAKEY_TAG, IV, readkey, KEYLEN)
def ssk_storage_index_hash(readkey):
    return tagged_hash(MUTABLE_STORAGEINDEX_TAG, readkey, KEYLEN)
