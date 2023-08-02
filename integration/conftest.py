"""
Ported to Python 3.
"""

from __future__ import annotations

import os
import sys
import shutil
from attr import frozen
from time import sleep
from os import mkdir, environ
from os.path import join, exists
from tempfile import mkdtemp

from eliot import (
    to_file,
    log_call,
)

from twisted.python.filepath import FilePath
from twisted.python.procutils import which
from twisted.internet.defer import DeferredList, succeed
from twisted.internet.error import (
    ProcessExitedAlready,
    ProcessTerminated,
)

import pytest
import pytest_twisted
from typing import Mapping

from .util import (
    _MagicTextProtocol,
    _DumpOutputProtocol,
    _ProcessExitedProtocol,
    _create_node,
    _tahoe_runner_optional_coverage,
    await_client_ready,
    block_with_timeout,
)
from .grid import (
    create_flog_gatherer,
    create_grid,
)
from allmydata.node import read_config
from allmydata.util.iputil import allocate_tcp_port

# No reason for HTTP requests to take longer than four minutes in the
# integration tests. See allmydata/scripts/common_http.py for usage.
os.environ["__TAHOE_CLI_HTTP_TIMEOUT"] = "240"

# Make Foolscap logging go into Twisted logging, so that integration test logs
# include extra information
# (https://github.com/warner/foolscap/blob/latest-release/doc/logging.rst):
os.environ["FLOGTOTWISTED"] = "1"

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
    parser.addoption(
        "--force-foolscap", action="store_true", default=False,
        dest="force_foolscap",
        help=("If set, force Foolscap only for the storage protocol. " +
              "Otherwise HTTP will be used.")
    )
    parser.addoption(
        "--runslow", action="store_true", default=False,
        dest="runslow",
        help="If set, run tests marked as slow.",
    )

def pytest_collection_modifyitems(session, config, items):
    if not config.option.runslow:
        # The --runslow option was not given; keep only collected items not
        # marked as slow.
        items[:] = [
            item
            for item
            in items
            if item.get_closest_marker("slow") is None
        ]


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
    # these will appear basically random, which can make especially
    # manual debugging harder but we're re-using code instead of
    # writing our own...so, win?
    def allocate():
        port = allocate_tcp_port()
        return succeed(port)
    return allocate


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:temp_dir", include_args=[])
def temp_dir(request) -> str:
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
    """
    Provides a new Grid with a single Introducer and flog-gathering process.

    Notably does _not_ provide storage servers; use the storage_nodes
    fixture if your tests need a Grid that can be used for puts / gets.
    """
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


@pytest.fixture
@log_call(
    action_type=u"integration:tor:introducer",
    include_args=["temp_dir", "flog_gatherer"],
    include_result=False,
)
def tor_introducer(reactor, temp_dir, flog_gatherer, request, tor_network):
    intro_dir = join(temp_dir, 'introducer_tor')
    print("making Tor introducer in {}".format(intro_dir))
    print("(this can take tens of seconds to allocate Onion address)")

    if not exists(intro_dir):
        mkdir(intro_dir)
        done_proto = _ProcessExitedProtocol()
        _tahoe_runner_optional_coverage(
            done_proto,
            reactor,
            request,
            (
                'create-introducer',
                '--tor-control-port', tor_network.client_control_endpoint,
                '--hide-ip',
                '--listen=tor',
                intro_dir,
            ),
        )
        pytest_twisted.blockon(done_proto.done)

    # adjust a few settings
    config = read_config(intro_dir, "tub.port")
    config.set_config("node", "nickname", "introducer-tor")
    config.set_config("node", "web.port", "4561")
    config.set_config("node", "log_gatherer.furl", flog_gatherer.furl)

    # "tahoe run" is consistent across Linux/macOS/Windows, unlike the old
    # "start" command.
    protocol = _MagicTextProtocol('introducer running', "tor_introducer")
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
            block_with_timeout(protocol.exited, reactor)
        except ProcessExitedAlready:
            pass
    request.addfinalizer(cleanup)

    print("Waiting for introducer to be ready...")
    pytest_twisted.blockon(protocol.magic_seen)
    print("Introducer ready.")
    return transport


@pytest.fixture
def tor_introducer_furl(tor_introducer, temp_dir):
    furl_fname = join(temp_dir, 'introducer_tor', 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        sleep(.1)
    furl = open(furl_fname, 'r').read()
    print(f"Found Tor introducer furl: {furl} in {furl_fname}")
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
        nodes_d.append(grid.add_storage_node())

    nodes_status = pytest_twisted.blockon(DeferredList(nodes_d))
    for ok, value in nodes_status:
        assert ok, "Storage node creation failed: {}".format(value)
    return grid.storage_servers


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:alice", include_args=[], include_result=False)
def alice(reactor, request, grid, storage_nodes):
    """
    :returns grid.Client: the associated instance for Alice
    """
    alice = pytest_twisted.blockon(grid.add_client("alice"))
    pytest_twisted.blockon(alice.add_sftp(reactor, request))
    print(f"Alice pid: {alice.process.transport.pid}")
    return alice


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
    pytest_twisted.blockon(await_client_ready(process))
    return process


@pytest.fixture(scope='session')
@pytest.mark.skipif(sys.platform.startswith('win'),
                    'Tor tests are unstable on Windows')
def chutney(reactor, temp_dir: str) -> tuple[str, dict[str, str]]:
    """
    Install the Chutney software that is required to run a small local Tor grid.

    (Chutney lacks the normal "python stuff" so we can't just declare
    it in Tox or similar dependencies)
    """
    # Try to find Chutney already installed in the environment.
    try:
        import chutney
    except ImportError:
        # Nope, we'll get our own in a moment.
        pass
    else:
        # We already have one, just use it.
        return (
            # from `checkout/lib/chutney/__init__.py` we want to get back to
            # `checkout` because that's the parent of the directory with all
            # of the network definitions.  So, great-grand-parent.
            FilePath(chutney.__file__).parent().parent().parent().path,
            # There's nothing to add to the environment.
            {},
        )

    chutney_dir = join(temp_dir, 'chutney')
    mkdir(chutney_dir)

    missing = [exe for exe in ["tor", "tor-gencert"] if not which(exe)]
    if missing:
        pytest.skip(f"Some command-line tools not found: {missing}")

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
            'git', 'clone',
            'https://gitlab.torproject.org/tpo/core/chutney.git',
            chutney_dir,
        ),
        env=environ,
    )
    pytest_twisted.blockon(proto.done)

    # XXX: Here we reset Chutney to a specific revision known to work,
    # since there are no stability guarantees or releases yet.
    proto = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        'git',
        (
            'git', '-C', chutney_dir,
            'reset', '--hard',
            'c4f6789ad2558dcbfeb7d024c6481d8112bfb6c2'
        ),
        env=environ,
    )
    pytest_twisted.blockon(proto.done)

    return chutney_dir, {"PYTHONPATH": join(chutney_dir, "lib")}


@frozen
class ChutneyTorNetwork:
    """
    Represents a running Chutney (tor) network. Returned by the
    "tor_network" fixture.
    """
    dir: FilePath
    environ: Mapping[str, str]
    client_control_port: int

    @property
    def client_control_endpoint(self) -> str:
        return "tcp:localhost:{}".format(self.client_control_port)


@pytest.fixture(scope='session')
@pytest.mark.skipif(sys.platform.startswith('win'),
                    reason='Tor tests are unstable on Windows')
def tor_network(reactor, temp_dir, chutney, request):
    """
    Build a basic Tor network.

    Instantiate the "networks/basic" Chutney configuration for a local
    Tor network.

    This provides a small, local Tor network that can run v3 Onion
    Services. It has 3 authorities, 5 relays and 2 clients.

    The 'chutney' fixture pins a Chutney git qrevision, so things
    shouldn't change. This network has two clients which are the only
    nodes with valid SocksPort configuration ("008c" and "009c" 9008
    and 9009)

    The control ports start at 8000 (so the ControlPort for the client
    nodes are 8008 and 8009).

    :param chutney: The root directory of a Chutney checkout and a dict of
        additional environment variables to set so a Python process can use
        it.

    :return: None
    """
    chutney_root, chutney_env = chutney
    basic_network = join(chutney_root, 'networks', 'basic')

    env = environ.copy()
    env.update(chutney_env)
    env.update({
        # default is 60, probably too short for reliable automated use.
        "CHUTNEY_START_TIME": "600",
    })
    chutney_argv = (sys.executable, '-m', 'chutney.TorNet')
    def chutney(argv):
        proto = _DumpOutputProtocol(None)
        reactor.spawnProcess(
            proto,
            sys.executable,
            chutney_argv + argv,
            path=join(chutney_root),
            env=env,
        )
        return proto.done

    # now, as per Chutney's README, we have to create the network
    pytest_twisted.blockon(chutney(("configure", basic_network)))

    # before we start the network, ensure we will tear down at the end
    def cleanup():
        print("Tearing down Chutney Tor network")
        try:
            block_with_timeout(chutney(("stop", basic_network)), reactor)
        except ProcessTerminated:
            # If this doesn't exit cleanly, that's fine, that shouldn't fail
            # the test suite.
            pass
    request.addfinalizer(cleanup)

    pytest_twisted.blockon(chutney(("start", basic_network)))

    # Wait for the nodes to "bootstrap" - ie, form a network among themselves.
    # Successful bootstrap is reported with a message something like:
    #
    # Everything bootstrapped after 151 sec
    # Bootstrap finished: 151 seconds
    # Node status:
    # test000a     :  100, done                     , Done
    # test001a     :  100, done                     , Done
    # test002a     :  100, done                     , Done
    # test003r     :  100, done                     , Done
    # test004r     :  100, done                     , Done
    # test005r     :  100, done                     , Done
    # test006r     :  100, done                     , Done
    # test007r     :  100, done                     , Done
    # test008c     :  100, done                     , Done
    # test009c     :  100, done                     , Done
    # Published dir info:
    # test000a     :  100, all nodes                , desc md md_cons ns_cons       , Dir info cached
    # test001a     :  100, all nodes                , desc md md_cons ns_cons       , Dir info cached
    # test002a     :  100, all nodes                , desc md md_cons ns_cons       , Dir info cached
    # test003r     :  100, all nodes                , desc md md_cons ns_cons       , Dir info cached
    # test004r     :  100, all nodes                , desc md md_cons ns_cons       , Dir info cached
    # test005r     :  100, all nodes                , desc md md_cons ns_cons       , Dir info cached
    # test006r     :  100, all nodes                , desc md md_cons ns_cons       , Dir info cached
    # test007r     :  100, all nodes                , desc md md_cons ns_cons       , Dir info cached
    pytest_twisted.blockon(chutney(("wait_for_bootstrap", basic_network)))

    # print some useful stuff
    try:
        pytest_twisted.blockon(chutney(("status", basic_network)))
    except ProcessTerminated:
        print("Chutney.TorNet status failed (continuing)")

    # the "8008" comes from configuring "networks/basic" in chutney
    # and then examining "net/nodes/008c/torrc" for ControlPort value
    return ChutneyTorNetwork(
        chutney_root,
        chutney_env,
        8008,
    )
