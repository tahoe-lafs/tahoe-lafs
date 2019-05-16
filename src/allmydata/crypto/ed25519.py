'''
Ed25519 keys and helpers.

Key Formatting
--------------

- in base32, keys are 52 chars long (both signing and verifying keys)
- in base62, keys is 43 chars long
- in base64, keys is 43 chars long

We can't use base64 because we want to reserve punctuation and preserve
cut-and-pasteability. The base62 encoding is shorter than the base32 form,
but the minor usability improvement is not worth the documentation and
specification confusion of using a non-standard encoding. So we stick with
base32.
'''

import six

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, \
    PublicFormat

# When we were still using `pycryptopp`, BadSignatureError was the name of the exception, and now
# that we've switched to `cryptography`, we are importing and "re-exporting" this to keep the name
# the same (for now).
from cryptography.exceptions import InvalidSignature
BadSignatureError = InvalidSignature
del InvalidSignature

from allmydata.util.base32 import a2b, b2a

_PRIV_PREFIX = 'priv-v0-'
_PUB_PREFIX = 'pub-v0-'


class BadPrefixError(Exception):
    pass


def _remove_prefix(s_bytes, prefix):
    if not s_bytes.startswith(prefix):
        raise BadPrefixError("did not see expected '%s' prefix" % (prefix,))
    return s_bytes[len(prefix):]


class SigningKey:

    def __init__(self, priv_key):
        if not isinstance(priv_key, Ed25519PrivateKey):
            raise ValueError('priv_key must be an Ed25519PrivateKey')
        self._priv_key = priv_key

    @classmethod
    def generate(cls):
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, priv_bytes):
        if not isinstance(priv_bytes, six.binary_type):
            raise ValueError('priv_bytes must be bytes')
        return SigningKey(Ed25519PrivateKey.from_private_bytes(priv_bytes))

    def private_bytes(self):
        return self._priv_key.private_bytes(
            Encoding.Raw,
            PrivateFormat.Raw,
            NoEncryption(),
        )

    def public_key(self):
        return VerifyingKey(self._priv_key.public_key())

    def sign(self, data):
        if not isinstance(data, six.binary_type):
            raise ValueError('data must be bytes')
        return self._priv_key.sign(data)

    @classmethod
    def parse_encoded_key(cls, priv_str):
        global _PRIV_PREFIX
        return cls.from_private_bytes(a2b(_remove_prefix(priv_str, _PRIV_PREFIX)))

    def encoded_key(self):
        global _PRIV_PREFIX
        return _PRIV_PREFIX + a2b(self.private_bytes())

    def __eq__(self, other):
        if isinstance(other, type(self)):
            return self.private_bytes() == other.private_bytes()
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)


class VerifyingKey:

    def __init__(self, pub_key):
        if not isinstance(pub_key, Ed25519PublicKey):
            raise ValueError('pub_key must be an Ed25519PublicKey')
        self._pub_key = pub_key

    @classmethod
    def from_public_bytes(cls, pub_bytes):
        if not isinstance(pub_bytes, six.binary_type):
            raise ValueError('pub_bytes must be bytes')
        return cls(Ed25519PublicKey.from_public_bytes(pub_bytes))

    def public_bytes(self):
        return self._pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    def verify(self, signature, data):
        if not isinstance(signature, six.binary_type):
            raise ValueError('signature must be bytes')

        if not isinstance(data, six.binary_type):
            raise ValueError('data must be bytes')

        self._pub_key.verify(signature, data)

    @classmethod
    def parse_encoded_key(cls, pub_str):
        global _PUB_PREFIX
        return cls.from_public_bytes(a2b(_remove_prefix(pub_str, _PUB_PREFIX)))

    def encoded_key(self):
        global _PUB_PREFIX
        return _PUB_PREFIX + a2b(self.public_bytes())

    def __eq__(self, other):
        if isinstance(other, type(self)):
            return self.public_bytes() == other.public_bytes()
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)
