from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.serialization import load_der_private_key, load_der_public_key, \
    Encoding, PrivateFormat, PublicFormat, NoEncryption

from allmydata.crypto import BadSignature


class RsaMixin(object):

    '''
    This is the value that was used by `pycryptopp`, and we must continue to use it for
    both backwards compatibility and interoperability.

    The docs for `cryptography` suggest to use the constant defined at
    `cryptography.hazmat.primitives.asymmetric.padding.PSS.MAX_LENGTH`, but this causes old
    signatures to fail to validate.
    '''
    RSA_PSS_SALT_LENGTH = 32


class PrivateKey(RsaMixin):

    def __init__(self, priv_key):
        self._priv_key = priv_key

    @classmethod
    def generate(cls, key_size):
        priv_key = rsa.generate_private_key(
            public_exponent=65537,  # serisously don't change this value
            key_size=key_size,
            backend=default_backend()
        )
        return cls(priv_key)

    @classmethod
    def parse_string(cls, priv_key_str):
        priv_key = load_der_private_key(
            priv_key_str,
            password=None,
            backend=default_backend(),
        )
        return cls(priv_key)

    def public_key(self):
        return PublicKey(self._priv_key.public_key())

    def serialize(self):
        return self._priv_key.private_bytes(
            encoding=Encoding.DER,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )

    def sign(self, data):
        return self._priv_key.sign(
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=self.RSA_PSS_SALT_LENGTH,
            ),
            hashes.SHA256(),
        )

    def __eq__(self, other):
        if isinstance(other, type(self)):
            return self.serialize() == other.serialize()
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)


class PublicKey(RsaMixin):

    def __init__(self, pub_key):
        self._pub_key = pub_key

    @classmethod
    def parse_string(cls, pub_key_str):
        pub_key = load_der_public_key(
            pub_key_str,
            backend=default_backend(),
        )
        return cls(pub_key)

    def serialize(self):
        return self._pub_key.public_bytes(
            encoding=Encoding.DER,
            format=PublicFormat.SubjectPublicKeyInfo,
        )

    def verify(self, signature, data):
        try:
            self._pub_key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=self.RSA_PSS_SALT_LENGTH,
                ),
                hashes.SHA256(),
            )
        except InvalidSignature:
            raise BadSignature

    def __eq__(self, other):
        if isinstance(other, type(self)):
            return self.serialize() == other.serialize()
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)
