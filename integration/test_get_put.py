import sys
import time
import shutil
from os import mkdir, unlink, listdir, environ
from os.path import join, exists
from twisted.internet import defer

import util

import pytest


@pytest.inlineCallbacks
def test_put_get_alias(reactor, alice):
    output = yield util.run_tahoe(reactor, alice, 'create-alias', 'get0:')
    assert "Alias 'get0' created" in output

    with open('some_content.txt', 'w') as f:
        f.write(
            'here is some content\n'
        )
    output = yield util.run_tahoe(reactor, alice, 'put', 'some_content.txt', 'get0:foo')
    assert 'URI:LIT:' in output

    contents = yield util.run_tahoe(reactor, alice, 'get', 'get0:foo')
    assert contents.strip() == 'here is some content'

@pytest.inlineCallbacks
def test_put_progress(reactor, alice):
    from .util import _CollectOutputProtocol
    protocol = _CollectOutputProtocol()
    half_writing = defer.Deferred()
    half_writing_continue = defer.Deferred()
    done_writing = defer.Deferred()

    def write_data_to_stdin():
        chunk = 'a' * 1024

        def do_one_chunk(count):
            print("write {}".format(count))
            if count == 512:
                half_writing.callback(None)
                half_writing_continue.addCallback(
                    lambda _: do_one_chunk, count - 1
                )
            if count == 0:
                print("closing stdin")
                protocol.transport.closeStdin()
                done_writing.callback(None)
            else:
                protocol.transport.write(chunk)
                reactor.callLater(0, do_one_chunk, count - 1)
        reactor.callLater(2, do_one_chunk, 1024)

    protocol.connectionMade = write_data_to_stdin
    process = reactor.spawnProcess(
        protocol,
        sys.executable,
        (
            sys.executable, '-m', 'allmydata.scripts.runner',
            '-d', alice._node_dir,
            'put', '-',
        ),
        env=environ,
    )
    yield half_writing
    print("half done writing")
    output0 = yield util.run_tahoe(reactor, alice, 'status')
    output1 = yield util.run_tahoe(reactor, alice, 'status', '--verbose')
    half_writing_continue.callback(None)
    yield done_writing
    print("done writing")

    print(output0)
    print("----")
    print(output1)
    output2 = yield protocol.done
    print(output2)
    cap = output2.split('\n')[-2].strip()
    print("CAP '{}'".format(cap))
    assert cap.startswith('URI:CHK:')

    # now download this cap with "tahoe get"
    output = yield util.run_tahoe(reactor, alice, 'get', cap)
    assert len(output) >= 1024 * 1024
    assert output[:10] == 'a' * 10
