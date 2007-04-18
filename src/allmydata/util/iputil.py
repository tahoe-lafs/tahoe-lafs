# portions extracted from ipaddresslib by Autonomous Zone Industries, LGPL (author: Greg Smith)
# portions adapted from nattraverso.ipdiscover
# portions authored by Brian Warner, working for Allmydata
# most recent version authored by Zooko O'Whielacronx, working for Allmydata

# from the Python Standard Library
import re, socket, sys

# from Twisted
from twisted.internet import defer
from twisted.python import log
from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.utils import getProcessOutput
from twisted.python.procutils import which

def get_local_addresses_async(target='A.ROOT-SERVERS.NET'):
    """
    Return a Deferred that fires with a list of IPv4 addresses (as dotted-quad
    strings) that are currently configured on this host.

    @param target: we want to learn an IP address they could try using to
        connect to us; The default value is fine, but it might help if you
        pass the address of a host that you are actually trying to be
        reachable to.
    """
    addresses = set()
    addresses.add(get_local_ip_for(target))

    d = _find_addresses_via_config()
    def _collect(res):
        addresses.update(res)
        return addresses
    d.addCallback(_collect)

    return d

def get_local_ip_for(target):
    """Find out what our IP address is for use by a given target.

    Returns a string that holds the IP address which could be used by
    'target' to connect to us. It might work for them, it might not.
    """
    try:
        target_ipaddr = socket.gethostbyname(target)
    except socket.gaierror:
        return "127.0.0.1"
    udpprot = DatagramProtocol()
    port = reactor.listenUDP(0, udpprot)
    udpprot.transport.connect(target_ipaddr, 7)
    localip = udpprot.transport.getHost().host
    port.stopListening() # note, this returns a Deferred
    return localip

# k: result of sys.platform, v: which kind of IP configuration reader we use
_platform_map = {
    "linux-i386": "linux", # redhat
    "linux-ppc": "linux",  # redhat
    "linux2": "linux",     # debian
    "win32": "win32",
    "irix6-n32": "irix",
    "irix6-n64": "irix",
    "irix6": "irix",
    "openbsd2": "bsd",
    "darwin": "bsd",       # Mac OS X
    "freebsd4": "bsd",
    "freebsd5": "bsd",
    "netbsd1": "bsd",
    "sunos5": "sunos",
    "cygwin": "cygwin",
    }

class UnsupportedPlatformError(Exception):
    pass

# Wow, I'm really amazed at home much mileage we've gotten out of calling
# the external route.exe program on windows...  It appears to work on all
# versions so far.  Still, the real system calls would much be preferred...
# ... thus wrote Greg Smith in time immemorial...
_win32_path = 'route.exe'
_win32_args = ('print',)
_win32_re = re.compile('^\s*\d+\.\d+\.\d+\.\d+\s.+\s(?P<address>\d+\.\d+\.\d+\.\d+)\s+(?P<metric>\d+)\s*$', flags=re.M|re.I|re.S)

# These work in Redhat 6.x and Debian 2.2 potato
_linux_path = '/sbin/ifconfig'
_linux_re = re.compile('^\s*inet addr:(?P<address>\d+\.\d+\.\d+\.\d+)\s.+$', flags=re.M|re.I|re.S)

# NetBSD 1.4 (submitted by Rhialto), Darwin, Mac OS X
_netbsd_path = '/sbin/ifconfig'
_netbsd_args = ('-a',)
_netbsd_re = re.compile('^\s+inet (?P<address>\d+\.\d+\.\d+\.\d+)\s.+$', flags=re.M|re.I|re.S)

# Irix 6.5
_irix_path = '/usr/etc/ifconfig'
_irix_args = ('-a',)

# Solaris 2.x
_sunos_path = '/usr/sbin/ifconfig'
_sunos_args = ('-a',)

def _find_addresses_via_config():
    # originally by Greg Smith, hacked by Zooko to conform to Brian's API
    
    platform = _platform_map.get(sys.platform)
    if not platform:
        raise UnsupportedPlatformError(sys.platform)

    if platform in ('win32', 'cygwin',):
        l = []
        for executable in which(_win32_path):
            l.append(_query(executable, _win32_re, _win32_args))
        dl = defer.DeferredList(l)
        def _gather_results(res):
            addresses = set()
            for r in res:
                if r[0]:
                    addresses.update(r[1])
            return addresses
        dl.addCallback(_gather_results)
        return dl
    elif platform == 'linux':
        return _query(_linux_path, _linux_re)
    elif platform == 'bsd':
        return _query(_netbsd_path, _netbsd_re, _netbsd_args)
    elif platform == 'irix' :
        return _query(_irix_path, _netbsd_re, _irix_args)
    elif platform == 'sunos':
        return _query(_sunos_path, _netbsd_re, _sunos_args)

def _query(path, regex, args=()):
    d = getProcessOutput(path, args)
    def _parse(output):
        addresses = set()
        outputsplit = output.split('\n')
        for outline in outputsplit:
            m = regex.match(outline)
            if m:
                d = m.groupdict()
                addresses.add(d['address'])
    
        return addresses
    d.addCallback(_parse)
    return d
