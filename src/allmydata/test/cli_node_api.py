
__all__ = [
    "CLINodeAPI",
    "Expect",
    "on_stdout",
    "wait_for_exit",
]

import os
import sys

import attr

from twisted.internet.error import (
    ProcessDone,
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

from allmydata.client import _Client

class Expect(Protocol):
    def __init__(self):
        self._expectations = []

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


class _Stdout(ProcessProtocol):
    def __init__(self, stdout_protocol):
        self._stdout_protocol = stdout_protocol

    def connectionMade(self):
        self._stdout_protocol.makeConnection(self.transport)

    def outReceived(self, data):
        self._stdout_protocol.dataReceived(data)

    def processEnded(self, reason):
        self._stdout_protocol.connectionLost(reason)


def on_stdout(protocol):
    return _Stdout(protocol)


@attr.s
class CLINodeAPI(object):
    reactor = attr.ib()
    basedir = attr.ib(type=FilePath)

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

    def run(self, protocol):
        """
        Start the node running.

        :param ProcessProtocol protocol: This protocol will be hooked up to
            the node process and can handle output or generate input.
        """
        self.process = self._execute(
            protocol,
            [u"run", self.basedir.asTextMode().path],
        )
        # Don't let the process run away forever.
        self.active()

    def stop(self, protocol):
        self._execute(
            protocol,
            [u"stop", self.basedir.asTextMode().path],
        )

    def active(self):
        # By writing this file, we get two minutes before the client will
        # exit. This ensures that even if the 'stop' command doesn't work (and
        # the test fails), the client should still terminate.
        self.exit_trigger_file.touch()


class _WaitForEnd(ProcessProtocol):
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
