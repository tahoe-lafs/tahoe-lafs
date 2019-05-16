import six

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, \
    PublicFormat


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
