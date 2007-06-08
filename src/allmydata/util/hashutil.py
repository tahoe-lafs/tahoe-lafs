from allmydata.Crypto.Hash import SHA256

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

def block_hash(data):
    return tagged_hash("allmydata_encoded_subshare_v1", data)
def block_hasher():
    return tagged_hasher("allmydata_encoded_subshare_v1")

def uri_extension_hash(data):
    return tagged_hash("allmydata_uri_extension_v1", data)
def uri_extension_hasher():
    return tagged_hasher("allmydata_uri_extension_v1")

def fileid_hash(data):
    return tagged_hash("allmydata_fileid_v1", data)
def fileid_hasher():
    return tagged_hasher("allmydata_fileid_v1")

def verifierid_hash(data):
    return tagged_hash("allmydata_verifierid_v1", data)
def verifierid_hasher():
    return tagged_hasher("allmydata_verifierid_v1")

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

