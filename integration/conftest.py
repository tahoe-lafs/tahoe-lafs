from __future__ import print_function

import sys
import shutil
from sys import stdout as _stdout
from os import mkdir, listdir, unlink
from os.path import join, abspath, curdir, exists
from tempfile import mkdtemp, mktemp
from StringIO import StringIO
from shutilwhich import which

from twisted.internet.defer import Deferred, DeferredList
from twisted.internet.task import deferLater
from twisted.internet.protocol import ProcessProtocol
from twisted.internet.error import ProcessExitedAlready, ProcessDone

import pytest

pytest_plugins = 'pytest_twisted'

# pytest customization hooks

def pytest_addoption(parser):
    parser.addoption(
        "--keep-tempdir", action="store_true", dest="keep",
        help="Keep the tmpdir with the client directories (introducer, etc)",
    )

# I've mostly defined these fixtures from "easiest" to "most
# complicated", and the dependencies basically go "down the
# page". They're all session-scoped which has the "pro" that we only
# set up the grid once, but the "con" that each test has to be a
# little careful they're not stepping on toes etc :/


@pytest.fixture(scope='session')
def reactor():
    # this is a fixture in case we might want to try different
    # reactors for some reason.
    from twisted.internet import reactor as _reactor
    return _reactor


@pytest.fixture(scope='session')
def temp_dir(request):
    """
    Invoke like 'py.test --keep ...' to avoid deleting the temp-dir
    """
    tmp = mkdtemp(prefix="tahoe")
    if request.config.getoption('keep', True):
        print("Will retain tempdir '{}'".format(tmp))

    # I'm leaving this in and always calling it so that the tempdir
    # path is (also) printed out near the end of the run
    def cleanup():
        if request.config.getoption('keep', True):
            print("Keeping tempdir '{}'".format(tmp))
        else:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception as e:
                print("Failed to remove tmpdir: {}".format(e))
    request.addfinalizer(cleanup)

    return tmp


@pytest.fixture(scope='session')
def flog_binary():
    return which('flogtool')


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


@pytest.fixture(scope='session')
def flog_gatherer(reactor, temp_dir, flog_binary, request):
    out_protocol = _CollectOutputProtocol()
    gather_dir = join(temp_dir, 'flog_gather')
    process = reactor.spawnProcess(
        out_protocol,
        flog_binary,
        (
            'flogtool', 'create-gatherer',
            '--location', 'tcp:localhost:3117',
            '--port', '3117',
            gather_dir,
        )
    )
    pytest.blockon(out_protocol.done)

    twistd_protocol = _MagicTextProtocol("Gatherer waiting at")
    twistd_process = reactor.spawnProcess(
        twistd_protocol,
        which('twistd'),
        (
            'twistd', '--nodaemon', '--python',
            join(gather_dir, 'gatherer.tac'),
        ),
        path=gather_dir,
    )
    pytest.blockon(twistd_protocol.magic_seen)

    def cleanup():
        try:
            twistd_process.signalProcess('TERM')
            pytest.blockon(twistd_protocol.exited)
        except ProcessExitedAlready:
            pass

        flog_file = mktemp('.flog_dump')
        flog_protocol = _DumpOutputProtocol(open(flog_file, 'w'))
        flog_dir = join(temp_dir, 'flog_gather')
        flogs = [x for x in listdir(flog_dir) if x.endswith('.flog')]

        print("Dumping {} flogtool logfiles to '{}'".format(len(flogs), flog_file))
        reactor.spawnProcess(
            flog_protocol,
            flog_binary,
            (
                'flogtool', 'dump', join(temp_dir, 'flog_gather', flogs[0])
            ),
        )
        pytest.blockon(flog_protocol.done)

    request.addfinalizer(cleanup)

    with open(join(gather_dir, 'log_gatherer.furl'), 'r') as f:
        furl = f.read().strip()
    return furl


@pytest.fixture(scope='session')
def introducer(reactor, temp_dir, flog_gatherer, request):
    config = '''
[node]
nickname = introducer0
web.port = 4560
log_gatherer.furl = {log_furl}
'''.format(log_furl=flog_gatherer)

    intro_dir = join(temp_dir, 'introducer')
    print("making introducer", intro_dir)

    if not exists(intro_dir):
        mkdir(intro_dir)
        done_proto = _ProcessExitedProtocol()
        reactor.spawnProcess(
            done_proto,
            sys.executable,
            (
                sys.executable, '-m', 'allmydata.scripts.runner',
                'create-introducer',
                '--listen=tcp',
                '--hostname=localhost',
                intro_dir,
            ),
        )
        pytest.blockon(done_proto.done)

    # over-write the config file with our stuff
    with open(join(intro_dir, 'tahoe.cfg'), 'w') as f:
        f.write(config)

    # on windows, "tahoe start" means: run forever in the foreground,
    # but on linux it means daemonize. "tahoe run" is consistent
    # between platforms.
    protocol = _MagicTextProtocol('introducer running')
    process = reactor.spawnProcess(
        protocol,
        sys.executable,
        (
            sys.executable, '-m', 'allmydata.scripts.runner',
            'run',
            intro_dir,
        ),
    )

    def cleanup():
        try:
            process.signalProcess('TERM')
            pytest.blockon(protocol.exited)
        except ProcessExitedAlready:
            pass
    request.addfinalizer(cleanup)

    pytest.blockon(protocol.magic_seen)
    return process


@pytest.fixture(scope='session')
def introducer_furl(introducer, temp_dir):
    furl_fname = join(temp_dir, 'introducer', 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        time.sleep(.1)
    furl = open(furl_fname, 'r').read()
    return furl


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


@pytest.fixture(scope='session')
def storage_nodes(reactor, temp_dir, introducer, introducer_furl, flog_gatherer, request):
    nodes = []
    # start all 5 nodes in parallel
    for x in range(5):
        name = 'node{}'.format(x)
        # tub_port = 9900 + x
        nodes.append(
            pytest.blockon(
                _create_node(
                    reactor, request, temp_dir, introducer_furl, flog_gatherer, name,
                    web_port=None, storage=True,
                )
            )
        )
    #nodes = pytest.blockon(DeferredList(nodes))
    return nodes


@pytest.fixture(scope='session')
def alice(reactor, temp_dir, introducer_furl, flog_gatherer, storage_nodes, request):
    try:
        mkdir(join(temp_dir, 'magic-alice'))
    except OSError:
        pass

    process = pytest.blockon(
        _create_node(
            reactor, request, temp_dir, introducer_furl, flog_gatherer, "alice",
            web_port="tcp:9980:interface=localhost",
            storage=False,
        )
    )
    return process


@pytest.fixture(scope='session')
def bob(reactor, temp_dir, introducer_furl, flog_gatherer, storage_nodes, request):
    try:
        mkdir(join(temp_dir, 'magic-bob'))
    except OSError:
        pass

    process = pytest.blockon(
        _create_node(
            reactor, request, temp_dir, introducer_furl, flog_gatherer, "bob",
            web_port="tcp:9981:interface=localhost",
            storage=False,
        )
    )
    return process


@pytest.fixture(scope='session')
def alice_invite(reactor, alice, temp_dir, request):
    node_dir = join(temp_dir, 'alice')

    # FIXME XXX by the time we see "client running" in the logs, the
    # storage servers aren't "really" ready to roll yet (uploads
    # fairly consistently fail if we don't hack in this pause...)
    import time ; time.sleep(5)
    proto = _CollectOutputProtocol()
    transport = reactor.spawnProcess(
        proto,
        sys.executable,
        [
            sys.executable, '-m', 'allmydata.scripts.runner',
            'magic-folder', 'create',
            '--basedir', node_dir, 'magik:', 'alice',
            join(temp_dir, 'magic-alice'),
        ]
    )
    pytest.blockon(proto.done)

    proto = _CollectOutputProtocol()
    transport = reactor.spawnProcess(
        proto,
        sys.executable,
        [
            sys.executable, '-m', 'allmydata.scripts.runner',
            'magic-folder', 'invite',
            '--basedir', node_dir, 'magik:', 'bob',
        ]
    )
    pytest.blockon(proto.done)
    invite = proto.output.getvalue()
    print("invite from alice", invite)

    # before magic-folder works, we have to stop and restart (this is
    # crappy for the tests -- can we fix it in magic-folder?)
    try:
        alice.signalProcess('TERM')
        pytest.blockon(alice.exited)
    except ProcessExitedAlready:
        pass
    magic_text = 'Completed initial Magic Folder scan successfully'
    pytest.blockon(_run_node(reactor, node_dir, request, magic_text))
    return invite


@pytest.fixture(scope='session')
def magic_folder(reactor, alice_invite, alice, bob, temp_dir, request):
    print("pairing magic-folder")
    bob_dir = join(temp_dir, 'bob')
    proto = _CollectOutputProtocol()
    transport = reactor.spawnProcess(
        proto,
        sys.executable,
        [
            sys.executable, '-m', 'allmydata.scripts.runner',
            'magic-folder', 'join',
            '--basedir', bob_dir,
            alice_invite,
            join(temp_dir, 'magic-bob'),
        ]
    )
    pytest.blockon(proto.done)

    # before magic-folder works, we have to stop and restart (this is
    # crappy for the tests -- can we fix it in magic-folder?)
    try:
        print("Sending TERM to Bob")
        bob.signalProcess('TERM')
        pytest.blockon(bob.exited)
    except ProcessExitedAlready:
        pass

    magic_text = 'Completed initial Magic Folder scan successfully'
    pytest.blockon(_run_node(reactor, bob_dir, request, magic_text))
    return (join(temp_dir, 'magic-alice'), join(temp_dir, 'magic-bob'))
