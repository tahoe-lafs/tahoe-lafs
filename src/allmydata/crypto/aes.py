import six

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

class AES:

    __DEFAULT_IV = '\x00' * 16

    def __init__(self, key, iv=None):
        # validate the key
        if not isinstance(key, six.binary_type):
            raise TypeError('Key was not bytes')
        if len(key) not in (16, 32):
            raise ValueError('Key was not 16 or 32 bytes long')

        # validate the IV
        if iv is None:
            iv = self.__DEFAULT_IV
        if not isinstance(iv, six.binary_type):
            raise TypeError('IV was not bytes')
        if len(iv) != 16:
            raise ValueError('IV was not 16 bytes long')

        self._cipher = Cipher(algorithms.AES(key), modes.CTR(iv), backend=default_backend())
        self._encryptor = self._cipher.encryptor()

    def process(self, plaintext):
        if not isinstance(plaintext, six.binary_type):
            raise TypeError('Plaintext was not bytes')

        return self._encryptor.update(plaintext)
