import os, signal, time
from random import randrange

from twisted.internet import reactor, defer, task
from twisted.python import failure

def insecurerandstr(n):
    return ''.join(map(chr, map(randrange, [0]*n, [256]*n)))

def flip_bit(good, which):
    # flip the low-order bit of good[which]
    if which == -1:
        pieces = good[:which], good[-1:], ""
    else:
        pieces = good[:which], good[which:which+1], good[which+1:]
    return pieces[0] + chr(ord(pieces[1]) ^ 0x01) + pieces[2]

def flip_one_bit(s, offset=0, size=None):
    """ flip one random bit of the string s, in a byte greater than or equal to offset and less
    than offset+size. """
    if size is None:
        size=len(s)
    i = randrange(offset, offset+size)
    result = s[:i] + chr(ord(s[i])^(0x01<<randrange(0, 8))) + s[i+1:]
    assert result != s, "Internal error -- flip_one_bit() produced the same string as its input: %s == %s" % (result, s)
    return result

class SignalMixin:
    # This class is necessary for any code which wants to use Processes
    # outside the usual reactor.run() environment. It is copied from
    # Twisted's twisted.test.test_process
    sigchldHandler = None

    def setUpClass(self):
        # make sure SIGCHLD handler is installed, as it should be on
        # reactor.run(). problem is reactor may not have been run when this
        # test runs.
        if hasattr(reactor, "_handleSigchld") and hasattr(signal, "SIGCHLD"):
            self.sigchldHandler = signal.signal(signal.SIGCHLD,
                                                reactor._handleSigchld)

    def tearDownClass(self):
        if self.sigchldHandler:
            signal.signal(signal.SIGCHLD, self.sigchldHandler)

class TimeoutError(Exception):
    pass

class PollComplete(Exception):
    pass

class PollMixin:

    def poll(self, check_f, pollinterval=0.01, timeout=100):
        # Return a Deferred, then call check_f periodically until it returns
        # True, at which point the Deferred will fire.. If check_f raises an
        # exception, the Deferred will errback. If the check_f does not
        # indicate success within timeout= seconds, the Deferred will
        # errback. If timeout=None, no timeout will be enforced, and the loop
        # will poll forever (or really until Trial times out).
        cutoff = None
        if timeout is not None:
            cutoff = time.time() + timeout
        lc = task.LoopingCall(self._poll, check_f, cutoff)
        d = lc.start(pollinterval)
        def _convert_done(f):
            f.trap(PollComplete)
            return None
        d.addErrback(_convert_done)
        return d

    def _poll(self, check_f, cutoff):
        if cutoff is not None and time.time() > cutoff:
            raise TimeoutError("PollMixin never saw %s return True" % check_f)
        if check_f():
            raise PollComplete()

class StallMixin:
    def stall(self, res=None, delay=1):
        d = defer.Deferred()
        reactor.callLater(delay, d.callback, res)
        return d

class ShouldFailMixin:

    def shouldFail(self, expected_failure, which, substring,
                   callable, *args, **kwargs):
        assert substring is None or isinstance(substring, str)
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnless(substring in str(res),
                                    "%s: substring '%s' not in '%s'"
                                    % (which, substring, str(res)))
                # return the Failure for further analysis, but in a form that
                # doesn't make the Deferred chain think that we failed.
                return [res]
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d


class TestMixin(SignalMixin):
    def setUp(self, repeatable=False):
        """
        @param repeatable: install the repeatable_randomness hacks to attempt
            to without access to real randomness and real time.time from the
            code under test
        """
        self.repeatable = repeatable
        if self.repeatable:
            import repeatable_random
            repeatable_random.force_repeatability()
        if hasattr(time, 'realtime'):
            self.teststarttime = time.realtime()
        else:
            self.teststarttime = time.time()

    def tearDown(self):
        if self.repeatable:
            import repeatable_random
            repeatable_random.restore_non_repeatability()
        self.clean_pending(required_to_quiesce=True)

    def clean_pending(self, dummy=None, required_to_quiesce=True):
        """
        This handy method cleans all pending tasks from the reactor.

        When writing a unit test, consider the following question:

            Is the code that you are testing required to release control once it
            has done its job, so that it is impossible for it to later come around
            (with a delayed reactor task) and do anything further?

        If so, then trial will usefully test that for you -- if the code under
        test leaves any pending tasks on the reactor then trial will fail it.

        On the other hand, some code is *not* required to release control -- some
        code is allowed to continuously maintain control by rescheduling reactor
        tasks in order to do ongoing work.  Trial will incorrectly require that
        code to clean up all its tasks from the reactor.

        Most people think that such code should be amended to have an optional
        "shutdown" operation that releases all control, but on the contrary it is
        good design for some code to *not* have a shutdown operation, but instead
        to have a "crash-only" design in which it recovers from crash on startup.

        If the code under test is of the "long-running" kind, which is *not*
        required to shutdown cleanly in order to pass tests, then you can simply
        call testutil.clean_pending() at the end of the unit test, and trial will
        be satisfied.
        """
        pending = reactor.getDelayedCalls()
        active = bool(pending)
        for p in pending:
            if p.active():
                p.cancel()
            else:
                print "WEIRNESS! pending timed call not active+!"
        if required_to_quiesce and active:
            self.fail("Reactor was still active when it was required to be quiescent.")

try:
    import win32file
    import win32con
    def make_readonly(path):
        win32file.SetFileAttributes(path, win32con.FILE_ATTRIBUTE_READONLY)
    def make_accessible(path):
        win32file.SetFileAttributes(path, win32con.FILE_ATTRIBUTE_NORMAL)
except ImportError:
    import stat
    def _make_readonly(path):
        os.chmod(path, stat.S_IREAD)
        os.chmod(os.path.dirname(path), stat.S_IREAD)
    def _make_accessible(path):
        os.chmod(os.path.dirname(path), stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
        os.chmod(path, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
    make_readonly = _make_readonly
    make_accessible = _make_accessible
