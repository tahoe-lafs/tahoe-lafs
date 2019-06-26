"""
Helper functions for cryptography-related operations inside Tahoe
using RSA public-key encryption and decryption.

In cases where these functions happen to use and return objects that
are documented in the `cryptography` library, code outside this module
should only use functions from allmydata.crypto.rsa and not rely on
features of any objects that `cryptography` documents.

That is, the public and private keys are opaque objects; DO NOT depend
on any of their methods.
"""


from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.serialization import load_der_private_key, load_der_public_key, \
    Encoding, PrivateFormat, PublicFormat, NoEncryption

from allmydata.crypto.error import BadSignature


# This is the value that was used by `pycryptopp`, and we must continue to use it for
# both backwards compatibility and interoperability.
#
# The docs for `cryptography` suggest to use the constant defined at
# `cryptography.hazmat.primitives.asymmetric.padding.PSS.MAX_LENGTH`, but this causes old
# signatures to fail to validate.
RSA_PSS_SALT_LENGTH = 32

RSA_PADDING = padding.PSS(
    mgf=padding.MGF1(hashes.SHA256()),
    salt_length=RSA_PSS_SALT_LENGTH,
)



def create_signing_keypair(key_size):
    """
    Create a new RSA signing (private) keypair from scratch. Can be used with
    `sign_data` function.

    :param int key_size: length of key in bits

    :returns: 2-tuple of (private_key, public_key)
    """
    # Tahoe's original use of pycryptopp would use cryptopp's default
    # public_exponent, which is 17
    #
    # Thus, we are using 17 here as well. However, there are other
    # choices; see this for more discussion:
    # https://security.stackexchange.com/questions/2335/should-rsa-public-exponent-be-only-in-3-5-17-257-or-65537-due-to-security-c
    #
    # Another popular choice is 65537. See:
    # https://cryptography.io/en/latest/hazmat/primitives/asymmetric/rsa/#cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key
    # https://www.daemonology.net/blog/2009-06-11-cryptographic-right-answers.html
    priv_key = rsa.generate_private_key(
        public_exponent=17,
        key_size=key_size,
        backend=default_backend()
    )
    return priv_key, priv_key.public_key()


def create_signing_keypair_from_string(private_key_der):
    """
    Create an RSA signing (private) key from previously serialized
    private key bytes.

    :param bytes private_key_der: blob as returned from `der_string_from_signing_keypair`

    :returns: 2-tuple of (private_key, public_key)
    """
    priv_key = load_der_private_key(
        private_key_der,
        password=None,
        backend=default_backend(),
    )
    return priv_key, priv_key.public_key()


def der_string_from_signing_key(private_key):
    """
    Serializes a given RSA private key to a DER string

    :param private_key: a private key object as returned from
        `create_signing_keypair` or `create_signing_keypair_from_string`

    :returns: bytes representing `private_key`
    """
    _validate_private_key(private_key)
    return private_key.private_bytes(
        encoding=Encoding.DER,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )


def der_string_from_verifying_key(public_key):
    """
    Serializes a given RSA public key to a DER string.

    :param public_key: a public key object as returned from
        `create_signing_keypair` or `create_signing_keypair_from_string`

    :returns: bytes representing `public_key`
    """
    _validate_public_key(public_key)
    return public_key.public_bytes(
        encoding=Encoding.DER,
        format=PublicFormat.SubjectPublicKeyInfo,
    )


def create_verifying_key_from_string(public_key_der):
    """
    Create an RSA verifying key from a previously serialized public key

    :param bytes public_key_der: a blob as returned by `der_string_from_verifying_key`

    :returns: a public key object suitable for use with other
        functions in this module
    """
    pub_key = load_der_public_key(
        public_key_der,
        backend=default_backend(),
    )
    return pub_key


def sign_data(private_key, data):
    """
    :param private_key: the private part of a keypair returned from
        `create_signing_keypair_from_string` or `create_signing_keypair`

    :param bytes data: the bytes to sign

    :returns: bytes which are a signature of the bytes given as `data`.
    """
    _validate_private_key(private_key)
    return private_key.sign(
        data,
        RSA_PADDING,
        hashes.SHA256(),
    )

def verify_signature(public_key, alleged_signature, data):
    """
    :param public_key: a verifying key, returned from `create_verifying_key_from_string` or `create_verifying_key_from_private_key`

    :param bytes alleged_signature: the bytes of the alleged signature

    :param bytes data: the data which was allegedly signed
    """
    _validate_public_key(public_key)
    try:
        public_key.verify(
            alleged_signature,
            data,
            RSA_PADDING,
            hashes.SHA256(),
        )
    except InvalidSignature:
        raise BadSignature()


def _validate_public_key(public_key):
    """
    Internal helper. Checks that `public_key` is a valid cryptography
    object
    """
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError(
            "public_key must be an RSAPublicKey"
        )


def _validate_private_key(private_key):
    """
    Internal helper. Checks that `public_key` is a valid cryptography
    object
    """
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError(
            "private_key must be an RSAPrivateKey"
        )
