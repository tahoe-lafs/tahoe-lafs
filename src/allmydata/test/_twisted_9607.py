"""
A copy of the implementation of Twisted's ``getProcessOutputAndValue``
with the fix for Twisted #9607 (support for stdinBytes) patched in.
"""

from __future__ import (
    division,
    absolute_import,
    print_function,
    unicode_literals,
)

from io import BytesIO

from twisted.internet import protocol, defer


class _EverythingGetter(protocol.ProcessProtocol, object):

    def __init__(self, deferred, stdinBytes=None):
        self.deferred = deferred
        self.outBuf = BytesIO()
        self.errBuf = BytesIO()
        self.outReceived = self.outBuf.write
        self.errReceived = self.errBuf.write
        self.stdinBytes = stdinBytes

    def connectionMade(self):
        if self.stdinBytes is not None:
            self.transport.writeToChild(0, self.stdinBytes)
            # The only compelling reason not to _always_ close stdin here is
            # backwards compatibility.
            self.transport.closeStdin()

    def processEnded(self, reason):
        out = self.outBuf.getvalue()
        err = self.errBuf.getvalue()
        e = reason.value
        code = e.exitCode
        if e.signal:
            self.deferred.errback((out, err, e.signal))
        else:
            self.deferred.callback((out, err, code))



def _callProtocolWithDeferred(protocol, executable, args, env, path,
                              reactor=None, protoArgs=()):
    if reactor is None:
        from twisted.internet import reactor

    d = defer.Deferred()
    p = protocol(d, *protoArgs)
    reactor.spawnProcess(p, executable, (executable,)+tuple(args), env, path)
    return d



def getProcessOutputAndValue(executable, args=(), env={}, path=None,
                             reactor=None, stdinBytes=None):
    """Spawn a process and returns a Deferred that will be called back with
    its output (from stdout and stderr) and it's exit code as (out, err, code)
    If a signal is raised, the Deferred will errback with the stdout and
    stderr up to that point, along with the signal, as (out, err, signalNum)
    """
    return _callProtocolWithDeferred(
        _EverythingGetter,
        executable,
        args,
        env,
        path,
        reactor,
        protoArgs=(stdinBytes,),
    )
