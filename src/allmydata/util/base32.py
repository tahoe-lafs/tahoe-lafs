"""
Base32 encoding.

Ported to Python 3.
"""
def backwardscompat_bytes(b):
    return b
maketrans = bytes.maketrans

from typing import Optional
import base64

from allmydata.util.assertutil import precondition

rfc3548_alphabet = b"abcdefghijklmnopqrstuvwxyz234567" # RFC3548 standard used by Gnutella, Content-Addressable Web, THEX, Bitzi, Web-Calculus...
chars = rfc3548_alphabet

vals = bytes(range(32))
c2vtranstable = maketrans(chars, vals)
v2ctranstable = maketrans(vals, chars)
identitytranstable = maketrans(b'', b'')

def _get_trailing_chars_without_lsbs(N, d):
    """
    @return: a list of chars that can legitimately appear in the last place when the least significant N bits are ignored.
    """
    s = []
    if N < 4:
        s.extend(_get_trailing_chars_without_lsbs(N+1, d=d))
    i = 0
    while i < len(chars):
        if i not in d:
            d[i] = None
            s.append(chars[i:i+1])
        i = i + 2**N
    return s

def get_trailing_chars_without_lsbs(N):
    precondition((N >= 0) and (N < 5), "N is required to be > 0 and < len(chars).", N=N)
    if N == 0:
        return chars
    d = {}
    return b''.join(_get_trailing_chars_without_lsbs(N, d=d))

BASE32CHAR = b'['+get_trailing_chars_without_lsbs(0)+b']'
BASE32CHAR_4bits = b'['+get_trailing_chars_without_lsbs(1)+b']'
BASE32CHAR_3bits = b'['+get_trailing_chars_without_lsbs(2)+b']'
BASE32CHAR_2bits = b'['+get_trailing_chars_without_lsbs(3)+b']'
BASE32CHAR_1bits = b'['+get_trailing_chars_without_lsbs(4)+b']'
BASE32STR_1byte = BASE32CHAR+BASE32CHAR_3bits
BASE32STR_2bytes = BASE32CHAR+b'{3}'+BASE32CHAR_1bits
BASE32STR_3bytes = BASE32CHAR+b'{4}'+BASE32CHAR_4bits
BASE32STR_4bytes = BASE32CHAR+b'{6}'+BASE32CHAR_2bits
BASE32STR_anybytes = bytes(b'((?:%s{8})*') % (BASE32CHAR,) + bytes(b"(?:|%s|%s|%s|%s))") % (BASE32STR_1byte, BASE32STR_2bytes, BASE32STR_3bytes, BASE32STR_4bytes)

def b2a(os):  # type: (bytes) -> bytes
    """
    @param os the data to be encoded (as bytes)

    @return the contents of os in base-32 encoded form, as bytes
    """
    return base64.b32encode(os).rstrip(b"=").lower()

def b2a_or_none(os: Optional[bytes]) -> Optional[bytes]:
    if os is not None:
        return b2a(os)
    return None

# b2a() uses the minimal number of quintets sufficient to encode the binary
# input.  It just so happens that the relation is like this (everything is
# modulo 40 bits).
# num_qs = NUM_OS_TO_NUM_QS[num_os]
NUM_OS_TO_NUM_QS=(0, 2, 4, 5, 7,)

# num_os = NUM_QS_TO_NUM_OS[num_qs], but if not NUM_QS_LEGIT[num_qs] then
# there is *no* number of octets which would have resulted in this number of
# quintets, so either the encoded string has been mangled (truncated) or else
# you were supposed to decode it with a2b_l() (which means you were supposed
# to know the actual length of the encoded data).

NUM_QS_TO_NUM_OS=(0, 1, 1, 2, 2, 3, 3, 4)
NUM_QS_LEGIT=(1, 0, 1, 0, 1, 1, 0, 1,)
NUM_QS_TO_NUM_BITS=tuple([_x*8 for _x in NUM_QS_TO_NUM_OS])

# A fast way to determine whether a given string *could* be base-32 encoded data, assuming that the
# original data had 8K bits for a positive integer K.
# The boolean value of s8[len(s)%8][ord(s[-1])], where s is the possibly base-32 encoded string
# tells whether the final character is reasonable.
def add_check_array(cs, sfmap):
    checka=[0] * 256
    for c in bytes(cs):
        checka[c] = 1
    sfmap.append(tuple(checka))

def init_s8():
    s8 = []
    add_check_array(chars, s8)
    for lenmod8 in (1, 2, 3, 4, 5, 6, 7,):
        if NUM_QS_LEGIT[lenmod8]:
            add_check_array(get_trailing_chars_without_lsbs(4-(NUM_QS_TO_NUM_BITS[lenmod8]%5)), s8)
        else:
            add_check_array(b'', s8)
    return tuple(s8)
s8 = init_s8()

def could_be_base32_encoded(s, s8=s8, tr=bytes.translate, identitytranstable=identitytranstable, chars=chars):
    precondition(isinstance(s, bytes), s)
    if s == b'':
        return True
    s = bytes(s)  # On Python 2, make sure we're using modern bytes
    return s8[len(s)%8][s[-1]] and not tr(s, identitytranstable, chars)

def a2b(cs):  # type: (bytes) -> bytes
    """
    @param cs the base-32 encoded data (as bytes)
    """
    precondition(could_be_base32_encoded(cs), "cs is required to be possibly base32 encoded data.", cs=cs)
    precondition(isinstance(cs, bytes), cs)

    cs = cs.upper()
    # Add padding back, to make Python's base64 module happy:
    while (len(cs) * 5) % 8 != 0:
        cs += b"="

    return base64.b32decode(cs)


__all__ = ["b2a", "a2b", "b2a_or_none", "BASE32CHAR_3bits", "BASE32CHAR_1bits", "BASE32CHAR", "BASE32STR_anybytes", "could_be_base32_encoded"]
