
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

def gatherResults(deferredList):
    """Returns list with result of given Deferreds.

    This builds on C{DeferredList} but is useful since you don't
    need to parse the result for success/failure.

    @type deferredList:  C{list} of L{Deferred}s
    """
    d = defer.DeferredList(deferredList, fireOnOneErrback=True, consumeErrors=True)
    d.addCallbacks(_parseDListResult, _unwrapFirstError)
    return d

