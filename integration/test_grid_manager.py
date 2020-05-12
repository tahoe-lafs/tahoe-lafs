import sys
import time
import json
import shutil
from os import mkdir, unlink, listdir, utime
from os.path import join, exists, getmtime

from allmydata.crypto import ed25519
from allmydata.util import base32
from allmydata.util import configutil

import util
from grid import (
    create_grid,
)

import pytest_twisted


@pytest_twisted.inlineCallbacks
def test_create_certificate(reactor, request):
    """
    The Grid Manager produces a valid, correctly-signed certificate.
    """
    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "create",
    )
    privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    privkey, pubkey = ed25519.signing_keypair_from_string(privkey_bytes)

    # Note that zara + her key here are arbitrary and don't match any
    # "actual" clients in the test-grid; we're just checking that the
    # Grid Manager signs this properly.
    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    zara_cert_bytes = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "sign", "zara",
        stdin=gm_config,
    )
    zara_cert = json.loads(zara_cert_bytes)

    # confirm that zara's certificate is made by the Grid Manager
    # (.verify returns None on success, raises exception on error)
    pubkey.verify(
        base32.a2b(zara_cert['signature'].encode('ascii')),
        zara_cert['certificate'].encode('ascii'),
    )


@pytest_twisted.inlineCallbacks
def test_remove_client(reactor, request):
    """
    A Grid Manager can add and successfully remove a client
    """
    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "create",
    )

    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "add",
        "yakov", "pub-v0-kvxhb3nexybmipkrar2ztfrwp4uxxsmrjzkpzafit3ket4u5yldq",
        stdin=gm_config,
    )
    assert "zara" in json.loads(gm_config)['storage_servers']
    assert "yakov" in json.loads(gm_config)['storage_servers']

    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "remove",
        "zara",
        stdin=gm_config,
    )
    assert "zara" not in json.loads(gm_config)['storage_servers']
    assert "yakov" in json.loads(gm_config)['storage_servers']


@pytest_twisted.inlineCallbacks
def test_remove_last_client(reactor, request):
    """
    A Grid Manager can remove all clients
    """
    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "create",
    )

    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    assert "zara" in json.loads(gm_config)['storage_servers']

    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "remove",
        "zara",
        stdin=gm_config,
    )
    # there are no storage servers left at all now
    assert "storage_servers" not in json.loads(gm_config)


@pytest_twisted.inlineCallbacks
def test_reject_storage_server(reactor, request, temp_dir, flog_gatherer, port_allocator):
    """
    A client with happines=2 fails to upload to a Grid when it is
    using Grid Manager and there is only 1 storage server with a valid
    certificate.
    """
    grid = yield create_grid(reactor, request, temp_dir, flog_gatherer, port_allocator)
    storage0 = yield grid.add_storage_node()
    storage1 = yield grid.add_storage_node()

    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "create",
    )
    gm_privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    gm_privkey, gm_pubkey = ed25519.signing_keypair_from_string(gm_privkey_bytes)

    # create certificate for the first storage-server
    pubkey_fname = join(storage0.process.node_dir, "node.pubkey")
    with open(pubkey_fname, 'r') as f:
        pubkey_str = f.read().strip()

    gm_config = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "add",
        "storage0", pubkey_str,
        stdin=gm_config,
    )
    assert json.loads(gm_config)['storage_servers'].keys() == ['storage0']

    print("inserting certificate")
    cert = yield util.run_tahoe(
        reactor, request, "grid-manager", "--config", "-", "sign", "storage0",
        stdin=gm_config,
    )
    with open(join(storage0.process.node_dir, "gridmanager.cert"), "w") as f:
        f.write(cert)
    config = configutil.get_config(join(storage0.process.node_dir, "tahoe.cfg"))
    config.set("storage", "grid_management", "True")
    config.add_section("grid_manager_certificates")
    config.set("grid_manager_certificates", "default", "gridmanager.cert")
    with open(join(storage0.process.node_dir, "tahoe.cfg"), "w") as f:
        config.write(f)

    # re-start this storage server
    yield storage0.restart(reactor, request)

    # now only one storage-server has the certificate .. configure
    # diana to have the grid-manager certificate

    diana = yield grid.add_client("diana", needed=2, happy=2, total=2)

    config = configutil.get_config(join(diana.process.node_dir, "tahoe.cfg"))
    config.add_section("grid_managers")
    config.set("grid_managers", "test", ed25519.string_from_verifying_key(gm_pubkey))
    with open(join(diana.process.node_dir, "tahoe.cfg"), "w") as f:
        config.write(f)

    yield diana.restart(reactor, request, servers=2)

    # try to put something into the grid, which should fail (because
    # diana has happy=2 but should only find storage0 to be acceptable
    # to upload to)

    try:
        yield util.run_tahoe(
            reactor, request, "--node-directory", diana.process.node_dir,
            "put", "-",
            stdin="some content\n" * 200,
        )
        assert False, "Should get a failure"
    except util.ProcessFailed as e:
        assert 'UploadUnhappinessError' in e.output.getvalue()
