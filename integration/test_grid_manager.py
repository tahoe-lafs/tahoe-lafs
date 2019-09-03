import sys
import time
import json
import shutil
from os import mkdir, unlink, listdir, utime
from os.path import join, exists, getmtime

from allmydata.crypto import ed25519
from allmydata.util import base32
from allmydata.util import configutil
from allmydata.interfaces import UploadUnhappinessError

import util

import pytest_twisted


@pytest_twisted.inlineCallbacks
def test_create_certificate(request, reactor):
    gm_config = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "create",
    )
    privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    privkey, pubkey = ed25519.signing_keypair_from_string(privkey_bytes)

    gm_config = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "add",
        "alice", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    alice_cert_bytes = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "sign", "alice",
        stdin=gm_config,
    )
    alice_cert = json.loads(alice_cert_bytes)

    # confirm that alice's certificate is made by the Grid Manager
    # (verify_signature raises an exception if signature is invalid)
    ed25519.verify_signature(
        pubkey,
        base32.a2b(alice_cert['signature'].encode('ascii')),
        alice_cert['certificate'].encode('ascii'),
    )


@pytest_twisted.inlineCallbacks
def test_remove_client(request, reactor):
    gm_config = yield util.cli(
        request, reactor, "/dev/null", "grid-manager", "--config", "-", "create",
    )

    gm_config = yield util.cli(
        request, reactor, "/dev/null", "grid-manager", "--config", "-", "add",
        "alice", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    gm_config = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "add",
        "bob", "pub-v0-kvxhb3nexybmipkrar2ztfrwp4uxxsmrjzkpzafit3ket4u5yldq",
        stdin=gm_config,
    )
    assert json.loads(gm_config)['storage_servers'].has_key("alice")
    assert json.loads(gm_config)['storage_servers'].has_key("bob")
    return

    gm_config = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "remove",
        "alice",
        stdin=gm_config,
    )
    assert not json.loads(gm_config)['storage_servers'].has_key('alice')
    assert json.loads(gm_config)['storage_servers'].has_key('bob')


@pytest_twisted.inlineCallbacks
def test_remove_last_client(request, reactor):
    gm_config = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "create",
    )

    gm_config = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "add",
        "alice", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    assert json.loads(gm_config)['storage_servers'].has_key("alice")

    gm_config = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "remove",
        "alice",
        stdin=gm_config,
    )
    # there are no storage servers left at all now
    assert not json.loads(gm_config).has_key('storage_servers')


@pytest_twisted.inlineCallbacks
def test_reject_storage_server(reactor, request, storage_nodes, temp_dir, introducer_furl, flog_gatherer):
    gm_config = yield util.cli(
        request, reactor, "/dev/null",  "grid-manager", "--config", "-", "create",
    )
    privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    privkey, pubkey = ed25519.signing_keypair_from_string(privkey_bytes)
    print("config:\n{}".format(gm_config))

    # enroll first two storage-servers into the grid-manager
    for idx, storage in enumerate(storage_nodes[:2]):
        pubkey_fname = join(storage._node_dir, "node.pubkey")
        with open(pubkey_fname, 'r') as f:
            pubkey = f.read().strip()

        gm_config = yield util.cli(
            request, reactor, "/dev/null",  "grid-manager", "--config", "-", "add",
            "storage{}".format(idx), pubkey,
            stdin=gm_config,
        )
    assert sorted(json.loads(gm_config)['storage_servers'].keys()) == ['storage0', 'storage1']

    # XXX FIXME need to shut-down and nuke carol when we're done this
    # test (i.d. request.addfinalizer)
    carol = yield util._create_node(
        reactor, request, temp_dir, introducer_furl, flog_gatherer, "carol",
        web_port="tcp:9982:interface=localhost",
        storage=False,
    )

    # have the grid-manager sign certificates for the first two
    # storage-servers and insert them into the config
    for idx, storage in enumerate(storage_nodes[:2]):
        cert = yield util.cli(
            request, reactor, "/dev/null",  "grid-manager", "--config", "-", "sign",
            "storage{}".format(idx),
            stdin=gm_config,
        )
        with open(join(storage._node_dir, "gridmanager.cert"), "w") as f:
            f.write(cert)
        config = configutil.get_config(join(storage._node_dir, "tahoe.cfg"))
        config.set("storage", "grid_management", "True")
        config.add_section("grid_manager_certificates")
        config.set("grid_manager_certificates", "default", "gridmanager.cert")
        with open(join(storage._node_dir, "tahoe.cfg"), "w") as cfg_file:
            config.write(cfg_file)

        # re-start this one storage server
        storage.transport.signalProcess('TERM')
        yield storage.transport._protocol.exited
        storage_nodes[idx] = yield util._run_node(
            reactor, storage.node_dir, request, None,
        )

    # we still haven't added the grid-manager public key to Carol's
    # config, so with happy=3 she should be able to upload still
    yield util.cli(
        request, reactor, carol.node_dir,
        "put", "-",
        stdin="kwalitee content " * 50,
    )

    # now only two storage-servers have certificates .. configure
    # carol to have the grid-manager public key

    config = configutil.get_config(join(carol._node_dir, "tahoe.cfg"))
    config.add_section("grid_managers")
    config.set("grid_managers", "test", pubkey)
    with open(join(carol._node_dir, "tahoe.cfg"), "w") as carol_cfg:
        config.write(carol_cfg)
    carol.transport.signalProcess('TERM')
    yield carol.transport._protocol.exited
    carol = yield util._run_node(
        reactor, carol._node_dir, request, None,
    )
    yield util.await_client_ready(carol, minimum_storage_servers=5)

    # try to put something into the grid, which should fail (because
    # carol has happy=3 but should only find storage0, storage1 to be
    # acceptable to upload to)

    try:
        yield util.cli(
            request, reactor, carol.node_dir,
            "put", "-",
            stdin="some content" * 200,
        )
        assert False, "Should get a failure"
    except UploadUnhappinessError:
        print("found expected UploadUnhappinessError")
        # we kind of want to (also) assert "only 2 servers found" but
        # UploadUnhappinessError doesn't include those details.

    # other exceptions will be (and should be) errors in the test
