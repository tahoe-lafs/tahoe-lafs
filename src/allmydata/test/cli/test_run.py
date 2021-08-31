"""
Tests for ``allmydata.scripts.tahoe_run``.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from six.moves import (
    StringIO,
)

from testtools import (
    skipIf,
)

from testtools.matchers import (
    Contains,
    Equals,
    HasLength,
)

from twisted.python.runtime import (
    platform,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.testing import (
    MemoryReactor,
)
from twisted.internet.test.modulehelpers import (
    AlternateReactor,
)

from ...scripts.tahoe_run import (
    DaemonizeTheRealService,
    RunOptions,
    run,
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


class RunTests(SyncTestCase):
    """
    Tests for ``run``.
    """
    @skipIf(platform.isWindows(), "There are no PID files on Windows.")
    def test_non_numeric_pid(self):
        """
        If the pidfile exists but does not contain a numeric value, a complaint to
        this effect is written to stderr.
        """
        basedir = FilePath(self.mktemp()).asTextMode()
        basedir.makedirs()
        basedir.child(u"twistd.pid").setContent(b"foo")
        basedir.child(u"tahoe-client.tac").setContent(b"")

        config = RunOptions()
        config.stdout = StringIO()
        config.stderr = StringIO()
        config['basedir'] = basedir.path
        config.twistd_args = []

        runs = []
        result_code = run(config, runApp=runs.append)
        self.assertThat(
            config.stderr.getvalue(),
            Contains("found invalid PID file in"),
        )
        self.assertThat(
            runs,
            HasLength(1),
        )
        self.assertThat(
            result_code,
            Equals(0),
        )
