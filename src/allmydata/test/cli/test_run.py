"""
Tests for ``allmydata.scripts.tahoe_run``.
"""

from __future__ import annotations

import re
from six.moves import (
    StringIO,
)

from hypothesis.strategies import text
from hypothesis import given, assume

from testtools.matchers import (
    Contains,
    Equals,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.testing import (
    MemoryReactor,
)
from twisted.python.failure import (
    Failure,
)
from twisted.internet.error import (
    ConnectionDone,
)
from twisted.internet.test.modulehelpers import (
    AlternateReactor,
)

from ...scripts.tahoe_run import (
    DaemonizeTheRealService,
    RunOptions,
    run,
)
from ...util.pid import (
    check_pid_process,
    InvalidPidFile,
)

from ...scripts.runner import (
    parse_options
)
from ..common import (
    SyncTestCase,
)

class DaemonizeTheRealServiceTests(SyncTestCase):
    """
    Tests for ``DaemonizeTheRealService``.
    """
    def _verify_error(self, config, expected):
        """
        Assert that when ``DaemonizeTheRealService`` is started using the given
        configuration it writes the given message to stderr and stops the
        reactor.

        :param bytes config: The contents of a ``tahoe.cfg`` file to give to
            the service.

        :param bytes expected: A string to assert appears in stderr after the
            service starts.
        """
        nodedir = FilePath(self.mktemp())
        nodedir.makedirs()
        nodedir.child("tahoe.cfg").setContent(config.encode("ascii"))
        nodedir.child("tahoe-client.tac").touch()

        options = parse_options(["run", nodedir.path])
        stdout = options.stdout = StringIO()
        stderr = options.stderr = StringIO()
        run_options = options.subOptions

        reactor = MemoryReactor()
        with AlternateReactor(reactor):
            service = DaemonizeTheRealService(
                "client",
                nodedir.path,
                run_options,
            )
            service.startService()

            # We happen to know that the service uses reactor.callWhenRunning
            # to schedule all its work (though I couldn't tell you *why*).
            # Make sure those scheduled calls happen.
            waiting = reactor.whenRunningHooks[:]
            del reactor.whenRunningHooks[:]
            for f, a, k in waiting:
                f(*a, **k)

        self.assertThat(
            reactor.hasStopped,
            Equals(True),
        )

        self.assertThat(
            stdout.getvalue(),
            Equals(""),
        )

        self.assertThat(
            stderr.getvalue(),
            Contains(expected),
        )

    def test_unknown_config(self):
        """
        If there are unknown items in the node configuration file then a short
        message introduced with ``"Configuration error:"`` is written to
        stderr.
        """
        self._verify_error("[invalid-section]\n", "Configuration error:")

    def test_port_assignment_required(self):
        """
        If ``tub.port`` is configured to use port 0 then a short message rejecting
        this configuration is written to stderr.
        """
        self._verify_error(
            """
            [node]
            tub.port = 0
            """,
            "tub.port cannot be 0",
        )

    def test_privacy_error(self):
        """
        If ``reveal-IP-address`` is set to false and the tub is not configured in
        a way that avoids revealing the node's IP address, a short message
        about privacy is written to stderr.
        """
        self._verify_error(
            """
            [node]
            tub.port = AUTO
            reveal-IP-address = false
            """,
            "Privacy requested",
        )


class DaemonizeStopTests(SyncTestCase):
    """
    Tests relating to stopping the daemon
    """
    def setUp(self):
        self.nodedir = FilePath(self.mktemp())
        self.nodedir.makedirs()
        config = ""
        self.nodedir.child("tahoe.cfg").setContent(config.encode("ascii"))
        self.nodedir.child("tahoe-client.tac").touch()

        # arrange to know when reactor.stop() is called
        self.reactor = MemoryReactor()
        self.stop_calls = []

        def record_stop():
            self.stop_calls.append(object())
        self.reactor.stop = record_stop

        super().setUp()

    def _make_daemon(self, extra_argv: list[str]) -> DaemonizeTheRealService:
        """
        Create the daemonization service.

        :param extra_argv: Extra arguments to pass between ``run`` and the
            node path.
        """
        options = parse_options(["run"] + extra_argv + [self.nodedir.path])
        options.stdout = StringIO()
        options.stderr = StringIO()
        options.stdin = StringIO()
        run_options = options.subOptions
        return DaemonizeTheRealService(
            "client",
            self.nodedir.path,
            run_options,
        )

    def _run_daemon(self) -> None:
        """
        Simulate starting up the reactor so the daemon plugin can do its
        stuff.
        """
        # We happen to know that the service uses reactor.callWhenRunning
        # to schedule all its work (though I couldn't tell you *why*).
        # Make sure those scheduled calls happen.
        waiting = self.reactor.whenRunningHooks[:]
        del self.reactor.whenRunningHooks[:]
        for f, a, k in waiting:
            f(*a, **k)

    def _close_stdin(self) -> None:
        """
        Simulate closing the daemon plugin's stdin.
        """
        # there should be a single reader: our StandardIO process
        # reader for stdin. Simulate it closing.
        for r in self.reactor.getReaders():
            r.connectionLost(Failure(ConnectionDone()))

    def test_stop_on_stdin_close(self):
        """
        We stop when stdin is closed.
        """
        with AlternateReactor(self.reactor):
            service = self._make_daemon([])
            service.startService()
            self._run_daemon()
            self._close_stdin()
            self.assertEqual(len(self.stop_calls), 1)

    def test_allow_stdin_close(self):
        """
        If --allow-stdin-close is specified then closing stdin doesn't
        stop the process
        """
        with AlternateReactor(self.reactor):
            service = self._make_daemon(["--allow-stdin-close"])
            service.startService()
            self._run_daemon()
            self._close_stdin()
            self.assertEqual(self.stop_calls, [])


class RunTests(SyncTestCase):
    """
    Tests for ``run``.
    """

    def test_non_numeric_pid(self):
        """
        If the pidfile exists but does not contain a numeric value, a complaint to
        this effect is written to stderr.
        """
        basedir = FilePath(self.mktemp()).asTextMode()
        basedir.makedirs()
        basedir.child(u"running.process").setContent(b"foo")
        basedir.child(u"tahoe-client.tac").setContent(b"")

        config = RunOptions()
        config.stdout = StringIO()
        config.stderr = StringIO()
        config['basedir'] = basedir.path
        config.twistd_args = []

        reactor = MemoryReactor()

        runs = []
        result_code = run(reactor, config, runApp=runs.append)
        self.assertThat(
            config.stderr.getvalue(),
            Contains("found invalid PID file in"),
        )
        # because the pidfile is invalid we shouldn't get to the
        # .run() call itself.
        self.assertThat(runs, Equals([]))
        self.assertThat(result_code, Equals(1))

    good_file_content_re = re.compile(r"\s*[0-9]*\s[0-9]*\s*", re.M)

    @given(text())
    def test_pidfile_contents(self, content):
        """
        invalid contents for a pidfile raise errors
        """
        assume(not self.good_file_content_re.match(content))
        pidfile = FilePath("pidfile")
        pidfile.setContent(content.encode("utf8"))

        with self.assertRaises(InvalidPidFile):
            with check_pid_process(pidfile):
                pass
