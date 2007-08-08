# -*- test-case-name: foolscap.test.test_banana -*-

from twisted.python import log
from twisted.internet.defer import Deferred
from foolscap.tokens import Violation, BananaError
from foolscap.slicer import BaseSlicer, BaseUnslicer
from foolscap.constraint import OpenerConstraint, Any, UnboundedSchema, IConstraint
from foolscap.util import AsyncAND

class DictSlicer(BaseSlicer):
    opentype = ('dict',)
    trackReferences = True
    slices = None
    def sliceBody(self, streamable, banana):
        for key,value in self.obj.items():
            yield key
            yield value

class DictUnslicer(BaseUnslicer):
    opentype = ('dict',)

    gettingKey = True
    keyConstraint = None
    valueConstraint = None
    maxKeys = None

    def setConstraint(self, constraint):
        if isinstance(constraint, Any):
            return
        assert isinstance(constraint, DictConstraint)
        self.keyConstraint = constraint.keyConstraint
        self.valueConstraint = constraint.valueConstraint
        self.maxKeys = constraint.maxKeys

    def start(self, count):
        self.d = {}
        self.protocol.setObject(count, self.d)
        self.key = None
        self._ready_deferreds = []

    def checkToken(self, typebyte, size):
        if self.maxKeys != None:
            if len(self.d) >= self.maxKeys:
                raise Violation("the dict is full")
        if self.gettingKey:
            if self.keyConstraint:
                self.keyConstraint.checkToken(typebyte, size)
        else:
            if self.valueConstraint:
                self.valueConstraint.checkToken(typebyte, size)

    def doOpen(self, opentype):
        if self.maxKeys != None:
            if len(self.d) >= self.maxKeys:
                raise Violation("the dict is full")
        if self.gettingKey:
            if self.keyConstraint:
                self.keyConstraint.checkOpentype(opentype)
        else:
            if self.valueConstraint:
                self.valueConstraint.checkOpentype(opentype)
        unslicer = self.open(opentype)
        if unslicer:
            if self.gettingKey:
                if self.keyConstraint:
                    unslicer.setConstraint(self.keyConstraint)
            else:
                if self.valueConstraint:
                    unslicer.setConstraint(self.valueConstraint)
        return unslicer

    def update(self, value, key):
        # this is run as a Deferred callback, hence the backwards arguments
        self.d[key] = value

    def receiveChild(self, obj, ready_deferred=None):
        if ready_deferred:
            self._ready_deferreds.append(ready_deferred)
        if self.gettingKey:
            self.receiveKey(obj)
        else:
            self.receiveValue(obj)
        self.gettingKey = not self.gettingKey

    def receiveKey(self, key):
        # I don't think it is legal (in python) to use an incomplete object
        # as a dictionary key, because you must have all the contents to
        # hash it. Someone could fake up a token stream to hit this case,
        # however: OPEN(dict), OPEN(tuple), OPEN(reference), 0, CLOSE, CLOSE,
        # "value", CLOSE
        if isinstance(key, Deferred):
            raise BananaError("incomplete object as dictionary key")
        try:
            if self.d.has_key(key):
                raise BananaError("duplicate key '%s'" % key)
        except TypeError:
            raise BananaError("unhashable key '%s'" % key)
        self.key = key

    def receiveValue(self, value):
        if isinstance(value, Deferred):
            value.addCallback(self.update, self.key)
            value.addErrback(log.err)
        self.d[self.key] = value # placeholder

    def receiveClose(self):
        ready_deferred = None
        if self._ready_deferreds:
            ready_deferred = AsyncAND(self._ready_deferreds)
        return self.d, ready_deferred

    def describe(self):
        if self.gettingKey:
            return "{}"
        else:
            return "{}[%s]" % self.key


class OrderedDictSlicer(DictSlicer):
    slices = dict
    def sliceBody(self, streamable, banana):
        keys = self.obj.keys()
        keys.sort()
        for key in keys:
            value = self.obj[key]
            yield key
            yield value


class DictConstraint(OpenerConstraint):
    opentypes = [("dict",)]
    name = "DictConstraint"

    def __init__(self, keyConstraint, valueConstraint, maxKeys=30):
        self.keyConstraint = IConstraint(keyConstraint)
        self.valueConstraint = IConstraint(valueConstraint)
        self.maxKeys = maxKeys
    def checkObject(self, obj, inbound):
        if not isinstance(obj, dict):
            raise Violation, "'%s' (%s) is not a Dictionary" % (obj,
                                                                type(obj))
        if self.maxKeys != None and len(obj) > self.maxKeys:
            raise Violation, "Dict keys=%d > maxKeys=%d" % (len(obj),
                                                            self.maxKeys)
        for key, value in obj.iteritems():
            self.keyConstraint.checkObject(key, inbound)
            self.valueConstraint.checkObject(value, inbound)
    def maxSize(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        if self.maxKeys == None:
            raise UnboundedSchema
        keySize = self.keyConstraint.maxSize(seen[:])
        valueSize = self.valueConstraint.maxSize(seen[:])
        return self.OPENBYTES("dict") + self.maxKeys * (keySize + valueSize)
    def maxDepth(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        keyDepth = self.keyConstraint.maxDepth(seen[:])
        valueDepth = self.valueConstraint.maxDepth(seen[:])
        return 1 + max(keyDepth, valueDepth)


