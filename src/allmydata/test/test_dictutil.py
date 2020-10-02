"""
Tests for allmydata.util.dictutil.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

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
