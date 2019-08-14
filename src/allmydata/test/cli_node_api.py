
__all__ = [
    "CLINodeAPI",
    "Expect",
    "on_stdout",
    "on_stdout_and_stderr",
    "on_different",
    "wait_for_exit",
]

import os
import sys
from errno import ENOENT

import attr

from twisted.internet.error import (
    ProcessDone,
    ProcessTerminated,
    ProcessExitedAlready,
)
from twisted.internet.interfaces import (
    IProcessProtocol,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python.runtime import (
    platform,
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
from ..scripts.tahoe_stop import (
    COULD_NOT_STOP,
)
from ..util.eliotutil import (
    inline_callbacks,
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
        for proto in self._fds.values():
            proto.makeConnection(self.transport)

    def childDataReceived(self, childFD, data):
        try:
            proto = self._fds[childFD]
        except KeyError:
            pass
        else:
            proto.dataReceived(data)

    def processEnded(self, reason):
        notified = set()
        for proto in self._fds.values():
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
        return self.basedir.child(u"twistd.pid")

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
            u"-m",
            u"allmydata.scripts.runner",
        ] + argv
        return self.reactor.spawnProcess(
            processProtocol=process_protocol,
            executable=exe,
            args=argv,
            env=os.environ,
        )

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

    def stop(self, protocol):
        self._execute(
            protocol,
            [u"stop", self.basedir.asTextMode().path],
        )

    @inline_callbacks
    def stop_and_wait(self):
        if platform.isWindows():
            # On Windows there is no PID file and no "tahoe stop".
            if self.process is not None:
                while True:
                    try:
                        self.process.signalProcess("TERM")
                    except ProcessExitedAlready:
                        break
                    else:
                        yield deferLater(self.reactor, 0.1, lambda: None)
        else:
            protocol, ended = wait_for_exit()
            self.stop(protocol)
            yield ended

    def active(self):
        # By writing this file, we get two minutes before the client will
        # exit. This ensures that even if the 'stop' command doesn't work (and
        # the test fails), the client should still terminate.
        self.exit_trigger_file.touch()

    def _check_cleanup_reason(self, reason):
        # Let it fail because the process has already exited.
        reason.trap(ProcessTerminated)
        if reason.value.exitCode != COULD_NOT_STOP:
            return reason
        return None

    def cleanup(self):
        stopping = self.stop_and_wait()
        stopping.addErrback(self._check_cleanup_reason)
        return stopping


class _WaitForEnd(ProcessProtocol, object):
    def __init__(self, ended):
        self._ended = ended

    def processEnded(self, reason):
        if reason.check(ProcessDone):
            self._ended.callback(None)
        else:
            self._ended.errback(reason)


def wait_for_exit():
    ended = Deferred()
    protocol = _WaitForEnd(ended)
    return protocol, ended
