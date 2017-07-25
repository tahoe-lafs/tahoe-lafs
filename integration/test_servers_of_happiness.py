from re import search
import sys
import time
import shutil
from os import mkdir, unlink, listdir
from os.path import join, exists

from twisted.internet import defer, reactor, task
from twisted.internet.error import ProcessTerminated

import treq

import util

import pytest


@pytest.inlineCallbacks
def test_upload_immutable(reactor, temp_dir, introducer_furl, flog_gatherer, storage_nodes, request):

    # hmm, for some reason this still gets storage enabled ...
    process = yield util._create_node(
        reactor, request, temp_dir, introducer_furl, flog_gatherer, "edna",
        web_port="tcp:9983:interface=localhost",
        storage=False,
        needed=3,
        happy=10,
        total=10,
    )


    node_dir = join(temp_dir, 'edna')

    print("scraping introducer html")
    while True:
        resp = yield treq.get("http://localhost:9983/")
        content = yield treq.text_content(resp)
        match = search(r"Connected to <span>(\d+)</span>", content)
        if match is not None and int(match.group(1)) > 0:
            break
        print("apparently not connected to a storage server")
        yield task.deferLater(reactor, 1, lambda: None)

    # upload a file, which should fail because we have don't have 7
    # storage servers (but happiness is set to 7)
    proto = util._CollectOutputProtocol()
    transport = reactor.spawnProcess(
        proto,
        sys.executable,
        [
            sys.executable, '-m', 'allmydata.scripts.runner',
            '-d', node_dir,
            'put', __file__,
        ]
    )
    try:
        yield proto.done
        assert False, "should raise exception"
    except Exception as e:
        assert isinstance(e, ProcessTerminated)

    output = proto.output.getvalue()
    assert "shares could be placed on only" in output
