from __future__ import print_function

import sys
import shutil
from time import sleep
from os import mkdir, listdir, environ
from os.path import join, exists
from tempfile import mkdtemp, mktemp
from functools import partial

from eliot import (
    to_file,
    log_call,
    start_action,
)

from twisted.python.procutils import which
from twisted.internet.defer import DeferredList
from twisted.internet.error import (
    ProcessExitedAlready,
    ProcessTerminated,
)

import pytest
import pytest_twisted

from util import (
    _CollectOutputProtocol,
    _MagicTextProtocol,
    _DumpOutputProtocol,
    _ProcessExitedProtocol,
    _create_node,
    _run_node,
    _cleanup_tahoe_process,
    _tahoe_runner_optional_coverage,
    await_client_ready,
    TahoeProcess,
)
from grid import (
    create_port_allocator,
    create_flog_gatherer,
    create_grid,
)


# pytest customization hooks

def pytest_addoption(parser):
    parser.addoption(
        "--keep-tempdir", action="store_true", dest="keep",
        help="Keep the tmpdir with the client directories (introducer, etc)",
    )
    parser.addoption(
        "--coverage", action="store_true", dest="coverage",
        help="Collect coverage statistics",
    )

@pytest.fixture(autouse=True, scope='session')
def eliot_logging():
    with open("integration.eliot.json", "w") as f:
        to_file(f)
        yield


# I've mostly defined these fixtures from "easiest" to "most
# complicated", and the dependencies basically go "down the
# page". They're all session-scoped which has the "pro" that we only
# set up the grid once, but the "con" that each test has to be a
# little careful they're not stepping on toes etc :/

@pytest.fixture(scope='session')
@log_call(action_type=u"integration:reactor", include_result=False)
def reactor():
    # this is a fixture in case we might want to try different
    # reactors for some reason.
    from twisted.internet import reactor as _reactor
    return _reactor


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:port_allocator", include_result=False)
def port_allocator(reactor):
    return create_port_allocator(start_port=45000)


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:temp_dir", include_args=[])
def temp_dir(request):
    """
    Invoke like 'py.test --keep-tempdir ...' to avoid deleting the temp-dir
    """
    tmp = mkdtemp(prefix="tahoe")
    if request.config.getoption('keep'):
        print("\nWill retain tempdir '{}'".format(tmp))

    # I'm leaving this in and always calling it so that the tempdir
    # path is (also) printed out near the end of the run
    def cleanup():
        if request.config.getoption('keep'):
            print("Keeping tempdir '{}'".format(tmp))
        else:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception as e:
                print("Failed to remove tmpdir: {}".format(e))
    request.addfinalizer(cleanup)

    return tmp


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:flog_binary", include_args=[])
def flog_binary():
    return which('flogtool')[0]


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:flog_gatherer", include_args=[])
def flog_gatherer(reactor, temp_dir, flog_binary, request):
    fg = pytest_twisted.blockon(
        create_flog_gatherer(reactor, request, temp_dir, flog_binary)
    )
    return fg


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:grid", include_args=[])
def grid(reactor, request, temp_dir, flog_gatherer, port_allocator):
    g = pytest_twisted.blockon(
        create_grid(reactor, request, temp_dir, flog_gatherer, port_allocator)
    )
    return g


@pytest.fixture(scope='session')
def introducer(grid):
    return grid.introducer


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:introducer:furl", include_args=["temp_dir"])
def introducer_furl(introducer, temp_dir):
    return introducer.furl


@pytest.fixture(scope='session')
@log_call(
    action_type=u"integration:tor:introducer",
    include_args=["temp_dir", "flog_gatherer"],
    include_result=False,
)
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
        _tahoe_runner_optional_coverage(
            done_proto,
            reactor,
            request,
            (
                'create-introducer',
                '--tor-control-port', 'tcp:localhost:8010',
                '--listen=tor',
                intro_dir,
            ),
        )
        pytest_twisted.blockon(done_proto.done)

    # over-write the config file with our stuff
    with open(join(intro_dir, 'tahoe.cfg'), 'w') as f:
        f.write(config)

    # on windows, "tahoe start" means: run forever in the foreground,
    # but on linux it means daemonize. "tahoe run" is consistent
    # between platforms.
    protocol = _MagicTextProtocol('introducer running')
    transport = _tahoe_runner_optional_coverage(
        protocol,
        reactor,
        request,
        (
            'run',
            intro_dir,
        ),
    )

    def cleanup():
        try:
            transport.signalProcess('TERM')
            pytest_twisted.blockon(protocol.exited)
        except ProcessExitedAlready:
            pass
    request.addfinalizer(cleanup)

    pytest_twisted.blockon(protocol.magic_seen)
    return transport


@pytest.fixture(scope='session')
def tor_introducer_furl(tor_introducer, temp_dir):
    furl_fname = join(temp_dir, 'introducer_tor', 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        sleep(.1)
    furl = open(furl_fname, 'r').read()
    return furl


@pytest.fixture(scope='session')
@log_call(
    action_type=u"integration:storage_nodes",
    include_args=["grid"],
    include_result=False,
)
def storage_nodes(grid):
    nodes_d = []
    # start all 5 nodes in parallel
    for x in range(5):
        #nodes_d.append(grid.add_storage_node())
        pytest_twisted.blockon(grid.add_storage_node())

    nodes_status = pytest_twisted.blockon(DeferredList(nodes_d))
    for ok, value in nodes_status:
        assert ok, "Storage node creation failed: {}".format(value)
    return grid.storage_servers


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:alice", include_args=[], include_result=False)
def alice(reactor, temp_dir, introducer_furl, flog_gatherer, storage_nodes, request):
    process = pytest_twisted.blockon(
        _create_node(
            reactor, request, temp_dir, introducer_furl, flog_gatherer, "alice",
            web_port="tcp:9980:interface=localhost",
            storage=False,
        )
    )
    await_client_ready(process)
    return process


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:bob", include_args=[], include_result=False)
def bob(reactor, temp_dir, introducer_furl, flog_gatherer, storage_nodes, request):
    process = pytest_twisted.blockon(
        _create_node(
            reactor, request, temp_dir, introducer_furl, flog_gatherer, "bob",
            web_port="tcp:9981:interface=localhost",
            storage=False,
        )
    )
    await_client_ready(process)
    return process


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
    #
    # https://trac.torproject.org/projects/tor/ticket/20343
    proto = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        'git',
        (
            'git', 'clone', '--depth=1',
            'https://git.torproject.org/chutney.git',
            chutney_dir,
        ),
        env=environ,
    )
    pytest_twisted.blockon(proto.done)
    return chutney_dir


@pytest.fixture(scope='session')
def tor_network(reactor, temp_dir, chutney, request):
    # this is the actual "chutney" script at the root of a chutney checkout
    chutney_dir = chutney
    chut = join(chutney_dir, 'chutney')

    # now, as per Chutney's README, we have to create the network
    # ./chutney configure networks/basic
    # ./chutney start networks/basic

    env = environ.copy()
    env.update({"PYTHONPATH": join(chutney_dir, "lib")})
    proto = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-m', 'chutney.TorNet', 'configure',
            join(chutney_dir, 'networks', 'basic'),
        ),
        path=join(chutney_dir),
        env=env,
    )
    pytest_twisted.blockon(proto.done)

    proto = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-m', 'chutney.TorNet', 'start',
            join(chutney_dir, 'networks', 'basic'),
        ),
        path=join(chutney_dir),
        env=env,
    )
    pytest_twisted.blockon(proto.done)

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
        env=env,
    )
    try:
        pytest_twisted.blockon(proto.done)
    except ProcessTerminated:
        print("Chutney.TorNet status failed (continuing):")
        print(proto.output.getvalue())

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
            env=env,
        )
        pytest_twisted.blockon(proto.done)
    request.addfinalizer(cleanup)

    return chut
