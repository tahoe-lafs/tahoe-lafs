"""
Helper functions for cryptograhpy-related operations inside Tahoe
using AES

These functions use and return objects that are documented in the
`cryptography` library -- however, code inside Tahoe should only use
functions from allmydata.crypto.aes and not rely on features of any
objects that `cryptography` documents.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import six

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import (
    Cipher,
    algorithms,
    modes,
    CipherContext,
)
from zope.interface import (
    Interface,
    directlyProvides,
)


DEFAULT_IV = b'\x00' * 16


class IEncryptor(Interface):
    """
    An object which can encrypt data.

    Create one using :func:`create_encryptor` and use it with
    :func:`encrypt_data`
    """


class IDecryptor(Interface):
    """
    An object which can decrypt data.

    Create one using :func:`create_decryptor` and use it with
    :func:`decrypt_data`
    """


def create_encryptor(key, iv=None):
    """
    Create and return a new object which can do AES encryptions with
    the given key and initialization vector (IV). The default IV is 16
    zero-bytes.

    :param bytes key: the key bytes, should be 128 or 256 bits (16 or
        32 bytes)

    :param bytes iv: the Initialization Vector consisting of 16 bytes,
        or None for the default (which is 16 zero bytes)

    :returns: an object suitable for use with :func:`encrypt_data` (an
        :class:`IEncryptor`)
    """
    cryptor = _create_cryptor(key, iv)
    directlyProvides(cryptor, IEncryptor)
    return cryptor


def encrypt_data(encryptor, plaintext):
    """
    AES-encrypt `plaintext` with the given `encryptor`.

    :param encryptor: an instance of :class:`IEncryptor` previously
        returned from `create_encryptor`

    :param bytes plaintext: the data to encrypt

    :returns: bytes of ciphertext
    """

    _validate_cryptor(encryptor, encrypt=True)
    if not isinstance(plaintext, six.binary_type):
        raise ValueError('Plaintext must be bytes')

    return encryptor.update(plaintext)


def create_decryptor(key, iv=None):
    """
    Create and return a new object which can do AES decryptions with
    the given key and initialization vector (IV). The default IV is 16
    zero-bytes.

    :param bytes key: the key bytes, should be 128 or 256 bits (16 or
        32 bytes)

    :param bytes iv: the Initialization Vector consisting of 16 bytes,
        or None for the default (which is 16 zero bytes)

    :returns: an object suitable for use with :func:`decrypt_data` (an
        :class:`IDecryptor` instance)
    """
    cryptor = _create_cryptor(key, iv)
    directlyProvides(cryptor, IDecryptor)
    return cryptor


def decrypt_data(decryptor, plaintext):
    """
    AES-decrypt `plaintext` with the given `decryptor`.

    :param decryptor: an instance of :class:`IDecryptor` previously
        returned from `create_decryptor`

    :param bytes plaintext: the data to decrypt

    :returns: bytes of ciphertext
    """

    _validate_cryptor(decryptor, encrypt=False)
    if not isinstance(plaintext, six.binary_type):
        raise ValueError('Plaintext must be bytes')

    return decryptor.update(plaintext)


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
    klass = IEncryptor if encrypt else IDecryptor
    name = "encryptor" if encrypt else "decryptor"
    if not isinstance(cryptor, CipherContext):
        raise ValueError(
            "'{}' must be a CipherContext".format(name)
        )
    if not klass.providedBy(cryptor):
        raise ValueError(
            "'{}' must be created with create_{}()".format(name, name)
        )


def _validate_key(key):
    """
    confirm `key` is suitable for AES encryption, or raise ValueError
    """
    if not isinstance(key, six.binary_type):
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
    if not isinstance(iv, six.binary_type):
        raise TypeError('IV must be bytes')
    if len(iv) != 16:
        raise ValueError('IV must be 16 bytes long')
    return iv
