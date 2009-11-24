
from twisted.internet import defer
from twisted.python.failure import Failure
from twisted.python import log
from allmydata.util.assertutil import precondition

class PipelineError(Exception):
    """One of the pipelined messages returned an error. The received Failure
    object is stored in my .error attribute."""
    def __init__(self, error):
        self.error = error

    def __repr__(self):
        return "<PipelineError error=(%r)>" % (self.error,)
    def __str__(self):
        return "<PipelineError error=(%s)>" % (self.error,)

class SingleFileError(Exception):
    """You are not permitted to add a job to a full pipeline."""


class ExpandableDeferredList(defer.Deferred):
    # like DeferredList(fireOnOneErrback=True) with a built-in
    # gatherResults(), but you can add new Deferreds until you close it. This
    # gives you a chance to add don't-complain-about-unhandled-error errbacks
    # immediately after attachment, regardless of whether you actually end up
    # wanting the list or not.
    def __init__(self):
        defer.Deferred.__init__(self)
        self.resultsReceived = 0
        self.resultList = []
        self.failure = None
        self.closed = False

    def addDeferred(self, d):
        precondition(not self.closed, "don't call addDeferred() on a closed ExpandableDeferredList")
        index = len(self.resultList)
        self.resultList.append(None)
        d.addCallbacks(self._cbDeferred, self._ebDeferred,
                       callbackArgs=(index,))
        return d

    def close(self):
        self.closed = True
        self.checkForFinished()

    def checkForFinished(self):
        if not self.closed:
            return
        if self.called:
            return
        if self.failure:
            self.errback(self.failure)
        elif self.resultsReceived == len(self.resultList):
            self.callback(self.resultList)

    def _cbDeferred(self, res, index):
        self.resultList[index] = res
        self.resultsReceived += 1
        self.checkForFinished()
        return res

    def _ebDeferred(self, f):
        self.failure = f
        self.checkForFinished()
        return f


class Pipeline:
    """I manage a size-limited pipeline of Deferred operations, usually
    callRemote() messages."""

    def __init__(self, capacity):
        self.capacity = capacity # how full we can be
        self.gauge = 0 # how full we are
        self.failure = None
        self.waiting = [] # callers of add() who are blocked
        self.unflushed = ExpandableDeferredList()

    def add(self, _size, _func, *args, **kwargs):
        # We promise that all the Deferreds we return will fire in the order
        # they were returned. To make it easier to keep this promise, we
        # prohibit multiple outstanding calls to add() .
        if self.waiting:
            raise SingleFileError
        if self.failure:
            return defer.fail(self.failure)
        self.gauge += _size
        fd = defer.maybeDeferred(_func, *args, **kwargs)
        fd.addBoth(self._call_finished, _size)
        self.unflushed.addDeferred(fd)
        fd.addErrback(self._eat_pipeline_errors)
        fd.addErrback(log.err, "_eat_pipeline_errors didn't eat it")
        if self.gauge < self.capacity:
            return defer.succeed(None)
        d = defer.Deferred()
        self.waiting.append(d)
        return d

    def flush(self):
        if self.failure:
            return defer.fail(self.failure)
        d, self.unflushed = self.unflushed, ExpandableDeferredList()
        d.close()
        d.addErrback(self._flushed_error)
        return d

    def _flushed_error(self, f):
        precondition(self.failure) # should have been set by _call_finished
        return self.failure

    def _call_finished(self, res, size):
        self.gauge -= size
        if isinstance(res, Failure):
            res = Failure(PipelineError(res))
            if not self.failure:
                self.failure = res
        if self.failure:
            while self.waiting:
                d = self.waiting.pop(0)
                d.errback(self.failure)
        else:
            while self.waiting and (self.gauge < self.capacity):
                d = self.waiting.pop(0)
                d.callback(None)
                # the d.callback() might trigger a new call to add(), which
                # will raise our gauge and might cause the pipeline to be
                # filled. So the while() loop gets a chance to tell the
                # caller to stop.
        return res

    def _eat_pipeline_errors(self, f):
        f.trap(PipelineError)
        return None
