from __future__ import print_function

import sys
import shutil
from sys import stdout as _stdout
from os import mkdir, listdir, unlink
from os.path import join, abspath, curdir, exists
from tempfile import mkdtemp, mktemp

from twisted.python.procutils import which
from twisted.internet.defer import Deferred, DeferredList
from twisted.internet.task import deferLater
from twisted.internet.error import ProcessExitedAlready

import pytest

from util import _CollectOutputProtocol
from util import _MagicTextProtocol
from util import _DumpOutputProtocol
from util import _ProcessExitedProtocol
from util import _create_node
from util import _run_node


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
    return which('flogtool')[0]


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
        which('twistd')[0],
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


@pytest.fixture(scope='session')
def tor_introducer(reactor, temp_dir, flog_gatherer, request):
    config = '''
[node]
nickname = introducer_tor
web.port = 4561
log_gatherer.furl = {log_furl}
'''.format(log_furl=flog_gatherer)

    intro_dir = join(temp_dir, 'introducer_tor')
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
                '--tor-control-port', 'tcp:localhost:8010',
                '--listen=tor',
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
def tor_introducer_furl(tor_introducer, temp_dir):
    furl_fname = join(temp_dir, 'introducer_tor', 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        time.sleep(.1)
    furl = open(furl_fname, 'r').read()
    return furl


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
            '--poll-interval', '2',
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
            '--poll-interval', '2',
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


@pytest.fixture(scope='session')
def chutney(reactor, temp_dir):
    chutney_dir = join(temp_dir, 'chutney')
    mkdir(chutney_dir)

    # TODO:

    # check for 'tor' binary explicitly and emit a "skip" if we can't
    # find it

    # XXX yuck! should add a setup.py to chutney so we can at least
    # "pip install <path to tarball>" and/or depend on chutney in "pip
    # install -e .[dev]" (i.e. in the 'dev' extra)
    proto = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        '/usr/bin/git',
        (
            '/usr/bin/git', 'clone', '--depth=1',
            'https://git.torproject.org/chutney.git',
            chutney_dir,
        )
    )
    pytest.blockon(proto.done)
    return chutney_dir


@pytest.fixture(scope='session')
def tor_network(reactor, temp_dir, chutney, request):
    # this is the actual "chutney" script at the root of a chutney checkout
    chutney_dir = chutney
    chut = join(chutney_dir, 'chutney')

    # now, as per Chutney's README, we have to create the network
    # ./chutney configure networks/basic
    # ./chutney start networks/basic

    proto = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-m', 'chutney.TorNet', 'configure',
            join(chutney_dir, 'networks', 'basic'),
        ),
        path=join(chutney_dir),
        env={"PYTHONPATH": join(chutney_dir, "lib")},
    )
    pytest.blockon(proto.done)

    proto = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-m', 'chutney.TorNet', 'start',
            join(chutney_dir, 'networks', 'basic'),
        ),
        path=join(chutney_dir),
        env={"PYTHONPATH": join(chutney_dir, "lib")},
    )
    pytest.blockon(proto.done)

    # print some useful stuff
    proto = _CollectOutputProtocol()
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-m', 'chutney.TorNet', 'status',
            join(chutney_dir, 'networks', 'basic'),
        ),
        path=join(chutney_dir),
        env={"PYTHONPATH": join(chutney_dir, "lib")},
    )
    pytest.blockon(proto.done)

    def cleanup():
        print("Tearing down Chutney Tor network")
        proto = _CollectOutputProtocol()
        reactor.spawnProcess(
            proto,
            sys.executable,
            (
                sys.executable, '-m', 'chutney.TorNet', 'stop',
                join(chutney_dir, 'networks', 'basic'),
            ),
            path=join(chutney_dir),
            env={"PYTHONPATH": join(chutney_dir, "lib")},
        )
        pytest.blockon(proto.done)
    request.addfinalizer(cleanup)

    return chut
