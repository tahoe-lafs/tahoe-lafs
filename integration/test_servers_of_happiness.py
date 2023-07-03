"""
Ported to Python 3.
"""

import sys
from os.path import join
from os import environ

from . import util

import pytest_twisted


@pytest_twisted.inlineCallbacks
def test_upload_immutable(reactor, temp_dir, introducer_furl, flog_gatherer, storage_nodes, request):

    edna = yield util._create_node(
        reactor, request, temp_dir, introducer_furl, flog_gatherer, "edna",
        web_port="tcp:9983:interface=localhost",
        storage=False,
        needed=3,
        happy=7,
        total=10,
    )
    yield util.await_client_ready(edna)

    node_dir = join(temp_dir, 'edna')

    # upload a file, which should fail because we have don't have 7
    # storage servers (but happiness is set to 7)
    proto = util._CollectOutputProtocol()
    reactor.spawnProcess(
        proto,
        sys.executable,
        [
            sys.executable, '-b', '-m', 'allmydata.scripts.runner',
            '-d', node_dir,
            'put', __file__,
        ],
        env=environ,
    )
    try:
        yield proto.done
        assert False, "should raise exception"
    except util.ProcessFailed as e:
        assert b"UploadUnhappinessError" in e.output

    output = proto.output.getvalue()
    assert b"shares could be placed on only" in output
