
import re, simplejson
from allmydata.util import keyutil, base32

def make_index(ann, key_s):
    """Return something that can be used as an index (e.g. a tuple of
    strings), such that two messages that refer to the same 'thing' will have
    the same index. This is a tuple of (service-name, signing-key, None) for
    signed announcements, or (service-name, None, tubid) for unsigned
    announcements."""

    service_name = str(ann["service-name"])
    if key_s:
        return (service_name, key_s, None)
    else:
        tubid = get_tubid_string_from_ann(ann)
        return (service_name, None, tubid)

def get_tubid_string_from_ann(ann):
    return get_tubid_string(str(ann.get("anonymous-storage-FURL")
                                or ann.get("FURL")))

def get_tubid_string(furl):
    m = re.match(r'pb://(\w+)@', furl)
    assert m
    return m.group(1).lower()

def convert_announcement_v1_to_v2(ann_t):
    (furl, service_name, ri_name, nickname, ver, oldest) = ann_t
    assert type(furl) is str
    assert type(service_name) is str
    # ignore ri_name
    assert type(nickname) is str
    assert type(ver) is str
    assert type(oldest) is str
    ann = {"version": 0,
           "nickname": nickname.decode("utf-8"),
           "app-versions": {},
           "my-version": ver,
           "oldest-supported": oldest,

           "service-name": service_name,
           "anonymous-storage-FURL": furl,
           "permutation-seed-base32": get_tubid_string(furl),
           }
    msg = simplejson.dumps(ann).encode("utf-8")
    return (msg, None, None)

def convert_announcement_v2_to_v1(ann_v2):
    (msg, sig, pubkey) = ann_v2
    ann = simplejson.loads(msg)
    assert ann["version"] == 0
    ann_t = (str(ann["anonymous-storage-FURL"]),
             str(ann["service-name"]),
             "remoteinterface-name is unused",
             ann["nickname"].encode("utf-8"),
             str(ann["my-version"]),
             str(ann["oldest-supported"]),
             )
    return ann_t


def sign_to_foolscap(ann, sk):
    # return (bytes, None, None) or (bytes, sig-str, pubkey-str). A future
    # HTTP-based serialization will use JSON({msg:b64(JSON(msg).utf8),
    # sig:v0-b64(sig), pubkey:v0-b64(pubkey)}) .
    msg = simplejson.dumps(ann).encode("utf-8")
    if sk:
        sig = "v0-"+base32.b2a(sk.sign(msg))
        vk_bytes = sk.get_verifying_key_bytes()
        ann_t = (msg, sig, "v0-"+base32.b2a(vk_bytes))
    else:
        ann_t = (msg, None, None)
    return ann_t

class UnknownKeyError(Exception):
    pass

def unsign_from_foolscap(ann_t):
    (msg, sig_vs, claimed_key_vs) = ann_t
    key_vs = None
    if sig_vs and claimed_key_vs:
        if not sig_vs.startswith("v0-"):
            raise UnknownKeyError("only v0- signatures recognized")
        if not claimed_key_vs.startswith("v0-"):
            raise UnknownKeyError("only v0- keys recognized")
        claimed_key = keyutil.parse_pubkey("pub-"+claimed_key_vs)
        sig_bytes = base32.a2b(keyutil.remove_prefix(sig_vs, "v0-"))
        claimed_key.verify(sig_bytes, msg)
        key_vs = claimed_key_vs
    ann = simplejson.loads(msg.decode("utf-8"))
    return (ann, key_vs)
