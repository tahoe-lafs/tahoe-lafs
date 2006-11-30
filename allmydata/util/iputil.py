
# adapted from nattraverso.ipdiscover

from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol
#from twisted.internet.error import CannotListenError
#from twisted.internet.interfaces import IReactorMulticast
#from amdlib.util.nattraverso.utils import is_rfc1918_ip, is_bogus_ip

def get_local_ip_for(target='A.ROOT-SERVERS.NET'):
    """Find out what our IP address is for use by a given target.

    Returns a Deferred which will be fired with a string that holds the IP
    address which could be used by 'target' to connect to us. It might work
    for them, it might not.

    The reactor must be running before you can call this, because we must
    perform a DNS lookup on the target.

    """
    d = reactor.resolve(target)
    def _resolved(target_ipaddr):
        udpprot = DatagramProtocol()
        port = reactor.listenUDP(0, udpprot)
        udpprot.transport.connect(target_ipaddr, 7)
        localip = udpprot.transport.getHost().host
        port.stopListening()
        return localip
    d.addCallback(_resolved)
    return d



def BROKEN_get_local_ip_for(target_ipaddr):
    """Find out what our IP address is for use by a given target.

    Returns a Deferred which will be fired with a string that holds the IP
    address which could be used by 'target' to connect to us. It might work
    for them, it might not. 'target' must be an IP address.

    """
    udpprot = DatagramProtocol()
    port = reactor.listenUDP(0, udpprot)
    udpprot.transport.connect(target_ipaddr, 7)
    localip = udpprot.transport.getHost().host
    port.stopListening()

    return localip
