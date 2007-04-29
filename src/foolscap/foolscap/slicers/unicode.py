# -*- test-case-name: foolscap.test.test_banana -*-

from twisted.internet.defer import Deferred
from foolscap.constraint import Any, StringConstraint
from foolscap.tokens import BananaError, STRING
from foolscap.slicer import BaseSlicer, LeafUnslicer

class UnicodeSlicer(BaseSlicer):
    opentype = ("unicode",)
    slices = unicode
    def sliceBody(self, streamable, banana):
        yield self.obj.encode("UTF-8")

class UnicodeUnslicer(LeafUnslicer):
    # accept a UTF-8 encoded string
    opentype = ("unicode",)
    string = None
    constraint = None

    def setConstraint(self, constraint):
        if isinstance(constraint, Any):
            return
        assert isinstance(constraint, StringConstraint)
        self.constraint = constraint

    def checkToken(self, typebyte, size):
        if typebyte != STRING:
            raise BananaError("UnicodeUnslicer only accepts strings")
        if self.constraint:
            self.constraint.checkToken(typebyte, size)

    def receiveChild(self, obj, ready_deferred=None):
        assert not isinstance(obj, Deferred)
        assert ready_deferred is None
        if self.string != None:
            raise BananaError("already received a string")
        self.string = unicode(obj, "UTF-8")

    def receiveClose(self):
        return self.string, None
    def describe(self):
        return "<unicode>"
