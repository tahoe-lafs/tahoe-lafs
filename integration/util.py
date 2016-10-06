import sys
import time
from os import mkdir
from os.path import exists, join
from StringIO import StringIO

from twisted.internet.defer import Deferred
from twisted.internet.protocol import ProcessProtocol
from twisted.internet.error import ProcessExitedAlready, ProcessDone

import pytest


class _ProcessExitedProtocol(ProcessProtocol):
    """
    Internal helper that .callback()s on self.done when the process
    exits (for any reason).
    """

    def __init__(self):
        self.done = Deferred()

    def processEnded(self, reason):
        self.done.callback(None)


class _CollectOutputProtocol(ProcessProtocol):
    """
    Internal helper. Collects all output (stdout + stderr) into
    self.output, and callback's on done with all of it after the
    process exits (for any reason).
    """
    def __init__(self):
        self.done = Deferred()
        self.output = StringIO()

    def processEnded(self, reason):
        if not self.done.called:
            self.done.callback(self.output.getvalue())

    def processExited(self, reason):
        if not isinstance(reason.value, ProcessDone):
            self.done.errback(reason)

    def outReceived(self, data):
        self.output.write(data)

    def errReceived(self, data):
        print("ERR", data)
        self.output.write(data)


class _DumpOutputProtocol(ProcessProtocol):
    """
    Internal helper.
    """
    def __init__(self, f):
        self.done = Deferred()
        self._out = f if f is not None else sys.stdout

    def processEnded(self, reason):
        if not self.done.called:
            self.done.callback(None)

    def processExited(self, reason):
        if not isinstance(reason.value, ProcessDone):
            self.done.errback(reason)

    def outReceived(self, data):
        self._out.write(data)

    def errReceived(self, data):
        self._out.write(data)


class _MagicTextProtocol(ProcessProtocol):
    """
    Internal helper. Monitors all stdout looking for a magic string,
    and then .callback()s on self.done and .errback's if the process exits
    """

    def __init__(self, magic_text):
        self.magic_seen = Deferred()
        self.exited = Deferred()
        self._magic_text = magic_text
        self._output = StringIO()

    def processEnded(self, reason):
        self.exited.callback(None)

    def outReceived(self, data):
        sys.stdout.write(data)
        self._output.write(data)
        if not self.magic_seen.called and self._magic_text in self._output.getvalue():
            print("Saw '{}' in the logs".format(self._magic_text))
            self.magic_seen.callback(None)

    def errReceived(self, data):
        sys.stdout.write(data)


def _run_node(reactor, node_dir, request, magic_text):
    if magic_text is None:
        magic_text = "client running"
    protocol = _MagicTextProtocol(magic_text)

    # on windows, "tahoe start" means: run forever in the foreground,
    # but on linux it means daemonize. "tahoe run" is consistent
    # between platforms.
    process = reactor.spawnProcess(
        protocol,
        sys.executable,
        (
            sys.executable, '-m', 'allmydata.scripts.runner',
            'run',
            node_dir,
        ),
    )
    process.exited = protocol.exited

    def cleanup():
        try:
            process.signalProcess('TERM')
            pytest.blockon(protocol.exited)
        except ProcessExitedAlready:
            pass
    request.addfinalizer(cleanup)

    # we return the 'process' ITransport instance
    # XXX abusing the Deferred; should use .when_magic_seen() or something?
    protocol.magic_seen.addCallback(lambda _: process)
    return protocol.magic_seen


def _create_node(reactor, request, temp_dir, introducer_furl, flog_gatherer, name, web_port, storage=True, magic_text=None):
    """
    Helper to create a single node, run it and return the instance
    spawnProcess returned (ITransport)
    """
    node_dir = join(temp_dir, name)
    if web_port is None:
        web_port = ''
    if not exists(node_dir):
        print("creating", node_dir)
        mkdir(node_dir)
        done_proto = _ProcessExitedProtocol()
        args = [
            sys.executable, '-m', 'allmydata.scripts.runner',
            'create-node',
            '--nickname', name,
            '--introducer', introducer_furl,
            '--hostname', 'localhost',
            '--listen', 'tcp',
        ]
        if not storage:
            args.append('--no-storage')
        args.append(node_dir)

        reactor.spawnProcess(
            done_proto,
            sys.executable,
            args,
        )
        pytest.blockon(done_proto.done)

        with open(join(node_dir, 'tahoe.cfg'), 'w') as f:
            f.write('''
[node]
nickname = %(name)s
web.port = %(web_port)s
web.static = public_html
log_gatherer.furl = %(log_furl)s

[client]
# Which services should this client connect to?
introducer.furl = %(furl)s
shares.needed = 2
shares.happy = 3
shares.total = 4

''' % {
    'name': name,
    'furl': introducer_furl,
    'web_port': web_port,
    'log_furl': flog_gatherer,
})

    return _run_node(reactor, node_dir, request, magic_text)


def await_file_contents(path, contents, timeout=15):
    start_time = time.time()
    while time.time() - start_time < timeout:
        print("  waiting for '{}'".format(path))
        if exists(path):
            try:
                with open(path, 'r') as f:
                    current = f.read()
            except IOError:
                print("IOError; trying again")
            else:
                if current == contents:
                    return True
                print("  file contents still mismatched")
                print("  wanted: {}".format(contents.replace('\n', ' ')))
                print("     got: {}".format(current.replace('\n', ' ')))
        time.sleep(1)
    if exists(path):
        raise Exception("Contents of '{}' mismatched after {}s".format(path, timeout))
    raise Exception("Didn't find '{}' after {}s".format(path, timeout))


def await_file_vanishes(path, timeout=10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        print("  waiting for '{}' to vanish".format(path))
        if not exists(path):
            return
        time.sleep(1)
    raise Exception("'{}' still exists after {}s".format(path, timeout))


