
from twisted.python import failure, log, reflect
from twisted.internet import defer

from foolscap import copyable, slicer, tokens
from foolscap.eventual import eventually
from foolscap.copyable import AttributeDictConstraint
from foolscap.constraint import ByteStringConstraint
from foolscap.slicers.list import ListConstraint
from tokens import BananaError, Violation


class FailureConstraint(AttributeDictConstraint):
    opentypes = [("copyable", "twisted.python.failure.Failure")]
    name = "FailureConstraint"
    klass = failure.Failure

    def __init__(self):
        attrs = [('type', ByteStringConstraint(200)),
                 ('value', ByteStringConstraint(1000)),
                 ('traceback', ByteStringConstraint(2000)),
                 ('parents', ListConstraint(ByteStringConstraint(200))),
                 ]
        AttributeDictConstraint.__init__(self, *attrs)

    def checkObject(self, obj, inbound):
        if not isinstance(obj, self.klass):
            raise Violation("is not an instance of %s" % self.klass)


class PendingRequest(object):
    # this object is a local representation of a message we have sent to
    # someone else, that will be executed on their end.
    active = True
    methodName = None # for debugging

    def __init__(self, reqID, rref=None):
        self.reqID = reqID
        self.rref = rref # keep it alive
        self.broker = None # if set, the broker knows about us
        self.deferred = defer.Deferred()
        self.constraint = None # this constrains the results

    def setConstraint(self, constraint):
        self.constraint = constraint

    def complete(self, res):
        if self.broker:
            self.broker.removeRequest(self)
        if self.active:
            self.active = False
            self.deferred.callback(res)
        else:
            log.msg("PendingRequest.complete called on an inactive request")

    def fail(self, why):
        if self.active:
            if self.broker:
                self.broker.removeRequest(self)
            self.active = False
            self.failure = why
            if (self.broker and
                self.broker.tub and
                self.broker.tub.logRemoteFailures):
                log.msg("an outbound callRemote (that we sent to someone "
                        "else) failed on the far end")
                log.msg(" reqID=%d, rref=%s, methname=%s"
                        % (self.reqID, self.rref, self.methodName))
                stack = why.getTraceback()
                # TODO: include the first few letters of the remote tubID in
                # this REMOTE tag
                stack = "REMOTE: " + stack.replace("\n", "\nREMOTE: ")
                log.msg(" the failure was:")
                log.msg(stack)
            self.deferred.errback(why)
        else:
            log.msg("multiple failures")
            log.msg("first one was:", self.failure)
            log.msg("this one was:", why)
            log.err("multiple failures indicate a problem")

class ArgumentSlicer(slicer.ScopedSlicer):
    opentype = ('arguments',)

    def __init__(self, args, kwargs):
        slicer.ScopedSlicer.__init__(self, None)
        self.args = args
        self.kwargs = kwargs
        self.which = ""

    def sliceBody(self, streamable, banana):
        yield len(self.args)
        for i,arg in enumerate(self.args):
            self.which = "arg[%d]" % i
            yield arg
        keys = self.kwargs.keys()
        keys.sort()
        for argname in keys:
            self.which = "arg[%s]" % argname
            yield argname
            yield self.kwargs[argname]

    def describe(self):
        return "<%s>" % self.which


class CallSlicer(slicer.ScopedSlicer):
    opentype = ('call',)

    def __init__(self, reqID, clid, methodname, args, kwargs):
        slicer.ScopedSlicer.__init__(self, None)
        self.reqID = reqID
        self.clid = clid
        self.methodname = methodname
        self.args = args
        self.kwargs = kwargs

    def sliceBody(self, streamable, banana):
        yield self.reqID
        yield self.clid
        yield self.methodname
        yield ArgumentSlicer(self.args, self.kwargs)

    def describe(self):
        return "<call-%s-%s-%s>" % (self.reqID, self.clid, self.methodname)

class InboundDelivery:
    """An inbound message that has not yet been delivered.

    This is created when a 'call' sequence has finished being received. The
    Broker will add it to a queue. The delivery at the head of the queue is
    serviced when all of its arguments have been resolved.

    The only way that the arguments might not all be available is if one of
    the Unslicers which created them has provided a 'ready_deferred' along
    with the prospective object. The only standard Unslicer which does this
    is the TheirReferenceUnslicer, which handles introductions. (custom
    Unslicers might also provide a ready_deferred, for example a URL
    slicer/unslicer pair for which the receiving end fetches the target of
    the URL as its value, or a UnixFD slicer/unslicer that had to wait for a
    side-channel unix-domain socket to finish transferring control over the
    FD to the recipient before being ready).

    Most Unslicers refuse to accept unready objects as their children (most
    implementations of receiveChild() do 'assert ready_deferred is None').
    The CallUnslicer is fairly unique in not rejecting such objects.

    We do require, however, that all of the arguments be at least
    referenceable. This is not generally a problem: the only time an
    unslicer's receiveChild() can get a non-referenceable object (represented
    by a Deferred) is if that unslicer is participating in a reference cycle
    that has not yet completed, and CallUnslicers only live at the top level,
    above any cycles.
    """

    def __init__(self, reqID, obj,
                 interface, methodname, methodSchema,
                 allargs):
        self.reqID = reqID
        self.obj = obj
        self.interface = interface
        self.methodname = methodname
        self.methodSchema = methodSchema
        self.allargs = allargs
        if allargs.isReady():
            self.runnable = True
        self.runnable = False

    def isRunnable(self):
        if self.allargs.isReady():
            return True
        return False

    def whenRunnable(self):
        if self.allargs.isReady():
            return defer.succeed(self)
        d = self.allargs.whenReady()
        d.addCallback(lambda res: self)
        return d

    def logFailure(self, f):
        # called if tub.logLocalFailures is True
        log.msg("an inbound callRemote that we executed (on behalf of "
                "someone else) failed")
        log.msg(" reqID=%d, rref=%s, methname=%s" %
                (self.reqID, self.obj, self.methodname))
        log.msg(" args=%s" % (self.allargs.args,))
        log.msg(" kwargs=%s" % (self.allargs.kwargs,))
        if isinstance(f.type, str):
            stack = "getTraceback() not available for string exceptions\n"
        else:
            stack = f.getTraceback()
        # TODO: trim stack to everything below Broker._doCall
        stack = "LOCAL: " + stack.replace("\n", "\nLOCAL: ")
        log.msg(" the failure was:")
        log.msg(stack)

class ArgumentUnslicer(slicer.ScopedUnslicer):
    methodSchema = None

    def setConstraint(self, methodSchema):
        self.methodSchema = methodSchema

    def start(self, count):
        self.numargs = None
        self.args = []
        self.kwargs = {}
        self.argname = None
        self.argConstraint = None
        self.num_unreferenceable_children = 0
        self.num_unready_children = 0
        self.closed = False

    def checkToken(self, typebyte, size):
        if self.numargs is None:
            # waiting for positional-arg count
            if typebyte != tokens.INT:
                raise BananaError("posarg count must be an INT")
            return
        if len(self.args) < self.numargs:
            # waiting for a positional arg
            if self.argConstraint:
                self.argConstraint.checkToken(typebyte, size)
            return
        if self.argname is None:
            # waiting for the name of a keyword arg
            if typebyte not in (tokens.STRING, tokens.VOCAB):
                raise BananaError("kwarg name must be a STRING")
            # TODO: limit to longest argument name of the method?
            return
        # waiting for the value of a kwarg
        if self.argConstraint:
            self.argConstraint.checkToken(typebyte, size)

    def doOpen(self, opentype):
        if self.argConstraint:
            self.argConstraint.checkOpentype(opentype)
        unslicer = self.open(opentype)
        if unslicer:
            if self.argConstraint:
                unslicer.setConstraint(self.argConstraint)
        return unslicer

    def receiveChild(self, token, ready_deferred=None):
        if self.numargs is None:
            # this token is the number of positional arguments
            assert isinstance(token, int)
            assert ready_deferred is None
            self.numargs = token
            if self.numargs:
                ms = self.methodSchema
                if ms:
                    accept, self.argConstraint = \
                            ms.getPositionalArgConstraint(0)
                    assert accept
            return

        if len(self.args) < self.numargs:
            # this token is a positional argument
            argvalue = token
            argpos = len(self.args)
            self.args.append(argvalue)
            if isinstance(argvalue, defer.Deferred):
                self.num_unreferenceable_children += 1
                argvalue.addCallback(self.updateChild, argpos)
                argvalue.addErrback(self.explode)
            if ready_deferred:
                self.num_unready_children += 1
                ready_deferred.addCallback(self.childReady)
            if len(self.args) < self.numargs:
                # more to come
                ms = self.methodSchema
                if ms:
                    nextargnum = len(self.args)
                    accept, self.argConstraint = \
                            ms.getPositionalArgConstraint(nextargnum)
                    assert accept
            return

        if self.argname is None:
            # this token is the name of a keyword argument
            self.argname = token
            # if the argname is invalid, this may raise Violation
            ms = self.methodSchema
            if ms:
                accept, self.argConstraint = \
                        ms.getKeywordArgConstraint(self.argname,
                                                   self.numargs,
                                                   self.kwargs.keys())
                assert accept
            return

        # this token is the value of a keyword argument
        argvalue = token
        self.kwargs[self.argname] = argvalue
        if isinstance(argvalue, defer.Deferred):
            self.num_unreferenceable_children += 1
            argvalue.addCallback(self.updateChild, self.argname)
            argvalue.addErrback(self.explode)
        if ready_deferred:
            self.num_unready_children += 1
            ready_deferred.addCallback(self.childReady)
        self.argname = None
        return
        
    def updateChild(self, obj, which):
        # one of our arguments has just now become referenceable. Normal
        # types can't trigger this (since the arguments to a method form a
        # top-level serialization domain), but special Unslicers might. For
        # example, the Gift unslicer will eventually provide us with a
        # RemoteReference, but for now all we get is a Deferred as a
        # placeholder.

        if isinstance(which, int):
            self.args[which] = obj
        else:
            self.kwargs[which] = obj
        self.num_unreferenceable_children -= 1
        self.checkComplete()
        return obj

    def childReady(self, obj):
        self.num_unready_children -= 1
        self.checkComplete()
        return obj

    def checkComplete(self):
        # this is called each time one of our children gets updated or
        # becomes ready (like when a Gift is finally resolved)
        if not self.closed:
            return
        if self.num_unreferenceable_children:
            return
        if self.num_unready_children:
            return
        # yup, we're done. Notify anyone who is still waiting
        for d in self.watchers:
            eventually(d.callback, self)
        del self.watchers

    def receiveClose(self):
        if (self.numargs is None or
            len(self.args) < self.numargs or
            self.argname is not None):
            raise BananaError("'arguments' sequence ended too early")
        self.closed = True
        self.watchers = []
        return self, None

    def isReady(self):
        assert self.closed
        if self.num_unreferenceable_children:
            return False
        if self.num_unready_children:
            return False
        return True

    def whenReady(self):
        assert self.closed
        if self.isReady():
            return defer.succeed(self)
        d = defer.Deferred()
        self.watchers.append(d)
        return d

    def describe(self):
        s = "<arguments"
        if self.numargs is not None:
            if len(self.args) < self.numargs:
                s += " arg[%d]" % len(self.args)
            else:
                if self.argname is not None:
                    s += " arg[%s]" % self.argname
                else:
                    s += " arg[?]"
        if self.closed:
            if self.isReady():
                # waiting to be delivered
                s += " ready"
            else:
                s += " waiting"
        s += ">"
        return s


class CallUnslicer(slicer.ScopedUnslicer):

    def start(self, count):
        # start=0:reqID, 1:objID, 2:methodname, 3: arguments
        self.stage = 0
        self.reqID = None
        self.obj = None
        self.interface = None
        self.methodname = None
        self.methodSchema = None # will be a MethodArgumentsConstraint

    def checkToken(self, typebyte, size):
        # TODO: limit strings by returning a number instead of None
        if self.stage == 0:
            if typebyte != tokens.INT:
                raise BananaError("request ID must be an INT")
        elif self.stage == 1:
            if typebyte not in (tokens.INT, tokens.NEG):
                raise BananaError("object ID must be an INT/NEG")
        elif self.stage == 2:
            if typebyte not in (tokens.STRING, tokens.VOCAB):
                raise BananaError("method name must be a STRING")
            # TODO: limit to longest method name of self.obj in the interface
        elif self.stage == 3:
            if typebyte != tokens.OPEN:
                raise BananaError("arguments must be an 'arguments' sequence")
        else:
            raise BananaError("too many objects given to CallUnslicer")

    def doOpen(self, opentype):
        # checkToken insures that this can only happen when we're receiving
        # an arguments object, so we don't have to bother checking self.stage
        assert self.stage == 3
        unslicer = self.open(opentype)
        if self.methodSchema:
            unslicer.setConstraint(self.methodSchema)
        return unslicer

    def reportViolation(self, f):
        # if the Violation is because we received an ABORT, then we know
        # that the sender knows there was a problem, so don't respond.
        if f.value.args[0] == "ABORT received":
            return f

        # if the Violation was raised after we know the reqID, we can send
        # back an Error.
        if self.stage > 0:
            self.broker.callFailed(f, self.reqID)
        return f # give up our sequence

    def receiveChild(self, token, ready_deferred=None):
        assert not isinstance(token, defer.Deferred)
        assert ready_deferred is None
        #print "CallUnslicer.receiveChild [s%d]" % self.stage, repr(token)

        if self.stage == 0: # reqID
            # we don't yet know which reqID to send any failure to
            self.reqID = token
            self.stage = 1
            if self.reqID != 0:
                assert self.reqID not in self.broker.activeLocalCalls
                self.broker.activeLocalCalls[self.reqID] = self
            return

        if self.stage == 1: # objID
            # this might raise an exception if objID is invalid
            self.objID = token
            self.obj = self.broker.getMyReferenceByCLID(token)
            #iface = self.broker.getRemoteInterfaceByName(token)
            if self.objID < 0:
                self.interface = None
            else:
                self.interface = self.obj.getInterface()
            self.stage = 2
            return

        if self.stage == 2: # methodname
            # validate the methodname, get the schema. This may raise an
            # exception for unknown methods

            # must find the schema, using the interfaces
            
            # TODO: getSchema should probably be in an adapter instead of in
            # a pb.Referenceable base class. Old-style (unconstrained)
            # flavors.Referenceable should be adapted to something which
            # always returns None

            # TODO: make this faster. A likely optimization is to take a
            # tuple of components.getInterfaces(obj) and use it as a cache
            # key. It would be even faster to use obj.__class__, but that
            # would probably violate the expectation that instances can
            # define their own __implements__ (independently from their
            # class). If this expectation were to go away, a quick
            # obj.__class__ -> RemoteReferenceSchema cache could be built.

            self.stage = 3

            if self.objID < 0:
                # the target is a bound method, ignore the methodname
                self.methodSchema = getattr(self.obj, "methodSchema", None)
                self.methodname = None # TODO: give it something useful
                if self.broker.requireSchema and not self.methodSchema:
                    why = "This broker does not accept unconstrained " + \
                          "method calls"
                    raise Violation(why)
                return

            self.methodname = token

            if self.interface:
                # they are calling an interface+method pair
                ms = self.interface.get(self.methodname)
                if not ms:
                    why = "method '%s' not defined in %s" % \
                          (self.methodname, self.interface.__remote_name__)
                    raise Violation(why)
                self.methodSchema = ms

            return

        if self.stage == 3: # arguments
            assert isinstance(token, ArgumentUnslicer)
            self.allargs = token
            # queue the message. It will not be executed until all the
            # arguments are ready. The .args list and .kwargs dict may change
            # before then.
            self.stage = 4
            return

    def receiveClose(self):
        if self.stage != 4:
            raise BananaError("'call' sequence ended too early")
        # time to create the InboundDelivery object so we can queue it
        delivery = InboundDelivery(self.reqID, self.obj,
                                   self.interface, self.methodname,
                                   self.methodSchema,
                                   self.allargs)
        return delivery, None

    def describe(self):
        s = "<methodcall"
        if self.stage == 0:
            pass
        if self.stage >= 1:
            s += " reqID=%d" % self.reqID
        if self.stage >= 2:
            s += " obj=%s" % (self.obj,)
            ifacename = "[none]"
            if self.interface:
                ifacename = self.interface.__remote_name__
            s += " iface=%s" % ifacename
        if self.stage >= 3:
            s += " methodname=%s" % self.methodname
        s += ">"
        return s


class AnswerSlicer(slicer.ScopedSlicer):
    opentype = ('answer',)

    def __init__(self, reqID, results):
        assert reqID != 0
        slicer.ScopedSlicer.__init__(self, None)
        self.reqID = reqID
        self.results = results

    def sliceBody(self, streamable, banana):
        yield self.reqID
        yield self.results

    def describe(self):
        return "<answer-%s>" % self.reqID

class AnswerUnslicer(slicer.ScopedUnslicer):
    request = None
    resultConstraint = None
    haveResults = False

    def checkToken(self, typebyte, size):
        if self.request is None:
            if typebyte != tokens.INT:
                raise BananaError("request ID must be an INT")
        elif not self.haveResults:
            if self.resultConstraint:
                try:
                    self.resultConstraint.checkToken(typebyte, size)
                except Violation, v:
                    # improve the error message
                    if v.args:
                        # this += gives me a TypeError "object doesn't
                        # support item assignment", which confuses me
                        #v.args[0] += " in inbound method results"
                        why = v.args[0] + " in inbound method results"
                        v.args = why,
                    else:
                        v.args = ("in inbound method results",)
                    raise # this will errback the request
        else:
            raise BananaError("stop sending me stuff!")

    def doOpen(self, opentype):
        if self.resultConstraint:
            self.resultConstraint.checkOpentype(opentype)
            # TODO: improve the error message
        unslicer = self.open(opentype)
        if unslicer:
            if self.resultConstraint:
                unslicer.setConstraint(self.resultConstraint)
        return unslicer

    def receiveChild(self, token, ready_deferred=None):
        assert not isinstance(token, defer.Deferred)
        assert ready_deferred is None
        if self.request == None:
            reqID = token
            # may raise Violation for bad reqIDs
            self.request = self.broker.getRequest(reqID)
            self.resultConstraint = self.request.constraint
        else:
            self.results = token
            self.haveResults = True

    def reportViolation(self, f):
        # if the Violation was received after we got the reqID, we can tell
        # the broker it was an error
        if self.request != None:
            self.request.fail(f)
        return f # give up our sequence

    def receiveClose(self):
        self.request.complete(self.results)
        return None, None

    def describe(self):
        if self.request:
            return "Answer(req=%s)" % self.request.reqID
        return "Answer(req=?)"



class ErrorSlicer(slicer.ScopedSlicer):
    opentype = ('error',)

    def __init__(self, reqID, f):
        slicer.ScopedSlicer.__init__(self, None)
        assert isinstance(f, failure.Failure)
        self.reqID = reqID
        self.f = f

    def sliceBody(self, streamable, banana):
        yield self.reqID
        yield self.f

    def describe(self):
        return "<error-%s>" % self.reqID

class ErrorUnslicer(slicer.ScopedUnslicer):
    request = None
    fConstraint = FailureConstraint()
    gotFailure = False

    def checkToken(self, typebyte, size):
        if self.request == None:
            if typebyte != tokens.INT:
                raise BananaError("request ID must be an INT")
        elif not self.gotFailure:
            self.fConstraint.checkToken(typebyte, size)
        else:
            raise BananaError("stop sending me stuff!")

    def doOpen(self, opentype):
        self.fConstraint.checkOpentype(opentype)
        unslicer = self.open(opentype)
        if unslicer:
            unslicer.setConstraint(self.fConstraint)
        return unslicer

    def reportViolation(self, f):
        # a failure while receiving the failure. A bit daft, really.
        if self.request != None:
            self.request.fail(f)
        return f # give up our sequence

    def receiveChild(self, token, ready_deferred=None):
        assert not isinstance(token, defer.Deferred)
        assert ready_deferred is None
        if self.request == None:
            reqID = token
            # may raise BananaError for bad reqIDs
            self.request = self.broker.getRequest(reqID)
        else:
            self.failure = token
            self.gotFailure = True

    def receiveClose(self):
        self.request.fail(self.failure)
        return None, None

    def describe(self):
        if self.request is None:
            return "<error-?>"
        return "<error-%s>" % self.request.reqID


# failures are sent as Copyables
class FailureSlicer(slicer.BaseSlicer):
    slices = failure.Failure
    classname = "twisted.python.failure.Failure"

    def slice(self, streamable, banana):
        self.streamable = streamable
        yield 'copyable'
        yield self.classname
        state = self.getStateToCopy(self.obj, banana)
        for k,v in state.iteritems():
            yield k
            yield v
    def describe(self):
        return "<%s>" % self.classname
        
    def getStateToCopy(self, obj, broker):
        #state = obj.__dict__.copy()
        #state['tb'] = None
        #state['frames'] = []
        #state['stack'] = []

        state = {}
        # string exceptions show up as obj.value == None and
        # isinstance(obj.type, str). Normal exceptions show up as obj.value
        # == text and obj.type == exception class. We need to make sure we
        # can handle both.
        if isinstance(obj.value, failure.Failure):
            # TODO: how can this happen? I got rid of failure2Copyable, so
            # if this case is possible, something needs to replace it
            raise RuntimeError("not implemented yet")
            #state['value'] = failure2Copyable(obj.value, banana.unsafeTracebacks)
        elif isinstance(obj.type, str):
            state['value'] = str(obj.value)
            state['type'] = obj.type # a string
        else:
            state['value'] = str(obj.value) # Exception instance
            state['type'] = reflect.qual(obj.type) # Exception class

        if broker.unsafeTracebacks:
            if isinstance(obj.type, str):
                stack = "getTraceback() not available for string exceptions\n"
            else:
                stack = obj.getTraceback()
            state['traceback'] = stack
            # TODO: provide something with globals and locals and HTML and
            # all that cool stuff
        else:
            state['traceback'] = 'Traceback unavailable\n'
        if len(state['traceback']) > 1900:
            state['traceback'] = (state['traceback'][:1900] +
                                  "\n\n-- TRACEBACK TRUNCATED --\n")
        state['parents'] = obj.parents
        return state

class CopiedFailure(failure.Failure, copyable.RemoteCopyOldStyle):
    # this is a RemoteCopyOldStyle because you can't raise new-style
    # instances as exceptions.

    """I am a shadow of some remote Failure instance. I contain less
    information than the original did.

    You can still extract a (brief) printable traceback from me. My .parents
    attribute is a list of strings describing the class of the exception
    that I contain, just like the real Failure had, so my trap() and check()
    methods work fine. My .type and .value attributes are string
    representations of the original exception class and exception instance,
    respectively. The most significant effect is that you cannot access
    f.value.args, and should instead just use f.value .

    My .frames and .stack attributes are empty, although this may change in
    the future (and with the cooperation of the sender).
    """

    nonCyclic = True
    stateSchema = FailureConstraint()

    def __init__(self):
        copyable.RemoteCopyOldStyle.__init__(self)

    def setCopyableState(self, state):
        #self.__dict__.update(state)
        self.__dict__ = state
        # state includes: type, value, traceback, parents
        #self.type = state['type']
        #self.value = state['value']
        #self.traceback = state['traceback']
        #self.parents = state['parents']
        self.tb = None
        self.frames = []
        self.stack = []

    def __str__(self):
        return "[CopiedFailure instance: %s]" % self.getBriefTraceback()

    pickled = 1
    def printTraceback(self, file=None, elideFrameworkCode=0,
                       detail='default'):
        if file is None: file = log.logerr
        file.write("Traceback from remote host -- ")
        file.write(self.traceback)

copyable.registerRemoteCopy(FailureSlicer.classname, CopiedFailure)
