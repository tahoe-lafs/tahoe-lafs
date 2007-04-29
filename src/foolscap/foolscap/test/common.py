# -*- test-case-name: foolscap.test.test_pb -*-

import re
from zope.interface import implements, implementsOnly, implementedBy, Interface
from twisted.python import log
from twisted.internet import defer, reactor
from foolscap import broker
from foolscap import Referenceable, RemoteInterface
from foolscap.eventual import eventually, fireEventually, flushEventualQueue
from foolscap.remoteinterface import getRemoteInterface, RemoteMethodSchema, \
     UnconstrainedMethod
from foolscap.schema import Any, SetOf, DictOf, ListOf, TupleOf, \
     NumberConstraint, StringConstraint, IntegerConstraint

from twisted.python import failure
from twisted.internet.main import CONNECTION_DONE

def getRemoteInterfaceName(obj):
    i = getRemoteInterface(obj)
    return i.__remote_name__

class Loopback:
    # The transport's promise is that write() can be treated as a
    # synchronous, isolated function call: specifically, the Protocol's
    # dataReceived() and connectionLost() methods shall not be called during
    # a call to write().

    connected = True
    def write(self, data):
        eventually(self._write, data)

    def _write(self, data):
        if not self.connected:
            return
        try:
            # isolate exceptions: if one occurred on a regular TCP transport,
            # they would hang up, so duplicate that here.
            self.peer.dataReceived(data)
        except:
            f = failure.Failure()
            log.err(f)
            print "Loopback.write exception:", f
            self.loseConnection(f)

    def loseConnection(self, why=failure.Failure(CONNECTION_DONE)):
        if self.connected:
            self.connected = False
            # this one is slightly weird because 'why' is a Failure
            eventually(self._loseConnection, why)

    def _loseConnection(self, why):
        self.protocol.connectionLost(why)
        self.peer.connectionLost(why)

    def flush(self):
        self.connected = False
        return fireEventually()

Digits = re.compile("\d*")
MegaSchema1 = DictOf(str,
                     ListOf(TupleOf(SetOf(int, maxLength=10, mutable=True),
                                    str, bool, int, long, float, None,
                                    Any(), NumberConstraint(),
                                    IntegerConstraint(),
                                    StringConstraint(maxLength=100,
                                                     minLength=90,
                                                     regexp="\w+"),
                                    StringConstraint(regexp=Digits),
                                    ),
                            maxLength=20),
                     maxKeys=5)
# containers should convert their arguments into schemas
MegaSchema2 = TupleOf(SetOf(int),
                      ListOf(int),
                      DictOf(int, str),
                      )


class RIHelper(RemoteInterface):
    def set(obj=Any()): return bool
    def set2(obj1=Any(), obj2=Any()): return bool
    def append(obj=Any()): return Any()
    def get(): return Any()
    def echo(obj=Any()): return Any()
    def defer(obj=Any()): return Any()
    def hang(): return Any()
    # test one of everything
    def megaschema(obj1=MegaSchema1, obj2=MegaSchema2): return None

class HelperTarget(Referenceable):
    implements(RIHelper)
    d = None
    def __init__(self, name="unnamed"):
        self.name = name
    def __repr__(self):
        return "<HelperTarget %s>" % self.name
    def waitfor(self):
        self.d = defer.Deferred()
        return self.d

    def remote_set(self, obj):
        self.obj = obj
        if self.d:
            self.d.callback(obj)
        return True
    def remote_set2(self, obj1, obj2):
        self.obj1 = obj1
        self.obj2 = obj2
        return True

    def remote_append(self, obj):
        self.calls.append(obj)

    def remote_get(self):
        return self.obj

    def remote_echo(self, obj):
        self.obj = obj
        return obj

    def remote_defer(self, obj):
        return fireEventually(obj)

    def remote_hang(self):
        self.d = defer.Deferred()
        return self.d

    def remote_megaschema(self, obj1, obj2):
        self.obj1 = obj1
        self.obj2 = obj2
        return None


class TargetMixin:

    def setUp(self):
        self.loopbacks = []

    def setupBrokers(self):

        self.targetBroker = broker.LoggingBroker()
        self.callingBroker = broker.LoggingBroker()

        t1 = Loopback()
        t1.peer = self.callingBroker
        t1.protocol = self.targetBroker
        self.targetBroker.transport = t1
        self.loopbacks.append(t1)

        t2 = Loopback()
        t2.peer = self.targetBroker
        t2.protocol = self.callingBroker
        self.callingBroker.transport = t2
        self.loopbacks.append(t2)

        self.targetBroker.connectionMade()
        self.callingBroker.connectionMade()

    def tearDown(self):
        # returns a Deferred which fires when the Loopbacks are drained
        dl = [l.flush() for l in self.loopbacks]
        d = defer.DeferredList(dl)
        d.addCallback(flushEventualQueue)
        return d

    def setupTarget(self, target, txInterfaces=False):
        # txInterfaces controls what interfaces the sender uses
        #  False: sender doesn't know about any interfaces
        #  True: sender gets the actual interface list from the target
        #  (list): sender uses an artificial interface list
        puid = target.processUniqueID()
        tracker = self.targetBroker.getTrackerForMyReference(puid, target)
        tracker.send()
        clid = tracker.clid
        if txInterfaces:
            iname = getRemoteInterfaceName(target)
        else:
            iname = None
        rtracker = self.callingBroker.getTrackerForYourReference(clid, iname)
        rr = rtracker.getRef()
        return rr, target

    def stall(self, res, timeout):
        d = defer.Deferred()
        reactor.callLater(timeout, d.callback, res)
        return d

    def poll(self, check_f, pollinterval=0.01):
        # Return a Deferred, then call check_f periodically until it returns
        # True, at which point the Deferred will fire.. If check_f raises an
        # exception, the Deferred will errback.
        d = defer.maybeDeferred(self._poll, None, check_f, pollinterval)
        return d

    def _poll(self, res, check_f, pollinterval):
        if check_f():
            return True
        d = defer.Deferred()
        d.addCallback(self._poll, check_f, pollinterval)
        reactor.callLater(pollinterval, d.callback, None)
        return d



class RIMyTarget(RemoteInterface):
    # method constraints can be declared directly:
    add1 = RemoteMethodSchema(_response=int, a=int, b=int)
    free = UnconstrainedMethod()

    # or through their function definitions:
    def add(a=int, b=int): return int
    #add = schema.callable(add) # the metaclass makes this unnecessary
    # but it could be used for adding options or something
    def join(a=str, b=str, c=int): return str
    def getName(): return str
    disputed = RemoteMethodSchema(_response=int, a=int)
    def fail(): return str  # actually raises an exception

class RIMyTarget2(RemoteInterface):
    __remote_name__ = "RIMyTargetInterface2"
    sub = RemoteMethodSchema(_response=int, a=int, b=int)

# For some tests, we want the two sides of the connection to disagree about
# the contents of the RemoteInterface they are using. This is remarkably
# difficult to accomplish within a single process. We do it by creating
# something that behaves just barely enough like a RemoteInterface to work.
class FakeTarget(dict):
    pass
RIMyTarget3 = FakeTarget()
RIMyTarget3.__remote_name__ = RIMyTarget.__remote_name__

RIMyTarget3['disputed'] = RemoteMethodSchema(_response=int, a=str)
RIMyTarget3['disputed'].name = "disputed"
RIMyTarget3['disputed'].interface = RIMyTarget3

RIMyTarget3['disputed2'] = RemoteMethodSchema(_response=str, a=int)
RIMyTarget3['disputed2'].name = "disputed"
RIMyTarget3['disputed2'].interface = RIMyTarget3

RIMyTarget3['sub'] = RemoteMethodSchema(_response=int, a=int, b=int)
RIMyTarget3['sub'].name = "sub"
RIMyTarget3['sub'].interface = RIMyTarget3

class Target(Referenceable):
    implements(RIMyTarget)

    def __init__(self, name=None):
        self.calls = []
        self.name = name
    def getMethodSchema(self, methodname):
        return None
    def remote_add(self, a, b):
        self.calls.append((a,b))
        return a+b
    remote_add1 = remote_add
    def remote_free(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "bird"
    def remote_getName(self):
        return self.name
    def remote_disputed(self, a):
        return 24
    def remote_fail(self):
        raise ValueError("you asked me to fail")

class TargetWithoutInterfaces(Target):
    # undeclare the RIMyTarget interface
    implementsOnly(implementedBy(Referenceable))

class BrokenTarget(Referenceable):
    implements(RIMyTarget)

    def remote_add(self, a, b):
        return "error"


class IFoo(Interface):
    # non-remote Interface
    pass

class Foo(Referenceable):
    implements(IFoo)

class RIDummy(RemoteInterface):
    pass

class RITypes(RemoteInterface):
    def returns_none(work=bool): return None
    def takes_remoteinterface(a=RIDummy): return str
    def returns_remoteinterface(work=int): return RIDummy
    def takes_interface(a=IFoo): return str
    def returns_interface(work=bool): return IFoo

class DummyTarget(Referenceable):
    implements(RIDummy)

class TypesTarget(Referenceable):
    implements(RITypes)

    def remote_returns_none(self, work):
        if work:
            return None
        return "not None"

    def remote_takes_remoteinterface(self, a):
        # TODO: really, I want to just be able to say:
        #   if RIDummy.providedBy(a):
        iface = a.tracker.interface
        if iface and iface == RIDummy:
            return "good"
        raise RuntimeError("my argument (%s) should provide RIDummy, "
                           "but doesn't" % a)

    def remote_returns_remoteinterface(self, work):
        if work == 1:
            return DummyTarget()
        if work == -1:
            return TypesTarget()
        return 15

    def remote_takes_interface(self, a):
        if IFoo.providedBy(a):
            return "good"
        raise RuntimeError("my argument (%s) should provide IFoo, but doesn't" % a)

    def remote_returns_interface(self, work):
        if work:
            return Foo()
        return "not implementor of IFoo"
