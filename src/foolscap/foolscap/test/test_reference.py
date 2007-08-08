
from zope.interface import implements
from twisted.trial import unittest
from twisted.python import failure
from foolscap.ipb import IRemoteReference
from foolscap.test.common import HelperTarget, Target
from foolscap.eventual import flushEventualQueue

class Remote:
    implements(IRemoteReference)
    pass


class LocalReference(unittest.TestCase):
    def tearDown(self):
        return flushEventualQueue()

    def ignored(self):
        pass

    def test_remoteReference(self):
        r = Remote()
        rref = IRemoteReference(r)
        self.failUnlessIdentical(r, rref)

    def test_callRemote(self):
        t = HelperTarget()
        t.obj = None
        rref = IRemoteReference(t)
        marker = rref.notifyOnDisconnect(self.ignored, "args", kwargs="foo")
        rref.dontNotifyOnDisconnect(marker)
        d = rref.callRemote("set", 12)
        # the callRemote should be put behind an eventual-send
        self.failUnlessEqual(t.obj, None)
        def _check(res):
            self.failUnlessEqual(t.obj, 12)
            self.failUnlessEqual(res, True)
        d.addCallback(_check)
        return d

    def test_callRemoteOnly(self):
        t = HelperTarget()
        t.obj = None
        rref = IRemoteReference(t)
        rc = rref.callRemoteOnly("set", 12)
        self.failUnlessEqual(rc, None)

    def shouldFail(self, res, expected_failure, which, substring=None):
        # attach this with:
        #  d = something()
        #  d.addBoth(self.shouldFail, IndexError, "something")
        # the 'which' string helps to identify which call to shouldFail was
        # triggered, since certain versions of Twisted don't display this
        # very well.

        if isinstance(res, failure.Failure):
            res.trap(expected_failure)
            if substring:
                self.failUnless(substring in str(res),
                                "substring '%s' not in '%s'"
                                % (substring, str(res)))
        else:
            self.fail("%s was supposed to raise %s, not get '%s'" %
                      (which, expected_failure, res))

    def test_fail(self):
        t = Target()
        d = IRemoteReference(t).callRemote("fail")
        d.addBoth(self.shouldFail, ValueError, "test_fail",
                  "you asked me to fail")
        return d
