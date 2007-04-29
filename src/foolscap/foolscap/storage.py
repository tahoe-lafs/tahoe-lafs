
"""
storage.py: support for using Banana as if it were pickle

This includes functions for serializing to and from strings, instead of a
network socket. It also has support for serializing 'unsafe' objects,
specifically classes, modules, functions, and instances of arbitrary classes.
These are 'unsafe' because to recreate the object on the deserializing end,
we must be willing to execute code of the sender's choosing (i.e. the
constructor of whatever package.module.class names they send us). It is
unwise to do this unless you are willing to allow your internal state to be
compromised by the author of the serialized data you're unpacking.

This functionality is isolated here because it is never used for data coming
over network connections.
"""

from cStringIO import StringIO
import types
from new import instance, instancemethod
from pickle import whichmodule  # used by FunctionSlicer

from foolscap import slicer, banana, tokens
from foolscap.tokens import BananaError
from twisted.internet.defer import Deferred
from twisted.python import reflect
from foolscap.slicers.dict import OrderedDictSlicer
from foolscap.slicers.root import RootSlicer, RootUnslicer


################## Slicers for "unsafe" things

# Extended types, not generally safe. The UnsafeRootSlicer checks for these
# with a separate table.

def getInstanceState(inst):
    """Utility function to default to 'normal' state rules in serialization.
    """
    if hasattr(inst, "__getstate__"):
        state = inst.__getstate__()
    else:
        state = inst.__dict__
    return state

class InstanceSlicer(OrderedDictSlicer):
    opentype = ('instance',)
    trackReferences = True

    def sliceBody(self, streamable, banana):
        yield reflect.qual(self.obj.__class__) # really a second index token
        self.obj = getInstanceState(self.obj)
        for t in OrderedDictSlicer.sliceBody(self, streamable, banana):
            yield t

class ModuleSlicer(slicer.BaseSlicer):
    opentype = ('module',)
    trackReferences = True

    def sliceBody(self, streamable, banana):
        yield self.obj.__name__

class ClassSlicer(slicer.BaseSlicer):
    opentype = ('class',)
    trackReferences = True

    def sliceBody(self, streamable, banana):
        yield reflect.qual(self.obj)

class MethodSlicer(slicer.BaseSlicer):
    opentype = ('method',)
    trackReferences = True

    def sliceBody(self, streamable, banana):
        yield self.obj.im_func.__name__
        yield self.obj.im_self
        yield self.obj.im_class

class FunctionSlicer(slicer.BaseSlicer):
    opentype = ('function',)
    trackReferences = True

    def sliceBody(self, streamable, banana):
        name = self.obj.__name__
        fullname = str(whichmodule(self.obj, self.obj.__name__)) + '.' + name
        yield fullname

UnsafeSlicerTable = {}
UnsafeSlicerTable.update({
    types.InstanceType: InstanceSlicer,
    types.ModuleType: ModuleSlicer,
    types.ClassType: ClassSlicer,
    types.MethodType: MethodSlicer,
    types.FunctionType: FunctionSlicer,
    #types.TypeType: NewstyleClassSlicer,
    # ???: NewstyleInstanceSlicer,  # pickle uses obj.__reduce__ to help
    # http://docs.python.org/lib/node68.html
    })




class UnsafeRootSlicer(RootSlicer):
    slicerTable = UnsafeSlicerTable

class StorageRootSlicer(UnsafeRootSlicer):
    # some pieces taken from ScopedSlicer
    def __init__(self, protocol):
        UnsafeRootSlicer.__init__(self, protocol)
        self.references = {}

    def registerReference(self, refid, obj):
        self.references[id(obj)] = (obj,refid)

    def slicerForObject(self, obj):
        # check for an object which was sent previously or has at least
        # started sending
        obj_refid = self.references.get(id(obj), None)
        if obj_refid is not None:
            return slicer.ReferenceSlicer(obj_refid[1])
        # otherwise go upstream
        return UnsafeRootSlicer.slicerForObject(self, obj)


################## Unslicers for "unsafe" things

def setInstanceState(inst, state):
    """Utility function to default to 'normal' state rules in unserialization.
    """
    if hasattr(inst, "__setstate__"):
        inst.__setstate__(state)
    else:
        inst.__dict__ = state
    return inst

class Dummy:
    def __repr__(self):
        return "<Dummy %s>" % self.__dict__
    def __cmp__(self, other):
        if not type(other) == type(self):
            return -1
        return cmp(self.__dict__, other.__dict__)

UnsafeUnslicerRegistry = {}

class InstanceUnslicer(slicer.BaseUnslicer):
    # this is an unsafe unslicer: an attacker could induce you to create
    # instances of arbitrary classes with arbitrary attributes: VERY
    # DANGEROUS!
    opentype = ('instance',)
    unslicerRegistry = UnsafeUnslicerRegistry
    
    # danger: instances are mutable containers. If an attribute value is not
    # yet available, __dict__ will hold a Deferred until it is. Other
    # objects might be created and use our object before this is fixed.
    # TODO: address this. Note that InstanceUnslicers aren't used in PB
    # (where we have pb.Referenceable and pb.Copyable which have schema
    # constraints and could have different restrictions like not being
    # allowed to participate in reference loops).

    def start(self, count):
        self.d = {}
        self.count = count
        self.classname = None
        self.attrname = None
        self.deferred = Deferred()
        self.protocol.setObject(count, self.deferred)

    def checkToken(self, typebyte, size):
        if self.classname is None:
            if typebyte not in (tokens.STRING, tokens.VOCAB):
                raise BananaError("InstanceUnslicer classname must be string")
        elif self.attrname is None:
            if typebyte not in (tokens.STRING, tokens.VOCAB):
                raise BananaError("InstanceUnslicer keys must be STRINGs")

    def receiveChild(self, obj, ready_deferred=None):
        assert ready_deferred is None
        if self.classname is None:
            self.classname = obj
            self.attrname = None
        elif self.attrname is None:
            self.attrname = obj
        else:
            if isinstance(obj, Deferred):
                # TODO: this is an artificial restriction, and it might
                # be possible to remove it, but I need to think through
                # it carefully first
                raise BananaError("unreferenceable object in attribute")
            if self.d.has_key(self.attrname):
                raise BananaError("duplicate attribute name '%s'" %
                                  self.attrname)
            self.setAttribute(self.attrname, obj)
            self.attrname = None

    def setAttribute(self, name, value):
        self.d[name] = value

    def receiveClose(self):
        # you could attempt to do some value-checking here, but there would
        # probably still be holes

        #obj = Dummy()
        klass = reflect.namedObject(self.classname)
        assert type(klass) == types.ClassType # TODO: new-style classes
        obj = instance(klass, {})

        setInstanceState(obj, self.d)

        self.protocol.setObject(self.count, obj)
        self.deferred.callback(obj)
        return obj, None

    def describe(self):
        if self.classname is None:
            return "<??>"
        me = "<%s>" % self.classname
        if self.attrname is None:
            return "%s.attrname??" % me
        else:
            return "%s.%s" % (me, self.attrname)

class ModuleUnslicer(slicer.LeafUnslicer):
    opentype = ('module',)
    unslicerRegistry = UnsafeUnslicerRegistry

    finished = False

    def checkToken(self, typebyte, size):
        if typebyte not in (tokens.STRING, tokens.VOCAB):
            raise BananaError("ModuleUnslicer only accepts strings")

    def receiveChild(self, obj, ready_deferred=None):
        assert not isinstance(obj, Deferred)
        assert ready_deferred is None
        if self.finished:
            raise BananaError("ModuleUnslicer only accepts one string")
        self.finished = True
        # TODO: taste here!
        mod = __import__(obj, {}, {}, "x")
        self.mod = mod

    def receiveClose(self):
        if not self.finished:
            raise BananaError("ModuleUnslicer requires a string")
        return self.mod, None

class ClassUnslicer(slicer.LeafUnslicer):
    opentype = ('class',)
    unslicerRegistry = UnsafeUnslicerRegistry

    finished = False

    def checkToken(self, typebyte, size):
        if typebyte not in (tokens.STRING, tokens.VOCAB):
            raise BananaError("ClassUnslicer only accepts strings")

    def receiveChild(self, obj, ready_deferred=None):
        assert not isinstance(obj, Deferred)
        assert ready_deferred is None
        if self.finished:
            raise BananaError("ClassUnslicer only accepts one string")
        self.finished = True
        # TODO: taste here!
        self.klass = reflect.namedObject(obj)

    def receiveClose(self):
        if not self.finished:
            raise BananaError("ClassUnslicer requires a string")
        return self.klass, None

class MethodUnslicer(slicer.BaseUnslicer):
    opentype = ('method',)
    unslicerRegistry = UnsafeUnslicerRegistry

    state = 0
    im_func = None
    im_self = None
    im_class = None

    # self.state:
    # 0: expecting a string with the method name
    # 1: expecting an instance (or None for unbound methods)
    # 2: expecting a class

    def checkToken(self, typebyte, size):
        if self.state == 0:
            if typebyte not in (tokens.STRING, tokens.VOCAB):
                raise BananaError("MethodUnslicer methodname must be a string")
        elif self.state == 1:
            if typebyte != tokens.OPEN:
                raise BananaError("MethodUnslicer instance must be OPEN")
        elif self.state == 2:
            if typebyte != tokens.OPEN:
                raise BananaError("MethodUnslicer class must be an OPEN")

    def doOpen(self, opentype):
        # check the opentype
        if self.state == 1:
            if opentype[0] not in ("instance", "none"):
                raise BananaError("MethodUnslicer instance must be " +
                                  "instance or None")
        elif self.state == 2:
            if opentype[0] != "class":
                raise BananaError("MethodUnslicer class must be a class")
        unslicer = self.open(opentype)
        # TODO: apply constraint
        return unslicer

    def receiveChild(self, obj, ready_deferred=None):
        assert not isinstance(obj, Deferred)
        assert ready_deferred is None
        if self.state == 0:
            self.im_func = obj
            self.state = 1
        elif self.state == 1:
            assert type(obj) in (types.InstanceType, types.NoneType)
            self.im_self = obj
            self.state = 2
        elif self.state == 2:
            assert type(obj) == types.ClassType # TODO: new-style classes?
            self.im_class = obj
            self.state = 3
        else:
            raise BananaError("MethodUnslicer only accepts three objects")

    def receiveClose(self):
        if self.state != 3:
            raise BananaError("MethodUnslicer requires three objects")
        if self.im_self is None:
            meth = getattr(self.im_class, self.im_func)
            # getattr gives us an unbound method
            return meth, None
        # TODO: late-available instances
        #if isinstance(self.im_self, NotKnown):
        #    im = _InstanceMethod(self.im_name, self.im_self, self.im_class)
        #    return im
        meth = self.im_class.__dict__[self.im_func]
        # whereas __dict__ gives us a function
        im = instancemethod(meth, self.im_self, self.im_class)
        return im, None


class FunctionUnslicer(slicer.LeafUnslicer):
    opentype = ('function',)
    unslicerRegistry = UnsafeUnslicerRegistry

    finished = False

    def checkToken(self, typebyte, size):
        if typebyte not in (tokens.STRING, tokens.VOCAB):
            raise BananaError("FunctionUnslicer only accepts strings")

    def receiveChild(self, obj, ready_deferred=None):
        assert not isinstance(obj, Deferred)
        assert ready_deferred is None
        if self.finished:
            raise BananaError("FunctionUnslicer only accepts one string")
        self.finished = True
        # TODO: taste here!
        self.func = reflect.namedObject(obj)

    def receiveClose(self):
        if not self.finished:
            raise BananaError("FunctionUnslicer requires a string")
        return self.func, None


class UnsafeRootUnslicer(RootUnslicer):
    topRegistries = [slicer.UnslicerRegistry,
                     slicer.BananaUnslicerRegistry,
                     UnsafeUnslicerRegistry]
    openRegistries = [slicer.UnslicerRegistry,
                      UnsafeUnslicerRegistry]

class StorageRootUnslicer(UnsafeRootUnslicer, slicer.ScopedUnslicer):
    # This version tracks references for the entire lifetime of the
    # protocol. It is most appropriate for single-use purposes, such as a
    # replacement for Pickle.

    def __init__(self):
        slicer.ScopedUnslicer.__init__(self)
        UnsafeRootUnslicer.__init__(self)

    def setObject(self, counter, obj):
        return slicer.ScopedUnslicer.setObject(self, counter, obj)
    def getObject(self, counter):
        return slicer.ScopedUnslicer.getObject(self, counter)


################## The unsafe form of Banana that uses these (Un)Slicers


class StorageBanana(banana.Banana):
    # this is "unsafe", in that it will do import() and create instances of
    # arbitrary classes. It is also scoped at the root, so each
    # StorageBanana should be used only once.
    slicerClass = StorageRootSlicer
    unslicerClass = StorageRootUnslicer

    # it also stashes top-level objects in .obj, so you can retrieve them
    # later
    def receivedObject(self, obj):
        self.object = obj

def serialize(obj):
    """Serialize an object graph into a sequence of bytes. Returns a Deferred
    that fires with the sequence of bytes."""
    b = StorageBanana()
    b.transport = StringIO()
    d = b.send(obj)
    d.addCallback(lambda res: b.transport.getvalue())
    return d

def unserialize(str):
    """Unserialize a sequence of bytes back into an object graph."""
    b = StorageBanana()
    b.dataReceived(str)
    return b.object

