# -*- test-case-name: foolscap.test.test_banana -*-

from foolscap.tokens import Violation, BananaError
from foolscap.slicer import BaseSlicer, LeafUnslicer
from foolscap.constraint import OpenerConstraint


class NoneSlicer(BaseSlicer):
    opentype = ('none',)
    trackReferences = False
    slices = type(None)
    def sliceBody(self, streamable, banana):
        # hmm, we need an empty generator. I think a sequence is the only way
        # to accomplish this, other than 'if 0: yield' or something silly
        return []

class NoneUnslicer(LeafUnslicer):
    opentype = ('none',)

    def checkToken(self, typebyte, size):
        raise BananaError("NoneUnslicer does not accept any tokens")
    def receiveClose(self):
        return None, None


class Nothing(OpenerConstraint):
    """Accept only 'None'."""
    strictTaster = True
    opentypes = [("none",)]
    name = "Nothing"

    def checkObject(self, obj, inbound):
        if obj is not None:
            raise Violation("'%s' is not None" % (obj,))
    def maxSize(self, seen=None):
        if not seen: seen = []
        return self.OPENBYTES("none")
    def maxDepth(self, seen=None):
        if not seen: seen = []
        return 1

