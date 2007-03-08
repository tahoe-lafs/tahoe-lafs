
# adapted from nattraverso.ipdiscover

import subprocess
import re
import socket
from cStringIO import StringIO
from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.utils import getProcessOutput

from fcntl import ioctl
import struct
SIOCGIFADDR    = 0x8915 # linux-specific

# inspired by scapy
def get_if_list():
    f = open("/proc/net/dev","r")
    f.readline(); f.readline()
    names = []
    for l in f.readlines():
        names.append(l[:l.index(":")].strip())
    return names

def get_if_addr(ifname):
    try:
        s=socket.socket()
        ifreq = ioctl(s, SIOCGIFADDR, struct.pack("16s16x", ifname))
        s.close()
        naddr = ifreq[20:24]
        return socket.inet_ntoa(naddr)
    except IOError:
        return None

def get_local_addresses():
    """Return a list of IPv4 addresses (as dotted-quad strings) that are
    currently configured on this host.

    This will only work under linux, because it uses both a linux-specific
    /proc/net/dev devices (to get the interface names) and a SIOCGIFADDR
    ioctl (to get their addresses). If the listing-the-interfaces were done
    with an ioctl too (and if if you're lucky enough to be using the same
    value for the ioctls), then it might work on other forms of unix too.
    Windows is right out."""

    ifnames = []
    for ifname in get_if_list():
        addr = get_if_addr(ifname)
        if addr:
            ifnames.append(addr)
    return ifnames

def get_local_addresses_sync():
    """Return a list of IPv4 addresses (as dotted-quad strings) that are
    currently configured on this host.

    Unfortunately this is not compatible with Twisted: it catches SIGCHLD and
    this usually results in errors about 'Interrupted system call'.

    This will probably work on both linux and OS-X, but probably not windows.
    """
    # eventually I want to use somebody else's cross-platform library for
    # this. For right now, I'm running ifconfig and grepping for the 'inet '
    # lines.

    cmd = "/sbin/ifconfig"
    #p = os.popen(cmd)
    c = subprocess.Popen(["ifconfig"], stdout=subprocess.PIPE)
    output = c.communicate()[0]
    p = StringIO(output)
    addresses = []
    for line in p.readlines():
        # linux shows: "   inet addr:1.2.3.4  Bcast:1.2.3.255..."
        # OS-X shows: "   inet 1.2.3.4 ..."
        m = re.match("^\s+inet\s+[a-z:]*([\d\.]+)\s", line)
        if m:
            addresses.append(m.group(1))
    return addresses

def get_local_addresses_async():
    """Return a Deferred that fires with a list of IPv4 addresses (as
    dotted-quad strings) that are currently configured on this host.

    This will probably work on both linux and OS-X, but probably not windows.
    """
    # eventually I want to use somebody else's cross-platform library for
    # this. For right now, I'm running ifconfig and grepping for the 'inet '
    # lines.

    # I'd love to do this synchronously.
    cmd = "/sbin/ifconfig"
    d = getProcessOutput(cmd)
    def _parse(output):
        addresses = []
        for line in StringIO(output).readlines():
            # linux shows: "   inet addr:1.2.3.4  Bcast:1.2.3.255..."
            # OS-X shows: "   inet 1.2.3.4 ..."
            m = re.match("^\s+inet\s+[a-z:]*([\d\.]+)\s", line)
            if m:
                addresses.append(m.group(1))
        return addresses
    def _fallback(f):
        return ["127.0.0.1", get_local_ip_for()]
    d.addCallbacks(_parse, _fallback)
    return d


def get_local_ip_for(target='A.ROOT-SERVERS.NET'):
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

