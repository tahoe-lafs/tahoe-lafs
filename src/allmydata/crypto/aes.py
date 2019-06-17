import six

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import (
    Cipher,
    algorithms,
    modes,
    CipherContext,
)


DEFAULT_IV = '\x00' * 16


def create_encryptor(key, iv=None):
    """
    Create and return a new object which can do AES encryptions with
    the given key and initialization vector (IV). The default IV is 16
    zero-bytes.

    The returned object is suitable for use with `encrypt_data`
    """
    key = _validate_key(key)
    iv = _validate_iv(iv)
    cipher = Cipher(
        algorithms.AES(key),
        modes.CTR(iv),
        backend=default_backend()
    )
    return cipher.encryptor()


def encrypt_data(encryptor, plaintext):
    """
    AES-encrypt `plaintext` with the given `encryptor`.

    :param encryptor: an instance previously returned from `create_encryptor`

    :param bytes plaintext: the data to encrypt

    :returns: ciphertext
    """

    _validate_encryptor(encryptor)
    if not isinstance(plaintext, six.binary_type):
        raise ValueError('Plaintext was not bytes')

    return encryptor.update(plaintext)


create_decryptor = create_encryptor


decrypt_data = encrypt_data


def _validate_encryptor(encryptor):
    """
    raise ValueError for `encryptor` is not a valid object
    """
    if not isinstance(encryptor, CipherContext):
        raise ValueError(
            "'encryptor' must be a CipherContext"
        )


def _validate_key(key):
    """
    confirm `key` is suitable for AES encryption, or raise ValueError
    """
    if not isinstance(key, six.binary_type):
        raise TypeError('Key was not bytes')
    if len(key) not in (16, 32):
        raise ValueError('Key was not 16 or 32 bytes long')
    return key


def _validate_iv(iv):
    """
    confirm `iv` is a suitable initialization vector
    """
    if iv is None:
        return DEFAULT_IV
    if not isinstance(iv, six.binary_type):
        raise TypeError('IV was not bytes')
    if len(iv) != 16:
        raise ValueError('IV was not 16 bytes long')
    return iv
