# from the Python Standard Library
import os, re, socket, subprocess, errno
from sys import platform

from zope.interface import implementer

import attr

# from Twisted
from twisted.python.reflect import requireModule
from twisted.internet import defer, threads, reactor
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.error import CannotListenError
from twisted.python.procutils import which
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
    return _synchronously_find_addresses_via_config()

def get_local_addresses_async(target="198.41.0.4"): # A.ROOT-SERVERS.NET
    """
    Return a Deferred that fires with a list of IPv4 addresses (as dotted-quad
    strings) that are currently configured on this host, sorted in descending
    order of how likely we think they are to work.

    @param target: we want to learn an IP address they could try using to
        connect to us; The default value is fine, but it might help if you
        pass the address of a host that you are actually trying to be
        reachable to.
    """
    addresses = []
    local_ip = get_local_ip_for(target)
    if local_ip is not None:
        addresses.append(local_ip)

    if platform == "cygwin":
        d = _cygwin_hack_find_addresses()
    else:
        d = _find_addresses_via_config()

    def _collect(res):
        for addr in res:
            if addr != "0.0.0.0" and not addr in addresses:
                addresses.append(addr)
        return addresses
    d.addCallback(_collect)

    return d

def get_local_ip_for(target):
    """Find out what our IP address is for use by a given target.

    @return: the IP address as a dotted-quad string which could be used by
              to connect to us. It might work for them, it might not. If
              there is no suitable address (perhaps we don't currently have an
              externally-visible interface), this will return None.
    """

    try:
        target_ipaddr = socket.gethostbyname(target)
    except socket.gaierror:
        # DNS isn't running, or somehow we encountered an error

        # note: if an interface is configured and up, but nothing is
        # connected to it, gethostbyname("A.ROOT-SERVERS.NET") will take 20
        # seconds to raise socket.gaierror . This is synchronous and occurs
        # for each node being started, so users of
        # test.common.SystemTestMixin (like test_system) will see something
        # like 120s of delay, which may be enough to hit the default trial
        # timeouts. For that reason, get_local_addresses_async() was changed
        # to default to the numerical ip address for A.ROOT-SERVERS.NET, to
        # avoid this DNS lookup. This also makes node startup fractionally
        # faster.
        return None

    try:
        udpprot = DatagramProtocol()
        port = reactor.listenUDP(0, udpprot)
        try:
            # connect() will fail if we're offline (e.g. running tests on a
            # disconnected laptop), which is fine (localip=None), but we must
            # still do port.stopListening() or we'll get a DirtyReactorError
            udpprot.transport.connect(target_ipaddr, 7)
            localip = udpprot.transport.getHost().host
            return localip
        finally:
            d = port.stopListening()
            d.addErrback(log.err)
    except (socket.error, CannotListenError):
        # no route to that host
        localip = None
    return localip


# Wow, I'm really amazed at home much mileage we've gotten out of calling
# the external route.exe program on windows...  It appears to work on all
# versions so far.
# ... thus wrote Greg Smith in time immemorial...
# Also, the Win32 APIs for this are really klunky and error-prone. --Daira

_win32_re = re.compile(r'^\s*\d+\.\d+\.\d+\.\d+\s.+\s(?P<address>\d+\.\d+\.\d+\.\d+)\s+(?P<metric>\d+)\s*$', flags=re.M|re.I|re.S)
_win32_commands = (('route.exe', ('print',), _win32_re),)

# These work in most Unices.
_addr_re = re.compile(r'^\s*inet [a-zA-Z]*:?(?P<address>\d+\.\d+\.\d+\.\d+)[\s/].+$', flags=re.M|re.I|re.S)
_unix_commands = (('/bin/ip', ('addr',), _addr_re),
                  ('/sbin/ip', ('addr',), _addr_re),
                  ('/sbin/ifconfig', ('-a',), _addr_re),
                  ('/usr/sbin/ifconfig', ('-a',), _addr_re),
                  ('/usr/etc/ifconfig', ('-a',), _addr_re),
                  ('ifconfig', ('-a',), _addr_re),
                  ('/sbin/ifconfig', (), _addr_re),
                 )


def _find_addresses_via_config():
    return threads.deferToThread(_synchronously_find_addresses_via_config)

def _synchronously_find_addresses_via_config():
    # originally by Greg Smith, hacked by Zooko and then Daira

    # We don't reach here for cygwin.
    if platform == 'win32':
        commands = _win32_commands
    else:
        commands = _unix_commands

    for (pathtotool, args, regex) in commands:
        # If pathtotool is a fully qualified path then we just try that.
        # If it is merely an executable name then we use Twisted's
        # "which()" utility and try each executable in turn until one
        # gives us something that resembles a dotted-quad IPv4 address.

        if os.path.isabs(pathtotool):
            exes_to_try = [pathtotool]
        else:
            exes_to_try = which(pathtotool)

        for exe in exes_to_try:
            try:
                addresses = _query(exe, args, regex)
            except Exception:
                addresses = []
            if addresses:
                return addresses

    return []

def _query(path, args, regex):
    if not os.path.isfile(path):
        return []
    env = {'LANG': 'en_US.UTF-8'}
    TRIES = 5
    for trial in xrange(TRIES):
        try:
            p = subprocess.Popen([path] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            (output, err) = p.communicate()
            break
        except OSError as e:
            if e.errno == errno.EINTR and trial < TRIES-1:
                continue
            raise

    addresses = []
    outputsplit = output.split('\n')
    for outline in outputsplit:
        m = regex.match(outline)
        if m:
            addr = m.group('address')
            if addr not in addresses:
                addresses.append(addr)

    return addresses

def _cygwin_hack_find_addresses():
    addresses = []
    for h in ["localhost", "127.0.0.1",]:
        addr = get_local_ip_for(h)
        if addr is not None and addr not in addresses:
            addresses.append(addr)

    return defer.succeed(addresses)


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
    return (portnum, "tcp:%d" % (portnum,))


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
    tub.setLocation("localhost:%d" % (portnum,))
    return portnum


__all__ = ["allocate_tcp_port",
           "increase_rlimits",
           "get_local_addresses_sync",
           "get_local_addresses_async",
           "get_local_ip_for",
           ]
