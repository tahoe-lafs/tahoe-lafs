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

