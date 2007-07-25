# from the Python Standard Library
import string

from assertutil import precondition

z_base_32_alphabet = "ybndrfg8ejkmcpqxot1uwisza345h769" # Zooko's choice, rationale in "DESIGN" doc
rfc3548_alphabet = "abcdefghijklmnopqrstuvwxyz234567" # RFC3548 standard used by Gnutella, Content-Addressable Web, THEX, Bitzi, Web-Calculus...
chars = z_base_32_alphabet

vals = ''.join(map(chr, range(32)))
c2vtranstable = string.maketrans(chars, vals)
v2ctranstable = string.maketrans(vals, chars)
identitytranstable = string.maketrans(chars, chars)

def _get_trailing_chars_without_lsbs(N, d):
    """
    @return: a list of chars that can legitimately appear in the last place when the least significant N bits are ignored.
    """
    s = []
    if N < 4:
        s.extend(_get_trailing_chars_without_lsbs(N+1, d=d))
    i = 0
    while i < len(chars):
        if not d.has_key(i):
            d[i] = None
            s.append(chars[i])
        i = i + 2**N
    return s

def get_trailing_chars_without_lsbs(N):
    precondition((N >= 0) and (N < 5), "N is required to be > 0 and < len(chars).", N=N)
    if N == 0:
        return chars
    d = {}
    return ''.join(_get_trailing_chars_without_lsbs(N, d=d))

def b2a(os):
    """
    @param os the data to be encoded (a string)

    @return the contents of os in base-32 encoded form
    """
    return b2a_l(os, len(os)*8)

def b2a_or_none(os):
    if os is not None:
        return b2a(os)
        
def b2a_l(os, lengthinbits):
    """
    @param os the data to be encoded (a string)
    @param lengthinbits the number of bits of data in os to be encoded

    b2a_l() will generate a base-32 encoded string big enough to encode lengthinbits bits.  So for
    example if os is 2 bytes long and lengthinbits is 15, then b2a_l() will generate a 3-character-
    long base-32 encoded string (since 3 quintets is sufficient to encode 15 bits).  If os is
    2 bytes long and lengthinbits is 16 (or None), then b2a_l() will generate a 4-character string.
    Note that b2a_l() does not mask off unused least-significant bits, so for example if os is
    2 bytes long and lengthinbits is 15, then you must ensure that the unused least-significant bit
    of os is a zero bit or you will get the wrong result.  This precondition is tested by assertions
    if assertions are enabled.

    Warning: if you generate a base-32 encoded string with b2a_l(), and then someone else tries to
    decode it by calling a2b() instead of  a2b_l(), then they will (probably) get a different
    string than the one you encoded!  So only use b2a_l() when you are sure that the encoding and
    decoding sides know exactly which lengthinbits to use.  If you do not have a way for the
    encoder and the decoder to agree upon the lengthinbits, then it is best to use b2a() and
    a2b().  The only drawback to using b2a() over b2a_l() is that when you have a number of
    bits to encode that is not a multiple of 8, b2a() can sometimes generate a base-32 encoded
    string that is one or two characters longer than necessary.

    @return the contents of os in base-32 encoded form
    """
    precondition(isinstance(lengthinbits, (int, long,)), "lengthinbits is required to be an integer.", lengthinbits=lengthinbits)
    precondition((lengthinbits+7)/8 == len(os), "lengthinbits is required to specify a number of bits storable in exactly len(os) octets.", lengthinbits=lengthinbits, lenos=len(os))

    os = map(ord, os)

    numquintets = (lengthinbits+4)/5
    numoctetsofdata = (lengthinbits+7)/8
    # print "numoctetsofdata: %s, len(os): %s, lengthinbits: %s, numquintets: %s" % (numoctetsofdata, len(os), lengthinbits, numquintets,)
    # strip trailing octets that won't be used
    del os[numoctetsofdata:]
    # zero out any unused bits in the final octet
    if lengthinbits % 8 != 0:
        os[-1] = os[-1] >> (8-(lengthinbits % 8))
        os[-1] = os[-1] << (8-(lengthinbits % 8))
    # append zero octets for padding if needed
    numoctetsneeded = (numquintets*5+7)/8 + 1
    os.extend([0]*(numoctetsneeded-len(os)))

    quintets = []
    cutoff = 256
    num = os[0]
    i = 0
    while len(quintets) < numquintets:
        i = i + 1
        assert len(os) > i, "len(os): %s, i: %s, len(quintets): %s, numquintets: %s, lengthinbits: %s, numoctetsofdata: %s, numoctetsneeded: %s, os: %s" % (len(os), i, len(quintets), numquintets, lengthinbits, numoctetsofdata, numoctetsneeded, os,)
        num = num * 256
        num = num + os[i]
        if cutoff == 1:
            cutoff = 256
            continue
        cutoff = cutoff * 8
        quintet = num / cutoff
        quintets.append(quintet)
        num = num - (quintet * cutoff)

        cutoff = cutoff / 32
        quintet = num / cutoff
        quintets.append(quintet)
        num = num - (quintet * cutoff)

    if len(quintets) > numquintets:
        assert len(quintets) == (numquintets+1), "len(quintets): %s, numquintets: %s, quintets: %s" % (len(quintets), numquintets, quintets,)
        quintets = quintets[:numquintets]
    res = string.translate(string.join(map(chr, quintets), ''), v2ctranstable)
    assert could_be_base32_encoded_l(res, lengthinbits), "lengthinbits: %s, res: %s" % (lengthinbits, res,)
    return res

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
NUM_QS_TO_NUM_BITS=tuple(map(lambda x: x*8, NUM_QS_TO_NUM_OS))

# A fast way to determine whether a given string *could* be base-32 encoded data, assuming that the
# original data had 8K bits for a positive integer K.
# The boolean value of s8[len(s)%8][ord(s[-1])], where s is the possibly base-32 encoded string
# tells whether the final character is reasonable.
def add_check_array(cs, sfmap):
    checka=[0] * 256
    for c in cs:
        checka[ord(c)] = 1
    sfmap.append(tuple(checka))

def init_s8():
    s8 = []
    add_check_array(chars, s8)
    for lenmod8 in (1, 2, 3, 4, 5, 6, 7,):
        if NUM_QS_LEGIT[lenmod8]:
            add_check_array(get_trailing_chars_without_lsbs(4-(NUM_QS_TO_NUM_BITS[lenmod8]%5)), s8)
        else:
            add_check_array('', s8)
    return tuple(s8)
s8 = init_s8()

# A somewhat fast way to determine whether a given string *could* be base-32 encoded data, given a
# lengthinbits.
# The boolean value of s5[lengthinbits%5][ord(s[-1])], where s is the possibly base-32 encoded
# string tells whether the final character is reasonable.
def init_s5():
    s5 = []
    add_check_array(chars, s5)
    for lenmod5 in (1, 2, 3, 4,):
        add_check_array(get_trailing_chars_without_lsbs(4-lenmod5), s5)
    return tuple(s5)
s5 = init_s5()

def could_be_base32_encoded(s, s8=s8, tr=string.translate, identitytranstable=identitytranstable, chars=chars):
    if s == '':
        return True
    return s8[len(s)%8][ord(s[-1])] and not tr(s, identitytranstable, chars)

def could_be_base32_encoded_l(s, lengthinbits, s5=s5, tr=string.translate, identitytranstable=identitytranstable, chars=chars):
    if s == '':
        return True
    assert lengthinbits%5 < len(s5), lengthinbits
    assert ord(s[-1]) < s5[lengthinbits%5]
    return (((lengthinbits+4)/5) == len(s)) and s5[lengthinbits%5][ord(s[-1])] and not string.translate(s, identitytranstable, chars)

def num_octets_that_encode_to_this_many_quintets(numqs):
    # Here is a computation that conveniently expresses this:
    return (numqs*5+3)/8

def a2b(cs):
    """
    @param cs the base-32 encoded data (a string)
    """
    precondition(could_be_base32_encoded(cs), "cs is required to be possibly base32 encoded data.", cs=cs)

    return a2b_l(cs, num_octets_that_encode_to_this_many_quintets(len(cs))*8)

def a2b_l(cs, lengthinbits):
    """
    @param lengthinbits the number of bits of data in encoded into cs

    a2b_l() will return a result big enough to hold lengthinbits bits.  So for example if cs is
    4 characters long (encoding at least 15 and up to 20 bits) and lengthinbits is 16, then a2b_l()
    will return a string of length 2 (since 2 bytes is sufficient to store 16 bits).  If cs is 4
    characters long and lengthinbits is 20, then a2b_l() will return a string of length 3 (since
    3 bytes is sufficient to store 20 bits).  Note that b2a_l() does not mask off unused least-
    significant bits, so for example if cs is 4 characters long and lengthinbits is 17, then you
    must ensure that all three of the unused least-significant bits of cs are zero bits or you will
    get the wrong result.  This precondition is tested by assertions if assertions are enabled.
    (Generally you just require the encoder to ensure this consistency property between the least
    significant zero bits and value of lengthinbits, and reject strings that have a length-in-bits
    which isn't a multiple of 8 and yet don't have trailing zero bits, as improperly encoded.)

    Please see the warning in the docstring of b2a_l() regarding the use of b2a() versus b2a_l().

    @return the data encoded in cs
    """
    precondition(could_be_base32_encoded_l(cs, lengthinbits), "cs is required to be possibly base32 encoded data.", cs=cs, lengthinbits=lengthinbits)
    if cs == '':
        return ''

    qs = map(ord, string.translate(cs, c2vtranstable))

    numoctets = (lengthinbits+7)/8
    numquintetsofdata = (lengthinbits+4)/5
    # strip trailing quintets that won't be used
    del qs[numquintetsofdata:]
    # zero out any unused bits in the final quintet
    if lengthinbits % 5 != 0:
        qs[-1] = qs[-1] >> (5-(lengthinbits % 5))
        qs[-1] = qs[-1] << (5-(lengthinbits % 5))
    # append zero quintets for padding if needed
    numquintetsneeded = (numoctets*8+4)/5
    qs.extend([0]*(numquintetsneeded-len(qs)))

    octets = []
    pos = 2048
    num = qs[0] * pos
    readybits = 5
    i = 1
    while len(octets) < numoctets:
        while pos > 256:
            pos = pos / 32
            num = num + (qs[i] * pos)
            i = i + 1
        octet = num / 256
        octets.append(octet)
        num = num - (octet * 256)
        num = num * 256
        pos = pos * 256
    assert len(octets) == numoctets, "len(octets): %s, numoctets: %s, octets: %s" % (len(octets), numoctets, octets,)
    res = ''.join(map(chr, octets))
    precondition(b2a_l(res, lengthinbits) == cs, "cs is required to be the canonical base-32 encoding of some data.", b2a(res), res=res, cs=cs)
    return res

