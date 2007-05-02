
# This module contains all user-visible Constraint subclasses, for
# convenience by user code which is defining RemoteInterfaces. The primitive
# ones are defined in constraint.py, while the constraints associated with
# specific open sequences (list, unicode, etc) are defined in the related
# slicer/list.py module, etc. A few are defined here.

# It also defines the constraintMap and constraintTypeMap, used when
# constructing constraints out of the convenience shorthand. This is used
# when processing the methods defined in a RemoteInterface (such that a
# default argument like x=int gets turned into an IntegerConstraint). New
# slicers that want to add to these mappings can use addToConstraintTypeMap
# or manipulate constraintMap directly.

# this imports slicers and constraints.py, but is not allowed to import any
# other Foolscap modules, to avoid import cycles.

"""
primitive constraints:
   - types.StringType: string with maxLength=1k
   - String(maxLength=1000): string with arbitrary maxLength
   - types.BooleanType: boolean
   - types.IntType: integer that fits in s_int32_t
   - types.LongType: integer with abs(num) < 2**8192 (fits in 1024 bytes)
   - Int(maxBytes=1024): integer with arbitrary maxValue=2**(8*maxBytes)
   - types.FloatType: number
   - Number(maxBytes=1024): float or integer with maxBytes
   - interface: instance which implements (or adapts to) the Interface
   - class: instance of the class or a subclass
   - # unicode? types? none?

container constraints:
   - TupleOf(constraint1, constraint2..): fixed size, per-element constraint
   - ListOf(constraint, maxLength=30): all elements obey constraint
   - DictOf(keyconstraint, valueconstraint): keys and values obey constraints
   - AttributeDict(*attrTuples, ignoreUnknown=False):
      - attrTuples are (name, constraint)
      - ignoreUnknown=True means that received attribute names which aren't
        listed in attrTuples should be ignored instead of raising an
        UnknownAttrName exception

composite constraints:
   - tuple: alternatives: must obey one of the different constraints

modifiers:
   - Shared(constraint, refLimit=None): object may be referenced multiple times
     within the serialization domain (question: which domain?). All
     constraints default to refLimit=1, and a MultiplyReferenced exception
     is raised as soon as the reference count goes above the limit.
     refLimit=None means no limit is enforced.
   - Optional(name, constraint, default=None): key is not required. If not
            provided and default is None, key/attribute will not be created
            Only valid inside DictOf and AttributeDict.
   

"""

from foolscap.tokens import Violation, UnknownSchemaType

# make constraints available in a single location
from foolscap.constraint import Constraint, Any, ByteStringConstraint, \
     IntegerConstraint, NumberConstraint, \
     UnboundedSchema, IConstraint, Optional, Shared
from foolscap.slicers.unicode import UnicodeConstraint
from foolscap.slicers.bool import BooleanConstraint
from foolscap.slicers.dict import DictConstraint
from foolscap.slicers.list import ListConstraint
from foolscap.slicers.set import SetConstraint
from foolscap.slicers.tuple import TupleConstraint
from foolscap.slicers.none import Nothing
#  we don't import RemoteMethodSchema from remoteinterface.py, because
#  remoteinterface.py needs to import us (for addToConstraintTypeMap)
ignored = [Constraint, Any, ByteStringConstraint, UnicodeConstraint,
           IntegerConstraint, NumberConstraint, BooleanConstraint,
           DictConstraint, ListConstraint, SetConstraint, TupleConstraint,
           Nothing, Optional, Shared,
           ] # hush pyflakes

# convenience shortcuts

TupleOf = TupleConstraint
ListOf = ListConstraint
DictOf = DictConstraint
SetOf = SetConstraint


# note: using PolyConstraint (aka ChoiceOf) for inbound tasting is probably
# not fully vetted. One of the issues would be with something like
# ListOf(ChoiceOf(TupleOf(stuff), SetOf(stuff))). The ListUnslicer, when
# handling an inbound Tuple, will do
# TupleUnslicer.setConstraint(polyconstraint), since that's all it really
# knows about, and the TupleUnslicer will then try to look inside the
# polyconstraint for attributes that talk about tuples, and might fail.

class PolyConstraint(Constraint):
    name = "PolyConstraint"

    def __init__(self, *alternatives):
        self.alternatives = [IConstraint(a) for a in alternatives]
        self.alternatives = tuple(self.alternatives)
        # TODO: taster/opentypes should be a union of the alternatives'

    def checkObject(self, obj, inbound):
        ok = False
        for c in self.alternatives:
            try:
                c.checkObject(obj, inbound)
                ok = True
            except Violation:
                pass
        if not ok:
            raise Violation("does not satisfy any of %s" \
                            % (self.alternatives,))

    def maxSize(self, seen=None):
        if not seen: seen = []
        if self in seen:
            # TODO: if the PolyConstraint contains itself directly, the effect
            # is a nop. If a descendent contains the ancestor PolyConstraint,
            # then I think it's unbounded.. must draw this out
            raise UnboundedSchema # recursion
        seen.append(self)
        return reduce(max, [c.maxSize(seen[:])
                            for c in self.alternatives])

    def maxDepth(self, seen=None):
        if not seen: seen = []
        if self in seen:
            raise UnboundedSchema # recursion
        seen.append(self)
        return reduce(max, [c.maxDepth(seen[:]) for c in self.alternatives])

ChoiceOf = PolyConstraint

def AnyStringConstraint(*args, **kwargs):
    return ChoiceOf(ByteStringConstraint(*args, **kwargs),
                    UnicodeConstraint(*args, **kwargs))

# keep the old meaning, for now. Eventually StringConstraint should become an
# AnyStringConstraint
StringConstraint = ByteStringConstraint

constraintMap = {
    str: ByteStringConstraint(),
    unicode: UnicodeConstraint(),
    bool: BooleanConstraint(),
    int: IntegerConstraint(),
    long: IntegerConstraint(maxBytes=1024),
    float: NumberConstraint(),
    None: Nothing(),
    }

# This module provides a function named addToConstraintTypeMap() which helps
# to resolve some import cycles.

constraintTypeMap = []
def addToConstraintTypeMap(typ, constraintMaker):
    constraintTypeMap.insert(0, (typ, constraintMaker))

def _tupleConstraintMaker(t):
    return TupleConstraint(*t)
addToConstraintTypeMap(tuple, _tupleConstraintMaker)

# this function transforms the simple syntax (as used in RemoteInterface
# method definitions) into Constraint instances. This function is registered
# as a zope.interface adapter hook, so that once we've been loaded, other
# code can just do IConstraint(stuff) and expect it to work.

def adapt_obj_to_iconstraint(iface, t):
    if iface is not IConstraint:
        return None
    assert not IConstraint.providedBy(t) # not sure about this

    c = constraintMap.get(t, None)
    if c:
        return c

    for (typ, constraintMaker) in constraintTypeMap:
        if isinstance(t, typ):
            c = constraintMaker(t)
            if c:
                return c

    # RIFoo means accept either a Referenceable that implements RIFoo, or a
    # RemoteReference that points to just such a Referenceable. This is
    # hooked in by remoteinterface.py, when it calls addToConstraintTypeMap

    # we are the only way to make constraints
    raise UnknownSchemaType("can't make constraint from '%s' (%s)" %
                            (t, type(t)))

from zope.interface.interface import adapter_hooks
adapter_hooks.append(adapt_obj_to_iconstraint)


# how to accept "([(ref0" ?
# X = "TupleOf(ListOf(TupleOf(" * infinity
# ok, so you can't write a constraint that accepts it. I'm ok with that.
