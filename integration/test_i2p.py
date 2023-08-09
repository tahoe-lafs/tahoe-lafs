"""
Integration tests for I2P support.
"""

import sys
from os.path import join, exists
from os import mkdir, environ
from time import sleep
from shutil import which

from eliot import log_call

import pytest
import pytest_twisted

from . import util

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.error import ProcessExitedAlready

from allmydata.test.common import (
    write_introducer,
)
from allmydata.node import read_config
from allmydata.util.iputil import allocate_tcp_port


if which("docker") is None:
    pytest.skip('Skipping I2P tests since Docker is unavailable', allow_module_level=True)
# Docker on Windows machines sometimes expects Windows-y Docker images, so just
# don't bother.
if sys.platform.startswith('win'):
    pytest.skip('Skipping I2P tests on Windows', allow_module_level=True)


@pytest.fixture
def i2p_network(reactor, temp_dir, request):
    """Fixture to start up local i2pd."""
    proto = util._MagicTextProtocol("ephemeral keys", "i2pd")
    reactor.spawnProcess(
        proto,
        which("docker"),
        (
            "docker", "run", "-p", "7656:7656", "purplei2p/i2pd:release-2.45.1",
            # Bad URL for reseeds, so it can't talk to other routers.
            "--reseed.urls", "http://localhost:1/",
            # Make sure we see the "ephemeral keys message"
            "--log=stdout",
            "--loglevel=info"
        ),
        env=environ,
    )

    def cleanup():
        try:
            proto.transport.signalProcess("INT")
            util.block_with_timeout(proto.exited, reactor)
        except ProcessExitedAlready:
            pass
    request.addfinalizer(cleanup)

    util.block_with_timeout(proto.magic_seen, reactor, timeout=30)


@pytest.fixture
@log_call(
    action_type=u"integration:i2p:introducer",
    include_args=["temp_dir", "flog_gatherer"],
    include_result=False,
)
def i2p_introducer(reactor, temp_dir, flog_gatherer, request):
    intro_dir = join(temp_dir, 'introducer_i2p')
    print("making introducer", intro_dir)

    if not exists(intro_dir):
        mkdir(intro_dir)
        done_proto = util._ProcessExitedProtocol()
        util._tahoe_runner_optional_coverage(
            done_proto,
            reactor,
            request,
            (
                'create-introducer',
                '--listen=i2p',
                intro_dir,
            ),
        )
        pytest_twisted.blockon(done_proto.done)

    # over-write the config file with our stuff
    config = read_config(intro_dir, "tub.port")
    config.set_config("node", "nickname", "introducer_i2p")
    config.set_config("node", "web.port", "4563")
    config.set_config("node", "log_gatherer.furl", flog_gatherer)

    # "tahoe run" is consistent across Linux/macOS/Windows, unlike the old
    # "start" command.
    protocol = util._MagicTextProtocol('introducer running', "introducer")
    transport = util._tahoe_runner_optional_coverage(
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
            util.block_with_timeout(protocol.exited, reactor)
        except ProcessExitedAlready:
            pass
    request.addfinalizer(cleanup)

    pytest_twisted.blockon(protocol.magic_seen)
    return transport


@pytest.fixture
def i2p_introducer_furl(i2p_introducer, temp_dir):
    furl_fname = join(temp_dir, 'introducer_i2p', 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        sleep(.1)
    furl = open(furl_fname, 'r').read()
    return furl


@pytest_twisted.inlineCallbacks
@pytest.mark.skip("I2P tests are not functioning at all, for unknown reasons")
def test_i2p_service_storage(reactor, request, temp_dir, flog_gatherer, i2p_network, i2p_introducer_furl):
    web_port0 = allocate_tcp_port()
    web_port1 = allocate_tcp_port()
    yield _create_anonymous_node(reactor, 'carol_i2p', web_port0, request, temp_dir, flog_gatherer, i2p_network, i2p_introducer_furl)
    yield _create_anonymous_node(reactor, 'dave_i2p', web_port1, request, temp_dir, flog_gatherer, i2p_network, i2p_introducer_furl)
    # ensure both nodes are connected to "a grid" by uploading
    # something via carol, and retrieve it using dave.
    gold_path = join(temp_dir, "gold")
    with open(gold_path, "w") as f:
        f.write(
            "The object-capability model is a computer security model. A "
            "capability describes a transferable right to perform one (or "
            "more) operations on a given object."
        )
    # XXX could use treq or similar to POST these to their respective
    # WUIs instead ...

    proto = util._CollectOutputProtocol()
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-b', '-m', 'allmydata.scripts.runner',
            '-d', join(temp_dir, 'carol_i2p'),
            'put', gold_path,
        ),
        env=environ,
    )
    yield proto.done
    cap = proto.output.getvalue().strip().split()[-1]
    print("TEH CAP!", cap)

    proto = util._CollectOutputProtocol(capture_stderr=False)
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-b', '-m', 'allmydata.scripts.runner',
            '-d', join(temp_dir, 'dave_i2p'),
            'get', cap,
        ),
        env=environ,
    )
    yield proto.done

    dave_got = proto.output.getvalue().strip()
    assert dave_got == open(gold_path, 'rb').read().strip()


@pytest_twisted.inlineCallbacks
def _create_anonymous_node(reactor, name, web_port, request, temp_dir, flog_gatherer, i2p_network, introducer_furl):
    node_dir = FilePath(temp_dir).child(name)

    print("creating", node_dir.path)
    node_dir.makedirs()
    proto = util._DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-b', '-m', 'allmydata.scripts.runner',
            'create-node',
            '--nickname', name,
            '--introducer', introducer_furl,
            '--hide-ip',
            '--listen', 'i2p',
            node_dir.path,
        ),
        env=environ,
    )
    yield proto.done


    # Which services should this client connect to?
    write_introducer(node_dir, "default", introducer_furl)
    with node_dir.child('tahoe.cfg').open('w') as f:
        node_config = '''
[node]
nickname = %(name)s
web.port = %(web_port)s
web.static = public_html
log_gatherer.furl = %(log_furl)s

[i2p]
enabled = true

[client]
shares.needed = 1
shares.happy = 1
shares.total = 2

''' % {
    'name': name,
    'web_port': web_port,
    'log_furl': flog_gatherer,
}
        node_config = node_config.encode("utf-8")
        f.write(node_config)

    print("running")
    yield util._run_node(reactor, node_dir.path, request, None)
    print("okay, launched")
