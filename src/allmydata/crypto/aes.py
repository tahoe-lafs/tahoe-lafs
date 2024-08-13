"""
Helper functions for cryptograhpy-related operations inside Tahoe
using AES

These functions use and return objects that are documented in the
`cryptography` library -- however, code inside Tahoe should only use
functions from allmydata.crypto.aes and not rely on features of any
objects that `cryptography` documents.

Ported to Python 3.
"""

from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import (
    Cipher,
    algorithms,
    modes,
    CipherContext,
)


DEFAULT_IV = b'\x00' * 16


@dataclass
class Encryptor:
    """
    An object which can encrypt data.

    Create one using :func:`create_encryptor` and use it with
    :func:`encrypt_data`
    """
    encrypt_context: CipherContext


@dataclass
class Decryptor:
    """
    An object which can decrypt data.

    Create one using :func:`create_decryptor` and use it with
    :func:`decrypt_data`
    """
    decrypt_context: CipherContext


def create_encryptor(key: bytes, iv: Optional[bytes]=None) -> Encryptor:
    """
    Create and return a new object which can do AES encryptions with
    the given key and initialization vector (IV). The default IV is 16
    zero-bytes.

    :param bytes key: the key bytes, should be 128 or 256 bits (16 or
        32 bytes)

    :param bytes iv: the Initialization Vector consisting of 16 bytes,
        or None for the default (which is 16 zero bytes)

    :returns: an object suitable for use with :func:`encrypt_data` (an
        :class:`Encryptor`)
    """
    cryptor = _create_cryptor(key, iv)
    return Encryptor(cryptor)


def encrypt_data(encryptor: Encryptor, plaintext: bytes) -> bytes:
    """
    AES-encrypt `plaintext` with the given `encryptor`.

    :param encryptor: an instance of :class:`Encryptor` previously
        returned from `create_encryptor`

    :param bytes plaintext: the data to encrypt

    :returns: bytes of ciphertext
    """
    if not isinstance(plaintext, (bytes, memoryview)):
        raise ValueError(f'Plaintext must be bytes or memoryview: {type(plaintext)}')

    return encryptor.encrypt_context.update(plaintext)


def create_decryptor(key: bytes, iv: Optional[bytes]=None) -> Decryptor:
    """
    Create and return a new object which can do AES decryptions with
    the given key and initialization vector (IV). The default IV is 16
    zero-bytes.

    :param bytes key: the key bytes, should be 128 or 256 bits (16 or
        32 bytes)

    :param bytes iv: the Initialization Vector consisting of 16 bytes,
        or None for the default (which is 16 zero bytes)

    :returns: an object suitable for use with :func:`decrypt_data` (an
        :class:`Decryptor` instance)
    """
    cryptor = _create_cryptor(key, iv)
    return Decryptor(cryptor)


def decrypt_data(decryptor: Decryptor, plaintext: bytes) -> bytes:
    """
    AES-decrypt `plaintext` with the given `decryptor`.

    :param decryptor: an instance of :class:`Decryptor` previously
        returned from `create_decryptor`

    :param bytes plaintext: the data to decrypt

    :returns: bytes of ciphertext
    """
    if not isinstance(plaintext, (bytes, memoryview)):
        raise ValueError(f'Plaintext must be bytes or memoryview: {type(plaintext)}')

    return decryptor.decrypt_context.update(plaintext)


def _create_cryptor(key: bytes, iv: Optional[bytes]) -> CipherContext:
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
    return cipher.encryptor()  # type: ignore[return-type]


def _validate_key(key: bytes) -> bytes:
    """
    confirm `key` is suitable for AES encryption, or raise ValueError
    """
    if not isinstance(key, bytes):
        raise TypeError('Key must be bytes')
    if len(key) not in (16, 32):
        raise ValueError('Key must be 16 or 32 bytes long')
    return key


def _validate_iv(iv: Optional[bytes]) -> bytes:
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
