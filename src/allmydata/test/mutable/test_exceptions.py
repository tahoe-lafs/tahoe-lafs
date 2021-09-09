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

from ..common import SyncTestCase
from allmydata.mutable.common import NeedMoreDataError, UncoordinatedWriteError


class Exceptions(SyncTestCase):
    def test_repr(self):
        nmde = NeedMoreDataError(100, 50, 100)
        self.assertTrue("NeedMoreDataError" in repr(nmde), msg=repr(nmde))
        self.assertTrue("NeedMoreDataError" in repr(nmde), msg=repr(nmde))
        ucwe = UncoordinatedWriteError()
        self.assertTrue("UncoordinatedWriteError" in repr(ucwe), msg=repr(ucwe))
