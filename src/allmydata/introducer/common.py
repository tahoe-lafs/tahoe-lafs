"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import re

from foolscap.furl import decode_furl
from allmydata.crypto.util import remove_prefix
from allmydata.crypto import ed25519
from allmydata.util import base32, jsonbytes as json


def get_tubid_string_from_ann(ann):
    furl = ann.get("anonymous-storage-FURL") or ann.get("FURL")
    return get_tubid_string(furl)

def get_tubid_string(furl):
    m = re.match(r'pb://(\w+)@', furl)
    assert m
    return m.group(1).lower().encode("ascii")


def sign_to_foolscap(announcement, signing_key):
    """
    :param signing_key: a (private) signing key, as returned from
        e.g. :func:`allmydata.crypto.ed25519.signing_keypair_from_string`

    :returns: 3-tuple of (msg, sig, vk) where msg is a UTF8 JSON
        serialization of the `announcement` (bytes), sig is bytes (a
        signature of msg) and vk is the verifying key bytes
    """
    # return (bytes, sig-str, pubkey-str). A future HTTP-based serialization
    # will use JSON({msg:b64(JSON(msg).utf8), sig:v0-b64(sig),
    # pubkey:v0-b64(pubkey)}) .
    msg = json.dumps(announcement).encode("utf-8")
    sig = b"v0-" + base32.b2a(
        ed25519.sign_data(signing_key, msg)
    )
    verifying_key_string = ed25519.string_from_verifying_key(
        ed25519.verifying_key_from_signing_key(signing_key)
    )
    ann_t = (msg, sig, remove_prefix(verifying_key_string, b"pub-"))
    return ann_t


class UnknownKeyError(Exception):
    pass


def unsign_from_foolscap(ann_t):
    (msg, sig_vs, claimed_key_vs) = ann_t
    if not sig_vs or not claimed_key_vs:
        raise UnknownKeyError("only signed announcements recognized")
    if not sig_vs.startswith(b"v0-"):
        raise UnknownKeyError("only v0- signatures recognized")
    if not claimed_key_vs.startswith(b"v0-"):
        raise UnknownKeyError("only v0- keys recognized")

    claimed_key = ed25519.verifying_key_from_string(b"pub-" + claimed_key_vs)
    sig_bytes = base32.a2b(remove_prefix(sig_vs, b"v0-"))
    ed25519.verify_signature(claimed_key, sig_bytes, msg)
    key_vs = claimed_key_vs
    ann = json.loads(msg.decode("utf-8"))
    return (ann, key_vs)


class SubscriberDescriptor(object):
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

class AnnouncementDescriptor(object):
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
        (_, key_s) = index
        self.serverid = key_s
        furl = ann_d.get("anonymous-storage-FURL")
        if furl:
            _, self.connection_hints, _ = decode_furl(furl)
        else:
            self.connection_hints = []
