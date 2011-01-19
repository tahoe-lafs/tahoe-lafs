
# This is a dummy test suite that we can use to check that 'tahoe debug trial'
# is working properly. Since the module name does not start with 'test_', it
# will not be run by the main test suite.

from twisted.trial import unittest
from twisted.internet import defer


class Success(unittest.TestCase):
    def test_succeed(self):
        pass

    def test_skip(self):
        raise unittest.SkipTest('skip')

    def test_todo(self):
        self.fail('umm')
    test_todo.todo = 'never mind'


class Failure(unittest.TestCase):
    def test_fail(self):
        self.fail('fail')

    def test_error(self):
        raise AssertionError('clang')

    def test_deferred_error(self):
        return defer.fail(AssertionError('screech'))
