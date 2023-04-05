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

# see "conftest.py" for the fixtures (e.g. "tor_network")

# XXX: Integration tests that involve Tor do not run reliably on
# Windows.  They are skipped for now, in order to reduce CI noise.
#
# https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3347
if sys.platform.startswith('win'):
    pytest.skip('Skipping Tor tests on Windows', allow_module_level=True)

@pytest_twisted.inlineCallbacks
def test_onion_service_storage(reactor, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl):
    carol = yield _create_anonymous_node(reactor, 'carol', 8008, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl)
    dave = yield _create_anonymous_node(reactor, 'dave', 8009, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl)
    yield util.await_client_ready(carol, minimum_number_of_servers=2, timeout=60)
    yield util.await_client_ready(dave, minimum_number_of_servers=2, timeout=60)

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
            '-d', join(temp_dir, 'carol'),
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
            '-d', join(temp_dir, 'dave'),
            'get', cap,
        ),
        env=environ,
    )
    yield proto.done

    dave_got = proto.output.getvalue().strip()
    assert dave_got == open(gold_path, 'rb').read().strip()


@pytest_twisted.inlineCallbacks
def _create_anonymous_node(reactor, name, control_port, request, temp_dir, flog_gatherer, tor_network, introducer_furl):
    node_dir = FilePath(temp_dir).child(name)
    web_port = "tcp:{}:interface=localhost".format(control_port + 2000)

    if True:
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
                '--webport', web_port,
                '--introducer', introducer_furl,
                '--hide-ip',
                '--tor-control-port', 'tcp:localhost:{}'.format(control_port),
                '--listen', 'tor',
                '--shares-needed', '1',
                '--shares-happy', '1',
                '--shares-total', '2',
                node_dir.path,
            ),
            env=environ,
        )
        yield proto.done


    # Which services should this client connect to?
    write_introducer(node_dir, "default", introducer_furl)

    config = read_config(node_dir.path, "tub.port")
    config.set_config("node", "log_gatherer.furl", flog_gatherer)
    config.set_config("tor", "onion", "true")
    config.set_config("tor", "onion.external_port", "3457")
    config.set_config("tor", "control.port", f"tcp:port={control_port}:host=127.0.0.1")
    #config.set_config("tor", "launch", "True")
    config.set_config("tor", "onion.local_port", str(control_port + 1000))
    config.set_config("tor", "onion.private_key_file", "private/tor_onion.privkey")

    print("running")
    result = yield util._run_node(reactor, node_dir.path, request, None)
    print("okay, launched")
    return result
