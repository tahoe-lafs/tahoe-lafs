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
from os.path import join

import pytest
import pytest_twisted

from . import util

from twisted.python.filepath import (
    FilePath,
)

from allmydata.test.common import (
    write_introducer,
)

# see "conftest.py" for the fixtures (e.g. "tor_network")

# XXX: Integration tests that involve Tor do not run reliably on
# Windows.  They are skipped for now, in order to reduce CI noise.
#
# https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3347
if sys.platform.startswith('win'):
    pytest.skip('Skipping Tor tests on Windows', allow_module_level=True)

@pytest_twisted.inlineCallbacks
def test_onion_service_storage(reactor, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl):
    yield _create_anonymous_node(reactor, 'carol', 8008, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl)
    yield _create_anonymous_node(reactor, 'dave', 8009, request, temp_dir, flog_gatherer, tor_network, tor_introducer_furl)
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
        )
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
        )
    )
    yield proto.done

    dave_got = proto.output.getvalue().strip()
    assert dave_got == open(gold_path, 'rb').read().strip()


@pytest_twisted.inlineCallbacks
def _create_anonymous_node(reactor, name, control_port, request, temp_dir, flog_gatherer, tor_network, introducer_furl):
    node_dir = FilePath(temp_dir).child(name)
    web_port = "tcp:{}:interface=localhost".format(control_port + 2000)

    if True:
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
                '--tor-control-port', 'tcp:localhost:{}'.format(control_port),
                '--listen', 'tor',
                node_dir.path,
            )
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

[tor]
control.port = tcp:localhost:%(control_port)d
onion.external_port = 3457
onion.local_port = %(local_port)d
onion = true
onion.private_key_file = private/tor_onion.privkey

[client]
shares.needed = 1
shares.happy = 1
shares.total = 2

''' % {
    'name': name,
    'web_port': web_port,
    'log_furl': flog_gatherer,
    'control_port': control_port,
    'local_port': control_port + 1000,
}
        node_config = node_config.encode("utf-8")
        f.write(node_config)

    print("running")
    yield util._run_node(reactor, node_dir.path, request, None)
    print("okay, launched")
