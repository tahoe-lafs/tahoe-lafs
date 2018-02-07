import sys
import time
import shutil
from os import mkdir, unlink, listdir, environ
from os.path import join, exists
from twisted.internet.utils import getProcessOutput

import util

import pytest


@pytest.inlineCallbacks
def test_alias_create_list(reactor, alice):
    output = yield util.run_tahoe(reactor, alice, 'create-alias', 'alias0:')
    assert "Alias 'alias0' created" in output

    output = yield util.run_tahoe(reactor, alice, 'list-aliases')
    assert "alias0:" in output

    output = yield util.run_tahoe(reactor, alice, 'list-aliases', '--readonly-uri')
    assert "alias0:" in output


@pytest.inlineCallbacks
def test_alias_add(reactor, alice):
    output = yield util.run_tahoe(reactor, alice, 'mkdir')
    dircap = output.strip()
    print("created dircap: {}".format(dircap))

    output = yield util.run_tahoe(reactor, alice, 'add-alias', 'alias1:', dircap)
    assert "Alias 'alias1' added" in output
