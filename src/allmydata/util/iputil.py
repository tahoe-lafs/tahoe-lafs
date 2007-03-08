
# adapted from nattraverso.ipdiscover

import socket
from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol

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

