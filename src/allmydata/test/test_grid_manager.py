from datetime import (
    timedelta,
)
import json

from twisted.python.filepath import (
    FilePath,
)

from allmydata.node import (
    config_from_string,
)
from allmydata.client import (
    _valid_config as client_valid_config,
)
from allmydata.crypto import (
    ed25519,
)
from allmydata.util import (
    base32,
)
from allmydata.grid_manager import (
    load_grid_manager,
    save_grid_manager,
    create_grid_manager,
    parse_grid_manager_certificate,
    create_grid_manager_verifier,
)

from .common import SyncTestCase


class GridManagerUtilities(SyncTestCase):
    """
    Confirm operation of utility functions used by GridManager
    """

    def test_load_certificates(self):
        """
        Grid Manager certificates are deserialized from config properly
        """
        cert_path = self.mktemp()
        fake_cert = {
            "certificate": "{\"expires\":1601687822,\"public_key\":\"pub-v0-cbq6hcf3pxcz6ouoafrbktmkixkeuywpcpbcomzd3lqbkq4nmfga\",\"version\":1}",
            "signature": "fvjd3uvvupf2v6tnvkwjd473u3m3inyqkwiclhp7balmchkmn3px5pei3qyfjnhymq4cjcwvbpqmcwwnwswdtrfkpnlaxuih2zbdmda"
        }
        with open(cert_path, "w") as f:
            f.write(json.dumps(fake_cert))
        config_data = (
            "[grid_managers]\n"
            "fluffy = pub-v0-vqimc4s5eflwajttsofisp5st566dbq36xnpp4siz57ufdavpvlq\n"
            "[grid_manager_certificates]\n"
            "ding = {}\n".format(cert_path)
        )
        config = config_from_string("/foo", "portnum", config_data, client_valid_config())
        self.assertEqual(
            {"fluffy": "pub-v0-vqimc4s5eflwajttsofisp5st566dbq36xnpp4siz57ufdavpvlq"},
            config.enumerate_section("grid_managers")
        )
        certs = config.get_grid_manager_certificates()
        self.assertEqual([fake_cert], certs)


class GridManagerVerifier(SyncTestCase):
    """
    Tests related to rejecting or accepting Grid Manager certificates.
    """

    def setUp(self):
        self.gm = create_grid_manager()
        return super(GridManagerVerifier, self).setUp()

    def test_sign_cert(self):
        """
        For a storage server previously added to a grid manager,
        _GridManager.sign returns a dict with "certificate" and
        "signature" properties where the value of "signature" gives
        the ed25519 signature (using the grid manager's private key of
        the value) of "certificate".
        """
        priv, pub = ed25519.create_signing_keypair()
        self.gm.add_storage_server("test", pub)
        cert = self.gm.sign("test", timedelta(seconds=86400))

        self.assertEqual(
            set(cert.keys()),
            {"certificate", "signature"},
        )
        gm_key = ed25519.verifying_key_from_string(self.gm.public_identity())
        self.assertEqual(
            ed25519.verify_signature(
                gm_key,
                base32.a2b(cert["signature"]),
                cert["certificate"],
            ),
            None
        )

    def test_sign_cert_wrong_name(self):
        """
        Try to sign a storage-server that doesn't exist
        """
        with self.assertRaises(KeyError):
            self.gm.sign("doesn't exist", timedelta(seconds=86400))

    def test_add_cert(self):
        """
        Add a storage-server and serialize it
        """
        priv, pub = ed25519.create_signing_keypair()
        self.gm.add_storage_server("test", pub)

        data = self.gm.marshal()
        self.assertEqual(
            data["storage_servers"],
            {
                "test": {
                    "public_key": ed25519.string_from_verifying_key(pub),
                }
            }
        )

    def test_remove(self):
        """
        Add then remove a storage-server
        """
        priv, pub = ed25519.create_signing_keypair()
        self.gm.add_storage_server("test", pub)
        self.gm.remove_storage_server("test")
        self.assertEqual(len(self.gm.storage_servers), 0)

    def test_serialize(self):
        """
        Write and then read a Grid Manager config
        """
        priv0, pub0 = ed25519.create_signing_keypair()
        priv1, pub1 = ed25519.create_signing_keypair()
        self.gm.add_storage_server("test0", pub0)
        self.gm.add_storage_server("test1", pub1)

        tempdir = self.mktemp()
        fp = FilePath(tempdir)

        save_grid_manager(fp, self.gm)
        gm2 = load_grid_manager(fp)
        self.assertEqual(
            self.gm.public_identity(),
            gm2.public_identity(),
        )
        self.assertEqual(
            len(self.gm.storage_servers),
            len(gm2.storage_servers),
        )
        for name, ss0 in self.gm.storage_servers.items():
            ss1 = gm2.storage_servers[name]
            self.assertEqual(ss0.name, ss1.name)
            self.assertEqual(ss0.public_key_string(), ss1.public_key_string())
        self.assertEqual(self.gm.marshal(), gm2.marshal())

    def test_invalid_no_version(self):
        """
        Invalid Grid Manager config with no version
        """
        tempdir = self.mktemp()
        fp = FilePath(tempdir)
        bad_config = {
            "private_key": "at least we have one",
        }
        fp.makedirs()
        with fp.child("config.json").open("w") as f:
            json.dump(bad_config, f)

        with self.assertRaises(ValueError) as ctx:
            load_grid_manager(fp)
        self.assertIn(
            "unknown version",
            str(ctx.exception),
        )

    def test_invalid_no_private_key(self):
        """
        Invalid Grid Manager config with no private key
        """
        tempdir = self.mktemp()
        fp = FilePath(tempdir)
        bad_config = {
            "grid_manager_config_version": 0,
        }
        fp.makedirs()
        with fp.child("config.json").open("w") as f:
            json.dump(bad_config, f)

        with self.assertRaises(ValueError) as ctx:
            load_grid_manager(fp)
        self.assertIn(
            "'private_key' required",
            str(ctx.exception),
        )

    def test_invalid_bad_private_key(self):
        """
        Invalid Grid Manager config with bad private-key
        """
        tempdir = self.mktemp()
        fp = FilePath(tempdir)
        bad_config = {
            "grid_manager_config_version": 0,
            "private_key": "not actually encoded key",
        }
        fp.makedirs()
        with fp.child("config.json").open("w") as f:
            json.dump(bad_config, f)

        with self.assertRaises(ValueError) as ctx:
            load_grid_manager(fp)
        self.assertIn(
            "Invalid Grid Manager private_key",
            str(ctx.exception),
        )

    def test_invalid_storage_server(self):
        """
        Invalid Grid Manager config with missing public-key for
        storage-server
        """
        tempdir = self.mktemp()
        fp = FilePath(tempdir)
        bad_config = {
            "grid_manager_config_version": 0,
            "private_key": "priv-v0-ub7knkkmkptqbsax4tznymwzc4nk5lynskwjsiubmnhcpd7lvlqa",
            "storage_servers": {
                "bad": {}
            }
        }
        fp.makedirs()
        with fp.child("config.json").open("w") as f:
            json.dump(bad_config, f)

        with self.assertRaises(ValueError) as ctx:
            load_grid_manager(fp)
        self.assertIn(
            "No 'public_key' for storage server",
            str(ctx.exception),
        )

    def test_parse_cert(self):
        """
        Parse an ostensibly valid storage certificate
        """
        js = parse_grid_manager_certificate('{"certificate": "", "signature": ""}')
        self.assertEqual(
            set(js.keys()),
            {"certificate", "signature"}
        )
        # the signature isn't *valid*, but that's checked in a
        # different function

    def test_parse_cert_not_dict(self):
        """
        Certificate data not even a dict
        """
        with self.assertRaises(ValueError) as ctx:
            parse_grid_manager_certificate("[]")
        self.assertIn(
            "must be a dict",
            str(ctx.exception),
        )

    def test_parse_cert_missing_signature(self):
        """
        Missing the signature
        """
        with self.assertRaises(ValueError) as ctx:
            parse_grid_manager_certificate('{"certificate": ""}')
        self.assertIn(
            "must contain",
            str(ctx.exception),
        )

    def test_validate_cert(self):
        """
        Validate a correctly-signed certificate
        """
        priv0, pub0 = ed25519.create_signing_keypair()
        self.gm.add_storage_server("test0", pub0)
        cert0 = self.gm.sign("test0", timedelta(seconds=86400))

        verify = create_grid_manager_verifier(
            [self.gm._public_key],
            [cert0],
            ed25519.string_from_verifying_key(pub0),
        )

        self.assertTrue(verify())
