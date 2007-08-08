

# This module is responsible for the per-connection Broker object

import types
from itertools import count

from zope.interface import implements
from twisted.python import log
from twisted.internet import defer, error
from twisted.internet import interfaces as twinterfaces
from twisted.internet.protocol import connectionDone

from foolscap import banana, tokens, ipb, vocab
from foolscap import call, slicer, referenceable, copyable, remoteinterface
from foolscap.constraint import Any
from foolscap.tokens import Violation, BananaError
from foolscap.ipb import DeadReferenceError
from foolscap.slicers.root import RootSlicer, RootUnslicer
from foolscap.eventual import eventually


PBTopRegistry = {
    ("call",): call.CallUnslicer,
    ("answer",): call.AnswerUnslicer,
    ("error",): call.ErrorUnslicer,
    }

PBOpenRegistry = {
    ('arguments',): call.ArgumentUnslicer,
    ('my-reference',): referenceable.ReferenceUnslicer,
    ('your-reference',): referenceable.YourReferenceUnslicer,
    ('their-reference',): referenceable.TheirReferenceUnslicer,
    # ('copyable', classname) is handled inline, through the CopyableRegistry
    }

class PBRootUnslicer(RootUnslicer):
    # topRegistries defines what objects are allowed at the top-level
    topRegistries = [PBTopRegistry]
    # openRegistries defines what objects are allowed at the second level and
    # below
    openRegistries = [slicer.UnslicerRegistry, PBOpenRegistry]
    logViolations = False

    def checkToken(self, typebyte, size):
        if typebyte != tokens.OPEN:
            raise BananaError("top-level must be OPEN")

    def openerCheckToken(self, typebyte, size, opentype):
        if typebyte == tokens.STRING:
            if len(opentype) == 0:
                if size > self.maxIndexLength:
                    why = "first opentype STRING token is too long, %d>%d" % \
                          (size, self.maxIndexLength)
                    raise Violation(why)
            if opentype == ("copyable",):
                # TODO: this is silly, of course (should pre-compute maxlen)
                maxlen = reduce(max,
                                [len(cname) \
                                 for cname in copyable.CopyableRegistry.keys()]
                                )
                if size > maxlen:
                    why = "copyable-classname token is too long, %d>%d" % \
                          (size, maxlen)
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
        # used for lower-level objects, delegated up from childunslicer.open
        assert len(self.protocol.receiveStack) > 1
        if opentype[0] == 'copyable':
            if len(opentype) > 1:
                classname = opentype[1]
                try:
                    factory = copyable.CopyableRegistry[classname]
                except KeyError:
                    raise Violation("unknown RemoteCopy class '%s'" \
                                    % classname)
                child = factory()
                child.broker = self.broker
                return child
            else:
                return None # still need classname
        for reg in self.openRegistries:
            opener = reg.get(opentype)
            if opener is not None:
                child = opener()
                break
        else:
            raise Violation("unknown OPEN type %s" % (opentype,))
        child.broker = self.broker
        return child

    def doOpen(self, opentype):
        child = RootUnslicer.doOpen(self, opentype)
        if child:
            child.broker = self.broker
        return child

    def reportViolation(self, f):
        if self.logViolations:
            print "hey, something failed:", f
        return None # absorb the failure

    def receiveChild(self, token, ready_deferred):
        if isinstance(token, call.InboundDelivery):
            self.broker.scheduleCall(token, ready_deferred)



class PBRootSlicer(RootSlicer):
    slicerTable = {types.MethodType: referenceable.CallableSlicer,
                   types.FunctionType: referenceable.CallableSlicer,
                   }
    def registerReference(self, refid, obj):
        assert 0

    def slicerForObject(self, obj):
        # zope.interface doesn't do transitive adaptation, which is a shame
        # because we want to let people register ICopyable adapters for
        # third-party code, and there is an ICopyable->ISlicer adapter
        # defined in copyable.py, but z.i won't do the transitive
        #  ThirdPartyClass -> ICopyable -> ISlicer
        # so instead we manually do it here
        s = tokens.ISlicer(obj, None)
        if s:
            return s
        copier = copyable.ICopyable(obj, None)
        if copier:
            s = tokens.ISlicer(copier)
            return s
        return RootSlicer.slicerForObject(self, obj)


class RIBroker(remoteinterface.RemoteInterface):
    def getReferenceByName(name=str):
        """If I have published an object by that name, return a reference to
        it."""
        # return Remote(interface=any)
        return Any()
    def decref(clid=int, count=int):
        """Release some references to my-reference 'clid'. I will return an
        ack when the operation has completed."""
        return None
    def decgift(giftID=int, count=int):
        """Release some reference to a their-reference 'giftID' that was
        sent earlier."""
        return None


class Broker(banana.Banana, referenceable.Referenceable):
    """I manage a connection to a remote Broker.

    @ivar tub: the L{Tub} which contains us
    @ivar yourReferenceByCLID: maps your CLID to a RemoteReferenceData
    #@ivar yourReferenceByName: maps a per-Tub name to a RemoteReferenceData
    @ivar yourReferenceByURL: maps a global URL to a RemoteReferenceData

    """

    implements(RIBroker)
    slicerClass = PBRootSlicer
    unslicerClass = PBRootUnslicer
    unsafeTracebacks = True
    requireSchema = False
    disconnected = False
    factory = None
    tub = None
    remote_broker = None
    startingTLS = False
    startedTLS = False

    def __init__(self, params={},
                 keepaliveTimeout=None, disconnectTimeout=None):
        banana.Banana.__init__(self, params)
        self.keepaliveTimeout = keepaliveTimeout
        self.disconnectTimeout = disconnectTimeout
        self._banana_decision_version = params.get("banana-decision-version")
        vocab_table_index = params.get('initial-vocab-table-index')
        if vocab_table_index:
            table = vocab.INITIAL_VOCAB_TABLES[vocab_table_index]
            self.populateVocabTable(table)
        self.initBroker()

    def initBroker(self):
        self.rootSlicer.broker = self
        self.rootUnslicer.broker = self

        # tracking Referenceables
        # sending side uses these
        self.nextCLID = count(1).next # 0 is for the broker
        self.myReferenceByPUID = {} # maps ref.processUniqueID to a tracker
        self.myReferenceByCLID = {} # maps CLID to a tracker
        # receiving side uses these
        self.yourReferenceByCLID = {}
        self.yourReferenceByURL = {}

        # tracking Gifts
        self.nextGiftID = count().next
        self.myGifts = {} # maps (broker,clid) to (rref, giftID, count)
        self.myGiftsByGiftID = {} # maps giftID to (broker,clid)

        # remote calls
        # sending side uses these
        self.nextReqID = count(1).next # 0 means "we don't want a response"
        self.waitingForAnswers = {} # we wait for the other side to answer
        self.disconnectWatchers = []
        # receiving side uses these
        self.inboundDeliveryQueue = []
        self._call_is_running = False
        self.activeLocalCalls = {} # the other side wants an answer from us

    def setTub(self, tub):
        assert ipb.ITub.providedBy(tub)
        self.tub = tub
        self.unsafeTracebacks = tub.unsafeTracebacks
        if tub.debugBanana:
            self.debugSend = True
            self.debugReceive = True

    def connectionMade(self):
        banana.Banana.connectionMade(self)
        # create the remote_broker object. We don't use the usual
        # reference-counting mechanism here, because this is a synthetic
        # object that lives forever.
        tracker = referenceable.RemoteReferenceTracker(self, 0, None,
                                                       "RIBroker")
        self.remote_broker = referenceable.RemoteReference(tracker)

    # connectionTimedOut is called in response to the Banana layer detecting
    # the lack of connection activity

    def connectionTimedOut(self):
        self.shutdown()

    def shutdown(self):
        self.disconnectWatchers = []
        self.transport.loseConnection()

    def connectionLost(self, why):
        self.disconnected = True
        self.remote_broker = None
        self.abandonAllRequests(why)
        # TODO: why reset all the tables to something useable? There may be
        # outstanding RemoteReferences that point to us, but I don't see why
        # that requires all these empty dictionaries.
        self.myReferenceByPUID = {}
        self.myReferenceByCLID = {}
        self.yourReferenceByCLID = {}
        self.yourReferenceByURL = {}
        self.myGifts = {}
        self.myGiftsByGiftID = {}
        for (cb,args,kwargs) in self.disconnectWatchers:
            eventually(cb, *args, **kwargs)
        self.disconnectWatchers = []
        banana.Banana.connectionLost(self, why)
        if self.tub:
            # TODO: remove the conditional. It is only here to accomodate
            # some tests: test_pb.TestCall.testDisconnect[123]
            self.tub.brokerDetached(self, why)

    def notifyOnDisconnect(self, callback, *args, **kwargs):
        marker = (callback, args, kwargs)
        if self.disconnected:
            eventually(callback, *args, **kwargs)
        else:
            self.disconnectWatchers.append(marker)
        return marker
    def dontNotifyOnDisconnect(self, marker):
        if self.disconnected:
            return
        # be tolerant of attempts to unregister a callback that has already
        # fired. I think it is hard to write safe code without this
        # tolerance.

        # TODO: on the other hand, I'm not sure this is the best policy,
        # since you lose the feedback that tells you about
        # unregistering-the-wrong-thing bugs. We need to look at the way that
        # register/unregister gets used and see if there is a way to retain
        # the typechecking that results from insisting that you can only
        # remove something that was stil in the list.
        if marker in self.disconnectWatchers:
            self.disconnectWatchers.remove(marker)

    # methods to handle RemoteInterfaces
    def getRemoteInterfaceByName(self, name):
        return remoteinterface.RemoteInterfaceRegistry[name]

    # methods to send my Referenceables to the other side

    def getTrackerForMyReference(self, puid, obj):
        tracker = self.myReferenceByPUID.get(puid)
        if not tracker:
            # need to add one
            clid = self.nextCLID()
            tracker = referenceable.ReferenceableTracker(self.tub,
                                                         obj, puid, clid)
            self.myReferenceByPUID[puid] = tracker
            self.myReferenceByCLID[clid] = tracker
        return tracker

    def getTrackerForMyCall(self, puid, obj):
        # just like getTrackerForMyReference, but with a negative clid
        tracker = self.myReferenceByPUID.get(puid)
        if not tracker:
            # need to add one
            clid = self.nextCLID()
            clid = -clid
            tracker = referenceable.ReferenceableTracker(self.tub,
                                                         obj, puid, clid)
            self.myReferenceByPUID[puid] = tracker
            self.myReferenceByCLID[clid] = tracker
        return tracker

    # methods to handle inbound 'my-reference' sequences

    def getTrackerForYourReference(self, clid, interfaceName=None, url=None):
        """The far end holds a Referenceable and has just sent us a reference
        to it (expressed as a small integer). If this is a new reference,
        they will give us an interface name too, and possibly a global URL
        for it. Obtain a RemoteReference object (creating it if necessary) to
        give to the local recipient.
        
        The sender remembers that we hold a reference to their object. When
        our RemoteReference goes away, we send a decref message to them, so
        they can possibly free their object. """

        assert type(interfaceName) is str or interfaceName is None
        if url is not None:
            assert type(url) is str
        tracker = self.yourReferenceByCLID.get(clid)
        if not tracker:
            # TODO: translate interfaceNames to RemoteInterfaces
            if clid >= 0:
                trackerclass = referenceable.RemoteReferenceTracker
            else:
                trackerclass = referenceable.RemoteMethodReferenceTracker
            tracker = trackerclass(self, clid, url, interfaceName)
            self.yourReferenceByCLID[clid] = tracker
            if url:
                self.yourReferenceByURL[url] = tracker
        return tracker
        
    def freeYourReference(self, tracker, count):
        # this is called when the RemoteReference is deleted
        if not self.remote_broker: # tests do not set this up
            self.freeYourReferenceTracker(None, tracker)
            return
        try:
            rb = self.remote_broker
            # TODO: do we want callRemoteOnly here? is there a way we can
            # avoid wanting to know when the decref has completed? Only if we
            # send the interface list and URL on every occurrence of the
            # my-reference sequence. Either A) we use callRemote("decref")
            # and wait until the ack to free the tracker, or B) we use
            # callRemoteOnly("decref") and free the tracker right away. In
            # case B, the far end has no way to know that we've just freed
            # the tracker and will therefore forget about everything they
            # told us (including the interface list), so they cannot
            # accurately do anything special on the "first" send of this
            # reference. Which means that if we do B, we must either send
            # that extra information on every my-reference sequence, or do
            # without it, or make it optional, or retrieve it separately, or
            # something.

            # rb.callRemoteOnly("decref", clid=tracker.clid, count=count)
            # self.freeYourReferenceTracker('bogus', tracker)
            # return

            d = rb.callRemote("decref", clid=tracker.clid, count=count)
            # if the connection was lost before we can get an ack, we're
            # tearing this down anyway
            def _ignore_loss(f):
                f.trap(DeadReferenceError,
                       error.ConnectionLost,
                       error.ConnectionDone)
                return None
            d.addErrback(_ignore_loss)
            # once the ack comes back, or if we know we'll never get one,
            # release the tracker
            d.addCallback(self.freeYourReferenceTracker, tracker)
        except:
            log.msg("failure during freeRemoteReference")
            log.err()

    def freeYourReferenceTracker(self, res, tracker):
        if tracker.received_count != 0:
            return
        if self.yourReferenceByCLID.has_key(tracker.clid):
            del self.yourReferenceByCLID[tracker.clid]
        if tracker.url and self.yourReferenceByURL.has_key(tracker.url):
            del self.yourReferenceByURL[tracker.url]


    # methods to handle inbound 'your-reference' sequences

    def getMyReferenceByCLID(self, clid):
        """clid is the connection-local ID of the Referenceable the other
        end is trying to invoke or point to. If it is a number, they want an
        implicitly-created per-connection object that we sent to them at
        some point in the past. If it is a string, they want an object that
        was registered with our Factory.
        """

        obj = None
        assert isinstance(clid, (int, long))
        if clid == 0:
            return self
        return self.myReferenceByCLID[clid].obj
        # obj = IReferenceable(obj)
        # assert isinstance(obj, pb.Referenceable)
        # obj needs .getMethodSchema, which needs .getArgConstraint

    def remote_decref(self, clid, count):
        # invoked when the other side sends us a decref message
        assert isinstance(clid, (int, long))
        assert clid != 0
        tracker = self.myReferenceByCLID[clid]
        done = tracker.decref(count)
        if done:
            del self.myReferenceByPUID[tracker.puid]
            del self.myReferenceByCLID[clid]

    # methods to send RemoteReference 'gifts' to third-parties

    def makeGift(self, rref):
        # return the giftid
        broker, clid = rref.tracker.broker, rref.tracker.clid
        i = (broker, clid)
        old = self.myGifts.get(i)
        if old:
            rref, giftID, count = old
            self.myGifts[i] = (rref, giftID, count+1)
        else:
            giftID = self.nextGiftID()
            self.myGiftsByGiftID[giftID] = i
            self.myGifts[i] = (rref, giftID, 1)
        return giftID

    def remote_decgift(self, giftID, count):
        broker, clid = self.myGiftsByGiftID[giftID]
        rref, giftID, gift_count = self.myGifts[(broker, clid)]
        gift_count -= count
        if gift_count == 0:
            del self.myGiftsByGiftID[giftID]
            del self.myGifts[(broker, clid)]
        else:
            self.myGifts[(broker, clid)] = (rref, giftID, gift_count)

    # methods to deal with URLs

    def getYourReferenceByName(self, name):
        d = self.remote_broker.callRemote("getReferenceByName", name=name)
        return d

    def remote_getReferenceByName(self, name):
        return self.tub.getReferenceForName(name)

    # remote-method-invocation methods, calling side, invoked by
    # RemoteReference.callRemote and CallSlicer

    def newRequestID(self):
        if self.disconnected:
            raise DeadReferenceError("Calling Stale Broker")
        return self.nextReqID()

    def addRequest(self, req):
        req.broker = self
        self.waitingForAnswers[req.reqID] = req

    def removeRequest(self, req):
        del self.waitingForAnswers[req.reqID]

    def getRequest(self, reqID):
        # invoked by AnswerUnslicer and ErrorUnslicer
        try:
            return self.waitingForAnswers[reqID]
        except KeyError:
            raise Violation("non-existent reqID '%d'" % reqID)

    def abandonAllRequests(self, why):
        for req in self.waitingForAnswers.values():
            req.fail(why)
        self.waitingForAnswers = {}

    # target-side, invoked by CallUnslicer

    def getRemoteInterfaceByName(self, riname):
        # this lives in the broker because it ought to be per-connection
        return remoteinterface.RemoteInterfaceRegistry[riname]

    def getSchemaForMethod(self, rifaces, methodname):
        # this lives in the Broker so it can override the resolution order,
        # not that overlapping RemoteInterfaces should be allowed to happen
        # all that often
        for ri in rifaces:
            m = ri.get(methodname)
            if m:
                return m
        return None

    def scheduleCall(self, delivery, ready_deferred):
        self.inboundDeliveryQueue.append( (delivery,ready_deferred) )
        eventually(self.doNextCall)

    def doNextCall(self):
        if self._call_is_running:
            return
        if not self.inboundDeliveryQueue:
            return
        delivery, ready_deferred = self.inboundDeliveryQueue.pop(0)
        self._call_is_running = True
        if not ready_deferred:
            ready_deferred = defer.succeed(None)
        d = ready_deferred
        d.addCallback(lambda res: self._doCall(delivery))
        d.addCallback(self._callFinished, delivery)
        d.addErrback(self.callFailed, delivery.reqID, delivery)
        def _done(res):
            self._call_is_running = False
            eventually(self.doNextCall)
        d.addBoth(_done)
        return None

    def _doCall(self, delivery):
        obj = delivery.obj
        args = delivery.allargs.args
        kwargs = delivery.allargs.kwargs
        for i in args + kwargs.values():
            assert not isinstance(i, defer.Deferred)

        if delivery.methodSchema:
            # we asked about each argument on the way in, but ask again so
            # they can look for missing arguments. TODO: see if we can remove
            # the redundant per-argument checks.
            delivery.methodSchema.checkAllArgs(args, kwargs, True)

        # interesting case: if the method completes successfully, but
        # our schema prohibits us from sending the result (perhaps the
        # method returned an int but the schema insists upon a string).
        # TODO: move the return-value schema check into
        # Referenceable.doRemoteCall, so the exception's traceback will be
        # attached to the object that caused it
        if delivery.methodname is None:
            assert callable(obj)
            return obj(*args, **kwargs)
        else:
            obj = ipb.IRemotelyCallable(obj)
            return obj.doRemoteCall(delivery.methodname, args, kwargs)


    def _callFinished(self, res, delivery):
        reqID = delivery.reqID
        if reqID == 0:
            return
        methodSchema = delivery.methodSchema
        assert self.activeLocalCalls[reqID]
        if methodSchema:
            try:
                methodSchema.checkResults(res, False) # may raise Violation
            except Violation, v:
                v.prependLocation("in return value of %s.%s" %
                                  (delivery.obj, methodSchema.name))
                raise

        answer = call.AnswerSlicer(reqID, res)
        # once the answer has started transmitting, any exceptions must be
        # logged and dropped, and not turned into an Error to be sent.
        try:
            self.send(answer)
            # TODO: .send should return a Deferred that fires when the last
            # byte has been queued, and we should delete the local note then
        except:
            log.err()
        del self.activeLocalCalls[reqID]

    def callFailed(self, f, reqID, delivery=None):
        # this may be called either when an inbound schema is violated, or
        # when the method is run and raises an exception. If a Violation is
        # raised after we receive the reqID but before we've actually invoked
        # the method, we are called by CallUnslicer.reportViolation and don't
        # get a delivery= argument.
        if delivery:
            if (self.tub and self.tub.logLocalFailures) or not self.tub:
                # the 'not self.tub' case is for unit tests
                delivery.logFailure(f)
        if reqID != 0:
            assert self.activeLocalCalls[reqID]
            self.send(call.ErrorSlicer(reqID, f))
            del self.activeLocalCalls[reqID]

# this loopback stuff is based upon twisted.protocols.loopback, except that
# we use it for real, not just for testing. The IConsumer stuff hasn't been
# tested at all.

class _LoopbackAddress(object):
    implements(twinterfaces.IAddress)

class LoopbackTransport(object):
    # we always create these in pairs, with .peer pointing at each other
    implements(twinterfaces.ITransport, twinterfaces.IConsumer)

    producer = None

    def __init__(self):
        self.connected = True
    def setPeer(self, peer):
        self.peer = peer

    def write(self, bytes):
        eventually(self.peer.dataReceived, bytes)
    def writeSequence(self, iovec):
        self.write(''.join(iovec))

    def dataReceived(self, data):
        if self.connected:
            self.protocol.dataReceived(data)

    def loseConnection(self, _connDone=connectionDone):
        if not self.connected:
            return
        self.connected = False
        eventually(self.peer.connectionLost, _connDone)
        eventually(self.protocol.connectionLost, _connDone)
    def connectionLost(self, reason):
        if not self.connected:
            return
        self.connected = False
        self.protocol.connectionLost(reason)

    def getPeer(self):
        return _LoopbackAddress()
    def getHost(self):
        return _LoopbackAddress()

    # IConsumer
    def registerProducer(self, producer, streaming):
        assert self.producer is None
        self.producer = producer
        self.streamingProducer = streaming
        self._pollProducer()

    def unregisterProducer(self):
        assert self.producer is not None
        self.producer = None

    def _pollProducer(self):
        if self.producer is not None and not self.streamingProducer:
            self.producer.resumeProducing()


import debug
class LoggingBroker(debug.LoggingBananaMixin, Broker):
    pass

