
"""This file defines a basic download-to-memory consumer, suitable for use in
a filenode's read() method. See download_to_data() for an example of its use.
"""

from zope.interface import implements
from twisted.internet.interfaces import IConsumer

class MemoryConsumer:
    implements(IConsumer)

    def __init__(self, progress=None):
        self.chunks = []
        self.done = False
        self._progress = progress

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
        if self._progress is not None:
            self._progress.set_progress(sum([len(c) for c in self.chunks]))

    def unregisterProducer(self):
        self.done = True

def download_to_data(n, offset=0, size=None, progress=None):
    """
    :param on_progress: if set, a single-arg callable that receives total bytes downloaded
    """
    d = n.read(MemoryConsumer(progress=progress), offset, size)
    d.addCallback(lambda mc: "".join(mc.chunks))
    return d
