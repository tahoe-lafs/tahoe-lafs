"""
Utilities for getting IP addresses.
"""

from future.utils import native_str

from typing import Callable

import os, socket

from zope.interface import implementer

import attr

from netifaces import (
    interfaces,
    ifaddresses,
)

# from Twisted
from twisted.python.reflect import requireModule
from twisted.python import log
from twisted.internet.endpoints import AdoptedStreamServerEndpoint
from twisted.internet.interfaces import (
    IReactorSocket,
    IStreamServerEndpoint,
)

from .gcutil import (
    fileDescriptorResource,
)

fcntl = requireModule("fcntl")

allocate_tcp_port: Callable[[], int]
from foolscap.util import allocate_tcp_port # re-exported

try:
    import resource
    def increase_rlimits():
        # We'd like to raise our soft resource.RLIMIT_NOFILE, since certain
        # systems (OS-X, probably solaris) start with a relatively low limit
        # (256), and some unit tests want to open up more sockets than this.
        # Most linux systems start with both hard and soft limits at 1024,
        # which is plenty.

        # unfortunately the values to pass to setrlimit() vary widely from
        # one system to another. OS-X reports (256, HUGE), but the real hard
        # limit is 10240, and accepts (-1,-1) to mean raise it to the
        # maximum. Cygwin reports (256, -1), then ignores a request of
        # (-1,-1): instead you have to guess at the hard limit (it appears to
        # be 3200), so using (3200,-1) seems to work. Linux reports a
        # sensible (1024,1024), then rejects (-1,-1) as trying to raise the
        # maximum limit, so you could set it to (1024,1024) but you might as
        # well leave it alone.

        try:
            current = resource.getrlimit(resource.RLIMIT_NOFILE)
        except AttributeError:
            # we're probably missing RLIMIT_NOFILE
            return

        if current[0] >= 1024:
            # good enough, leave it alone
            return

        try:
            if current[1] > 0 and current[1] < 1000000:
                # solaris reports (256, 65536)
                resource.setrlimit(resource.RLIMIT_NOFILE,
                                   (current[1], current[1]))
            else:
                # this one works on OS-X (bsd), and gives us 10240, but
                # it doesn't work on linux (on which both the hard and
                # soft limits are set to 1024 by default).
                resource.setrlimit(resource.RLIMIT_NOFILE, (-1,-1))
                new = resource.getrlimit(resource.RLIMIT_NOFILE)
                if new[0] == current[0]:
                    # probably cygwin, which ignores -1. Use a real value.
                    resource.setrlimit(resource.RLIMIT_NOFILE, (3200,-1))

        except ValueError:
            log.msg("unable to set RLIMIT_NOFILE: current value %s"
                     % (resource.getrlimit(resource.RLIMIT_NOFILE),))
        except:
            # who knows what. It isn't very important, so log it and continue
            log.err()
except ImportError:
    def _increase_rlimits():
        # TODO: implement this for Windows.  Although I suspect the
        # solution might be "be running under the iocp reactor and
        # make this function be a no-op".
        pass
    # pyflakes complains about two 'def FOO' statements in the same time,
    # since one might be shadowing the other. This hack appeases pyflakes.
    increase_rlimits = _increase_rlimits


def get_local_addresses_sync():
    """
    Get locally assigned addresses as dotted-quad native strings.

    :return [str]: A list of IPv4 addresses which are assigned to interfaces
        on the local system.
    """
    return list(
        native_str(address[native_str("addr")])
        for iface_name
        in interfaces()
        for address
        in ifaddresses(iface_name).get(socket.AF_INET, [])
    )


def _foolscapEndpointForPortNumber(portnum):
    """
    Create an endpoint that can be passed to ``Tub.listen``.

    :param portnum: Either an integer port number indicating which TCP/IPv4
        port number the endpoint should bind or ``None`` to automatically
        allocate such a port number.

    :return: A two-tuple of the integer port number allocated and a
        Foolscap-compatible endpoint object.
    """
    if portnum is None:
        # Bury this reactor import here to minimize the chances of it having
        # the effect of installing the default reactor.
        from twisted.internet import reactor
        if fcntl is not None and IReactorSocket.providedBy(reactor):
            # On POSIX we can take this very safe approach of binding the
            # actual socket to an address.  Once the bind succeeds here, we're
            # no longer subject to any future EADDRINUSE problems.
            s = socket.socket()
            try:
                s.bind(('', 0))
                portnum = s.getsockname()[1]
                s.listen(1)
                # File descriptors are a relatively scarce resource.  The
                # cleanup process for the file descriptor we're about to dup
                # is unfortunately complicated.  In particular, it involves
                # the Python garbage collector.  See CleanupEndpoint for
                # details of that.  Here, we need to make sure the garbage
                # collector actually runs frequently enough to make a
                # difference.  Normally, the garbage collector is triggered by
                # allocations.  It doesn't know about *file descriptor*
                # allocation though.  So ... we'll "teach" it about those,
                # here.
                fileDescriptorResource.allocate()
                fd = os.dup(s.fileno())
                flags = fcntl.fcntl(fd, fcntl.F_GETFD)
                flags = flags | os.O_NONBLOCK | fcntl.FD_CLOEXEC
                fcntl.fcntl(fd, fcntl.F_SETFD, flags)
                endpoint = AdoptedStreamServerEndpoint(reactor, fd, socket.AF_INET)
                return (portnum, CleanupEndpoint(endpoint, fd))
            finally:
                s.close()
        else:
            # Get a random port number and fall through.  This is necessary on
            # Windows where Twisted doesn't offer IReactorSocket.  This
            # approach is error prone for the reasons described on
            # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2787
            portnum = allocate_tcp_port()
    return (portnum, native_str("tcp:%d" % (portnum,)))


@implementer(IStreamServerEndpoint)
@attr.s
class CleanupEndpoint(object):
    """
    An ``IStreamServerEndpoint`` wrapper which closes a file descriptor if the
    wrapped endpoint is never used.

    :ivar IStreamServerEndpoint _wrapped: The wrapped endpoint.  The
        ``listen`` implementation is delegated to this object.

    :ivar int _fd: The file descriptor to close if ``listen`` is never called
        by the time this object is garbage collected.

    :ivar bool _listened: A flag recording whether or not ``listen`` has been
        called.
    """
    _wrapped = attr.ib()
    _fd = attr.ib()
    _listened = attr.ib(default=False)

    def listen(self, protocolFactory):
        self._listened = True
        return self._wrapped.listen(protocolFactory)

    def __del__(self):
        """
        If ``listen`` was never called then close the file descriptor.
        """
        if not self._listened:
            os.close(self._fd)
            fileDescriptorResource.release()


def listenOnUnused(tub, portnum=None):
    """
    Start listening on an unused TCP port number with the given tub.

    :param portnum: Either an integer port number indicating which TCP/IPv4
        port number the endpoint should bind or ``None`` to automatically
        allocate such a port number.

    :return: An integer indicating the TCP port number on which the tub is now
        listening.
    """
    portnum, endpoint = _foolscapEndpointForPortNumber(portnum)
    tub.listenOn(endpoint)
    tub.setLocation(native_str("localhost:%d" % (portnum,)))
    return portnum


__all__ = ["allocate_tcp_port",
           "increase_rlimits",
           "get_local_addresses_sync",
           "listenOnUnused",
           ]
