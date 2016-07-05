
import re, simplejson
from allmydata.util import keyutil, base32, rrefutil

def get_tubid_string_from_ann(ann):
    return get_tubid_string(str(ann.get("anonymous-storage-FURL")
                                or ann.get("FURL")))

def get_tubid_string(furl):
    m = re.match(r'pb://(\w+)@', furl)
    assert m
    return m.group(1).lower()


def sign_to_foolscap(ann, sk):
    # return (bytes, sig-str, pubkey-str). A future HTTP-based serialization
    # will use JSON({msg:b64(JSON(msg).utf8), sig:v0-b64(sig),
    # pubkey:v0-b64(pubkey)}) .
    msg = simplejson.dumps(ann).encode("utf-8")
    sig = "v0-"+base32.b2a(sk.sign(msg))
    vk_bytes = sk.get_verifying_key_bytes()
    ann_t = (msg, sig, "v0-"+base32.b2a(vk_bytes))
    return ann_t

class UnknownKeyError(Exception):
    pass

def unsign_from_foolscap(ann_t):
    (msg, sig_vs, claimed_key_vs) = ann_t
    if not sig_vs or not claimed_key_vs:
        raise UnknownKeyError("only signed announcements recognized")
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

class SubscriberDescriptor:
    """This describes a subscriber, for status display purposes. It contains
    the following attributes:

    .service_name: what they subscribed to (string)
    .when: time when they subscribed (seconds since epoch)
    .nickname: their self-provided nickname, or "?" (unicode)
    .version: their self-provided version (string)
    .app_versions: versions of each library they use (dict str->str)
    .remote_address: the external address from which they connected (string)
    .tubid: for subscribers connecting with Foolscap, their tubid (string)
    """

    def __init__(self, service_name, when,
                 nickname, version, app_versions,
                 remote_address, tubid):
        self.service_name = service_name
        self.when = when
        self.nickname = nickname
        self.version = version
        self.app_versions = app_versions
        self.remote_address = remote_address
        self.tubid = tubid

class AnnouncementDescriptor:
    """This describes an announcement, for status display purposes. It
    contains the following attributes, which will be empty ("" for
    strings) if the client did not provide them:

     .when: time the announcement was first received (seconds since epoch)
     .index: the announcements 'index', a tuple of (string-or-None).
             The server remembers one announcement per index.
     .canary: a Referenceable on the announcer, so the server can learn
              when they disconnect (for the status display)
     .announcement: raw dictionary of announcement data
     .service_name: which service they are announcing (string)
     .version: 'my-version' portion of announcement (string)
     .nickname: their self-provided nickname, or "" (unicode)
     .serverid: the server identifier. This is a pubkey (for V2 clients),
                or a tubid (for V1 clients).
     .connection_hints: where they listen (list of strings) if the
                        announcement included a key for
                        'anonymous-storage-FURL', else an empty list.
    """

    def __init__(self, when, index, canary, ann_d):
        self.when = when
        self.index = index
        self.canary = canary
        self.announcement = ann_d
        self.service_name = ann_d["service-name"]
        self.version = ann_d.get("my-version", "")
        self.nickname = ann_d.get("nickname", u"")
        (service_name, key_s) = index
        self.serverid = key_s
        furl = ann_d.get("anonymous-storage-FURL")
        if furl:
            self.connection_hints = rrefutil.connection_hints_for_furl(furl)
        else:
            self.connection_hints = []
