"""
Tests for allmydata.util.spans.
"""

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from past.builtins import long

import binascii
import hashlib

from twisted.trial import unittest

from allmydata.util.spans import Spans, overlap, DataSpans


def sha256(data):
    """
    :param bytes data: data to hash

    :returns: a hex-encoded SHA256 hash of the data
    """
    return binascii.hexlify(hashlib.sha256(data).digest())


class SimpleSpans(object):
    # this is a simple+inefficient form of util.spans.Spans . We compare the
    # behavior of this reference model against the real (efficient) form.

    def __init__(self, _span_or_start=None, length=None):
        self._have = set()
        if length is not None:
            for i in range(_span_or_start, _span_or_start+length):
                self._have.add(i)
        elif _span_or_start:
            for (start,length) in _span_or_start:
                self.add(start, length)

    def add(self, start, length):
        for i in range(start, start+length):
            self._have.add(i)
        return self

    def remove(self, start, length):
        for i in range(start, start+length):
            self._have.discard(i)
        return self

    def each(self):
        return sorted(self._have)

    def __iter__(self):
        items = sorted(self._have)
        prevstart = None
        prevend = None
        for i in items:
            if prevstart is None:
                prevstart = prevend = i
                continue
            if i == prevend+1:
                prevend = i
                continue
            yield (prevstart, prevend-prevstart+1)
            prevstart = prevend = i
        if prevstart is not None:
            yield (prevstart, prevend-prevstart+1)

    def __bool__(self): # this gets us bool()
        return bool(self.len())

    def len(self):
        return len(self._have)

    def __add__(self, other):
        s = self.__class__(self)
        for (start, length) in other:
            s.add(start, length)
        return s

    def __sub__(self, other):
        s = self.__class__(self)
        for (start, length) in other:
            s.remove(start, length)
        return s

    def __iadd__(self, other):
        for (start, length) in other:
            self.add(start, length)
        return self

    def __isub__(self, other):
        for (start, length) in other:
            self.remove(start, length)
        return self

    def __and__(self, other):
        s = self.__class__()
        for i in other.each():
            if i in self._have:
                s.add(i, 1)
        return s

    def __contains__(self, start_and_length):
        (start, length) = start_and_length
        for i in range(start, start+length):
            if i not in self._have:
                return False
        return True

class ByteSpans(unittest.TestCase):
    def test_basic(self):
        s = Spans()
        self.failUnlessEqual(list(s), [])
        self.failIf(s)
        self.failIf((0,1) in s)
        self.failUnlessEqual(s.len(), 0)

        s1 = Spans(3, 4) # 3,4,5,6
        self._check1(s1)

        s1 = Spans(long(3), long(4)) # 3,4,5,6
        self._check1(s1)

        s2 = Spans(s1)
        self._check1(s2)

        s2.add(10,2) # 10,11
        self._check1(s1)
        self.failUnless((10,1) in s2)
        self.failIf((10,1) in s1)
        self.failUnlessEqual(list(s2.each()), [3,4,5,6,10,11])
        self.failUnlessEqual(s2.len(), 6)

        s2.add(15,2).add(20,2)
        self.failUnlessEqual(list(s2.each()), [3,4,5,6,10,11,15,16,20,21])
        self.failUnlessEqual(s2.len(), 10)

        s2.remove(4,3).remove(15,1)
        self.failUnlessEqual(list(s2.each()), [3,10,11,16,20,21])
        self.failUnlessEqual(s2.len(), 6)

        s1 = SimpleSpans(3, 4) # 3 4 5 6
        s2 = SimpleSpans(5, 4) # 5 6 7 8
        i = s1 & s2
        self.failUnlessEqual(list(i.each()), [5, 6])

    def _check1(self, s):
        self.failUnlessEqual(list(s), [(3,4)])
        self.failUnless(s)
        self.failUnlessEqual(s.len(), 4)
        self.failIf((0,1) in s)
        self.failUnless((3,4) in s)
        self.failUnless((3,1) in s)
        self.failUnless((5,2) in s)
        self.failUnless((6,1) in s)
        self.failIf((6,2) in s)
        self.failIf((7,1) in s)
        self.failUnlessEqual(list(s.each()), [3,4,5,6])

    def test_large(self):
        s = Spans(4, 2**65) # don't do this with a SimpleSpans
        self.failUnlessEqual(list(s), [(4, 2**65)])
        self.failUnless(s)
        self.failUnlessEqual(s.len(), 2**65)
        self.failIf((0,1) in s)
        self.failUnless((4,2) in s)
        self.failUnless((2**65,2) in s)

    def test_math(self):
        s1 = Spans(0, 10) # 0,1,2,3,4,5,6,7,8,9
        s2 = Spans(5, 3) # 5,6,7
        s3 = Spans(8, 4) # 8,9,10,11

        s = s1 - s2
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,8,9])
        s = s1 - s3
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7])
        s = s2 - s3
        self.failUnlessEqual(list(s.each()), [5,6,7])
        s = s1 & s2
        self.failUnlessEqual(list(s.each()), [5,6,7])
        s = s2 & s1
        self.failUnlessEqual(list(s.each()), [5,6,7])
        s = s1 & s3
        self.failUnlessEqual(list(s.each()), [8,9])
        s = s3 & s1
        self.failUnlessEqual(list(s.each()), [8,9])
        s = s2 & s3
        self.failUnlessEqual(list(s.each()), [])
        s = s3 & s2
        self.failUnlessEqual(list(s.each()), [])
        s = Spans() & s3
        self.failUnlessEqual(list(s.each()), [])
        s = s3 & Spans()
        self.failUnlessEqual(list(s.each()), [])

        s = s1 + s2
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7,8,9])
        s = s1 + s3
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7,8,9,10,11])
        s = s2 + s3
        self.failUnlessEqual(list(s.each()), [5,6,7,8,9,10,11])

        s = Spans(s1)
        s -= s2
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,8,9])
        s = Spans(s1)
        s -= s3
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7])
        s = Spans(s2)
        s -= s3
        self.failUnlessEqual(list(s.each()), [5,6,7])

        s = Spans(s1)
        s += s2
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7,8,9])
        s = Spans(s1)
        s += s3
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7,8,9,10,11])
        s = Spans(s2)
        s += s3
        self.failUnlessEqual(list(s.each()), [5,6,7,8,9,10,11])

    def test_random(self):
        # attempt to increase coverage of corner cases by comparing behavior
        # of a simple-but-slow model implementation against the
        # complex-but-fast actual implementation, in a large number of random
        # operations
        S1 = SimpleSpans
        S2 = Spans
        s1 = S1(); s2 = S2()
        seed = b""
        def _create(subseed):
            ns1 = S1(); ns2 = S2()
            for i in range(10):
                what = sha256(subseed+bytes(i))
                start = int(what[2:4], 16)
                length = max(1,int(what[5:6], 16))
                ns1.add(start, length); ns2.add(start, length)
            return ns1, ns2

        #print()
        for i in range(1000):
            what = sha256(seed+bytes(i))
            op = what[0:1]
            subop = what[1:2]
            start = int(what[2:4], 16)
            length = max(1,int(what[5:6], 16))
            #print(what)
            if op in b"0":
                if subop in b"01234":
                    s1 = S1(); s2 = S2()
                elif subop in b"5678":
                    s1 = S1(start, length); s2 = S2(start, length)
                else:
                    s1 = S1(s1); s2 = S2(s2)
                #print("s2 = %s" % s2.dump())
            elif op in b"123":
                #print("s2.add(%d,%d)" % (start, length))
                s1.add(start, length); s2.add(start, length)
            elif op in b"456":
                #print("s2.remove(%d,%d)" % (start, length))
                s1.remove(start, length); s2.remove(start, length)
            elif op in b"78":
                ns1, ns2 = _create(what[7:11])
                #print("s2 + %s" % ns2.dump())
                s1 = s1 + ns1; s2 = s2 + ns2
            elif op in b"9a":
                ns1, ns2 = _create(what[7:11])
                #print("%s - %s" % (s2.dump(), ns2.dump()))
                s1 = s1 - ns1; s2 = s2 - ns2
            elif op in b"bc":
                ns1, ns2 = _create(what[7:11])
                #print("s2 += %s" % ns2.dump())
                s1 += ns1; s2 += ns2
            elif op in b"de":
                ns1, ns2 = _create(what[7:11])
                #print("%s -= %s" % (s2.dump(), ns2.dump()))
                s1 -= ns1; s2 -= ns2
            else:
                ns1, ns2 = _create(what[7:11])
                #print("%s &= %s" % (s2.dump(), ns2.dump()))
                s1 = s1 & ns1; s2 = s2 & ns2
            #print("s2 now %s" % s2.dump())
            self.failUnlessEqual(list(s1.each()), list(s2.each()))
            self.failUnlessEqual(s1.len(), s2.len())
            self.failUnlessEqual(bool(s1), bool(s2))
            self.failUnlessEqual(list(s1), list(s2))
            for j in range(10):
                what = sha256(what[12:14]+bytes(j))
                start = int(what[2:4], 16)
                length = max(1, int(what[5:6], 16))
                span = (start, length)
                self.failUnlessEqual(bool(span in s1), bool(span in s2))


    # s()
    # s(start,length)
    # s(s0)
    # s.add(start,length) : returns s
    # s.remove(start,length)
    # s.each() -> list of byte offsets, mostly for testing
    # list(s) -> list of (start,length) tuples, one per span
    # (start,length) in s -> True if (start..start+length-1) are all members
    #  NOT equivalent to x in list(s)
    # s.len() -> number of bytes, for testing, bool(), and accounting/limiting
    # bool(s)  (__nonzeron__)
    # s = s1+s2, s1-s2, +=s1, -=s1

    def test_overlap(self):
        for a in range(20):
            for b in range(10):
                for c in range(20):
                    for d in range(10):
                        self._test_overlap(a,b,c,d)

    def _test_overlap(self, a, b, c, d):
        s1 = set(range(a,a+b))
        s2 = set(range(c,c+d))
        #print("---")
        #self._show_overlap(s1, "1")
        #self._show_overlap(s2, "2")
        o = overlap(a,b,c,d)
        expected = s1.intersection(s2)
        if not expected:
            self.failUnlessEqual(o, None)
        else:
            start,length = o
            so = set(range(start,start+length))
            #self._show(so, "o")
            self.failUnlessEqual(so, expected)

    def _show_overlap(self, s, c):
        import sys
        out = sys.stdout
        if s:
            for i in range(max(s)):
                if i in s:
                    out.write(c)
                else:
                    out.write(" ")
        out.write("\n")

def extend(s, start, length, fill):
    if len(s) >= start+length:
        return s
    assert len(fill) == 1
    return s + fill*(start+length-len(s))

def replace(s, start, data):
    assert len(s) >= start+len(data)
    return s[:start] + data + s[start+len(data):]

class SimpleDataSpans(object):
    def __init__(self, other=None):
        self.missing = "" # "1" where missing, "0" where found
        self.data = b""
        if other:
            for (start, data) in other.get_chunks():
                self.add(start, data)

    def __bool__(self): # this gets us bool()
        return bool(self.len())

    def len(self):
        return len(self.missing.replace("1", ""))

    def _dump(self):
        return [i for (i,c) in enumerate(self.missing) if c == "0"]

    def _have(self, start, length):
        m = self.missing[start:start+length]
        if not m or len(m)<length or int(m):
            return False
        return True
    def get_chunks(self):
        for i in self._dump():
            yield (i, self.data[i:i+1])
    def get_spans(self):
        return SimpleSpans([(start,len(data))
                            for (start,data) in self.get_chunks()])
    def get(self, start, length):
        if self._have(start, length):
            return self.data[start:start+length]
        return None
    def pop(self, start, length):
        data = self.get(start, length)
        if data:
            self.remove(start, length)
        return data
    def remove(self, start, length):
        self.missing = replace(extend(self.missing, start, length, "1"),
                               start, "1"*length)
    def add(self, start, data):
        self.missing = replace(extend(self.missing, start, len(data), "1"),
                               start, "0"*len(data))
        self.data = replace(extend(self.data, start, len(data), b" "),
                            start, data)


class StringSpans(unittest.TestCase):
    def do_basic(self, klass):
        ds = klass()
        self.failUnlessEqual(ds.len(), 0)
        self.failUnlessEqual(list(ds._dump()), [])
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 0)
        s1 = ds.get_spans()
        self.failUnlessEqual(ds.get(0, 4), None)
        self.failUnlessEqual(ds.pop(0, 4), None)
        ds.remove(0, 4)

        ds.add(2, b"four")
        self.failUnlessEqual(ds.len(), 4)
        self.failUnlessEqual(list(ds._dump()), [2,3,4,5])
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 4)
        s1 = ds.get_spans()
        self.failUnless((2,2) in s1)
        self.failUnlessEqual(ds.get(0, 4), None)
        self.failUnlessEqual(ds.pop(0, 4), None)
        self.failUnlessEqual(ds.get(4, 4), None)

        ds2 = klass(ds)
        self.failUnlessEqual(ds2.len(), 4)
        self.failUnlessEqual(list(ds2._dump()), [2,3,4,5])
        self.failUnlessEqual(sum([len(d) for (s,d) in ds2.get_chunks()]), 4)
        self.failUnlessEqual(ds2.get(0, 4), None)
        self.failUnlessEqual(ds2.pop(0, 4), None)
        self.failUnlessEqual(ds2.pop(2, 3), b"fou")
        self.failUnlessEqual(sum([len(d) for (s,d) in ds2.get_chunks()]), 1)
        self.failUnlessEqual(ds2.get(2, 3), None)
        self.failUnlessEqual(ds2.get(5, 1), b"r")
        self.failUnlessEqual(ds.get(2, 3), b"fou")
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 4)

        ds.add(0, b"23")
        self.failUnlessEqual(ds.len(), 6)
        self.failUnlessEqual(list(ds._dump()), [0,1,2,3,4,5])
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 6)
        self.failUnlessEqual(ds.get(0, 4), b"23fo")
        self.failUnlessEqual(ds.pop(0, 4), b"23fo")
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 2)
        self.failUnlessEqual(ds.get(0, 4), None)
        self.failUnlessEqual(ds.pop(0, 4), None)

        ds = klass()
        ds.add(2, b"four")
        ds.add(3, b"ea")
        self.failUnlessEqual(ds.get(2, 4), b"fear")

        ds = klass()
        ds.add(long(2), b"four")
        ds.add(long(3), b"ea")
        self.failUnlessEqual(ds.get(long(2), long(4)), b"fear")


    def do_scan(self, klass):
        # do a test with gaps and spans of size 1 and 2
        #  left=(1,11) * right=(1,11) * gapsize=(1,2)
        # 111, 112, 121, 122, 211, 212, 221, 222
        #    211
        #      121
        #         112
        #            212
        #               222
        #                   221
        #                      111
        #                        122
        #  11 1  1 11 11  11  1 1  111
        # 0123456789012345678901234567
        # abcdefghijklmnopqrstuvwxyz-=
        pieces = [(1, b"bc"),
                  (4, b"e"),
                  (7, b"h"),
                  (9, b"jk"),
                  (12, b"mn"),
                  (16, b"qr"),
                  (20, b"u"),
                  (22, b"w"),
                  (25, b"z-="),
                  ]
        p_elements = set([1,2,4,7,9,10,12,13,16,17,20,22,25,26,27])
        S = b"abcdefghijklmnopqrstuvwxyz-="
        # TODO: when adding data, add capital letters, to make sure we aren't
        # just leaving the old data in place
        l = len(S)
        def base():
            ds = klass()
            for start, data in pieces:
                ds.add(start, data)
            return ds
        def dump(s):
            p = set(s._dump())
            d = b"".join([((i not in p) and b" " or S[i]) for i in range(l)])
            assert len(d) == l
            return d
        DEBUG = False
        for start in range(0, l):
            for end in range(start+1, l):
                # add [start-end) to the baseline
                which = "%d-%d" % (start, end-1)
                p_added = set(range(start, end))
                b = base()
                if DEBUG:
                    print()
                    print(dump(b), which)
                    add = klass(); add.add(start, S[start:end])
                    print(dump(add))
                b.add(start, S[start:end])
                if DEBUG:
                    print(dump(b))
                # check that the new span is there
                d = b.get(start, end-start)
                self.failUnlessEqual(d, S[start:end], which)
                # check that all the original pieces are still there
                for t_start, t_data in pieces:
                    t_len = len(t_data)
                    self.failUnlessEqual(b.get(t_start, t_len),
                                         S[t_start:t_start+t_len],
                                         "%s %d+%d" % (which, t_start, t_len))
                # check that a lot of subspans are mostly correct
                for t_start in range(l):
                    for t_len in range(1,4):
                        d = b.get(t_start, t_len)
                        if d is not None:
                            which2 = "%s+(%d-%d)" % (which, t_start,
                                                     t_start+t_len-1)
                            self.failUnlessEqual(d, S[t_start:t_start+t_len],
                                                 which2)
                        # check that removing a subspan gives the right value
                        b2 = klass(b)
                        b2.remove(t_start, t_len)
                        removed = set(range(t_start, t_start+t_len))
                        for i in range(l):
                            exp = (((i in p_elements) or (i in p_added))
                                   and (i not in removed))
                            which2 = "%s-(%d-%d)" % (which, t_start,
                                                     t_start+t_len-1)
                            self.failUnlessEqual(bool(b2.get(i, 1)), exp,
                                                 which2+" %d" % i)

    def test_test(self):
        self.do_basic(SimpleDataSpans)
        self.do_scan(SimpleDataSpans)

    def test_basic(self):
        self.do_basic(DataSpans)
        self.do_scan(DataSpans)

    def test_random(self):
        # attempt to increase coverage of corner cases by comparing behavior
        # of a simple-but-slow model implementation against the
        # complex-but-fast actual implementation, in a large number of random
        # operations
        S1 = SimpleDataSpans
        S2 = DataSpans
        s1 = S1(); s2 = S2()
        seed = b""
        def _randstr(length, seed):
            created = 0
            pieces = []
            while created < length:
                piece = sha256(seed + bytes(created))
                pieces.append(piece)
                created += len(piece)
            return b"".join(pieces)[:length]
        def _create(subseed):
            ns1 = S1(); ns2 = S2()
            for i in range(10):
                what = sha256(subseed+bytes(i))
                start = int(what[2:4], 16)
                length = max(1,int(what[5:6], 16))
                ns1.add(start, _randstr(length, what[7:9]));
                ns2.add(start, _randstr(length, what[7:9]))
            return ns1, ns2

        #print()
        for i in range(1000):
            what = sha256(seed+bytes(i))
            op = what[0:1]
            subop = what[1:2]
            start = int(what[2:4], 16)
            length = max(1,int(what[5:6], 16))
            #print(what)
            if op in b"0":
                if subop in b"0123456":
                    s1 = S1(); s2 = S2()
                else:
                    s1, s2 = _create(what[7:11])
                #print("s2 = %s" % list(s2._dump()))
            elif op in b"123456":
                #print("s2.add(%d,%d)" % (start, length))
                s1.add(start, _randstr(length, what[7:9]));
                s2.add(start, _randstr(length, what[7:9]))
            elif op in b"789abc":
                #print("s2.remove(%d,%d)" % (start, length))
                s1.remove(start, length); s2.remove(start, length)
            else:
                #print("s2.pop(%d,%d)" % (start, length))
                d1 = s1.pop(start, length); d2 = s2.pop(start, length)
                self.failUnlessEqual(d1, d2)
            #print("s1 now %s" % list(s1._dump()))
            #print("s2 now %s" % list(s2._dump()))
            self.failUnlessEqual(s1.len(), s2.len())
            self.failUnlessEqual(list(s1._dump()), list(s2._dump()))
            for j in range(100):
                what = sha256(what[12:14]+bytes(j))
                start = int(what[2:4], 16)
                length = max(1, int(what[5:6], 16))
                d1 = s1.get(start, length); d2 = s2.get(start, length)
                self.failUnlessEqual(d1, d2, "%d+%d" % (start, length))
