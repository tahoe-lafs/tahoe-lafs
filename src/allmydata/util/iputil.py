
# adapted from nattraverso.ipdiscover

import os
from cStringIO import StringIO
import re
import socket
from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.utils import getProcessOutput

def get_local_addresses():
    """Return a Deferred that fires with a list of IPv4 addresses (as
    dotted-quad strings) that are currently configured on this host.
    """
    # eventually I want to use somebody else's cross-platform library for
    # this. For right now, I'm running ifconfig and grepping for the 'inet '
    # lines.

    cmd = "/sbin/ifconfig"
    p = os.popen(cmd)
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

