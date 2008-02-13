#!/usr/bin/env python

# Copyright (c) 2002-2008 Bryce "Zooko" Wilcox-O'Hearn
# mailto:zooko@zooko.com
# Permission is hereby granted to any person obtaining a copy of this work to
# deal in this work without restriction (including the rights to use, modify,
# distribute, sublicense, and/or sell copies).

# from the Python Standard Library
import string

from allmydata.util.mathutil import div_ceil, log_ceil, log_floor

chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

ZBASE62CHAR = '[' + chars + ']'

vals = ''.join([chr(i) for i in range(62)])
c2vtranstable = string.maketrans(chars, vals)
v2ctranstable = string.maketrans(vals, chars)
identitytranstable = string.maketrans(chars, chars)

def b2a(os):
    """
    @param os the data to be encoded (a string)

    @return the contents of os in base-62 encoded form
    """
    cs = b2a_l(os, len(os)*8)
    assert num_octets_that_encode_to_this_many_chars(len(cs)) == len(os), "%s != %s, numchars: %s" % (num_octets_that_encode_to_this_many_chars(len(cs)), len(os), len(cs))
    return cs

def b2a_l(os, lengthinbits):
    """
    @param os the data to be encoded (a string)
    @param lengthinbits the number of bits of data in os to be encoded

    b2a_l() will generate a base-62 encoded string big enough to encode
    lengthinbits bits.  So for example if os is 3 bytes long and lengthinbits is
    17, then b2a_l() will generate a 3-character- long base-62 encoded string
    (since 3 chars is sufficient to encode more than 2^17 values).  If os is 3
    bytes long and lengthinbits is 18 (or None), then b2a_l() will generate a
    4-character string (since 4 chars are required to hold 2^18 values).  Note
    that if os is 3 bytes long and lengthinbits is 17, the least significant 7
    bits of os are ignored.

    Warning: if you generate a base-62 encoded string with b2a_l(), and then someone else tries to
    decode it by calling a2b() instead of  a2b_l(), then they will (potentially) get a different
    string than the one you encoded!  So use b2a_l() only when you are sure that the encoding and
    decoding sides know exactly which lengthinbits to use.  If you do not have a way for the
    encoder and the decoder to agree upon the lengthinbits, then it is best to use b2a() and
    a2b().  The only drawback to using b2a() over b2a_l() is that when you have a number of
    bits to encode that is not a multiple of 8, b2a() can sometimes generate a base-62 encoded
    string that is one or two characters longer than necessary.

    @return the contents of os in base-62 encoded form
    """
    os = [ord(o) for o in reversed(os)] # treat os as big-endian -- and we want to process the least-significant o first

    value = 0
    numvalues = 1 # the number of possible values that value could be
    for o in os:
        o *= numvalues
        value += o
        numvalues *= 256

    chars = []
    while numvalues > 0:
        chars.append(value % 62)
        value //= 62
        numvalues //= 62

    return string.translate(''.join([chr(c) for c in reversed(chars)]), v2ctranstable) # make it big-endian

def num_octets_that_encode_to_this_many_chars(numcs):
    return log_floor(62**numcs, 256)

def num_chars_that_this_many_octets_encode_to(numos):
    return log_ceil(256**numos, 62)

def a2b(cs):
    """
    @param cs the base-62 encoded data (a string)
    """
    return a2b_l(cs, num_octets_that_encode_to_this_many_chars(len(cs))*8)

def a2b_l(cs, lengthinbits):
    """
    @param lengthinbits the number of bits of data in encoded into cs

    a2b_l() will return a result just big enough to hold lengthinbits bits.  So
    for example if cs is 2 characters long (encoding between 5 and 12 bits worth
    of data) and lengthinbits is 8, then a2b_l() will return a string of length
    1 (since 1 byte is sufficient to store 8 bits), but if lengthinbits is 9,
    then a2b_l() will return a string of length 2.

    Please see the warning in the docstring of b2a_l() regarding the use of
    b2a() versus b2a_l().

    @return the data encoded in cs
    """
    cs = [ord(c) for c in reversed(string.translate(cs, c2vtranstable))] # treat cs as big-endian -- and we want to process the least-significant c first

    value = 0
    numvalues = 1 # the number of possible values that value could be
    for c in cs:
        c *= numvalues
        value += c
        numvalues *= 62

    numvalues = 2**lengthinbits
    bytes = []
    while numvalues > 1:
        bytes.append(value % 256)
        value //= 256
        numvalues //= 256

    return ''.join([chr(b) for b in reversed(bytes)]) # make it big-endian
