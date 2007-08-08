# -*- test-case-name: foolscap.test.test_banana -*-

from twisted.python import log
from twisted.internet.defer import Deferred
from foolscap.tokens import Violation
from foolscap.slicer import BaseSlicer, BaseUnslicer
from foolscap.constraint import OpenerConstraint, Any, UnboundedSchema, IConstraint
from foolscap.util import AsyncAND


class ListSlicer(BaseSlicer):
    opentype = ("list",)
    trackReferences = True
    slices = list

    def sliceBody(self, streamable, banana):
        for i in self.obj:
            yield i

class ListUnslicer(BaseUnslicer):
    opentype = ("list",)

    maxLength = None
    itemConstraint = None
    debug = False

    def setConstraint(self, constraint):
        if isinstance(constraint, Any):
            return
        assert isinstance(constraint, ListConstraint)
        self.maxLength = constraint.maxLength
        self.itemConstraint = constraint.constraint

    def start(self, count):
        #self.opener = foo # could replace it if we wanted to
        self.list = []
        self.count = count
        if self.debug:
            log.msg("%s[%d].start with %s" % (self, self.count, self.list))
        self.protocol.setObject(count, self.list)
        self._ready_deferreds = []

    def checkToken(self, typebyte, size):
        if self.maxLength != None and len(self.list) >= self.maxLength:
            # list is full, no more tokens accepted
            # this is hit if the max+1 item is a primitive type
            raise Violation("the list is full")
        if self.itemConstraint:
            self.itemConstraint.checkToken(typebyte, size)

    def doOpen(self, opentype):
        # decide whether the given object type is acceptable here. Raise a
        # Violation exception if not, otherwise give it to our opener (which
        # will normally be the RootUnslicer). Apply a constraint to the new
        # unslicer.
        if self.maxLength != None and len(self.list) >= self.maxLength:
            # this is hit if the max+1 item is a non-primitive type
            raise Violation("the list is full")
        if self.itemConstraint:
            self.itemConstraint.checkOpentype(opentype)
        unslicer = self.open(opentype)
        if unslicer:
            if self.itemConstraint:
                unslicer.setConstraint(self.itemConstraint)
        return unslicer

    def update(self, obj, index):
        # obj has already passed typechecking
        if self.debug:
            log.msg("%s[%d].update: [%d]=%s" % (self, self.count, index, obj))
        assert isinstance(index, int)
        self.list[index] = obj
        return obj

    def receiveChild(self, obj, ready_deferred=None):
        if ready_deferred:
            self._ready_deferreds.append(ready_deferred)
        if self.debug:
            log.msg("%s[%d].receiveChild(%s)" % (self, self.count, obj))
        # obj could be a primitive type, a Deferred, or a complex type like
        # those returned from an InstanceUnslicer. However, the individual
        # object has already been through the schema validation process. The
        # only remaining question is whether the larger schema will accept
        # it.
        if self.maxLength != None and len(self.list) >= self.maxLength:
            # this is redundant
            # (if it were a non-primitive one, it would be caught in doOpen)
            # (if it were a primitive one, it would be caught in checkToken)
            raise Violation("the list is full")
        if isinstance(obj, Deferred):
            if self.debug:
                log.msg(" adding my update[%d] to %s" % (len(self.list), obj))
            obj.addCallback(self.update, len(self.list))
            obj.addErrback(self.printErr)
            placeholder = "list placeholder for arg[%d], rd=%s" % \
                          (len(self.list), ready_deferred)
            self.list.append(placeholder)
        else:
            self.list.append(obj)

    def printErr(self, why):
        print "ERR!"
        print why.getBriefTraceback()
        log.err(why)

    def receiveClose(self):
        ready_deferred = None
        if self._ready_deferreds:
            ready_deferred = AsyncAND(self._ready_deferreds)
        return self.list, ready_deferred

    def describe(self):
        return "[%d]" % len(self.list)


class ListConstraint(OpenerConstraint):
    """The object must be a list of objects, with a given maximum length. To
    accept lists of any length, use maxLength=None (but you will get a
    UnboundedSchema warning). All member objects must obey the given
    constraint."""

    opentypes = [("list",)]
    name = "ListConstraint"

    def __init__(self, constraint, maxLength=30, minLength=0):
        self.constraint = IConstraint(constraint)
        self.maxLength = maxLength
        self.minLength = minLength

    def checkObject(self, obj, inbound):
        if not isinstance(obj, list):
            raise Violation("not a list")
        if self.maxLength is not None and len(obj) > self.maxLength:
            raise Violation("list too long")
        if len(obj) < self.minLength:
            raise Violation("list too short")
        for o in obj:
            self.constraint.checkObject(o, inbound)

    def maxSize(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        if self.maxLength == None:
            raise UnboundedSchema
        return (self.OPENBYTES("list") +
                self.maxLength * self.constraint.maxSize(seen))
    def maxDepth(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        return 1 + self.constraint.maxDepth(seen)
