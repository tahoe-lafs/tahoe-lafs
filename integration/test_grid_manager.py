import sys
import time
import json
import shutil
from os import mkdir, unlink, listdir, utime
from os.path import join, exists, getmtime

from allmydata.util import keyutil
from allmydata.util import base32

import util

import pytest


@pytest.inlineCallbacks
def test_create_certificate(reactor):
    gm_config = yield util.cli(
        reactor, "grid-manager", "--config", "-", "create",
    )
    privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    privkey, pubkey_bytes = keyutil.parse_privkey(privkey_bytes)
    pubkey = keyutil.parse_pubkey(pubkey_bytes)

    gm_config = yield util.cli(
        reactor, "grid-manager", "--config", "-", "add",
        "alice", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    alice_cert_bytes = yield util.cli(
        reactor, "grid-manager", "--config", "-", "sign", "alice",
        stdin=gm_config,
    )
    alice_cert = json.loads(alice_cert_bytes)

    # confirm that alice's certificate is made by the Grid Manager
    assert pubkey.verify(
        base32.a2b(alice_cert['signature'].encode('ascii')),
        alice_cert['certificate'].encode('ascii'),
    )


@pytest.inlineCallbacks
def test_remove_client(reactor):
    gm_config = yield util.cli(
        reactor, "grid-manager", "--config", "-", "create",
    )

    gm_config = yield util.cli(
        reactor, "grid-manager", "--config", "-", "add",
        "alice", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    assert json.loads(gm_config)['storage_servers'].has_key("alice")

    gm_config = yield util.cli(
        reactor, "grid-manager", "--config", "-", "remove",
        "alice",
        stdin=gm_config,
    )
    # there are no storage servers left at all now
    assert not json.loads(gm_config).has_key('storage_servers')
