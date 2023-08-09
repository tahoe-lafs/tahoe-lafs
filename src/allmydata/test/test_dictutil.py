"""
Tests for allmydata.util.dictutil.
"""
from __future__ import annotations

from future.utils import PY2, PY3

from unittest import skipIf

from twisted.trial import unittest

from allmydata.util import dictutil


class DictUtil(unittest.TestCase):
    def test_dict_of_sets(self):
        ds = dictutil.DictOfSets()
        ds.add(1, "a")
        ds.add(2, "b")
        ds.add(2, "b")
        ds.add(2, "c")
        self.failUnlessEqual(ds[1], set(["a"]))
        self.failUnlessEqual(ds[2], set(["b", "c"]))
        ds.discard(3, "d") # should not raise an exception
        ds.discard(2, "b")
        self.failUnlessEqual(ds[2], set(["c"]))
        ds.discard(2, "c")
        self.failIf(2 in ds)

        ds.add(3, "f")
        ds2 = dictutil.DictOfSets()
        ds2.add(3, "f")
        ds2.add(3, "g")
        ds2.add(4, "h")
        ds.update(ds2)
        self.failUnlessEqual(ds[1], set(["a"]))
        self.failUnlessEqual(ds[3], set(["f", "g"]))
        self.failUnlessEqual(ds[4], set(["h"]))

    def test_auxdict(self):
        d = dictutil.AuxValueDict()
        # we put the serialized form in the auxdata
        d.set_with_aux("key", ("filecap", "metadata"), "serialized")

        self.failUnlessEqual(list(d.keys()), ["key"])
        self.failUnlessEqual(d["key"], ("filecap", "metadata"))
        self.failUnlessEqual(d.get_aux("key"), "serialized")
        def _get_missing(key):
            return d[key]
        self.failUnlessRaises(KeyError, _get_missing, "nonkey")
        self.failUnlessEqual(d.get("nonkey"), None)
        self.failUnlessEqual(d.get("nonkey", "nonvalue"), "nonvalue")
        self.failUnlessEqual(d.get_aux("nonkey"), None)
        self.failUnlessEqual(d.get_aux("nonkey", "nonvalue"), "nonvalue")

        d["key"] = ("filecap2", "metadata2")
        self.failUnlessEqual(d["key"], ("filecap2", "metadata2"))
        self.failUnlessEqual(d.get_aux("key"), None)

        d.set_with_aux("key2", "value2", "aux2")
        self.failUnlessEqual(sorted(d.keys()), ["key", "key2"])
        del d["key2"]
        self.failUnlessEqual(list(d.keys()), ["key"])
        self.failIf("key2" in d)
        self.failUnlessRaises(KeyError, _get_missing, "key2")
        self.failUnlessEqual(d.get("key2"), None)
        self.failUnlessEqual(d.get_aux("key2"), None)
        d["key2"] = "newvalue2"
        self.failUnlessEqual(d.get("key2"), "newvalue2")
        self.failUnlessEqual(d.get_aux("key2"), None)

        d = dictutil.AuxValueDict({1:2,3:4})
        self.failUnlessEqual(sorted(d.keys()), [1,3])
        self.failUnlessEqual(d[1], 2)
        self.failUnlessEqual(d.get_aux(1), None)

        d = dictutil.AuxValueDict([ (1,2), (3,4) ])
        self.failUnlessEqual(sorted(d.keys()), [1,3])
        self.failUnlessEqual(d[1], 2)
        self.failUnlessEqual(d.get_aux(1), None)

        d = dictutil.AuxValueDict(one=1, two=2)
        self.failUnlessEqual(sorted(d.keys()), ["one","two"])
        self.failUnlessEqual(d["one"], 1)
        self.failUnlessEqual(d.get_aux("one"), None)


class TypedKeyDict(unittest.TestCase):
    """Tests for dictionaries that limit keys."""

    @skipIf(PY2, "Python 2 doesn't have issues mixing bytes and unicode.")
    def setUp(self):
        pass

    def test_bytes(self):
        """BytesKeyDict is limited to just byte keys."""
        self.assertRaises(TypeError, dictutil.BytesKeyDict, {u"hello": 123})
        d = dictutil.BytesKeyDict({b"123": 200})
        with self.assertRaises(TypeError):
            d[u"hello"] = "blah"
        with self.assertRaises(TypeError):
            d[u"hello"]
        with self.assertRaises(TypeError):
            del d[u"hello"]
        with self.assertRaises(TypeError):
            d.setdefault(u"hello", "123")
        with self.assertRaises(TypeError):
            d.get(u"xcd")

        # Byte keys are fine:
        self.assertEqual(d, {b"123": 200})
        d[b"456"] = 400
        self.assertEqual(d[b"456"], 400)
        del d[b"456"]
        self.assertEqual(d.get(b"456", 50), 50)
        self.assertEqual(d.setdefault(b"456", 300), 300)
        self.assertEqual(d[b"456"], 300)

    def test_unicode(self):
        """UnicodeKeyDict is limited to just unicode keys."""
        self.assertRaises(TypeError, dictutil.UnicodeKeyDict, {b"hello": 123})
        d = dictutil.UnicodeKeyDict({u"123": 200})
        with self.assertRaises(TypeError):
            d[b"hello"] = "blah"
        with self.assertRaises(TypeError):
            d[b"hello"]
        with self.assertRaises(TypeError):
            del d[b"hello"]
        with self.assertRaises(TypeError):
            d.setdefault(b"hello", "123")
        with self.assertRaises(TypeError):
            d.get(b"xcd")

        # Byte keys are fine:
        self.assertEqual(d, {u"123": 200})
        d[u"456"] = 400
        self.assertEqual(d[u"456"], 400)
        del d[u"456"]
        self.assertEqual(d.get(u"456", 50), 50)
        self.assertEqual(d.setdefault(u"456", 300), 300)
        self.assertEqual(d[u"456"], 300)


class TypedKeyDictPython2(unittest.TestCase):
    """Tests for dictionaries that limit keys on Python 2."""

    @skipIf(PY3, "Testing Python 2 behavior.")
    def test_python2(self):
        """
        On Python2, BytesKeyDict and UnicodeKeyDict are unnecessary, because
        dicts can mix both without problem so you don't get confusing behavior
        if you get the type wrong.

        Eventually in a Python 3-only world mixing bytes and unicode will be
        bad, thus the existence of these classes, but as we port there will be
        situations where it's mixed on Python 2, which again is fine.
        """
        self.assertIs(dictutil.UnicodeKeyDict, dict)
        self.assertIs(dictutil.BytesKeyDict, dict)
        # Demonstration of how bytes and unicode can be mixed:
        d = {u"abc": 1}
        self.assertEqual(d[b"abc"], 1)


class FilterTests(unittest.TestCase):
    """
    Tests for ``dictutil.filter``.
    """
    def test_filter(self) -> None:
        """
        ``dictutil.filter`` returns a ``dict`` that contains the key/value
        pairs for which the value is matched by the given predicate.
        """
        self.assertEqual(
            {1: 2},
            dictutil.filter(lambda v: v == 2, {1: 2, 2: 3}),
        )
