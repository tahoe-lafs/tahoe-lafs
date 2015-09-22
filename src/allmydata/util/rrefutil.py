
from twisted.internet import address
from foolscap.api import Violation, RemoteException, DeadReferenceError, \
     SturdyRef

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

def trap_and_discard(f, *errorTypes):
    f.trap(*errorTypes)
    pass

def trap_deadref(f):
    return trap_and_discard(f, DeadReferenceError)


def connection_hints_for_furl(furl):
    hints = []
    for h in SturdyRef(furl).locationHints:
        # Foolscap-0.2.5 and earlier used strings in .locationHints, 0.2.6
        # through 0.6.4 used tuples of ("ipv4",host,port), 0.6.5 through
        # 0.8.0 used tuples of ("tcp",host,port), and >=0.9.0 uses strings
        # again. Tolerate them all.
        if isinstance(h, tuple):
            hints.append(":".join([str(s) for s in h]))
        else:
            hints.append(h)
    return hints

def stringify_remote_address(rref):
    remote = rref.getPeer()
    if isinstance(remote, address.IPv4Address):
        return "%s:%d" % (remote.host, remote.port)
    # loopback is a non-IPv4Address
    return str(remote)
