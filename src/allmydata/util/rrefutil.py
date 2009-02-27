import exceptions

from foolscap.tokens import Violation

class ServerFailure(exceptions.Exception):
    # If the server returns a Failure instead of the normal response to a
    # protocol, then this exception will be raised, with the Failure that the
    # server returned as its .remote_failure attribute.
    def __init__(self, remote_failure):
        self.remote_failure = remote_failure
    def __repr__(self):
        return repr(self.remote_failure)
    def __str__(self):
        return str(self.remote_failure)

def is_remote(f):
    if isinstance(f.value, ServerFailure):
        return True
    return False

def is_local(f):
    return not is_remote(f)

def check_remote(f, *errorTypes):
    if is_remote(f):
        return f.value.remote_failure.check(*errorTypes)
    return None

def check_local(f, *errorTypes):
    if is_local(f):
        return f.check(*errorTypes)
    return None

def trap_remote(f, *errorTypes):
    if is_remote(f):
        return f.value.remote_failure.trap(*errorTypes)
    raise f

def trap_local(f, *errorTypes):
    if is_local(f):
        return f.trap(*errorTypes)
    raise f

def _wrap_server_failure(f):
    raise ServerFailure(f)

class WrappedRemoteReference(object):
    """I intercept any errback from the server and wrap it in a
    ServerFailure."""

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

    def dontNotifyOnDisconnect(self, *args, **kwargs):
        return self.rref.dontNotifyOnDisconnect(*args, **kwargs)

class VersionedRemoteReference(WrappedRemoteReference):
    """I wrap a RemoteReference, and add a .version attribute. I also
    intercept any errback from the server and wrap it in a ServerFailure."""

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

