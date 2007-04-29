# -*- test-case-name: foolscap.test.test_banana -*-

from twisted.python.components import registerAdapter
from zope.interface import implements
from twisted.internet.defer import Deferred
import tokens
from tokens import Violation, BananaError


class SlicerClass(type):
    # auto-register Slicers
    def __init__(self, name, bases, dict):
        type.__init__(self, name, bases, dict)
        typ = dict.get('slices')
        #reg = dict.get('slicerRegistry')
        if typ:
            registerAdapter(self, typ, tokens.ISlicer)


class BaseSlicer:
    __metaclass__ = SlicerClass
    implements(tokens.ISlicer)

    slices = None

    parent = None
    sendOpen = True
    opentype = ()
    trackReferences = False

    def __init__(self, obj):
        # this simplifies Slicers which are adapters
        self.obj = obj

    def registerReference(self, refid, obj):
        # optimize: most Slicers will delegate this up to the Root
        return self.parent.registerReference(refid, obj)
    def slicerForObject(self, obj):
        # optimize: most Slicers will delegate this up to the Root
        return self.parent.slicerForObject(obj)
    def slice(self, streamable, banana):
        # this is what makes us ISlicer
        self.streamable = streamable
        assert self.opentype
        for o in self.opentype:
            yield o
        for t in self.sliceBody(streamable, banana):
            yield t
    def sliceBody(self, streamable, banana):
        raise NotImplementedError
    def childAborted(self, f):
        return f

    def describe(self):
        return "??"


class ScopedSlicer(BaseSlicer):
    """This Slicer provides a containing scope for referenceable things like
    lists. The same list will not be serialized twice within this scope, but
    it will not survive outside it."""

    def __init__(self, obj):
        BaseSlicer.__init__(self, obj)
        self.references = {} # maps id(obj) -> (obj,refid)

    def registerReference(self, refid, obj):
        # keep references here, not in the actual PBRootSlicer

        # This use of id(obj) requires a bit of explanation. We are making
        # the assumption that the object graph remains unmodified until
        # serialization is complete. In particular, we assume that all the
        # objects in it remain alive, and no new objects are added to it,
        # until serialization is complete. id(obj) is only unique for live
        # objects: once the object is garbage-collected, a new object may be
        # created with the same id(obj) value.
        #
        # The concern is that a custom Slicer will call something that
        # mutates the object graph before it has finished being serialized.
        # This might be one which calls some user-level function during
        # Slicing, or one which uses a Deferred to put off serialization for
        # a while, creating an opportunity for some other code to get
        # control.

        # The specific concern is that if, in the middle of serialization, an
        # object that was already serialized is gc'ed, and a new object is
        # created and attached to a portion of the object graph that hasn't
        # been serialized yet, and if the new object gets the same id(obj) as
        # the dead object, then we could be tricked into sending the
        # reference number of the old (dead) object. On the receiving end,
        # this would result in a mangled object graph.

        # User code isn't supposed to allow the object graph to change during
        # serialization, so this mangling "should not happen" under normal
        # circumstances. However, as a reasonably cheap way to mitigate the
        # worst sort of mangling when user code *does* mess up,
        # self.references maps from id(obj) to a tuple of (obj,refid) instead
        # of just the refid. This insures that the object will stay alive
        # until the ScopedSlicer dies, guaranteeing that we won't get
        # duplicate id(obj) values. If user code mutates the object graph
        # during serialization we might still get inconsistent results, but
        # they'll be the ordinary kind of inconsistent results (snapshots of
        # different branches of the object graph at different points in time)
        # rather than the blatantly wrong mangling that would occur with
        # re-used id(obj) values.
        
        self.references[id(obj)] = (obj,refid)

    def slicerForObject(self, obj):
        # check for an object which was sent previously or has at least
        # started sending
        obj_refid = self.references.get(id(obj), None)
        if obj_refid is not None:
            # we've started to send this object already, so just include a
            # reference to it
            return ReferenceSlicer(obj_refid[1])
        # otherwise go upstream so we can serialize the object completely
        return self.parent.slicerForObject(obj)

UnslicerRegistry = {}
BananaUnslicerRegistry = {}

def registerUnslicer(opentype, factory, registry=None):
    if registry is None:
        registry = UnslicerRegistry
    assert not registry.has_key(opentype)
    registry[opentype] = factory

class UnslicerClass(type):
    # auto-register Unslicers
    def __init__(self, name, bases, dict):
        type.__init__(self, name, bases, dict)
        opentype = dict.get('opentype')
        reg = dict.get('unslicerRegistry')
        if opentype:
            registerUnslicer(opentype, self, reg)

class BaseUnslicer:
    __metaclass__ = UnslicerClass
    opentype = None
    implements(tokens.IUnslicer)

    def __init__(self):
        pass

    def describe(self):
        return "??"

    def setConstraint(self, constraint):
        pass

    def start(self, count):
        pass

    def checkToken(self, typebyte, size):
        return # no restrictions

    def openerCheckToken(self, typebyte, size, opentype):
        return self.parent.openerCheckToken(typebyte, size, opentype)

    def open(self, opentype):
        """Return an IUnslicer object based upon the 'opentype' tuple.
        Subclasses that wish to change the way opentypes are mapped to
        Unslicers can do so by changing this behavior.

        This method does not apply constraints, it only serves to map
        opentype into Unslicer. Most subclasses will implement this by
        delegating the request to their parent (and thus, eventually, to the
        RootUnslicer), and will set the new child's .opener attribute so
        that they can do the same. Subclasses that wish to change the way
        opentypes are mapped to Unslicers can do so by changing this
        behavior."""

        return self.parent.open(opentype)

    def doOpen(self, opentype):
        """Return an IUnslicer object based upon the 'opentype' tuple. This
        object will receive all tokens destined for the subnode. 

        If you want to enforce a constraint, you must override this method
        and do two things: make sure your constraint accepts the opentype,
        and set a per-item constraint on the new child unslicer.

        This method gets the IUnslicer from our .open() method. That might
        return None instead of a child unslicer if the they want a
        multi-token opentype tuple, so be sure to check for Noneness before
        adding a per-item constraint.
        """

        return self.open(opentype)

    def receiveChild(self, obj, ready_deferred=None):
        pass

    def reportViolation(self, why):
        return why

    def receiveClose(self):
        raise NotImplementedError

    def finish(self):
        pass


    def setObject(self, counter, obj):
        """To pass references to previously-sent objects, the [OPEN,
        'reference', number, CLOSE] sequence is used. The numbers are
        generated implicitly by the sending Banana, counting from 0 for the
        object described by the very first OPEN sent over the wire,
        incrementing for each subsequent one. The objects themselves are
        stored in any/all Unslicers who cares to. Generally this is the
        RootUnslicer, but child slices could do it too if they wished.
        """
        # TODO: examine how abandoned child objects could mess up this
        # counter
        pass

    def getObject(self, counter):
        """'None' means 'ask our parent instead'.
        """
        return None

    def explode(self, failure):
        """If something goes wrong in a Deferred callback, it may be too
        late to reject the token and to normal error handling. I haven't
        figured out how to do sensible error-handling in this situation.
        This method exists to make sure that the exception shows up
        *somewhere*. If this is called, it is also likely that a placeholder
        (probably a Deferred) will be left in the unserialized object about
        to be handed to the RootUnslicer.
        """
        print "KABOOM"
        print failure
        self.protocol.exploded = failure

class ScopedUnslicer(BaseUnslicer):
    """This Unslicer provides a containing scope for referenceable things
    like lists. It corresponds to the ScopedSlicer base class."""

    def __init__(self):
        BaseUnslicer.__init__(self)
        self.references = {}

    def setObject(self, counter, obj):
        if self.protocol.debugReceive:
            print "setObject(%s): %s{%s}" % (counter, obj, id(obj))
        self.references[counter] = obj

    def getObject(self, counter):
        obj = self.references.get(counter)
        if self.protocol.debugReceive:
            print "getObject(%s) -> %s{%s}" % (counter, obj, id(obj))
        return obj


class LeafUnslicer(BaseUnslicer):
    # inherit from this to reject any child nodes

    # .checkToken in LeafUnslicer subclasses should reject OPEN tokens

    def doOpen(self, opentype):
        raise Violation("'%s' does not accept sub-objects" % self)


# References are special enough to put here instead of slicers/

class ReferenceSlicer(BaseSlicer):
    # this is created explicitly, not as an adapter
    opentype = ('reference',)
    trackReferences = False

    def __init__(self, refid):
        assert type(refid) is int
        self.refid = refid
    def sliceBody(self, streamable, banana):
        yield self.refid

class ReferenceUnslicer(LeafUnslicer):
    opentype = ('reference',)

    constraint = None
    finished = False

    def setConstraint(self, constraint):
        self.constraint = constraint

    def checkToken(self, typebyte,size):
        if typebyte != tokens.INT:
            raise BananaError("ReferenceUnslicer only accepts INTs")

    def receiveChild(self, obj, ready_deferred=None):
        assert not isinstance(obj, Deferred)
        assert ready_deferred is None
        if self.finished:
            raise BananaError("ReferenceUnslicer only accepts one int")
        self.obj = self.protocol.getObject(obj)
        self.finished = True
        # assert that this conforms to the constraint
        if self.constraint:
            self.constraint.checkObject(self.obj, True)
        # TODO: it might be a Deferred, but we should know enough about the
        # incoming value to check the constraint. This requires a subclass
        # of Deferred which can give us the metadata.

    def receiveClose(self):
        return self.obj, None
