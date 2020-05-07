import sys
import time
import json
import shutil
from os import mkdir, unlink, listdir, utime
from os.path import join, exists, getmtime

from allmydata.util import keyutil
from allmydata.util import base32
from allmydata.util import configutil

import util

import pytest_twisted


@pytest_twisted.inlineCallbacks
def test_create_certificate(reactor):
    """
    The Grid Manager produces a valid, correctly-signed certificate.
    """
    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "create",
    )
    privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    privkey, pubkey_bytes = keyutil.parse_privkey(privkey_bytes)
    pubkey = keyutil.parse_pubkey(pubkey_bytes)

    # Note that zara + her key here are arbitrary and don't match any
    # "actual" clients in the test-grid; we're just checking that the
    # Grid Manager signs this properly.
    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    zara_cert_bytes = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "sign", "zara",
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
def test_remove_client(reactor):
    """
    A Grid Manager can add and successfully remove a client
    """
    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "create",
    )

    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "add",
        "yakov", "pub-v0-kvxhb3nexybmipkrar2ztfrwp4uxxsmrjzkpzafit3ket4u5yldq",
        stdin=gm_config,
    )
    assert "zara" in json.loads(gm_config)['storage_servers']
    assert "yakov" in json.loads(gm_config)['storage_servers']

    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "remove",
        "zara",
        stdin=gm_config,
    )
    assert "zara" not in json.loads(gm_config)['storage_servers']
    assert "yakov" in json.loads(gm_config)['storage_servers']


@pytest_twisted.inlineCallbacks
def test_remove_last_client(reactor):
    """
    A Grid Manager can remove all clients
    """
    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "create",
    )

    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "add",
        "zara", "pub-v0-kzug3ut2m7ziihf3ndpqlquuxeie4foyl36wn54myqc4wmiwe4ga",
        stdin=gm_config,
    )
    assert "zara" in json.loads(gm_config)['storage_servers']

    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "remove",
        "zara",
        stdin=gm_config,
    )
    # there are no storage servers left at all now
    assert "storage_servers" not in json.loads(gm_config)


@pytest_twisted.inlineCallbacks
def test_reject_storage_server(reactor, request, storage_nodes, temp_dir, introducer_furl, flog_gatherer):
    """
    A client using grid-manager refuses to upload to a storage-server
    without a valid certificate
    """
    gm_config = yield util.run_tahoe(
        reactor, "grid-manager", "--config", "-", "create",
    )
    privkey_bytes = json.loads(gm_config)['private_key'].encode('ascii')
    privkey, pubkey_bytes = keyutil.parse_privkey(privkey_bytes)
    pubkey = keyutil.parse_pubkey(pubkey_bytes)

    # create certificates for first 2 storage-servers
    for idx, storage in enumerate(storage_nodes[:2]):
        pubkey_fname = join(storage._node_dir, "node.pubkey")
        with open(pubkey_fname, 'r') as f:
            pubkey = f.read().strip()

        gm_config = yield util.run_tahoe(
            reactor, "grid-manager", "--config", "-", "add",
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

    print("inserting certificates")
    # insert their certificates
    for idx, storage in enumerate(storage_nodes[:2]):
        print(idx, storage)
        cert = yield util.run_tahoe(
            reactor, "grid-manager", "--config", "-", "sign",
            "storage{}".format(idx),
            stdin=gm_config,
        )
        with open(join(storage._node_dir, "gridmanager.cert"), "w") as f:
            f.write(cert)
        config = configutil.get_config(join(storage._node_dir, "tahoe.cfg"))
        config.set("storage", "grid_management", "True")
        config.add_section("grid_manager_certificates")
        config.set("grid_manager_certificates", "default", "gridmanager.cert")
        config.write(open(join(storage._node_dir, "tahoe.cfg"), "w"))

        # re-start this storage server
        storage.signalProcess('TERM')
        yield storage._protocol.exited
        time.sleep(1)
        storage_nodes[idx] = yield util._run_node(
            reactor, storage._node_dir, request, None,
        )

    # now only two storage-servers have certificates .. configure
    # carol to have the grid-manager certificate

    config = configutil.get_config(join(carol._node_dir, "tahoe.cfg"))
    print(dir(config))
    config.add_section("grid_managers")
    config.set("grid_managers", "test", pubkey_bytes)
    config.write(open(join(carol._node_dir, "tahoe.cfg"), "w"))
    carol.signalProcess('TERM')
    yield carol._protocol.exited

    carol = yield util._run_node(
        reactor, carol._node_dir, request, None,
    )

    # try to put something into the grid, which should fail (because
    # carol has happy=3 but should only find storage0, storage1 to be
    # acceptable to upload to)

    try:
        yield util.run_tahoe(
            reactor, "--node-directory", carol._node_dir,
            "put", "-",
            stdin="some content" * 200,
        )
        assert False, "Should get a failure"
    except Exception as e:
        # depending on the full output being in the error-message
        # here; see util.py
        assert 'UploadUnhappinessError' in str(e)
        print("found expected UploadUnhappinessError")
