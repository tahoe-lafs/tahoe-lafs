"""
Tests for allmydata.util.log.

Ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2, native_str
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.trial import unittest
from twisted.python.failure import Failure

from foolscap.logging import log

from allmydata.util import log as tahoe_log


class SampleError(Exception):
    pass


class Log(unittest.TestCase):
    def setUp(self):
        self.messages = []

        def msg(msg, facility, parent, *args, **kwargs):
            self.messages.append((msg, facility, parent, args, kwargs))
            return "msg{}".format(len(self.messages))

        self.patch(log, "msg", msg)

    def test_err(self):
        """Logging with log.err() causes tests to fail."""
        try:
            raise SampleError("simple sample")
        except:
            f = Failure()
        tahoe_log.err(format="intentional sample error",
                      failure=f, level=tahoe_log.OPERATIONAL, umid="wO9UoQ")
        result = self.flushLoggedErrors(SampleError)
        self.assertEqual(len(result), 1)

    def test_default_facility(self):
        """
        If facility is passed to PrefixingLogMixin.__init__, it is used as
        default facility.
        """
        class LoggingObject1(tahoe_log.PrefixingLogMixin):
            pass

        obj = LoggingObject1(facility="defaultfac")
        obj.log("hello")
        obj.log("world", facility="override")
        self.assertEqual(self.messages[-2][1], "defaultfac")
        self.assertEqual(self.messages[-1][1], "override")

    def test_with_prefix(self):
        """
        If prefix is passed to PrefixingLogMixin.__init__, it is used in
        message rendering.
        """
        class LoggingObject4(tahoe_log.PrefixingLogMixin):
            pass

        obj = LoggingObject4("fac", prefix="pre1")
        obj.log("hello")
        obj.log("world")
        self.assertEqual(self.messages[-2][0], '<LoggingObject4 #1>(pre1): hello')
        self.assertEqual(self.messages[-1][0], '<LoggingObject4 #1>(pre1): world')

    def test_with_bytes_prefix(self):
        """
        If bytes prefix is passed to PrefixingLogMixin.__init__, it is used in
        message rendering.
        """
        class LoggingObject5(tahoe_log.PrefixingLogMixin):
            pass

        obj = LoggingObject5("fac", prefix=b"pre1")
        obj.log("hello")
        obj.log("world")
        self.assertEqual(self.messages[-2][0], '<LoggingObject5 #1>(pre1): hello')
        self.assertEqual(self.messages[-1][0], '<LoggingObject5 #1>(pre1): world')

    def test_no_prefix(self):
        """
        If no prefix is passed to PrefixingLogMixin.__init__, it is not used in
        message rendering.
        """
        class LoggingObject2(tahoe_log.PrefixingLogMixin):
            pass

        obj = LoggingObject2()
        obj.log("hello")
        obj.log("world")
        self.assertEqual(self.messages[-2][0], '<LoggingObject2 #1>: hello')
        self.assertEqual(self.messages[-1][0], '<LoggingObject2 #1>: world')

    def test_numming(self):
        """
        Objects inheriting from PrefixingLogMixin get a unique number from a
        class-specific counter.
        """
        class LoggingObject3(tahoe_log.PrefixingLogMixin):
            pass

        obj = LoggingObject3()
        obj2 = LoggingObject3()
        obj.log("hello")
        obj2.log("world")
        self.assertEqual(self.messages[-2][0], '<LoggingObject3 #1>: hello')
        self.assertEqual(self.messages[-1][0], '<LoggingObject3 #2>: world')

    def test_parent_id(self):
        """
        The parent message id can be passed in, otherwise the first message's
        id is used as the parent.

        This logic is pretty bogus, but that's what the code does.
        """
        class LoggingObject1(tahoe_log.PrefixingLogMixin):
            pass

        obj = LoggingObject1()
        result = obj.log("zero")
        self.assertEqual(result, "msg1")
        obj.log("one", parent="par1")
        obj.log("two", parent="par2")
        obj.log("three")
        obj.log("four")
        self.assertEqual([m[2] for m in self.messages],
                         [None, "par1", "par2", "msg1", "msg1"])

    def test_grandparent_id(self):
        """
        If grandparent message id is given, it's used as parent id of the first
        message.
        """
        class LoggingObject1(tahoe_log.PrefixingLogMixin):
            pass

        obj = LoggingObject1(grandparentmsgid="grand")
        result = obj.log("zero")
        self.assertEqual(result, "msg1")
        obj.log("one", parent="par1")
        obj.log("two", parent="par2")
        obj.log("three")
        obj.log("four")
        self.assertEqual([m[2] for m in self.messages],
                         ["grand", "par1", "par2", "msg1", "msg1"])

    def test_native_string_keys(self):
        """Keyword argument keys are all native strings."""
        class LoggingObject17(tahoe_log.PrefixingLogMixin):
            pass

        obj = LoggingObject17()
        # Native string by default:
        obj.log(hello="world")
        # Will be Unicode on Python 2:
        obj.log(**{"my": "message"})
        for message in self.messages:
            for k in message[-1].keys():
                self.assertIsInstance(k, native_str)
