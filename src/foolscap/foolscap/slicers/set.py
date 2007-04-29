# -*- test-case-name: foolscap.test.test_banana -*-

import sets
from foolscap.slicers.list import ListSlicer, ListUnslicer
from foolscap.tokens import Violation
from foolscap.constraint import OpenerConstraint, UnboundedSchema, Any, \
     IConstraint

class SetSlicer(ListSlicer):
    opentype = ("set",)
    trackReferences = True
    slices = sets.Set

    def sliceBody(self, streamable, banana):
        for i in self.obj:
            yield i

class ImmutableSetSlicer(SetSlicer):
    opentype = ("immutable-set",)
    trackReferences = False
    slices = sets.ImmutableSet

have_builtin_set = False
try:
    set
    # python2.4 has a builtin 'set' type, which is mutable
    have_builtin_set = True
    class BuiltinSetSlicer(SetSlicer):
        slices = set
    class BuiltinFrozenSetSlicer(ImmutableSetSlicer):
        slices = frozenset
except NameError:
    # oh well, I guess we don't have 'set'
    pass

class SetUnslicer(ListUnslicer):
    opentype = ("set",)
    def receiveClose(self):
        return sets.Set(self.list), None

    def setConstraint(self, constraint):
        if isinstance(constraint, Any):
            return
        assert isinstance(constraint, SetConstraint)
        self.maxLength = constraint.maxLength
        self.itemConstraint = constraint.constraint

class ImmutableSetUnslicer(SetUnslicer):
    opentype = ("immutable-set",)
    def receiveClose(self):
        return sets.ImmutableSet(self.list), None


class SetConstraint(OpenerConstraint):
    """The object must be a Set of some sort, with a given maximum size. To
    accept sets of any size, use maxLength=None. All member objects must obey
    the given constraint. By default this will accept both mutable and
    immutable sets, if you want to require a particular type, set mutable= to
    either True or False.
    """

    # TODO: if mutable!=None, we won't throw out the wrong set type soon
    # enough. We need to override checkOpenType to accomplish this.
    opentypes = [("set",), ("immutable-set",)]
    name = "SetConstraint"

    if have_builtin_set:
        mutable_set_types = (set, sets.Set)
        immutable_set_types = (frozenset, sets.ImmutableSet)
    else:
        mutable_set_types = (sets.Set,)
        immutable_set_types = (sets.ImmutableSet,)
    all_set_types = mutable_set_types + immutable_set_types

    def __init__(self, constraint, maxLength=30, mutable=None):
        self.constraint = IConstraint(constraint)
        self.maxLength = maxLength
        self.mutable = mutable

    def checkObject(self, obj, inbound):
        if not isinstance(obj, self.all_set_types):
            raise Violation("not a set")
        if (self.mutable == True and
            not isinstance(obj, self.mutable_set_types)):
            raise Violation("obj is a set, but not a mutable one")
        if (self.mutable == False and
            not isinstance(obj, self.immutable_set_types)):
            raise Violation("obj is a set, but not an immutable one")
        if self.maxLength is not None and len(obj) > self.maxLength:
            raise Violation("set is too large")
        if self.constraint:
            for o in obj:
                self.constraint.checkObject(o, inbound)

    def maxSize(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        if self.maxLength == None:
            raise UnboundedSchema
        if not self.constraint:
            raise UnboundedSchema
        return (self.OPENBYTES("immutable-set") +
                self.maxLength * self.constraint.maxSize(seen))

    def maxDepth(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        if not self.constraint:
            raise UnboundedSchema
        seen.append(self)
        return 1 + self.constraint.maxDepth(seen)
