"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.trial import unittest
from .util import FakeStorage, make_nodemaker

class DifferentEncoding(unittest.TestCase):
    def setUp(self):
        self._storage = s = FakeStorage()
        self.nodemaker = make_nodemaker(s)

    def test_filenode(self):
        # create a file with 3-of-20, then modify it with a client configured
        # to do 3-of-10. #1510 tracks a failure here
        self.nodemaker.default_encoding_parameters["n"] = 20
        d = self.nodemaker.create_mutable_file(b"old contents")
        def _created(n):
            filecap = n.get_cap().to_string()
            del n # we want a new object, not the cached one
            self.nodemaker.default_encoding_parameters["n"] = 10
            n2 = self.nodemaker.create_from_cap(filecap)
            return n2
        d.addCallback(_created)
        def modifier(old_contents, servermap, first_time):
            return b"new contents"
        d.addCallback(lambda n: n.modify(modifier))
        return d
