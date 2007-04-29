# -*- test-case-name: foolscap.test.test_banana -*-

import types
from zope.interface import implements
from twisted.internet.defer import Deferred
from foolscap import tokens
from foolscap.tokens import Violation, BananaError
from foolscap.slicer import BaseUnslicer
from foolscap.slicer import UnslicerRegistry, BananaUnslicerRegistry
from foolscap.slicers.vocab import ReplaceVocabularyTable, AddToVocabularyTable

class RootSlicer:
    implements(tokens.ISlicer, tokens.IRootSlicer)

    streamableInGeneral = True
    producingDeferred = None
    objectSentDeferred = None
    slicerTable = {}
    debug = False

    def __init__(self, protocol):
        self.protocol = protocol
        self.sendQueue = []

    def allowStreaming(self, streamable):
        self.streamableInGeneral = streamable

    def registerReference(self, refid, obj):
        pass

    def slicerForObject(self, obj):
        # could use a table here if you think it'd be faster than an
        # adapter lookup
        if self.debug: print "slicerForObject(%s)" % type(obj)
        # do the adapter lookup first, so that registered adapters override
        # UnsafeSlicerTable's InstanceSlicer
        slicer = tokens.ISlicer(obj, None)
        if slicer:
            if self.debug: print "got ISlicer", slicer
            return slicer
        slicerFactory = self.slicerTable.get(type(obj))
        if slicerFactory:
            if self.debug: print " got slicerFactory", slicerFactory
            return slicerFactory(obj)
        if issubclass(type(obj), types.InstanceType):
            name = str(obj.__class__)
        else:
            name = str(type(obj))
        if self.debug: print "cannot serialize %s (%s)" % (obj, name)
        raise Violation("cannot serialize %s (%s)" % (obj, name))

    def slice(self):
        return self
    def __iter__(self):
        return self # we are our own iterator
    def next(self):
        if self.objectSentDeferred:
            self.objectSentDeferred.callback(None)
            self.objectSentDeferred = None
        if self.sendQueue:
            (obj, self.objectSentDeferred) = self.sendQueue.pop()
            self.streamable = self.streamableInGeneral
            return obj
        if self.protocol.debugSend:
            print "LAST BAG"
        self.producingDeferred = Deferred()
        self.streamable = True
        return self.producingDeferred

    def childAborted(self, f):
        assert self.objectSentDeferred
        self.objectSentDeferred.errback(f)
        self.objectSentDeferred = None
        return None

    def send(self, obj):
        # obj can also be a Slicer, say, a CallSlicer. We return a Deferred
        # which fires when the object has been fully serialized.
        idle = (len(self.protocol.slicerStack) == 1) and not self.sendQueue
        objectSentDeferred = Deferred()
        self.sendQueue.append((obj, objectSentDeferred))
        if idle:
            # wake up
            if self.protocol.debugSend:
                print " waking up to send"
            if self.producingDeferred:
                d = self.producingDeferred
                self.producingDeferred = None
                # TODO: consider reactor.callLater(0, d.callback, None)
                # I'm not sure it's actually necessary, though
                d.callback(None)
        return objectSentDeferred

    def describe(self):
        return "<RootSlicer>"

    def connectionLost(self, why):
        # abandon everything we wanted to send
        if self.objectSentDeferred:
            self.objectSentDeferred.errback(why)
            self.objectSentDeferred = None
        for obj, d in self.sendQueue:
            d.errback(why)
        self.sendQueue = []



class RootUnslicer(BaseUnslicer):
    # topRegistries is used for top-level objects
    topRegistries = [UnslicerRegistry, BananaUnslicerRegistry]
    # openRegistries is used for everything at lower levels
    openRegistries = [UnslicerRegistry]
    constraint = None
    openCount = None

    def __init__(self):
        self.objects = {}
        keys = []
        for r in self.topRegistries + self.openRegistries:
            for k in r.keys():
                keys.append(len(k[0]))
        self.maxIndexLength = reduce(max, keys)

    def start(self, count):
        pass

    def setConstraint(self, constraint):
        # this constraints top-level objects. E.g., if this is an
        # IntegerConstraint, then only integers will be accepted.
        self.constraint = constraint

    def checkToken(self, typebyte, size):
        if self.constraint:
            self.constraint.checkToken(typebyte, size)

    def openerCheckToken(self, typebyte, size, opentype):
        if typebyte == tokens.STRING:
            if size > self.maxIndexLength:
                why = "STRING token is too long, %d>%d" % \
                      (size, self.maxIndexLength)
                raise Violation(why)
        elif typebyte == tokens.VOCAB:
            return
        else:
            # TODO: hack for testing
            raise Violation("index token 0x%02x not STRING or VOCAB" % \
                              ord(typebyte))
            raise BananaError("index token 0x%02x not STRING or VOCAB" % \
                              ord(typebyte))

    def open(self, opentype):
        # called (by delegation) by the top Unslicer on the stack, regardless
        # of what kind of unslicer it is. This is only used for "internal"
        # objects: non-top-level nodes
        assert len(self.protocol.receiveStack) > 1
        for reg in self.openRegistries:
            opener = reg.get(opentype)
            if opener is not None:
                child = opener()
                return child
        else:
            raise Violation("unknown OPEN type %s" % (opentype,))

    def doOpen(self, opentype):
        # this is only called for top-level objects
        assert len(self.protocol.receiveStack) == 1
        if self.constraint:
            self.constraint.checkOpentype(opentype)
        for reg in self.topRegistries:
            opener = reg.get(opentype)
            if opener is not None:
                child = opener()
                break
        else:
            raise Violation("unknown top-level OPEN type %s" % (opentype,))

        if self.constraint:
            child.setConstraint(self.constraint)
        return child

    def receiveChild(self, obj, ready_deferred=None):
        assert not isinstance(obj, Deferred)
        assert ready_deferred is None
        if self.protocol.debugReceive:
            print "RootUnslicer.receiveChild(%s)" % (obj,)
        self.objects = {}
        if obj in (ReplaceVocabularyTable, AddToVocabularyTable):
            # the unslicer has already changed the vocab table
            return
        if self.protocol.exploded:
            print "protocol exploded, can't deliver object"
            print self.protocol.exploded
            self.protocol.receivedObject(self.protocol.exploded)
            return
        self.protocol.receivedObject(obj) # give finished object to Banana

    def receiveClose(self):
        raise BananaError("top-level should never receive CLOSE tokens")

    def reportViolation(self, why):
        return self.protocol.reportViolation(why)

    def describe(self):
        return "<RootUnslicer>"

    def setObject(self, counter, obj):
        pass

    def getObject(self, counter):
        return None

