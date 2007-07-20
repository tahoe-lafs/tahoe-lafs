from allmydata.Crypto.Hash import SHA256
import os

def netstring(s):
    return "%d:%s," % (len(s), s,)

def tagged_hash(tag, val):
    s = SHA256.new()
    s.update(netstring(tag))
    s.update(val)
    return s.digest()
            
def tagged_pair_hash(tag, val1, val2):
    s = SHA256.new()
    s.update(netstring(tag))
    s.update(netstring(val1))
    s.update(netstring(val2))
    return s.digest()

# specific hash tags that we use

def tagged_hasher(tag):
    return SHA256.new(netstring(tag))

def storage_index_chk_hash(data):
    return tagged_hash("allmydata_CHK_storage_index_v1", data)

def block_hash(data):
    return tagged_hash("allmydata_encoded_subshare_v1", data)
def block_hasher():
    return tagged_hasher("allmydata_encoded_subshare_v1")

def uri_extension_hash(data):
    return tagged_hash("allmydata_uri_extension_v1", data)
def uri_extension_hasher():
    return tagged_hasher("allmydata_uri_extension_v1")

def plaintext_hash(data):
    return tagged_hash("allmydata_plaintext_hash_v1", data)
def plaintext_hasher():
    return tagged_hasher("allmydata_plaintext_hash_v1")

def crypttext_hash(data):
    return tagged_hash("allmydata_crypttext_hash_v1", data)
def crypttext_hasher():
    return tagged_hasher("allmydata_crypttext_hash_v1")

def crypttext_segment_hash(data):
    return tagged_hash("allmydata_crypttext_segment_v1", data)
def crypttext_segment_hasher():
    return tagged_hasher("allmydata_crypttext_segment_v1")

def plaintext_segment_hash(data):
    return tagged_hash("allmydata_plaintext_segment_v1", data)
def plaintext_segment_hasher():
    return tagged_hasher("allmydata_plaintext_segment_v1")

def key_hash(data):
    return tagged_hash("allmydata_encryption_key_v1", data)
def key_hasher():
    return tagged_hasher("allmydata_encryption_key_v1")

KEYLEN = 16
def random_key():
    return os.urandom(KEYLEN)

def dir_write_enabler_hash(write_key):
    return tagged_hash("allmydata_dir_write_enabler_v1", write_key)
def dir_read_key_hash(write_key):
    return tagged_hash("allmydata_dir_read_key_v1", write_key)[:KEYLEN]
def dir_index_hash(read_key):
    return tagged_hash("allmydata_dir_index_v1", read_key)
def dir_name_hash(readkey, name):
    return tagged_pair_hash("allmydata_dir_name_v1", readkey, name)

def generate_dirnode_keys_from_writekey(write_key):
    readkey = dir_read_key_hash(write_key)
    write_enabler = dir_write_enabler_hash(write_key)
    index = dir_index_hash(readkey)
    return write_key, write_enabler, readkey, index

def generate_dirnode_keys_from_readkey(read_key):
    index = dir_index_hash(read_key)
    return None, None, read_key, index

def _xor(a, b):
    return "".join([chr(ord(c) ^ ord(b)) for c in a])

def hmac(tag, data):
    ikey = _xor(tag, "\x36")
    okey = _xor(tag, "\x5c")
    h1 = SHA256.new(ikey + data).digest()
    h2 = SHA256.new(okey + h1).digest()
    return h2
