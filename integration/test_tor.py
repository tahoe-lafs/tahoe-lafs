"""
Ported to Python 3.
"""

import sys
from os.path import join
from os import environ

import pytest
import pytest_twisted

from . import util

from twisted.python.filepath import (
    FilePath,
)

from allmydata.test.common import (
    write_introducer,
)
from allmydata.client import read_config
from allmydata.util.deferredutil import async_to_deferred

# see "conftest.py" for the fixtures (e.g. "tor_network")

# XXX: Integration tests that involve Tor do not run reliably on
# Windows.  They are skipped for now, in order to reduce CI noise.
#
# https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3347
if sys.platform.startswith('win'):
    pytest.skip('Skipping Tor tests on Windows', allow_module_level=True)

@pytest.mark.skipif(sys.version_info[:2] > (3, 11), reason='Chutney still does not support 3.12')
@pytest_twisted.inlineCallbacks
def test_onion_service_storage(reactor, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl):
    """
    Two nodes and an introducer all configured to use Tahoe.

    The two nodes can talk to the introducer and each other: we upload to one
    node, read from the other.
    """
    carol = yield _create_anonymous_node(reactor, 'carol', 8100, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl, 2)
    dave = yield _create_anonymous_node(reactor, 'dave', 8101, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl, 2)
    yield util.await_client_ready(carol, minimum_number_of_servers=2, timeout=600)
    yield util.await_client_ready(dave, minimum_number_of_servers=2, timeout=600)
    yield upload_to_one_download_from_the_other(reactor, temp_dir, carol, dave)


@async_to_deferred
async def upload_to_one_download_from_the_other(reactor, temp_dir, upload_to: util.TahoeProcess, download_from: util.TahoeProcess):
    """
    Ensure both nodes are connected to "a grid" by uploading something via one
    node, and retrieve it using the other.
    """

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
            '-d', upload_to.node_dir,
            'put', gold_path,
        ),
        env=environ,
    )
    await proto.done
    cap = proto.output.getvalue().strip().split()[-1]
    print("capability: {}".format(cap))

    proto = util._CollectOutputProtocol(capture_stderr=False)
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-b', '-m', 'allmydata.scripts.runner',
            '-d', download_from.node_dir,
            'get', cap,
        ),
        env=environ,
    )
    await proto.done
    download_got = proto.output.getvalue().strip()
    assert download_got == open(gold_path, 'rb').read().strip()


@pytest_twisted.inlineCallbacks
def _create_anonymous_node(reactor, name, web_port, request, temp_dir, flog_gatherer, tor_network, introducer_furl, shares_total: int) -> util.TahoeProcess:
    node_dir = FilePath(temp_dir).child(name)
    if node_dir.exists():
        raise RuntimeError(
            "A node already exists in '{}'".format(node_dir)
        )
    print(f"creating {node_dir.path} with introducer {introducer_furl}")
    node_dir.makedirs()
    proto = util._DumpOutputProtocol(None)
    reactor.spawnProcess(
        proto,
        sys.executable,
        (
            sys.executable, '-b', '-m', 'allmydata.scripts.runner',
            'create-node',
            '--nickname', name,
            '--webport', str(web_port),
            '--introducer', introducer_furl,
            '--hide-ip',
            '--tor-control-port', tor_network.client_control_endpoint,
            '--listen', 'tor',
            '--shares-needed', '1',
            '--shares-happy', '1',
            '--shares-total', str(shares_total),
            node_dir.path,
        ),
        env=environ,
        )
    yield proto.done


    # Which services should this client connect to?
    write_introducer(node_dir, "default", introducer_furl)
    util.basic_node_configuration(request, flog_gatherer.furl, node_dir.path)

    config = read_config(node_dir.path, "tub.port")
    config.set_config("tor", "onion", "true")
    config.set_config("tor", "onion.external_port", "3457")
    config.set_config("tor", "control.port", tor_network.client_control_endpoint)
    config.set_config("tor", "onion.private_key_file", "private/tor_onion.privkey")

    print("running")
    result = yield util._run_node(reactor, node_dir.path, request, None)
    print("okay, launched")
    return result

@pytest.mark.skipif(sys.version_info[:2] > (3, 11), reason='Chutney still does not support 3.12')
@pytest.mark.skipif(sys.platform.startswith('darwin'), reason='This test has issues on macOS')
@pytest_twisted.inlineCallbacks
def test_anonymous_client(reactor, request, temp_dir, flog_gatherer, tor_network, introducer_furl):
    """
    A normal node (normie) and a normal introducer are configured, and one node
    (anonymoose) which is configured to be anonymous by talking via Tor.

    Anonymoose should be able to communicate with normie.

    TODO how to ensure that anonymoose is actually using Tor?
    """
    normie = yield util._create_node(
        reactor, request, temp_dir, introducer_furl, flog_gatherer, "normie",
        web_port="tcp:9989:interface=localhost",
        storage=True, needed=1, happy=1, total=1,
    )
    yield util.await_client_ready(normie)

    anonymoose = yield _create_anonymous_node(reactor, 'anonymoose', 8102, request, temp_dir, flog_gatherer, tor_network, introducer_furl, 1)
    yield util.await_client_ready(anonymoose, minimum_number_of_servers=1, timeout=1200)

    yield upload_to_one_download_from_the_other(reactor, temp_dir, normie, anonymoose)
