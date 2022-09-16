"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

__all__ = [
    "CLINodeAPI",
    "Expect",
    "on_stdout",
    "on_stdout_and_stderr",
    "on_different",
]

import os
import sys
from errno import ENOENT

import attr

from eliot import (
    log_call,
)

from twisted.internet.error import (
    ProcessTerminated,
    ProcessExitedAlready,
)
from twisted.internet.interfaces import (
    IProcessProtocol,
)
from twisted.python.log import (
    msg,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.protocol import (
    Protocol,
    ProcessProtocol,
)
from twisted.internet.defer import (
    Deferred,
    succeed,
)
from twisted.internet.task import (
    deferLater,
)
from ..client import (
    _Client,
)
from ..util.eliotutil import (
    inline_callbacks,
    log_call_deferred,
)

class Expect(Protocol, object):
    def __init__(self):
        self._expectations = []

    def get_buffered_output(self):
        return self._buffer

    def expect(self, expectation):
        if expectation in self._buffer:
            return succeed(None)
        d = Deferred()
        self._expectations.append((expectation, d))
        return d

    def connectionMade(self):
        self._buffer = b""

    def dataReceived(self, data):
        self._buffer += data
        for i in range(len(self._expectations) - 1, -1, -1):
            expectation, d = self._expectations[i]
            if expectation in self._buffer:
                del self._expectations[i]
                d.callback(None)

    def connectionLost(self, reason):
        for ignored, d in self._expectations:
            d.errback(reason)


class _ProcessProtocolAdapter(ProcessProtocol, object):
    def __init__(self, fds):
        self._fds = fds

    def connectionMade(self):
        for proto in list(self._fds.values()):
            proto.makeConnection(self.transport)

    def childDataReceived(self, childFD, data):
        try:
            proto = self._fds[childFD]
        except KeyError:
            msg(format="Received unhandled output on %(fd)s: %(output)s",
                fd=childFD,
                output=data,
            )
        else:
            proto.dataReceived(data)

    def processEnded(self, reason):
        notified = set()
        for proto in list(self._fds.values()):
            if proto not in notified:
                proto.connectionLost(reason)
                notified.add(proto)


def on_stdout(protocol):
    return _ProcessProtocolAdapter({1: protocol})

def on_stdout_and_stderr(protocol):
    return _ProcessProtocolAdapter({1: protocol, 2: protocol})

def on_different(fd_mapping):
    return _ProcessProtocolAdapter(fd_mapping)

@attr.s
class CLINodeAPI(object):
    reactor = attr.ib()
    basedir = attr.ib(type=FilePath)
    process = attr.ib(default=None)

    @property
    def twistd_pid_file(self):
        return self.basedir.child(u"running.process")

    @property
    def node_url_file(self):
        return self.basedir.child(u"node.url")

    @property
    def storage_furl_file(self):
        return self.basedir.child(u"private").child(u"storage.furl")

    @property
    def introducer_furl_file(self):
        return self.basedir.child(u"private").child(u"introducer.furl")

    @property
    def config_file(self):
        return self.basedir.child(u"tahoe.cfg")

    @property
    def exit_trigger_file(self):
        return self.basedir.child(_Client.EXIT_TRIGGER_FILE)

    def _execute(self, process_protocol, argv):
        exe = sys.executable
        argv = [
            exe,
            "-b",
            u"-m",
            u"allmydata.scripts.runner",
        ] + argv
        msg(format="Executing %(argv)s",
            argv=argv,
        )
        return self.reactor.spawnProcess(
            processProtocol=process_protocol,
            executable=exe,
            args=argv,
            env=os.environ,
        )

    @log_call(action_type="test:cli-api:run", include_args=["extra_tahoe_args"])
    def run(self, protocol, extra_tahoe_args=()):
        """
        Start the node running.

        :param IProcessProtocol protocol: This protocol will be hooked up to
            the node process and can handle output or generate input.
        """
        if not IProcessProtocol.providedBy(protocol):
            raise TypeError("run requires process protocol, got {}".format(protocol))
        self.process = self._execute(
            protocol,
            list(extra_tahoe_args) + [u"run", self.basedir.asTextMode().path],
        )
        # Don't let the process run away forever.
        try:
            self.active()
        except OSError as e:
            if ENOENT != e.errno:
                raise

    @log_call_deferred(action_type="test:cli-api:stop")
    def stop(self):
        return self.stop_and_wait()

    @log_call_deferred(action_type="test:cli-api:stop-and-wait")
    @inline_callbacks
    def stop_and_wait(self):
        if self.process is not None:
            while True:
                try:
                    self.process.signalProcess("TERM")
                except ProcessExitedAlready:
                    break
                else:
                    yield deferLater(self.reactor, 0.1, lambda: None)

    def active(self):
        # By writing this file, we get two minutes before the client will
        # exit. This ensures that even if the 'stop' command doesn't work (and
        # the test fails), the client should still terminate.
        self.exit_trigger_file.touch()

    def _check_cleanup_reason(self, reason):
        # Let it fail because the process has already exited.
        reason.trap(ProcessTerminated)
        return None

    def cleanup(self):
        stopping = self.stop_and_wait()
        stopping.addErrback(self._check_cleanup_reason)
        return stopping
