import os
from pycryptopp.publickey import ed25519
from allmydata.util.base32 import a2b, b2a

BadSignatureError = ed25519.BadSignatureError

class BadPrefixError(Exception):
    pass

def remove_prefix(s_bytes, prefix):
    if not s_bytes.startswith(prefix):
        raise BadPrefixError("did not see expected '%s' prefix" % (prefix,))
    return s_bytes[len(prefix):]

# in base32, keys are 52 chars long (both signing and verifying keys)
# in base62, keys is 43 chars long
# in base64, keys is 43 chars long
#
# We can't use base64 because we want to reserve punctuation and preserve
# cut-and-pasteability. The base62 encoding is shorter than the base32 form,
# but the minor usability improvement is not worth the documentation and
# specification confusion of using a non-standard encoding. So we stick with
# base32.

def make_keypair():
    sk_bytes = os.urandom(32)
    sk = ed25519.SigningKey(sk_bytes)
    vk_bytes = sk.get_verifying_key_bytes()
    return ("priv-v0-"+b2a(sk_bytes), "pub-v0-"+b2a(vk_bytes))

def parse_privkey(privkey_vs):
    sk_bytes = a2b(remove_prefix(privkey_vs, "priv-v0-"))
    sk = ed25519.SigningKey(sk_bytes)
    vk_bytes = sk.get_verifying_key_bytes()
    return (sk, "pub-v0-"+b2a(vk_bytes))

def parse_pubkey(pubkey_vs):
    vk_bytes = a2b(remove_prefix(pubkey_vs, "pub-v0-"))
    return ed25519.VerifyingKey(vk_bytes)
