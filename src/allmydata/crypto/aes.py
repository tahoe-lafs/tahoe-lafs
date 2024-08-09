"""
Helper functions for cryptograhpy-related operations inside Tahoe
using AES

These functions use and return objects that are documented in the
`cryptography` library -- however, code inside Tahoe should only use
functions from allmydata.crypto.aes and not rely on features of any
objects that `cryptography` documents.

Ported to Python 3.
"""

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import (
    Cipher,
    algorithms,
    modes,
    CipherContext,
)
from attrs import frozen


DEFAULT_IV = b'\x00' * 16


# Note: previously Zope Interfaces were used to "mark" the Cipher
# directly; after cryptography 43.0.0 that became impossible and so
# wrapper objects are used to ensure that create_encryptor() is used
# to make the encryptor/decryptor objects


@frozen
class Encryptor:
    """
    An object which can encrypt data.

    Create one using :func:`create_encryptor` and use it with
    :func:`encrypt_data`

    There are no public methods or members.
    """
    _cipher: CipherContext


@frozen
class Decryptor:
    """
    An object which can decrypt data.

    Create one using :func:`create_decryptor` and use it with
    :func:`decrypt_data`

    There are no public methods or members.
    """
    _cipher: CipherContext


def create_encryptor(key, iv=None):
    """
    Create and return a new object which can do AES encryptions with
    the given key and initialization vector (IV). The default IV is 16
    zero-bytes.

    :param bytes key: the key bytes, should be 128 or 256 bits (16 or
        32 bytes)

    :param bytes iv: the Initialization Vector consisting of 16 bytes,
        or None for the default (which is 16 zero bytes)

    :returns Encryptor: an object suitable for use with :func:`encrypt_data`
    """
    cryptor = _create_cryptor(key, iv)
    return Encryptor(cryptor)


def encrypt_data(encryptor, plaintext):
    """
    AES-encrypt `plaintext` with the given `encryptor`.

    :param encryptor: an instance of :class:`IEncryptor` previously
        returned from `create_encryptor`

    :param bytes plaintext: the data to encrypt

    :returns: bytes of ciphertext
    """

    _validate_cryptor(encryptor, encrypt=True)
    if not isinstance(plaintext, (bytes, memoryview)):
        raise ValueError(f'Plaintext must be bytes or memoryview: {type(plaintext)}')

    return encryptor._cipher.update(plaintext)


def create_decryptor(key, iv=None):
    """
    Create and return a new object which can do AES decryptions with
    the given key and initialization vector (IV). The default IV is 16
    zero-bytes.

    :param bytes key: the key bytes, should be 128 or 256 bits (16 or
        32 bytes)

    :param bytes iv: the Initialization Vector consisting of 16 bytes,
        or None for the default (which is 16 zero bytes)

    :returns Decryptor: an object suitable for use with :func:`decrypt_data`
    """
    cryptor = _create_cryptor(key, iv)
    return Decryptor(cryptor)


def decrypt_data(decryptor, plaintext):
    """
    AES-decrypt `plaintext` with the given `decryptor`.

    :param decryptor: an instance of :class:`IDecryptor` previously
        returned from `create_decryptor`

    :param bytes plaintext: the data to decrypt

    :returns: bytes of ciphertext
    """

    _validate_cryptor(decryptor, encrypt=False)
    if not isinstance(plaintext, (bytes, memoryview)):
        raise ValueError(f'Plaintext must be bytes or memoryview: {type(plaintext)}')

    return decryptor._cipher.update(plaintext)


def _create_cryptor(key, iv):
    """
    Internal helper.

    See :func:`create_encryptor` or :func:`create_decryptor`.
    """
    key = _validate_key(key)
    iv = _validate_iv(iv)
    cipher = Cipher(
        algorithms.AES(key),
        modes.CTR(iv),
        backend=default_backend()
    )
    return cipher.encryptor()


def _validate_cryptor(cryptor, encrypt=True):
    """
    raise ValueError if `cryptor` is not a valid object
    """
    klass = Encryptor if encrypt else Decryptor
    name = "encryptor" if encrypt else "decryptor"
    if not isinstance(cryptor, klass):
        raise ValueError(
            "'{}' must be created with create_{}()".format(name, name)
        )
    assert isinstance(cryptor._cipher, CipherContext), "Internal inconsistency"


def _validate_key(key):
    """
    confirm `key` is suitable for AES encryption, or raise ValueError
    """
    if not isinstance(key, bytes):
        raise TypeError('Key must be bytes')
    if len(key) not in (16, 32):
        raise ValueError('Key must be 16 or 32 bytes long')
    return key


def _validate_iv(iv):
    """
    Returns a suitable initialiation vector. If `iv` is `None`, a
    default is returned. If `iv` is not a suitable initialization
    vector an error is raised. `iv` is returned if it valid.
    """
    if iv is None:
        return DEFAULT_IV
    if not isinstance(iv, bytes):
        raise TypeError('IV must be bytes')
    if len(iv) != 16:
        raise ValueError('IV must be 16 bytes long')
    return iv
