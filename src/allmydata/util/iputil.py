# from the Python Standard Library
import os, sys, re, socket, subprocess, errno

from sys import platform

# from Twisted
from twisted.internet import defer, threads, reactor
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.error import CannotListenError
from twisted.python.procutils import which
from twisted.python.runtime import platformType
from twisted.python import log

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
        except OSError, e:
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

def allocate_tcp_port():
    """Return an (integer) available TCP port on localhost. This briefly
    listens on the port in question, then closes it right away."""

    # Making this work correctly on multiple OSes is non-trivial:
    # * on OS-X:
    #   * Binding the test socket to 127.0.0.1 lets the kernel give us a
    #     LISTEN port that some other process is using, if they bound it to
    #     ANY (0.0.0.0). These will fail when we attempt to
    #     listen(bind=0.0.0.0) ourselves
    #   * Binding the test socket to 0.0.0.0 lets the kernel give us LISTEN
    #     ports bound to 127.0.0.1, although then our subsequent listen()
    #     call usually succeeds.
    #   * In both cases, the kernel can give us a port that's in use by the
    #     near side of an ESTABLISHED socket. If the process which owns that
    #     socket is not owned by the same user as us, listen() will fail.
    #   * Doing a listen() right away (on the kernel-allocated socket)
    #     succeeds, but a subsequent listen() on a new socket (bound to
    #     the same port) will fail.
    # * on Linux:
    #   * The kernel never gives us a port in use by a LISTEN socket, whether
    #     we bind the test socket to 127.0.0.1 or 0.0.0.0
    #   * Binding it to 127.0.0.1 does let the kernel give us ports used in
    #     an ESTABLISHED connection. Our listen() will fail regardless of who
    #     owns that socket. (note that we are using SO_REUSEADDR but not
    #     SO_REUSEPORT, which would probably affect things).
    #
    # So to make this work properly everywhere, allocate_tcp_port() needs two
    # phases: first we allocate a port (with 0.0.0.0), then we close that
    # socket, then we open a second socket, bind the second socket to the
    # same port, then try to listen. If the listen() fails, we loop back and
    # try again.

    # Ideally we'd refrain from doing listen(), to minimize impact on the
    # system, and we'd bind the port to 127.0.0.1, to avoid making it look
    # like we're accepting data from the outside world (in situations where
    # we're going to end up binding the port to 127.0.0.1 anyways). But for
    # the above reasons, neither would work. We *do* add SO_REUSEADDR, to
    # make sure our lingering socket won't prevent our caller from opening it
    # themselves in a few moments (note that Twisted's
    # tcp.Port.createInternetSocket sets SO_REUSEADDR, among other flags).

    count = 0
    while True:
        s = _make_socket()
        s.bind(("0.0.0.0", 0))
        port = s.getsockname()[1]
        s.close()

        s = _make_socket()
        try:
            s.bind(("0.0.0.0", port))
            s.listen(5) # this is what sometimes fails
            s.close()
            return port
        except socket.error:
            s.close()
            count += 1
            if count > 100:
                raise
            # try again

def _make_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if platformType == "posix" and sys.platform != "cygwin":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return s
