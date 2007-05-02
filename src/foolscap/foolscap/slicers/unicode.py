# -*- test-case-name: foolscap.test.test_banana -*-

import re
from twisted.internet.defer import Deferred
from foolscap.tokens import BananaError, STRING, VOCAB, Violation
from foolscap.slicer import BaseSlicer, LeafUnslicer
from foolscap.constraint import OpenerConstraint, Any, UnboundedSchema

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
        assert isinstance(constraint, UnicodeConstraint)
        self.constraint = constraint

    def checkToken(self, typebyte, size):
        if typebyte not in (STRING, VOCAB):
            raise BananaError("UnicodeUnslicer only accepts strings")
        #if self.constraint:
        #    self.constraint.checkToken(typebyte, size)

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

class UnicodeConstraint(OpenerConstraint):
    """The object must be a unicode object. The maxLength and minLength
    parameters restrict the number of characters (code points, *not* bytes)
    that may be present in the object, which means that the on-wire (UTF-8)
    representation may take up to 6 times as many bytes as characters.
    """

    strictTaster = True
    opentypes = [("unicode",)]
    name = "UnicodeConstraint"

    def __init__(self, maxLength=1000, minLength=0, regexp=None):
        self.maxLength = maxLength
        self.minLength = minLength
        # allow VOCAB in case the Banana-level tokenizer decides to tokenize
        # the UTF-8 encoded body of a unicode object, since this is just as
        # likely as tokenizing regular bytestrings. TODO: this is disabled
        # because it doesn't currently work.. once I remember how Constraints
        # work, I'll fix this. The current version is too permissive of
        # tokens.
        #self.taster = {STRING: 6*self.maxLength,
        #               VOCAB: None}
        # regexp can either be a string or a compiled SRE_Match object..
        # re.compile appears to notice SRE_Match objects and pass them
        # through unchanged.
        self.regexp = None
        if regexp:
            self.regexp = re.compile(regexp)

    def checkObject(self, obj, inbound):
        if not isinstance(obj, unicode):
            raise Violation("not a String")
        if self.maxLength != None and len(obj) > self.maxLength:
            raise Violation("string too long (%d > %d)" %
                            (len(obj), self.maxLength))
        if len(obj) < self.minLength:
            raise Violation("string too short (%d < %d)" %
                            (len(obj), self.minLength))
        if self.regexp:
            if not self.regexp.search(obj):
                raise Violation("regexp failed to match")

    def maxSize(self, seen=None):
        if self.maxLength == None:
            raise UnboundedSchema
        return self.OPENBYTES("unicode") + self.maxLength * 6

    def maxDepth(self, seen=None):
        return 1+1
