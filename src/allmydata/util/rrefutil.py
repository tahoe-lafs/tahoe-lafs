
from foolscap.tokens import Violation

class VersionedRemoteReference:
    """I wrap a RemoteReference, and add a .version attribute."""

    def __init__(self, original, version):
        self.rref = original
        self.version = version

    def callRemote(self, *args, **kwargs):
        return self.rref.callRemote(*args, **kwargs)

    def callRemoteOnly(self, *args, **kwargs):
        return self.rref.callRemoteOnly(*args, **kwargs)

    def notifyOnDisconnect(self, *args, **kwargs):
        return self.rref.notifyOnDisconnect(*args, **kwargs)


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

