from twisted.internet import defer

# utility wrapper for DeferredList
def _check_deferred_list(results):
    # if any of the component Deferreds failed, return the first failure such
    # that an addErrback() would fire. If all were ok, return a list of the
    # results (without the success/failure booleans)
    for success,f in results:
        if not success:
            return f
    return [r[1] for r in results]
def DeferredListShouldSucceed(dl):
    d = defer.DeferredList(dl)
    d.addCallback(_check_deferred_list)
    return d

def _parseDListResult(l):
    return [x[1] for x in l]

def _unwrapFirstError(f):
    f.trap(defer.FirstError)
    raise f.value.subFailure

class ResultsGatherer:
    def __init__(self, deferredlist):
        self.deferredlist = deferredlist
        self.fired = 0
        self.results = []
        self.d = defer.Deferred()
        for d in deferredlist:
            d.addCallbacks(self._cb, self._eb)
    def start(self):
        return self.d
    def _cb(self, res):
        self.results.append(res)
        self.fired += 1
        if self.fired >= len(self.deferredlist):
            self.d.callback(self.results)
    def _eb(self, f):
        self.d.errback(f)

def gatherResults(deferredlist):
    """ Return a deferred that fires with a list of the results of the deferreds, or else errbacks with any error. """
    r = ResultsGatherer(deferredlist)
    return r.start()
