
def foo(): pass # keep the line number constant

from twisted.trial import unittest

from allmydata.util import bencode, idlib, humanreadable


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
        self.failUnlessEqual(hr(foo), "<foo() at test_utils.py:2>")
        self.failUnlessEqual(hr(self.test_repr),
                             "<bound method HumanReadable.test_repr of <allmydata.test.test_utils.HumanReadable testMethod=test_repr>>")
        self.failUnlessEqual(hr(1L), "1")
        self.failUnlessEqual(hr(10**40),
                             "100000000000000000...000000000000000000")
        self.failUnlessEqual(hr(self), "<allmydata.test.test_utils.HumanReadable testMethod=test_repr>")
        self.failUnlessEqual(hr([1,2]), "[1, 2]")
        self.failUnlessEqual(hr({1:2}), "{1:2}")
        try:
            raise RuntimeError
        except Exception, e:
            self.failUnlessEqual(hr(e), "<RuntimeError: ()>")
        try:
            raise RuntimeError("oops")
        except Exception, e:
            self.failUnlessEqual(hr(e), "<RuntimeError: 'oops'>")
        try:
            raise NoArgumentException
        except Exception, e:
            self.failUnlessEqual(hr(e), "<NoArgumentException>")


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

