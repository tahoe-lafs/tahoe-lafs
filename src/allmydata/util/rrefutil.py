
from twisted.internet import address
from foolscap.api import Violation, RemoteException, SturdyRef


def add_version_to_remote_reference(rref, default):
    """I try to add a .version attribute to the given RemoteReference. I call
    the remote get_version() method to learn its version. I'll add the
    default value if the remote side doesn't appear to have a get_version()
    method."""
    d = rref.callRemote("get_version")
    def _got_version(version):
        rref.version = version
        return rref
    def _no_get_version(f):
        f.trap(Violation, RemoteException)
        rref.version = default
        return rref
    d.addCallbacks(_got_version, _no_get_version)
    return d


def stringify_remote_address(rref):
    remote = rref.getPeer()
    if isinstance(remote, address.IPv4Address):
        return "%s:%d" % (remote.host, remote.port)
    # loopback is a non-IPv4Address
    return str(remote)
