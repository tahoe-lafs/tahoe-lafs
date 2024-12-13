"""
Ported to Python 3.
"""

from ..common import SyncTestCase
from allmydata.mutable.common import NeedMoreDataError, UncoordinatedWriteError


class Exceptions(SyncTestCase):
    def test_repr(self):
        nmde = NeedMoreDataError(100, 50, 100)
        self.assertTrue("NeedMoreDataError" in repr(nmde), msg=repr(nmde))
        self.assertTrue("NeedMoreDataError" in repr(nmde), msg=repr(nmde))
        ucwe = UncoordinatedWriteError()
        self.assertTrue("UncoordinatedWriteError" in repr(ucwe), msg=repr(ucwe))
