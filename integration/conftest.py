"""
Ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import sys
import shutil
from time import sleep
from os import mkdir, listdir, environ
from os.path import join, exists
from tempfile import mkdtemp, mktemp
from functools import partial
from json import loads

from foolscap.furl import (
    decode_furl,
)

from eliot import (
    to_file,
    log_call,
)

from twisted.python.procutils import which
from twisted.internet.defer import DeferredList
from twisted.internet.error import (
    ProcessExitedAlready,
    ProcessTerminated,
)

import pytest
import pytest_twisted

from .util import (
    _CollectOutputProtocol,
    _MagicTextProtocol,
    _DumpOutputProtocol,
    _ProcessExitedProtocol,
    _create_node,
    _cleanup_tahoe_process,
    _tahoe_runner_optional_coverage,
    await_client_ready,
    TahoeProcess,
    cli,
    _run_node,
    generate_ssh_key,
    block_with_timeout,
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
    out_protocol = _CollectOutputProtocol()
    gather_dir = join(temp_dir, 'flog_gather')
    reactor.spawnProcess(
        out_protocol,
        flog_binary,
        (
            'flogtool', 'create-gatherer',
            '--location', 'tcp:localhost:3117',
            '--port', '3117',
            gather_dir,
        )
    )
    pytest_twisted.blockon(out_protocol.done)

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
    pytest_twisted.blockon(twistd_protocol.magic_seen)

    def cleanup():
        _cleanup_tahoe_process(twistd_process, twistd_protocol.exited)

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
        print("Waiting for flogtool to complete")
        try:
            block_with_timeout(flog_protocol.done, reactor)
        except ProcessTerminated as e:
            print("flogtool exited unexpectedly: {}".format(str(e)))
        print("Flogtool completed")

    request.addfinalizer(cleanup)

    with open(join(gather_dir, 'log_gatherer.furl'), 'r') as f:
        furl = f.read().strip()
    return furl


@pytest.fixture(scope='session')
@log_call(
    action_type=u"integration:introducer",
    include_args=["temp_dir", "flog_gatherer"],
    include_result=False,
)
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
        _tahoe_runner_optional_coverage(
            done_proto,
            reactor,
            request,
            (
                'create-introducer',
                '--listen=tcp',
                '--hostname=localhost',
                intro_dir,
            ),
        )
        pytest_twisted.blockon(done_proto.done)

    # over-write the config file with our stuff
    with open(join(intro_dir, 'tahoe.cfg'), 'w') as f:
        f.write(config)

    # "tahoe run" is consistent across Linux/macOS/Windows, unlike the old
    # "start" command.
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
    request.addfinalizer(partial(_cleanup_tahoe_process, transport, protocol.exited))

    pytest_twisted.blockon(protocol.magic_seen)
    return TahoeProcess(transport, intro_dir)


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:introducer:furl", include_args=["temp_dir"])
def introducer_furl(introducer, temp_dir):
    furl_fname = join(temp_dir, 'introducer', 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        sleep(.1)
    furl = open(furl_fname, 'r').read()
    tubID, location_hints, name = decode_furl(furl)
    if not location_hints:
        # If there are no location hints then nothing can ever possibly
        # connect to it and the only thing that can happen next is something
        # will hang or time out.  So just give up right now.
        raise ValueError(
            "Introducer ({!r}) fURL has no location hints!".format(
                introducer_furl,
            ),
        )
    return furl


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

    # "tahoe run" is consistent across Linux/macOS/Windows, unlike the old
    # "start" command.
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
            block_with_timeout(protocol.exited, reactor)
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
    include_args=["temp_dir", "introducer_furl", "flog_gatherer"],
    include_result=False,
)
def storage_nodes(reactor, temp_dir, introducer, introducer_furl, flog_gatherer, request):
    nodes_d = []
    # start all 5 nodes in parallel
    for x in range(5):
        name = 'node{}'.format(x)
        web_port=  9990 + x
        nodes_d.append(
            _create_node(
                reactor, request, temp_dir, introducer_furl, flog_gatherer, name,
                web_port="tcp:{}:interface=localhost".format(web_port),
                storage=True,
            )
        )
    nodes_status = pytest_twisted.blockon(DeferredList(nodes_d))
    nodes = []
    for ok, process in nodes_status:
        assert ok, "Storage node creation failed: {}".format(process)
        nodes.append(process)
    return nodes


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:alice", include_args=[], include_result=False)
def alice(reactor, temp_dir, introducer_furl, flog_gatherer, storage_nodes, request):
    process = pytest_twisted.blockon(
        _create_node(
            reactor, request, temp_dir, introducer_furl, flog_gatherer, "alice",
            web_port="tcp:9980:interface=localhost",
            storage=False,
            # We're going to kill this ourselves, so no need for finalizer to
            # do it:
            finalize=False,
        )
    )
    await_client_ready(process)

    # 1. Create a new RW directory cap:
    cli(process, "create-alias", "test")
    rwcap = loads(cli(process, "list-aliases", "--json"))["test"]["readwrite"]

    # 2. Enable SFTP on the node:
    host_ssh_key_path = join(process.node_dir, "private", "ssh_host_rsa_key")
    accounts_path = join(process.node_dir, "private", "accounts")
    with open(join(process.node_dir, "tahoe.cfg"), "a") as f:
        f.write("""\
[sftpd]
enabled = true
port = tcp:8022:interface=127.0.0.1
host_pubkey_file = {ssh_key_path}.pub
host_privkey_file = {ssh_key_path}
accounts.file = {accounts_path}
""".format(ssh_key_path=host_ssh_key_path, accounts_path=accounts_path))
    generate_ssh_key(host_ssh_key_path)

    # 3. Add a SFTP access file with username/password and SSH key auth.

    # The client SSH key path is typically going to be somewhere else (~/.ssh,
    # typically), but for convenience sake for testing we'll put it inside node.
    client_ssh_key_path = join(process.node_dir, "private", "ssh_client_rsa_key")
    generate_ssh_key(client_ssh_key_path)
    # Pub key format is "ssh-rsa <thekey> <username>". We want the key.
    ssh_public_key = open(client_ssh_key_path + ".pub").read().strip().split()[1]
    with open(accounts_path, "w") as f:
        f.write("""\
alice password {rwcap}

alice2 ssh-rsa {ssh_public_key} {rwcap}
""".format(rwcap=rwcap, ssh_public_key=ssh_public_key))

    # 4. Restart the node with new SFTP config.
    process.kill()
    pytest_twisted.blockon(_run_node(reactor, process.node_dir, request, None))

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
@pytest.mark.skipif(sys.platform.startswith('win'),
                    'Tor tests are unstable on Windows')
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
            'git', 'clone',
            'https://git.torproject.org/chutney.git',
            chutney_dir,
        ),
        env=environ,
    )
    pytest_twisted.blockon(proto.done)

    # XXX: Here we reset Chutney to the last revision known to work
    # with Python 2, as a workaround for Chutney moving to Python 3.
    # When this is no longer necessary, we will have to drop this and
    # add '--depth=1' back to the above 'git clone' subprocess.
    proto = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        'git',
        (
            'git', '-C', chutney_dir,
            'reset', '--hard',
            '99bd06c7554b9113af8c0877b6eca4ceb95dcbaa'
        ),
        env=environ,
    )
    pytest_twisted.blockon(proto.done)

    return chutney_dir


@pytest.fixture(scope='session')
@pytest.mark.skipif(sys.platform.startswith('win'),
                    reason='Tor tests are unstable on Windows')
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
        try:
            block_with_timeout(proto.done, reactor)
        except ProcessTerminated:
            # If this doesn't exit cleanly, that's fine, that shouldn't fail
            # the test suite.
            pass

    request.addfinalizer(cleanup)

    return chut
