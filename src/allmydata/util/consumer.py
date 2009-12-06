
"""This file defines a basic download-to-memory consumer, suitable for use in
a filenode's read() method. See download_to_data() for an example of its use.
"""

from zope.interface import implements
from twisted.internet.interfaces import IConsumer

class MemoryConsumer:
    implements(IConsumer)
    def __init__(self):
        self.chunks = []
        self.done = False
    def registerProducer(self, p, streaming):
        self.producer = p
        if streaming:
            # call resumeProducing once to start things off
            p.resumeProducing()
        else:
            while not self.done:
                p.resumeProducing()
    def write(self, data):
        self.chunks.append(data)
    def unregisterProducer(self):
        self.done = True

def download_to_data(n, offset=0, size=None):
    d = n.read(MemoryConsumer(), offset, size)
    d.addCallback(lambda mc: "".join(mc.chunks))
    return d
