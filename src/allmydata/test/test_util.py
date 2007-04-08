
def foo(): pass # keep the line number constant

from twisted.trial import unittest

from allmydata.util import bencode, idlib, humanreadable, mathutil
from allmydata.util import assertutil


class IDLib(unittest.TestCase):
    def test_b2a(self):
        self.failUnlessEqual(idlib.b2a("\x12\x34"), "ci2a====")
    def test_b2a_or_none(self):
        self.failUnlessEqual(idlib.b2a_or_none(None), None)
        self.failUnlessEqual(idlib.b2a_or_none("\x12\x34"), "ci2a====")
    def test_a2b(self):
        self.failUnlessEqual(idlib.a2b("ci2a===="), "\x12\x34")
        self.failUnlessRaises(TypeError, idlib.a2b, "bogus")
    def test_peerid(self):
        # these are 160-bit numbers
        peerid = "\x80" + "\x00" * 19
        short = idlib.peerid_to_short_string(peerid)
        self.failUnlessEqual(short, "qaaa")

class NoArgumentException(Exception):
    def __init__(self):
        pass

class HumanReadable(unittest.TestCase):
    def test_repr(self):
        hr = humanreadable.hr
        self.failUnlessEqual(hr(foo), "<foo() at test_util.py:2>")
        self.failUnlessEqual(hr(self.test_repr),
                             "<bound method HumanReadable.test_repr of <allmydata.test.test_util.HumanReadable testMethod=test_repr>>")
        self.failUnlessEqual(hr(1L), "1")
        self.failUnlessEqual(hr(10**40),
                             "100000000000000000...000000000000000000")
        self.failUnlessEqual(hr(self), "<allmydata.test.test_util.HumanReadable testMethod=test_repr>")
        self.failUnlessEqual(hr([1,2]), "[1, 2]")
        self.failUnlessEqual(hr({1:2}), "{1:2}")
        try:
            raise RuntimeError
        except Exception, e:
            self.failUnless(
                hr(e) == "<RuntimeError: ()>" # python-2.4
                or hr(e) == "RuntimeError()") # python-2.5
        try:
            raise RuntimeError("oops")
        except Exception, e:
            self.failUnless(
                hr(e) == "<RuntimeError: 'oops'>" # python-2.4
                or hr(e) == "RuntimeError('oops',)") # python-2.5
        try:
            raise NoArgumentException
        except Exception, e:
            self.failUnless(
                hr(e) == "<NoArgumentException>" # python-2.4
                or hr(e) == "NoArgumentException()") # python-2.5


class MyList(list):
    pass

class Bencode(unittest.TestCase):
    def test_bencode(self):
        e = bencode.bencode
        self.failUnlessEqual(e(4), "i4e")
        self.failUnlessEqual(e([1,2]), "li1ei2ee")
        self.failUnlessEqual(e(MyList([1,2])), "li1ei2ee")
        self.failUnlessEqual(e({1:2}), "di1ei2ee")
        self.failUnlessEqual(e(u"a"), "u1:a")
        self.failUnlessEqual(e([True,False]), "lb1b0e")
        self.failUnlessEqual(e(1.5), "f1.5e")
        self.failUnlessEqual(e("foo"), "3:foo")
        d = bencode.bdecode
        self.failUnlessEqual(d("li1ei2ee"), [1,2])
        self.failUnlessEqual(d("u1:a"), u"a")
        self.failUnlessRaises(ValueError, d, "u10:short")
        self.failUnlessEqual(d("lb1b0e"), [True,False])
        self.failUnlessRaises(ValueError, d, "b2")
        self.failUnlessEqual(d("f1.5e"), 1.5)
        self.failUnlessEqual(d("3:foo"), "foo")
        self.failUnlessRaises(ValueError, d,
                              "38:When doing layout, always plan ah")
        # ooh! fascinating! bdecode requires string keys! I think this ought
        # to be changed
        #self.failUnlessEqual(d("di1ei2ee"), {1:2})
        self.failUnlessEqual(d("d1:ai2eu1:bi3ee"), {"a":2, u"b":3})
        self.failUnlessRaises(ValueError, d, "di1ei2ee")
        self.failUnlessRaises(ValueError, d, "d1:ai1e1:ai2ee")

        self.failUnlessRaises(ValueError, d, "i1ei2e")

        # now run all the module's builtin tests
        bencode.test_decode_raw_string()
        bencode.test_encode_and_decode_unicode_results_in_unicode_type()
        bencode.test_encode_and_decode_unicode_at_least_preserves_the_content_even_if_it_flattens_the_type()
        bencode.test_dict_forbids_non_string_key()
        bencode.test_dict_forbids_key_repeat()
        bencode.test_empty_dict()
        bencode.test_dict_allows_unicode_keys()
        bencode.test_ValueError_in_decode_unknown()
        bencode.test_encode_and_decode_none()
        bencode.test_encode_and_decode_long()
        bencode.test_encode_and_decode_int()
        bencode.test_encode_and_decode_float()
        bencode.test_encode_and_decode_bool()
        #bencode.test_decode_noncanonical_int()
        bencode.test_encode_and_decode_dict()
        bencode.test_encode_and_decode_list()
        bencode.test_encode_and_decode_tuple()
        bencode.test_encode_and_decode_empty_dict()
        bencode.test_encode_and_decode_complex_object()
        bencode.test_unfinished_list()
        bencode.test_unfinished_dict()
        bencode.test_unsupported_type()

class Math(unittest.TestCase):
    def test_div_ceil(self):
        f = mathutil.div_ceil
        self.failUnlessEqual(f(0, 1), 0)
        self.failUnlessEqual(f(0, 2), 0)
        self.failUnlessEqual(f(0, 3), 0)
        self.failUnlessEqual(f(1, 3), 1)
        self.failUnlessEqual(f(2, 3), 1)
        self.failUnlessEqual(f(3, 3), 1)
        self.failUnlessEqual(f(4, 3), 2)
        self.failUnlessEqual(f(5, 3), 2)
        self.failUnlessEqual(f(6, 3), 2)
        self.failUnlessEqual(f(7, 3), 3)

    def test_next_multiple(self):
        f = mathutil.next_multiple
        self.failUnlessEqual(f(5, 1), 5)
        self.failUnlessEqual(f(5, 2), 6)
        self.failUnlessEqual(f(5, 3), 6)
        self.failUnlessEqual(f(5, 4), 8)
        self.failUnlessEqual(f(5, 5), 5)
        self.failUnlessEqual(f(5, 6), 6)
        self.failUnlessEqual(f(32, 1), 32)
        self.failUnlessEqual(f(32, 2), 32)
        self.failUnlessEqual(f(32, 3), 33)
        self.failUnlessEqual(f(32, 4), 32)
        self.failUnlessEqual(f(32, 5), 35)
        self.failUnlessEqual(f(32, 6), 36)
        self.failUnlessEqual(f(32, 7), 35)
        self.failUnlessEqual(f(32, 8), 32)
        self.failUnlessEqual(f(32, 9), 36)
        self.failUnlessEqual(f(32, 10), 40)
        self.failUnlessEqual(f(32, 11), 33)
        self.failUnlessEqual(f(32, 12), 36)
        self.failUnlessEqual(f(32, 13), 39)
        self.failUnlessEqual(f(32, 14), 42)
        self.failUnlessEqual(f(32, 15), 45)
        self.failUnlessEqual(f(32, 16), 32)
        self.failUnlessEqual(f(32, 17), 34)
        self.failUnlessEqual(f(32, 18), 36)
        self.failUnlessEqual(f(32, 589), 589)

    def test_pad_size(self):
        f = mathutil.pad_size
        self.failUnlessEqual(f(0, 4), 0)
        self.failUnlessEqual(f(1, 4), 3)
        self.failUnlessEqual(f(2, 4), 2)
        self.failUnlessEqual(f(3, 4), 1)
        self.failUnlessEqual(f(4, 4), 0)
        self.failUnlessEqual(f(5, 4), 3)

    def test_is_power_of_k(self):
        f = mathutil.is_power_of_k
        for i in range(1, 100):
            if i in (1, 2, 4, 8, 16, 32, 64):
                self.failUnless(f(i, 2), "but %d *is* a power of 2" % i)
            else:
                self.failIf(f(i, 2), "but %d is *not* a power of 2" % i)
        for i in range(1, 100):
            if i in (1, 3, 9, 27, 81):
                self.failUnless(f(i, 3), "but %d *is* a power of 3" % i)
            else:
                self.failIf(f(i, 3), "but %d is *not* a power of 3" % i)

    def test_next_power_of_k(self):
        f = mathutil.next_power_of_k
        self.failUnlessEqual(f(0,2), 1)
        self.failUnlessEqual(f(1,2), 1)
        self.failUnlessEqual(f(2,2), 2)
        self.failUnlessEqual(f(3,2), 4)
        self.failUnlessEqual(f(4,2), 4)
        for i in range(5, 8): self.failUnlessEqual(f(i,2), 8, "%d" % i)
        for i in range(9, 16): self.failUnlessEqual(f(i,2), 16, "%d" % i)
        for i in range(17, 32): self.failUnlessEqual(f(i,2), 32, "%d" % i)
        for i in range(33, 64): self.failUnlessEqual(f(i,2), 64, "%d" % i)
        for i in range(65, 100): self.failUnlessEqual(f(i,2), 128, "%d" % i)

        self.failUnlessEqual(f(0,3), 1)
        self.failUnlessEqual(f(1,3), 1)
        self.failUnlessEqual(f(2,3), 3)
        self.failUnlessEqual(f(3,3), 3)
        for i in range(4, 9): self.failUnlessEqual(f(i,3), 9, "%d" % i)
        for i in range(10, 27): self.failUnlessEqual(f(i,3), 27, "%d" % i)
        for i in range(28, 81): self.failUnlessEqual(f(i,3), 81, "%d" % i)
        for i in range(82, 200): self.failUnlessEqual(f(i,3), 243, "%d" % i)

    def test_ave(self):
        f = mathutil.ave
        self.failUnlessEqual(f([1,2,3]), 2)
        self.failUnlessEqual(f([0,0,0,4]), 1)
        self.failUnlessAlmostEqual(f([0.0, 1.0, 1.0]), .666666666666)


class Asserts(unittest.TestCase):
    def should_assert(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except AssertionError, e:
            return str(e)
        except Exception, e:
            self.fail("assert failed with non-AssertionError: %s" % e)
        self.fail("assert was not caught")

    def should_not_assert(self, func, *args, **kwargs):
        if "re" in kwargs:
            regexp = kwargs["re"]
            del kwargs["re"]
        try:
            func(*args, **kwargs)
        except AssertionError, e:
            self.fail("assertion fired when it should not have: %s" % e)
        except Exception, e:
            self.fail("assertion (which shouldn't have failed) failed with non-AssertionError: %s" % e)
        return # we're happy


    def test_assert(self):
        f = assertutil._assert
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual(m, "'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("othermsg: 'message2' <type 'str'>", m)

    def test_precondition(self):
        f = assertutil.precondition
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual("precondition: 'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("precondition: 'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("precondition: othermsg: 'message2' <type 'str'>", m)

    def test_postcondition(self):
        f = assertutil.postcondition
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual("postcondition: 'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("postcondition: 'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("postcondition: othermsg: 'message2' <type 'str'>", m)

