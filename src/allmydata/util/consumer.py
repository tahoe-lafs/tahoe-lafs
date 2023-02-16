"""
This file defines a basic download-to-memory consumer, suitable for use in
a filenode's read() method. See download_to_data() for an example of its use.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from zope.interface import implementer
from twisted.internet.interfaces import IConsumer
from typing import Any, Optional


@implementer(IConsumer)
class MemoryConsumer(object):

    def __init__(self) -> None:
        self.chunks: list = []
        self.done = False

    def registerProducer(self, p: Any, streaming: Any) -> None:
        self.producer = p
        if streaming:
            # call resumeProducing once to start things off
            p.resumeProducing()
        else:
            while not self.done:
                p.resumeProducing()

    def write(self, data: Any) -> None:
        self.chunks.append(data)

    def unregisterProducer(self: Any) -> None:
        self.done = True


def download_to_data(n: Any, offset: int=0, size: Optional[int]=None) -> Any:
    """
    Return Deferred that fires with results of reading from the given filenode.
    """
    d: Any = n.read(MemoryConsumer(), offset, size)
    d.addCallback(lambda mc: b"".join(mc.chunks))
    return d
