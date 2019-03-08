# Copyright (c) Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Tests for the inotify-alike implementation L{allmydata.watchdog}.
"""

# Note: See https://twistedmatrix.com/trac/ticket/8915 for a proposal
# to avoid all of this duplicated code from Twisted.

from twisted.internet import defer, reactor
from twisted.python import filepath, runtime

from allmydata.frontends.magic_folder import get_inotify_module
from .common import (
    AsyncTestCase,
    skipIf,
)
inotify = get_inotify_module()


@skipIf(runtime.platformType == "win32", "inotify does not yet work on windows")
class INotifyTests(AsyncTestCase):
    """
    Define all the tests for the basic functionality exposed by
    L{inotify.INotify}.
    """
    def setUp(self):
        self.ignore_count = 0
        self.dirname = filepath.FilePath(self.mktemp())
        self.dirname.createDirectory()
        self.inotify = inotify.INotify()
        self.inotify.startReading()
        self.addCleanup(self.inotify.stopReading)
        return super(INotifyTests, self).setUp()


    def _notificationTest(self, mask, operation, expectedPath=None, ignore_count=0):
        """
        Test notification from some filesystem operation.

        @param mask: The event mask to use when setting up the watch.

        @param operation: A function which will be called with the
            name of a file in the watched directory and which should
            trigger the event.

        @param expectedPath: Optionally, the name of the path which is
            expected to come back in the notification event; this will
            also be passed to C{operation} (primarily useful when the
            operation is being done to the directory itself, not a
            file in it).

        @return: A L{Deferred} which fires successfully when the
            expected event has been received or fails otherwise.
        """
        assert ignore_count >= 0
        if expectedPath is None:
            expectedPath = self.dirname.child("foo.bar")
        if ignore_count > 0:
            self.ignore_count -= 1
            return
        notified = defer.Deferred()
        def cbNotified(result):
            (watch, filename, events) = result
            self.assertEqual(filename.asBytesMode(), expectedPath.asBytesMode())
            self.assertTrue(events & mask)
            self.inotify.ignore(self.dirname)
        notified.addCallback(cbNotified)

        def notify_event(*args):
            notified.callback(args)
        self.inotify.watch(
            self.dirname, mask=mask,
            callbacks=[notify_event])
        operation(expectedPath)
        return notified


    def test_modify(self):
        """
        Writing to a file in a monitored directory sends an
        C{inotify.IN_MODIFY} event to the callback.
        """
        def operation(path):
            with path.open("w") as fObj:
                fObj.write(b'foo')

        return self._notificationTest(inotify.IN_MODIFY, operation, ignore_count=1)


    def test_attrib(self):
        """
        Changing the metadata of a file in a monitored directory
        sends an C{inotify.IN_ATTRIB} event to the callback.
        """
        def operation(path):
            path.touch()
            path.touch()

        return self._notificationTest(inotify.IN_ATTRIB, operation, ignore_count=1)


    def test_closeWrite(self):
        """
        Closing a file which was open for writing in a monitored
        directory sends an C{inotify.IN_CLOSE_WRITE} event to the
        callback.
        """
        def operation(path):
            path.open("w").close()

        return self._notificationTest(inotify.IN_CLOSE_WRITE, operation)


    def test_delete(self):
        """
        Deleting a file in a monitored directory sends an
        C{inotify.IN_DELETE} event to the callback.
        """
        expectedPath = self.dirname.child("foo.bar")
        expectedPath.touch()
        notified = defer.Deferred()
        def cbNotified(result):
            (watch, filename, events) = result
            self.assertEqual(filename.asBytesMode(), expectedPath.asBytesMode())
            self.assertTrue(events & inotify.IN_DELETE)
        notified.addCallback(cbNotified)
        self.inotify.watch(
            self.dirname, mask=inotify.IN_DELETE,
            callbacks=[lambda *args: notified.callback(args)])
        expectedPath.remove()
        return notified


    def test_humanReadableMask(self):
        """
        L{inotify.humaReadableMask} translates all the possible event
        masks to a human readable string.
        """
        for mask, value in inotify._FLAG_TO_HUMAN:
            self.assertEqual(inotify.humanReadableMask(mask)[0], value)

        checkMask = (
            inotify.IN_CLOSE_WRITE | inotify.IN_ACCESS | inotify.IN_OPEN)
        self.assertEqual(
            set(inotify.humanReadableMask(checkMask)),
            set(['close_write', 'access', 'open']))


    def test_noAutoAddSubdirectory(self):
        """
        L{inotify.INotify.watch} with autoAdd==False will stop inotify
        from watching subdirectories created under the watched one.
        """
        def _callback(wp, fp, mask):
            # We are notified before we actually process new
            # directories, so we need to defer this check.
            def _():
                try:
                    self.assertFalse(self.inotify._isWatched(subdir))
                    d.callback(None)
                except Exception:
                    d.errback()
            reactor.callLater(0, _)

        checkMask = inotify.IN_ISDIR | inotify.IN_CREATE
        self.inotify.watch(
            self.dirname, mask=checkMask, autoAdd=False,
            callbacks=[_callback])
        subdir = self.dirname.child('test')
        d = defer.Deferred()
        subdir.createDirectory()
        return d
