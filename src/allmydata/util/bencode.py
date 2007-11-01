#!/usr/bin/env python

"""
A library for streaming and unstreaming of simple objects, designed
for speed, compactness, and ease of implementation.

The basic functions are bencode and bdecode. bencode takes an object
and returns a string, bdecode takes a string and returns an object.
bdecode raises a ValueError if you give it an invalid string.

The objects passed in may be nested dicts, lists, ints, floats, strings,
and Python boolean and None types. For example, all of the following
may be bencoded -

{'a': [0, 1], 'b': None}

[None, ['a', 2, ['c', None]]]

{'spam': (2,3,4)}

{'name': 'Cronus', 'spouse': 'Rhea', 'children': ['Hades', 'Poseidon']}

In general bdecode(bencode(spam)) == spam, but tuples and lists are
encoded the same, so bdecode(bencode((0, 1))) is [0, 1] rather
than (0, 1). Longs and ints are also encoded the same way, so
bdecode(bencode(4)) is a long.

Dict keys are required to be basestrings (byte strings or unicode objects),
to avoid a mess of potential implementation incompatibilities. bencode is
intended to be used for protocols which are going to be re-implemented many
times, so it's very conservative in that regard.

Which type is encoded is determined by the first character, 'i', 'n', 'f',
'd', 'l', 'b', 'u', and any digit. They indicate integer, null, float,
dict, list, boolean, unicode string, and string, respectively.

Strings are length-prefixed in base 10, followed by a colon.

bencode('spam') == '4:spam'

Unicode string objects are indicated with an initial u, a base 10
length-prefix, and the remaining bytes in utf-8 encoding.

bencode(u'\u00bfHabla espa\u00f1ol?') == 'ËHabla espaÐol?'

Nulls are indicated by a single 'n'.

bencode(None) == 'n'

Integers are encoded base 10 and terminated with an 'e' -

bencode(3) == 'i3e'
bencode(-20) == 'i-20e'

Floats are encoded in base 10 and terminated with an 'e' -

bencode(3.2) == 'f3.2e'
bencode(-23.4532) == 'f-23.4532e'

Lists are encoded in list order, terminated by an 'e' -

bencode(['abc', 'd']) == 'l3:abc1:de'
bencode([2, 'f']) == 'li2e1:fe'

Dicts are encoded by containing alternating keys and values.
The keys are encoded in sorted order, but sort order is not
enforced on the decode.  Dicts are terminated by an 'e'. Dict
keys can be either bytestrings or unicode strings. For example -

bencode({'spam': 'eggs'}) == 'd4:spam4:eggse'
bencode({'ab': 2, 'a': None}) == 'd1:an2:abi2ee'
bencode({'a' : 1, u'\xab': 2}) == 'd1:ai1eu4:\xfe\xff\x00\xa8i2ee'

Truncated strings come first, so in sort order 'a' comes before 'abc'.
"""

# This file is licensed under the GNU Lesser General Public License v2.1.
#
# Originally written by Mojo Nation.
# Rewritten by Bram Cohen.
# Further enhanced by Allmydata to support additional Python types (Boolean
# None, Float, and Unicode strings.)

from types import IntType, LongType, FloatType, ListType, TupleType, DictType, StringType, UnicodeType, BooleanType, NoneType
from cStringIO import StringIO
import string

def bencode(data):
    """
    encodes objects as strings, see module documentation for more info
    """
    result = StringIO()
    bwrite(data, result)
    return result.getvalue()

def bwrite(data, result):
    # a generic using pje's type dispatch will be faster here
    try:
        encoder = encoders[type(data)]
    except KeyError:
        encoder = None
        # Catch subclasses of built-in types
        for t,coder in encoders.items():
            if isinstance(data, t):
                encoder = coder
                break
        if not encoder:
            raise ValueError("unsupported data type: %s" % type(data))
    encoder(data, result)

encoders = {}

def encode_int(data, result):
    result.write('i' + str(data) + 'e')

encoders[IntType] = encode_int
encoders[LongType] = encode_int

def encode_float(data, result):
    result.write('f' + str(data) + 'e')

encoders[FloatType] = encode_float

def encode_bool(data, result):
    if data:
        result.write('b1')
    else:
        result.write('b0')

encoders[BooleanType] = encode_bool

def encode_list(data, result):
    result.write('l')
    _bwrite = bwrite
    for item in data:
        _bwrite(item, result)
    result.write('e')

encoders[TupleType] = encode_list
encoders[ListType] = encode_list
encoders[set] = encode_list

def encode_string(data, result):
    result.write(str(len(data)) + ':' + data)

encoders[StringType] = encode_string

def encode_unicode(data, result):
    payload = data.encode('utf-8')
    result.write('u' + str(len(payload)) + ':' + payload)

encoders[UnicodeType] = encode_unicode

def encode_dict(data, result):
    result.write('d')
    _bwrite = bwrite
    keylist = data.keys()
    keylist.sort()
    for key in keylist:
        _bwrite(key, result)
        _bwrite(data[key], result)
    result.write('e')

encoders[DictType] = encode_dict

encoders[NoneType] = lambda data, result: result.write('n')

def bdecode(s):
    """
    Does the opposite of bencode. Raises a ValueError if there's a problem.
    """
    try:
        result, index = bread(s, 0)
        if index != len(s):
            raise ValueError('left over stuff at end: %s' % s[index:])
        return result
    except IndexError, e:
        raise ValueError(str(e))
    except KeyError, e:
        raise ValueError(str(e))

def bread(s, index):
    return decoders[s[index]](s, index)

decoders = {}

def decode_raw_string(s, index):
    ci = s.index(":", index)
    ei = ci + int(s[index:ci]) + 1
    if ei > len(s):
        raise ValueError('length encoding indicates premature end of string')
    return (s[ci+1:ei], ei)

for c in string.digits:
    decoders[c] = decode_raw_string

def decode_unicode_string(s, index):
    ci = s.index(":", index)
    ei = ci + int(s[index+1:ci]) + 1
    if ei > len(s):
        raise ValueError('length encoding indicates premature end of string')
    return (unicode(s[ci+1:ei], 'utf-8'), ei)

decoders['u'] = decode_unicode_string

def decode_int(s, index):
    ei = s.index('e', index)
    return (long(s[index+1:ei]), ei+1)

decoders['i'] = decode_int

def decode_float(s, index):
    ei = s.index('e', index)
    return (float(s[index+1:ei]), ei+1)

decoders['f'] = decode_float

def decode_bool(s, index):
    val = s[index+1]
    if val == '1':
        return True, index+2
    elif val == '0':
        return False, index+2
    else:
        raise ValueError('invalid boolean encoding: %s' % s[index:index+2])

decoders['b'] = decode_bool

# decoders['n'] = lambda s, index: decoders_n.inc('n') or (None, index + 1)
decoders['n'] = lambda s, index: (None, index + 1)

def decode_list(s, index):
    # decoders_n.inc('l')
    result = []
    index += 1
    _bread = bread
    while s[index] != 'e':
        next, index = _bread(s, index)
        result.append(next)
    return result, index + 1

decoders['l'] = decode_list

def decode_dict(s, index):
    # decoders_n.inc('d')
    result = {}
    index += 1
    _decode_string = decode_raw_string
    _decode_unicode = decode_unicode_string
    _bread = bread
    while s[index] != 'e':
        if s[index] in string.digits:
            key, index = _decode_string(s, index)
        elif s[index] == "u":
            key, index = _decode_unicode(s, index)
        else:
            raise ValueError("dict key must be basestring")
        if key in result:
            raise ValueError("dict key was repeated")
        value, index = _bread(s, index)
        result[key] = value
    return result, index + 1

decoders['d'] = decode_dict

def test_decode_raw_string():
    assert decode_raw_string('1:a', 0) == ('a', 3)
    assert decode_raw_string('0:', 0) == ('', 2)
    assert decode_raw_string('10:aaaaaaaaaaaaaaaaaaaaaaaaa', 0) == ('aaaaaaaaaa', 13)
    assert decode_raw_string('10:', 1) == ('', 3)
# non-reexp version does not check for this case
#    try:
#        decode_raw_string('01:a', 0)
#        assert 0, 'failed'
#    except ValueError:
#        pass
    try:
        decode_raw_string('--1:a', 0)
        assert 0, 'failed'
    except ValueError:
        pass
    try:
        decode_raw_string('h', 0)
        assert 0, 'failed'
    except ValueError:
        pass
    try:
        decode_raw_string('h:', 0)
        assert 0, 'failed'
    except ValueError:
        pass
    try:
        decode_raw_string('1', 0)
        assert 0, 'failed'
    except ValueError:
        pass
    try:
        decode_raw_string('', 0)
        assert 0, 'failed'
    except ValueError:
        pass
    try:
        decode_raw_string('5:a', 0)
        assert 0, 'failed'
    except ValueError:
        pass

def test_encode_and_decode_unicode_results_in_unicode_type():
    assert bdecode(bencode(u'\u00bfHabla espa\u00f1ol?')) == u'\u00bfHabla espa\u00f1ol?'

def test_encode_and_decode_unicode_at_least_preserves_the_content_even_if_it_flattens_the_type():
    test_string = bdecode(bencode(u'\u00bfHabla espa\u00f1ol?'))
    if isinstance(test_string, unicode):
        assert test_string == u'\u00bfHabla espa\u00f1ol?'
    elif isinstance(test_string, str):
        assert test_string.decode('utf-8') == u'\u00bfHabla espa\u00f1ol?'
    else:
        assert 0, 'flunked'

def test_dict_forbids_non_string_key():
    try:
        bdecode('di3ene')
        assert 0, 'failed'
    except ValueError:
        pass

def test_dict_forbids_key_repeat():
    try:
        bdecode('d1:an1:ane')
        assert 0, 'failed'
    except ValueError:
        pass

def test_empty_dict():
    assert bdecode('de') == {}

def test_dict_allows_unicode_keys():
    assert bdecode(bencode({'a': 1, u'\xa8': 2})) == {'a': 1L, u'\xa8': 2L}

def test_ValueError_in_decode_unknown():
    try:
        bdecode('x')
        assert 0, 'flunked'
    except ValueError:
        pass

def test_encode_and_decode_none():
    assert bdecode(bencode(None)) == None

def test_encode_and_decode_long():
    assert bdecode(bencode(-23452422452342L)) == -23452422452342L

def test_encode_and_decode_int():
    assert bdecode(bencode(2)) == 2

def test_encode_and_decode_float():
    assert bdecode(bencode(3.4)) == 3.4
    assert bdecode(bencode(0.0)) == 0.0
    assert bdecode(bencode(-4.56)) == -4.56
    assert bdecode(bencode(-0.0)) == -0.0

def test_encode_and_decode_bool():
    assert bdecode(bencode(True)) == True
    assert bdecode(bencode(False)) == False

# the non-regexp methods no longer check for canonical ints, but we
# don't parse input we did not generate using bencode, so I will leave
# these commented out for now
#def test_decode_noncanonical_int():
#    try:
#        bdecode('i03e')
#        assert 0
#    except ValueError:
#        pass
#    try:
#        bdecode('i3 e')
#        assert 0
#    except ValueError:
#        pass
#    try:
#        bdecode('i 3e')
#        assert 0
#    except ValueError:
#        pass
#    try:
#        bdecode('i-0e')
#        assert 0
#    except ValueError:
#        pass

def test_encode_and_decode_dict():
    x = {'42': 3}
    assert bdecode(bencode(x)) == x

def test_encode_and_decode_list():
    assert bdecode(bencode([])) == []

def test_encode_and_decode_tuple():
    assert bdecode(bencode(())) == []

def test_encode_and_decode_empty_dict():
    assert bdecode(bencode({})) == {}

def test_encode_and_decode_complex_object():
    spam = [[], 0, -3, -345234523543245234523L, {}, 'spam', None, {'a': [3]}, {}, {'a': 1L, u'\xa8': 2L}]
    assert bencode(bdecode(bencode(spam))) == bencode(spam)
    assert bdecode(bencode(spam)) == spam

def test_unfinished_list():
    try:
        bdecode('ln')
        assert 0
    except ValueError:
        pass

def test_unfinished_dict():
    try:
        bdecode('d')
        assert 0
    except ValueError:
        pass
    try:
        bdecode('d1:a')
        assert 0
    except ValueError:
        pass

def test_unsupported_type():
    try:
        bencode(lambda: None)
        assert 0
    except ValueError:
        pass
