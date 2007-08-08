
from twisted.internet import defer


class AsyncAND(defer.Deferred):
    """Like DeferredList, but results are discarded and failures handled
    in a more convenient fashion.

    Create me with a list of Deferreds. I will fire my callback (with None)
    if and when all of my component Deferreds fire successfully. I will fire
    my errback when and if any of my component Deferreds errbacks, in which
    case I will absorb the failure. If a second Deferred errbacks, I will not
    absorb that failure.

    This means that you can put a bunch of Deferreds together into an
    AsyncAND and then forget about them. If all succeed, the AsyncAND will
    fire. If one fails, that Failure will be propagated to the AsyncAND. If
    multiple ones fail, the first Failure will go to the AsyncAND and the
    rest will be left unhandled (and therefore logged).
    """

    def __init__(self, deferredList):
        defer.Deferred.__init__(self)

        if not deferredList:
            self.callback(None)
            return

        self.remaining = len(deferredList)
        self._fired = False

        for d in deferredList:
            d.addCallbacks(self._cbDeferred, self._cbDeferred,
                           callbackArgs=(True,), errbackArgs=(False,))

    def _cbDeferred(self, result, succeeded):
        self.remaining -= 1
        if succeeded:
            if not self._fired and self.remaining == 0:
                # the last input has fired. We fire.
                self._fired = True
                self.callback(None)
                return
        else:
            if not self._fired:
                # the first Failure is carried into our output
                self._fired = True
                self.errback(result)
                return None
            else:
                # second and later Failures are not absorbed
                return result
