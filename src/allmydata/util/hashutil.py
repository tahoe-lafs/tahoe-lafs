"""
Hashing utilities.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    # Don't import bytes to prevent leaking future's bytes.
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, dict, list, object, range, str, max, min, bytes as future_bytes  # noqa: F401
else:
    future_bytes = bytes

from past.builtins import chr as byteschr

import os
import hashlib
from allmydata.util.netstring import netstring

from typing import Optional

# Be very very cautious when modifying this file. Almost any change will
# cause a compatibility break, invalidating all outstanding URIs and making
# any previously uploaded files become inaccessible. BE CONSERVATIVE AND TEST
# AGAINST OLD DATA!

# Various crypto values are this size: hash outputs (from SHA-256d),
# randomly-generated secrets such as the lease secret, and symmetric encryption
# keys.  In the near future we will add DSA private keys, and salts of various
# kinds.
CRYPTO_VAL_SIZE = 32


class _SHA256d_Hasher(object):
    # use SHA-256d, as defined by Ferguson and Schneier: hash the output
    # again to prevent length-extension attacks
    def __init__(self, truncate_to: Optional[int]=None) -> None:
        self.h = hashlib.sha256()
        self.truncate_to = truncate_to
        self._digest: Optional[bytes] = None

    def update(self, data: bytes) -> None:
        assert isinstance(data, bytes)  # no unicode
        self.h.update(data)

    def digest(self) -> bytes:
        if self._digest is None:
            h1 = self.h.digest()
            del self.h
            h2 = hashlib.sha256(h1).digest()
            if self.truncate_to:
                h2 = h2[:self.truncate_to]
            self._digest = h2
        return self._digest


def tagged_hasher(tag: bytes, truncate_to: Optional[int]=None) -> _SHA256d_Hasher:
    hasher: _SHA256d_Hasher = _SHA256d_Hasher(truncate_to)
    hasher.update(netstring(tag))
    return hasher


def tagged_hash(tag: bytes, val: bytes, truncate_to: Optional[int]=None) -> bytes:
    hasher: _SHA256d_Hasher = tagged_hasher(tag, truncate_to)
    hasher.update(val)
    return hasher.digest()


def tagged_pair_hash(tag: bytes, val1: bytes, val2: bytes, truncate_to: Optional[int]=None) -> bytes:
    s: _SHA256d_Hasher = _SHA256d_Hasher(truncate_to)
    s.update(netstring(tag))
    s.update(netstring(val1))
    s.update(netstring(val2))
    return s.digest()

# specific hash tags that we use


# immutable
STORAGE_INDEX_TAG = b"allmydata_immutable_key_to_storage_index_v1"
BLOCK_TAG = b"allmydata_encoded_subshare_v1"
UEB_TAG = b"allmydata_uri_extension_v1"
PLAINTEXT_TAG = b"allmydata_plaintext_v1"
CIPHERTEXT_TAG = b"allmydata_crypttext_v1"
CIPHERTEXT_SEGMENT_TAG = b"allmydata_crypttext_segment_v1"
PLAINTEXT_SEGMENT_TAG = b"allmydata_plaintext_segment_v1"
CONVERGENT_ENCRYPTION_TAG = b"allmydata_immutable_content_to_key_with_added_secret_v1+"

CLIENT_RENEWAL_TAG = b"allmydata_client_renewal_secret_v1"
CLIENT_CANCEL_TAG = b"allmydata_client_cancel_secret_v1"
FILE_RENEWAL_TAG = b"allmydata_file_renewal_secret_v1"
FILE_CANCEL_TAG = b"allmydata_file_cancel_secret_v1"
BUCKET_RENEWAL_TAG = b"allmydata_bucket_renewal_secret_v1"
BUCKET_CANCEL_TAG = b"allmydata_bucket_cancel_secret_v1"

# mutable
MUTABLE_WRITEKEY_TAG = b"allmydata_mutable_privkey_to_writekey_v1"
MUTABLE_WRITE_ENABLER_MASTER_TAG = b"allmydata_mutable_writekey_to_write_enabler_master_v1"
MUTABLE_WRITE_ENABLER_TAG = b"allmydata_mutable_write_enabler_master_and_nodeid_to_write_enabler_v1"
MUTABLE_PUBKEY_TAG = b"allmydata_mutable_pubkey_to_fingerprint_v1"
MUTABLE_READKEY_TAG = b"allmydata_mutable_writekey_to_readkey_v1"
MUTABLE_DATAKEY_TAG = b"allmydata_mutable_readkey_to_datakey_v1"
MUTABLE_STORAGEINDEX_TAG = b"allmydata_mutable_readkey_to_storage_index_v1"

# dirnodes
DIRNODE_CHILD_WRITECAP_TAG = b"allmydata_mutable_writekey_and_salt_to_dirnode_child_capkey_v1"
DIRNODE_CHILD_SALT_TAG = b"allmydata_dirnode_child_rwcap_to_salt_v1"


def storage_index_hash(key: bytes) -> bytes:
    # storage index is truncated to 128 bits (16 bytes). We're only hashing a
    # 16-byte value to get it, so there's no point in using a larger value.  We
    # use this same tagged hash to go from encryption key to storage index for
    # random-keyed immutable files and convergent-encryption immutabie
    # files. Mutable files use ssk_storage_index_hash().
    return tagged_hash(STORAGE_INDEX_TAG, key, 16)


def block_hash(data: bytes) -> bytes:
    return tagged_hash(BLOCK_TAG, data)


def block_hasher() -> _SHA256d_Hasher:
    return tagged_hasher(BLOCK_TAG)


def uri_extension_hash(data: bytes) -> bytes:
    return tagged_hash(UEB_TAG, data)


def uri_extension_hasher() -> _SHA256d_Hasher:
    return tagged_hasher(UEB_TAG)


def plaintext_hash(data: bytes) -> bytes:
    return tagged_hash(PLAINTEXT_TAG, data)


def plaintext_hasher() -> _SHA256d_Hasher:
    return tagged_hasher(PLAINTEXT_TAG)


def crypttext_hash(data: bytes) -> bytes:
    return tagged_hash(CIPHERTEXT_TAG, data)


def crypttext_hasher() -> _SHA256d_Hasher:
    return tagged_hasher(CIPHERTEXT_TAG)


def crypttext_segment_hash(data: bytes) -> bytes:
    return tagged_hash(CIPHERTEXT_SEGMENT_TAG, data)


def crypttext_segment_hasher() -> _SHA256d_Hasher:
    return tagged_hasher(CIPHERTEXT_SEGMENT_TAG)


def plaintext_segment_hash(data: bytes) -> bytes:
    return tagged_hash(PLAINTEXT_SEGMENT_TAG, data)


def plaintext_segment_hasher() -> _SHA256d_Hasher:
    return tagged_hasher(PLAINTEXT_SEGMENT_TAG)


KEYLEN = 16
IVLEN = 16


def convergence_hash(k: int, n: int, segsize: int, data: bytes, convergence: bytes) -> bytes:
    h: _SHA256d_Hasher = convergence_hasher(k, n, segsize, convergence)
    h.update(data)
    return h.digest()


def _convergence_hasher_tag(k: int, n: int, segsize: int, convergence: bytes) -> bytes:
    """
    Create the convergence hashing tag.

    :param int k: Required shares (in [1..256]).
    :param int n: Total shares (in [1..256]).
    :param int segsize: Maximum segment size.
    :param bytes convergence: The convergence secret.

    :return bytes: The bytestring to use as a tag in the convergence hash.
    """
    assert isinstance(convergence, bytes)
    if k > n:
        raise ValueError(
            "k > n not allowed; k = {}, n = {}".format(k, n),
        )
    if k < 1 or n < 1:
        # It doesn't make sense to have zero shares.  Zero shares carry no
        # information, cannot encode any part of the application data.
        raise ValueError(
            "k, n < 1 not allowed; k = {}, n = {}".format(k, n),
        )
    if k > 256 or n > 256:
        # ZFEC supports encoding application data into a maximum of 256
        # shares.  If we ignore the limitations of ZFEC, it may be fine to use
        # a configuration with more shares than that and it may be fine to
        # construct a convergence tag from such a configuration.  Since ZFEC
        # is the only supported encoder, though, this is moot for now.
        raise ValueError(
            "k, n > 256 not allowed; k = {}, n = {}".format(k, n),
        )
    param_tag = netstring(b"%d,%d,%d" % (k, n, segsize))
    tag = CONVERGENT_ENCRYPTION_TAG + netstring(convergence) + param_tag
    return tag


def convergence_hasher(k: int, n: int, segsize: int, convergence: bytes) -> _SHA256d_Hasher:
    tag = _convergence_hasher_tag(k, n, segsize, convergence)
    return tagged_hasher(tag, KEYLEN)


def random_key() -> bytes:
    return os.urandom(KEYLEN)


def my_renewal_secret_hash(my_secret: bytes) -> bytes:
    return tagged_hash(my_secret, CLIENT_RENEWAL_TAG)


def my_cancel_secret_hash(my_secret: bytes) -> bytes:
    return tagged_hash(my_secret, CLIENT_CANCEL_TAG)


def file_renewal_secret_hash(client_renewal_secret: bytes, storage_index: bytes) -> bytes:
    return tagged_pair_hash(FILE_RENEWAL_TAG,
                            client_renewal_secret, storage_index)


def file_cancel_secret_hash(client_cancel_secret: bytes, storage_index: bytes) -> bytes:
    return tagged_pair_hash(FILE_CANCEL_TAG,
                            client_cancel_secret, storage_index)


def bucket_renewal_secret_hash(file_renewal_secret: bytes, peerid: bytes) -> bytes:
    assert len(peerid) == 20, "%s: %r" % (len(peerid), peerid)  # binary!
    return tagged_pair_hash(BUCKET_RENEWAL_TAG, file_renewal_secret, peerid)


def bucket_cancel_secret_hash(file_cancel_secret: bytes, peerid: bytes) -> bytes:
    assert len(peerid) == 20, "%s: %r" % (len(peerid), peerid)  # binary!
    return tagged_pair_hash(BUCKET_CANCEL_TAG, file_cancel_secret, peerid)


def _xor(a: bytes, b: int) -> bytes:
    return b"".join([byteschr(c ^ b) for c in future_bytes(a)])


def hmac(tag: bytes, data: bytes) -> bytes:
    tag = bytes(tag)  # Make sure it matches Python 3 behavior
    ikey = _xor(tag, 0x36)
    okey = _xor(tag, 0x5c)
    h1 = hashlib.sha256(ikey + data).digest()
    h2 = hashlib.sha256(okey + h1).digest()
    return h2


def mutable_rwcap_key_hash(iv: bytes, writekey: bytes) -> bytes:
    return tagged_pair_hash(DIRNODE_CHILD_WRITECAP_TAG, iv, writekey, KEYLEN)


def mutable_rwcap_salt_hash(writekey: bytes) -> bytes:
    return tagged_hash(DIRNODE_CHILD_SALT_TAG, writekey, IVLEN)


def ssk_writekey_hash(privkey: bytes) -> bytes:
    return tagged_hash(MUTABLE_WRITEKEY_TAG, privkey, KEYLEN)


def ssk_write_enabler_master_hash(writekey: bytes) -> bytes:
    return tagged_hash(MUTABLE_WRITE_ENABLER_MASTER_TAG, writekey)


def ssk_write_enabler_hash(writekey: bytes, peerid: bytes) -> bytes:
    assert len(peerid) == 20, "%s: %r" % (len(peerid), peerid)  # binary!
    wem = ssk_write_enabler_master_hash(writekey)
    return tagged_pair_hash(MUTABLE_WRITE_ENABLER_TAG, wem, peerid)


def ssk_pubkey_fingerprint_hash(pubkey: bytes) -> bytes:
    return tagged_hash(MUTABLE_PUBKEY_TAG, pubkey)


def ssk_readkey_hash(writekey: bytes) -> bytes:
    return tagged_hash(MUTABLE_READKEY_TAG, writekey, KEYLEN)


def ssk_readkey_data_hash(IV: bytes, readkey: bytes) -> bytes:
    return tagged_pair_hash(MUTABLE_DATAKEY_TAG, IV, readkey, KEYLEN)


def ssk_storage_index_hash(readkey: bytes) -> bytes:
    return tagged_hash(MUTABLE_STORAGEINDEX_TAG, readkey, KEYLEN)


def timing_safe_compare(a: bytes, b: bytes) -> bool:
    n = os.urandom(32)
    return bool(tagged_hash(n, a) == tagged_hash(n, b))


BACKUPDB_DIRHASH_TAG = b"allmydata_backupdb_dirhash_v1"


def backupdb_dirhash(contents: bytes) -> bytes:
    return tagged_hash(BACKUPDB_DIRHASH_TAG, contents)


def permute_server_hash(peer_selection_index: bytes, server_permutation_seed: bytes) -> bytes:
    return hashlib.sha1(peer_selection_index + server_permutation_seed).digest()
