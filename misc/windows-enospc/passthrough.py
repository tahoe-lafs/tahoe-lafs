"""
Writing to non-blocking pipe can result in ENOSPC when using Unix APIs on
Windows.  So, this program passes through data from stdin to stdout, using
Windows APIs instead of Unix-y APIs.
"""

from twisted.internet.stdio import StandardIO
from twisted.internet import reactor
from twisted.internet.protocol import Protocol
from twisted.internet.interfaces import IHalfCloseableProtocol
from twisted.internet.error import ReactorNotRunning
from zope.interface import implementer

@implementer(IHalfCloseableProtocol)
class Passthrough(Protocol):
    def readConnectionLost(self):
        self.transport.loseConnection()

    def writeConnectionLost(self):
        try:
            reactor.stop()
        except ReactorNotRunning:
            pass

    def dataReceived(self, data):
        self.transport.write(data)

    def connectionLost(self, reason):
        try:
            reactor.stop()
        except ReactorNotRunning:
            pass


std = StandardIO(Passthrough())
reactor.run()
