"""
Tests for ``allmydata.test.eliotutil``.
"""

from eliot import (
    Message,
)
from eliot.twisted import DeferredContext

from twisted.trial.unittest import TestCase
from twisted.internet.defer import succeed
from twisted.internet.task import deferLater
from twisted.internet import reactor

from .eliotutil import with_eliot

class WithEliotTests(TestCase):
    @with_eliot
    def test_returns_none(self):
        Message.log(hello="world")

    @with_eliot
    def test_returns_fired_deferred(self):
        Message.log(hello="world")
        return succeed(None)

    @with_eliot
    def test_returns_unfired_deferred(self):
        Message.log(hello="world")
        # @with_eliot automatically gives us an action context but it's still
        # our responsibility to maintain it across stack-busting operations.
        d = DeferredContext(deferLater(reactor, 0.0, lambda: None))
        d.addCallback(lambda ignored: Message.log(goodbye="world"))
        # We didn't start an action.  We're not finishing an action.
        return d.result
