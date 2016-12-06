from twisted.trial import unittest
from allmydata.mutable.common import NeedMoreDataError, UncoordinatedWriteError

class Exceptions(unittest.TestCase):
    def test_repr(self):
        nmde = NeedMoreDataError(100, 50, 100)
        self.failUnless("NeedMoreDataError" in repr(nmde), repr(nmde))
        ucwe = UncoordinatedWriteError()
        self.failUnless("UncoordinatedWriteError" in repr(ucwe), repr(ucwe))
