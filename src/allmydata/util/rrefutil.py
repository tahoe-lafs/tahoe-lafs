import exceptions

from foolscap.tokens import Violation

from twisted.python import failure

class ServerFailure(exceptions.Exception):
    # If the server returns a Failure instead of the normal response to a protocol, then this
    # exception will be raised, with the Failure that the server returned as its .remote_failure
    # attribute.
    def __init__(self, remote_failure):
        self.remote_failure = remote_failure
    def __repr__(self):
        return repr(self.remote_failure)
    def __str__(self):
        return str(self.remote_failure)

def _wrap_server_failure(f):
    raise ServerFailure(f)

class WrappedRemoteReference(object):
    """I intercept any errback from the server and wrap it in a ServerFailure."""

    def __init__(self, original):
        self.rref = original

    def callRemote(self, *args, **kwargs):
        d = self.rref.callRemote(*args, **kwargs)
        d.addErrback(_wrap_server_failure)
        return d

    def callRemoteOnly(self, *args, **kwargs):
        return self.rref.callRemoteOnly(*args, **kwargs)

    def notifyOnDisconnect(self, *args, **kwargs):
        return self.rref.notifyOnDisconnect(*args, **kwargs)

class VersionedRemoteReference(WrappedRemoteReference):
    """I wrap a RemoteReference, and add a .version attribute. I also intercept any errback from
    the server and wrap it in a ServerFailure."""

    def __init__(self, original, version):
        WrappedRemoteReference.__init__(self, original)
        self.version = version

def get_versioned_remote_reference(rref, default):
    """I return a Deferred that fires with a VersionedRemoteReference"""
    d = rref.callRemote("get_version")
    def _no_get_version(f):
        f.trap(Violation, AttributeError)
        return default
    d.addErrback(_no_get_version)
    def _got_version(version):
        return VersionedRemoteReference(rref, version)
    d.addCallback(_got_version)
    return d

