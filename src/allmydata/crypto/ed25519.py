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

from cryptography.exceptions import (
    InvalidSignature,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    NoEncryption,
    PublicFormat,
)

from allmydata.crypto.util import remove_prefix
from allmydata.crypto.error import BadSignature

from allmydata.util.base32 import (
    a2b,
    b2a,
)

PRIVATE_KEY_PREFIX = b'priv-v0-'
PUBLIC_KEY_PREFIX = b'pub-v0-'


def create_signing_keypair():
    """
    Creates a new ed25519 keypair.

    :returns: 2-tuple of (private_key, public_key)
    """
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def verifying_key_from_signing_key(private_key):
    """
    :returns: the public key associated to the given `private_key`
    """
    _validate_private_key(private_key)
    return private_key.public_key()


def sign_data(private_key, data: bytes) -> bytes:
    """
    Sign the given data using the given private key

    :param private_key: the private part returned from
        `create_signing_keypair` or from
        `signing_keypair_from_string`

    :param bytes data: the data to sign

    :returns: bytes representing the signature
    """

    _validate_private_key(private_key)
    if not isinstance(data, bytes):
        raise ValueError('data must be bytes')
    return private_key.sign(data)


def string_from_signing_key(private_key):
    """
    Encode a private key to a string of bytes

    :param private_key: the private part returned from
        `create_signing_keypair` or from
        `signing_keypair_from_string`

    :returns: byte-string representing this key
    """
    _validate_private_key(private_key)
    raw_key_bytes = private_key.private_bytes(
        Encoding.Raw,
        PrivateFormat.Raw,
        NoEncryption(),
    )
    return PRIVATE_KEY_PREFIX + b2a(raw_key_bytes)


def signing_keypair_from_string(private_key_bytes: bytes):
    """
    Load a signing keypair from a string of bytes (which includes the
    PRIVATE_KEY_PREFIX)

    :returns: a 2-tuple of (private_key, public_key)
    """

    if not isinstance(private_key_bytes, bytes):
        raise ValueError('private_key_bytes must be bytes')

    private_key = Ed25519PrivateKey.from_private_bytes(
        a2b(remove_prefix(private_key_bytes, PRIVATE_KEY_PREFIX))
    )
    return private_key, private_key.public_key()


def verify_signature(public_key, alleged_signature: bytes, data: bytes):
    """
    :param public_key: a verifying key

    :param bytes alleged_signature: the bytes of the alleged signature

    :param bytes data: the data which was allegedly signed

    :raises: BadSignature if the signature is bad
    :returns: None (or raises an exception).
    """

    if not isinstance(alleged_signature, bytes):
        raise ValueError('alleged_signature must be bytes')

    if not isinstance(data, bytes):
        raise ValueError('data must be bytes')

    _validate_public_key(public_key)
    try:
        public_key.verify(alleged_signature, data)
    except InvalidSignature:
        raise BadSignature()


def verifying_key_from_string(public_key_bytes):
    """
    Load a verifying key from a string of bytes (which includes the
    PUBLIC_KEY_PREFIX)

    :returns: a public_key
    """
    if not isinstance(public_key_bytes, bytes):
        raise ValueError('public_key_bytes must be bytes')

    return Ed25519PublicKey.from_public_bytes(
        a2b(remove_prefix(public_key_bytes, PUBLIC_KEY_PREFIX))
    )


def string_from_verifying_key(public_key) -> bytes:
    """
    Encode a public key to a string of bytes

    :param public_key: the public part of a keypair

    :returns: byte-string representing this key
    """
    _validate_public_key(public_key)
    raw_key_bytes = public_key.public_bytes(
        Encoding.Raw,
        PublicFormat.Raw,
    )
    return PUBLIC_KEY_PREFIX + b2a(raw_key_bytes)


def _validate_public_key(public_key: Ed25519PublicKey):
    """
    Internal helper. Verify that `public_key` is an appropriate object
    """
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError('public_key must be an Ed25519PublicKey')
    return None


def _validate_private_key(private_key: Ed25519PrivateKey):
    """
    Internal helper. Verify that `private_key` is an appropriate object
    """
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError('private_key must be an Ed25519PrivateKey')
    return None
