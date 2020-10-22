"""
Tests for allmydata.util.pipeline.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import gc

from twisted.internet import defer
from twisted.trial import unittest
from twisted.python import log
from twisted.python.failure import Failure

from allmydata.util import pipeline


class Pipeline(unittest.TestCase):
    def pause(self, *args, **kwargs):
        d = defer.Deferred()
        self.calls.append( (d, args, kwargs) )
        return d

    def failUnlessCallsAre(self, expected):
        #print(self.calls)
        #print(expected)
        self.failUnlessEqual(len(self.calls), len(expected), self.calls)
        for i,c in enumerate(self.calls):
            self.failUnlessEqual(c[1:], expected[i], str(i))

    def test_basic(self):
        self.calls = []
        finished = []
        p = pipeline.Pipeline(100)

        d = p.flush() # fires immediately
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        finished = []

        d = p.add(10, self.pause, "one")
        # the call should start right away, and our return Deferred should
        # fire right away
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessCallsAre([ ( ("one",) , {} ) ])
        self.failUnlessEqual(p.gauge, 10)

        # pipeline: [one]

        finished = []
        d = p.add(20, self.pause, "two", kw=2)
        # pipeline: [one, two]

        # the call and the Deferred should fire right away
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessCallsAre([ ( ("one",) , {} ),
                                  ( ("two",) , {"kw": 2} ),
                                  ])
        self.failUnlessEqual(p.gauge, 30)

        self.calls[0][0].callback("one-result")
        # pipeline: [two]
        self.failUnlessEqual(p.gauge, 20)

        finished = []
        d = p.add(90, self.pause, "three", "posarg1")
        # pipeline: [two, three]
        flushed = []
        fd = p.flush()
        fd.addCallbacks(flushed.append, log.err)
        self.failUnlessEqual(flushed, [])

        # the call will be made right away, but the return Deferred will not,
        # because the pipeline is now full.
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 0)
        self.failUnlessCallsAre([ ( ("one",) , {} ),
                                  ( ("two",) , {"kw": 2} ),
                                  ( ("three", "posarg1"), {} ),
                                  ])
        self.failUnlessEqual(p.gauge, 110)

        self.failUnlessRaises(pipeline.SingleFileError, p.add, 10, self.pause)

        # retiring either call will unblock the pipeline, causing the #3
        # Deferred to fire
        self.calls[2][0].callback("three-result")
        # pipeline: [two]

        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessEqual(flushed, [])

        # retiring call#2 will finally allow the flush() Deferred to fire
        self.calls[1][0].callback("two-result")
        self.failUnlessEqual(len(flushed), 1)

    def test_errors(self):
        self.calls = []
        p = pipeline.Pipeline(100)

        d1 = p.add(200, self.pause, "one")
        d2 = p.flush()

        finished = []
        d1.addBoth(finished.append)
        self.failUnlessEqual(finished, [])

        flushed = []
        d2.addBoth(flushed.append)
        self.failUnlessEqual(flushed, [])

        self.calls[0][0].errback(ValueError("oops"))

        self.failUnlessEqual(len(finished), 1)
        f = finished[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        self.failUnlessIn("PipelineError", str(f.value))
        self.failUnlessIn("ValueError", str(f.value))
        r = repr(f.value)
        self.failUnless("ValueError" in r, r)
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        # now that the pipeline is in the failed state, any new calls will
        # fail immediately

        d3 = p.add(20, self.pause, "two")

        finished = []
        d3.addBoth(finished.append)
        self.failUnlessEqual(len(finished), 1)
        f = finished[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        r = repr(f.value)
        self.failUnless("ValueError" in r, r)
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        d4 = p.flush()
        flushed = []
        d4.addBoth(flushed.append)
        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

    def test_errors2(self):
        self.calls = []
        p = pipeline.Pipeline(100)

        d1 = p.add(10, self.pause, "one")
        d2 = p.add(20, self.pause, "two")
        d3 = p.add(30, self.pause, "three")
        d4 = p.flush()

        # one call fails, then the second one succeeds: make sure
        # ExpandableDeferredList tolerates the second one

        flushed = []
        d4.addBoth(flushed.append)
        self.failUnlessEqual(flushed, [])

        self.calls[0][0].errback(ValueError("oops"))
        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        self.calls[1][0].callback("two-result")
        self.calls[2][0].errback(ValueError("three-error"))

        del d1,d2,d3,d4
        gc.collect()  # for PyPy
