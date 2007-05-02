
import types, inspect
from zope.interface import interface, providedBy, implements
from foolscap.constraint import Constraint, OpenerConstraint, nothingTaster, \
     IConstraint, UnboundedSchema, IRemoteMethodConstraint, Optional, Any
from foolscap.tokens import Violation, InvalidRemoteInterface
from foolscap.schema import addToConstraintTypeMap
from foolscap import ipb

class RemoteInterfaceClass(interface.InterfaceClass):
    """This metaclass lets RemoteInterfaces be a lot like Interfaces. The
    methods are parsed differently (PB needs more information from them than
    z.i extracts, and the methods can be specified with a RemoteMethodSchema
    directly).

    RemoteInterfaces can accept the following additional attribute::

     __remote_name__: can be set to a string to specify the globally-unique
                      name for this interface. This should be a URL in a
                      namespace you administer. If not set, defaults to the
                      short classname.

    RIFoo.names() returns the list of remote method names.

    RIFoo['bar'] is still used to get information about method 'bar', however
    it returns a RemoteMethodSchema instead of a z.i Method instance.
    
    """

    def __init__(self, iname, bases=(), attrs=None, __module__=None):
        if attrs is None:
            interface.InterfaceClass.__init__(self, iname, bases, attrs,
                                              __module__)
            return

        # parse (and remove) the attributes that make this a RemoteInterface
        try:
            rname, remote_attrs = self._parseRemoteInterface(iname, attrs)
        except:
            raise

        # now let the normal InterfaceClass do its thing
        interface.InterfaceClass.__init__(self, iname, bases, attrs,
                                          __module__)

        # now add all the remote methods that InterfaceClass would have
        # complained about. This is really gross, and it really makes me
        # question why we're bothing to inherit from z.i.Interface at all. I
        # will probably stop doing that soon, and just have our own
        # meta-class, but I want to make sure you can still do
        # 'implements(RIFoo)' from within a class definition.

        a = getattr(self, "_InterfaceClass__attrs") # the ickiest part
        a.update(remote_attrs)
        self.__remote_name__ = rname

        # finally, auto-register the interface
        try:
            registerRemoteInterface(self, rname)
        except:
            raise

    def _parseRemoteInterface(self, iname, attrs):
        remote_attrs = {}

        remote_name = attrs.get("__remote_name__", iname)

        # and see if there is a __remote_name__ . We delete it because
        # InterfaceClass doesn't like arbitrary attributes
        if attrs.has_key("__remote_name__"):
            del attrs["__remote_name__"]

        # determine all remotely-callable methods
        names = [name for name in attrs.keys()
                 if ((type(attrs[name]) == types.FunctionType and
                      not name.startswith("_")) or
                     IConstraint.providedBy(attrs[name]))]

        # turn them into constraints. Tag each of them with their name and
        # the RemoteInterface they came from.
        for name in names:
            m = attrs[name]
            if not IConstraint.providedBy(m):
                m = RemoteMethodSchema(method=m)
            m.name = name
            m.interface = self
            remote_attrs[name] = m
            # delete the methods, so zope's InterfaceClass doesn't see them.
            # Particularly necessary for things defined with IConstraints.
            del attrs[name]

        return remote_name, remote_attrs

RemoteInterface = RemoteInterfaceClass("RemoteInterface",
                                       __module__="pb.flavors")



def getRemoteInterface(obj):
    """Get the (one) RemoteInterface supported by the object, or None."""
    interfaces = list(providedBy(obj))
    # TODO: versioned Interfaces!
    ilist = []
    for i in interfaces:
        if isinstance(i, RemoteInterfaceClass):
            if i not in ilist:
                ilist.append(i)
    assert len(ilist) <= 1, "don't use multiple RemoteInterfaces! %s" % (obj,)
    if ilist:
        return ilist[0]
    return None

class DuplicateRemoteInterfaceError(Exception):
    pass

RemoteInterfaceRegistry = {}
def registerRemoteInterface(iface, name=None):
    if not name:
        name = iface.__remote_name__
    assert isinstance(iface, RemoteInterfaceClass)
    if RemoteInterfaceRegistry.has_key(name):
        old = RemoteInterfaceRegistry[name]
        msg = "remote interface %s was registered with the same name (%s) as %s, please use __remote_name__ to provide a unique name" % (old, name, iface)
        raise DuplicateRemoteInterfaceError(msg)
    RemoteInterfaceRegistry[name] = iface

def getRemoteInterfaceByName(iname):
    return RemoteInterfaceRegistry.get(iname)



class RemoteMethodSchema:
    """
    This is a constraint for a single remotely-invokable method. It gets to
    require, deny, or impose further constraints upon a set of named
    arguments.

    This constraint is created by using keyword arguments with the same
    names as the target method's arguments. Two special names are used:

    __ignoreUnknown__: if True, unexpected argument names are silently
    dropped. (note that this makes the schema unbounded)

    __acceptUnknown__: if True, unexpected argument names are always
    accepted without a constraint (which also makes this schema unbounded)

    The remotely-accesible object's .getMethodSchema() method may return one
    of these objects.
    """

    implements(IRemoteMethodConstraint)

    taster = {} # this should not be used as a top-level constraint
    opentypes = [] # overkill
    ignoreUnknown = False
    acceptUnknown = False

    name = None # method name, set when the RemoteInterface is parsed
    interface = None # points to the RemoteInterface which defines the method

    # under development
    def __init__(self, method=None, _response=None, __options=[], **kwargs):
        if method:
            self.initFromMethod(method)
            return
        self.argumentNames = []
        self.argConstraints = {}
        self.required = []
        self.responseConstraint = None
        # __response in the argslist gets treated specially, I think it is
        # mangled into _RemoteMethodSchema__response or something. When I
        # change it to use _response instead, it works.
        if _response:
            self.responseConstraint = IConstraint(_response)
        self.options = {} # return, wait, reliable, etc

        if kwargs.has_key("__ignoreUnknown__"):
            self.ignoreUnknown = kwargs["__ignoreUnknown__"]
            del kwargs["__ignoreUnknown__"]
        if kwargs.has_key("__acceptUnknown__"):
            self.acceptUnknown = kwargs["__acceptUnknown__"]
            del kwargs["__acceptUnknown__"]

        for argname, constraint in kwargs.items():
            self.argumentNames.append(argname)
            constraint = IConstraint(constraint)
            self.argConstraints[argname] = constraint
            if not isinstance(constraint, Optional):
                self.required.append(argname)

    def initFromMethod(self, method):
        # call this with the Interface's prototype method: the one that has
        # argument constraints expressed as default arguments, and which
        # does nothing but returns the appropriate return type

        names, _, _, typeList = inspect.getargspec(method)
        if names and names[0] == 'self':
            why = "RemoteInterface methods should not have 'self' in their argument list"
            raise InvalidRemoteInterface(why)
        if not names:
            typeList = []
        if len(names) != len(typeList):
            # TODO: relax this, use schema=Any for the args that don't have
            # default values. This would make:
            #  def foo(a, b=int): return None
            # equivalent to:
            #  def foo(a=Any, b=int): return None
            why = "RemoteInterface methods must have default values for all their arguments"
            raise InvalidRemoteInterface(why)
        self.argumentNames = names
        self.argConstraints = {}
        self.required = []
        for i in range(len(names)):
            argname = names[i]
            constraint = typeList[i]
            if not isinstance(constraint, Optional):
                self.required.append(argname)
            self.argConstraints[argname] = IConstraint(constraint)

        # call the method, its 'return' value is the return constraint
        self.responseConstraint = IConstraint(method())
        self.options = {} # return, wait, reliable, etc


    def getPositionalArgConstraint(self, argnum):
        if argnum >= len(self.argumentNames):
            raise Violation("too many positional arguments: %d >= %d" %
                            (argnum, len(self.argumentNames)))
        argname = self.argumentNames[argnum]
        c = self.argConstraints.get(argname)
        assert c
        if isinstance(c, Optional):
            c = c.constraint
        return (True, c)

    def getKeywordArgConstraint(self, argname,
                                num_posargs=0, previous_kwargs=[]):
        previous_args = self.argumentNames[:num_posargs]
        for pkw in previous_kwargs:
            assert pkw not in previous_args
            previous_args.append(pkw)
        if argname in previous_args:
            raise Violation("got multiple values for keyword argument '%s'"
                            % (argname,))
        c = self.argConstraints.get(argname)
        if c:
            if isinstance(c, Optional):
                c = c.constraint
            return (True, c)
        # what do we do with unknown arguments?
        if self.ignoreUnknown:
            return (False, None)
        if self.acceptUnknown:
            return (True, None)
        raise Violation("unknown argument '%s'" % argname)

    def getResponseConstraint(self):
        return self.responseConstraint

    def checkAllArgs(self, args, kwargs, inbound):
        # first we map the positional arguments
        allargs = {}
        if len(args) > len(self.argumentNames):
            raise Violation("method takes %d positional arguments (%d given)"
                            % (len(self.argumentNames), len(args)))
        for i,argvalue in enumerate(args):
            allargs[self.argumentNames[i]] = argvalue
        for argname,argvalue in kwargs.items():
            if argname in allargs:
                raise Violation("got multiple values for keyword argument '%s'"
                                % (argname,))
            allargs[argname] = argvalue

        for argname, argvalue in allargs.items():
            accept, constraint = self.getKeywordArgConstraint(argname)
            if not accept:
                # this argument will be ignored by the far end. TODO: emit a
                # warning
                pass
            try:
                constraint.checkObject(argvalue, inbound)
            except Violation, v:
                v.setLocation("%s=" % argname)
                raise

        for argname in self.required:
            if argname not in allargs:
                raise Violation("missing required argument '%s'" % argname)

    def checkResults(self, results, inbound):
        if self.responseConstraint:
            # this might raise a Violation. The caller will annotate its
            # location appropriately: they have more information than we do.
            self.responseConstraint.checkObject(results, inbound)

    def maxSize(self, seen=None):
        if self.acceptUnknown:
            raise UnboundedSchema # there is no limit on that thing
        if self.ignoreUnknown:
            # for now, we ignore unknown arguments by accepting the object
            # and then throwing it away. This makes us vulnerable to the
            # memory consumed by that object. TODO: in the CallUnslicer,
            # arrange to discard the ignored object instead of receiving it.
            # When this is done, ignoreUnknown will not cause the schema to
            # be unbounded and this clause should be removed.
            raise UnboundedSchema
        # TODO: implement the rest of maxSize, just like a dictionary
        raise NotImplementedError

class UnconstrainedMethod:
    """I am a method constraint that accepts any arguments and any return
    value.

    To use this, assign it to a method name in a RemoteInterface::

     class RIFoo(RemoteInterface):
         def constrained_method(foo=int, bar=str): # this one is constrained
             return str
         not_method = UnconstrainedMethod()  # this one is not
    """
    implements(IRemoteMethodConstraint)

    def getPositionalArgConstraint(self, argnum):
        return (True, Any())
    def getKeywordArgConstraint(self, argname, num_posargs=0,
                                previous_kwargs=[]):
        return (True, Any())
    def checkAllArgs(self, args, kwargs, inbound):
        pass # accept everything
    def getResponseConstraint(self):
        return Any()
    def checkResults(self, results, inbound):
        pass # accept everything


class LocalInterfaceConstraint(Constraint):
    """This constraint accepts any (local) instance which implements the
    given local Interface.
    """

    # TODO: maybe accept RemoteCopy instances
    # TODO: accept inbound your-references, if the local object they map to
    #       implements the interface

    # TODO: do we need an string-to-Interface map just like we have a
    # classname-to-class/factory map?
    taster = nothingTaster
    opentypes = []
    name = "LocalInterfaceConstraint"

    def __init__(self, interface):
        self.interface = interface
    def checkObject(self, obj, inbound):
        # TODO: maybe try to get an adapter instead?
        if not self.interface.providedBy(obj):
            raise Violation("'%s' does not provide interface %s"
                            % (obj, self.interface))

class RemoteInterfaceConstraint(OpenerConstraint):
    """This constraint accepts any RemoteReference that claims to be
    associated with a remote Referenceable that implements the given
    RemoteInterface. If 'interface' is None, just assert that it is a
    RemoteReference at all.
    """
    opentypes = [("my-reference",)]
    # TODO: accept their-references too
    name = "RemoteInterfaceConstraint"

    def __init__(self, interface):
        self.interface = interface
    def checkObject(self, obj, inbound):
        if inbound:
            # this ought to be a RemoteReference that claims to be associated
            # with a remote Referenceable that implements the desired
            # interface.
            if not ipb.IRemoteReference.providedBy(obj):
                raise Violation("'%s' does not provide RemoteInterface %s, "
                                "and doesn't even look like a RemoteReference"
                                % (obj, self.interface))
            if not self.interface:
                return
            iface = obj.tracker.interface
            # TODO: this test probably doesn't handle subclasses of
            # RemoteInterface, which might be useful (if it even works)
            if not iface or iface != self.interface:
                raise Violation("'%s' does not provide RemoteInterface %s"
                                % (obj, self.interface))
        else:
            # this ought to be a Referenceable which implements the desired
            # interface
            if not ipb.IReferenceable.providedBy(obj):
                # TODO: maybe distinguish between OnlyReferenceable and
                # Referenceable? which is more useful here?
                raise Violation("'%s' is not a Referenceable" % (obj,))
            if self.interface and not self.interface.providedBy(obj):
                raise Violation("'%s' does not provide RemoteInterface %s"
                                % (obj, self.interface))

def _makeConstraint(t):
    # This will be called for both local interfaces (IFoo) and remote
    # interfaces (RIFoo), so we have to distinguish between them. The late
    # import is to deal with a circular reference between this module and
    # remoteinterface.py
    if isinstance(t, RemoteInterfaceClass):
        return RemoteInterfaceConstraint(t)
    return LocalInterfaceConstraint(t)

addToConstraintTypeMap(interface.InterfaceClass, _makeConstraint)
