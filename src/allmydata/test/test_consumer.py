"""
Tests for allmydata.util.consumer.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from zope.interface import implementer
from twisted.internet.interfaces import IPushProducer, IPullProducer

from allmydata.util.consumer import MemoryConsumer

from .common import (
    SyncTestCase,
)
from testtools.matchers import (
    Equals,
)


@implementer(IPushProducer)
@implementer(IPullProducer)
class Producer(object):
    """Can be used as either streaming or non-streaming producer.

    If used as streaming, the test should call iterate() manually.
    """

    def __init__(self, consumer, data):
        self.data = data
        self.consumer = consumer
        self.done = False

    def stopProducing(self):
        pass

    def pauseProducing(self):
        pass

    def resumeProducing(self):
        """Kick off streaming."""
        self.iterate()

    def iterate(self):
        """Do another iteration of writing."""
        if self.done:
            raise RuntimeError(
                "There's a bug somewhere, shouldn't iterate after being done"
            )
        if self.data:
            self.consumer.write(self.data.pop(0))
        else:
            self.done = True
            self.consumer.unregisterProducer()


class MemoryConsumerTests(SyncTestCase):
    """Tests for MemoryConsumer."""

    def test_push_producer(self):
        """
        A MemoryConsumer accumulates all data sent by a streaming producer.
        """
        consumer = MemoryConsumer()
        producer = Producer(consumer, [b"abc", b"def", b"ghi"])
        consumer.registerProducer(producer, True)
        self.assertThat(consumer.chunks, Equals([b"abc"]))
        producer.iterate()
        producer.iterate()
        self.assertThat(consumer.chunks, Equals([b"abc", b"def", b"ghi"]))
        self.assertFalse(consumer.done)
        producer.iterate()
        self.assertThat(consumer.chunks, Equals([b"abc", b"def", b"ghi"]))
        self.assertTrue(consumer.done)

    def test_pull_producer(self):
        """
        A MemoryConsumer accumulates all data sent by a non-streaming producer.
        """
        consumer = MemoryConsumer()
        producer = Producer(consumer, [b"abc", b"def", b"ghi"])
        consumer.registerProducer(producer, False)
        self.assertThat(consumer.chunks, Equals([b"abc", b"def", b"ghi"]))
        self.assertTrue(consumer.done)


# download_to_data() is effectively tested by some of the filenode tests, e.g.
# test_immutable.py.
