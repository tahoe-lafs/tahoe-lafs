

from allmydata.client import (
    _load_grid_manager_certificates,
    create_storage_farm_broker,
)
from allmydata.node import (
    config_from_string,
)
from allmydata.client import (
    _valid_config as client_valid_config,
)

from .common import SyncTestCase


class GridManagerUtilities(SyncTestCase):
    """
    Confirm operation of utility functions used by GridManager
    """

    def test_client_grid_manager(self):
        config_data = (
            "[grid_managers]\n"
            "fluffy = pub-v0-vqimc4s5eflwajttsofisp5st566dbq36xnpp4siz57ufdavpvlq\n"
        )
        config = config_from_string("/foo", "portnum", config_data, client_valid_config())
        sfb = create_storage_farm_broker(config, {}, {}, {}, [])
        # could introspect sfb._grid_manager_certificates, but that's
        # "cheating"? even though _make_storage_sever is also
        # "private"?

        # ...but, okay, a "real" client will call set_static_servers()
        # with any configured/cached servers (thus causing
        # _make_storage_server to be called). The other way
        # _make_storage_server is called is when _got_announcement
        # runs, which is when an introducer client gets an
        # announcement...

        invalid_cert = {
            "certificate": "foo",
            "signature": "43564356435643564356435643564356",
        }
        announcement = {
            "anonymous-storage-FURL": b"pb://abcde@nowhere/fake",
            "grid-manager-certificates": [
                invalid_cert,
            ]
        }
        static_servers = {
            "v0-4uazse3xb6uu5qpkb7tel2bm6bpea4jhuigdhqcuvvse7hugtsia": {
                "ann": announcement,
            }
        }
        sfb.set_static_servers(static_servers)
        nss = sfb._make_storage_server(u"server0", {"ann": announcement})

        # we have some grid-manager keys defined so the server should
        # only upload if there's a valid certificate -- but the only
        # one we have is invalid
        self.assertFalse(nss.upload_permitted())

    def test_load_certificates(self):
        config_data = (
            "[grid_managers]\n"
            "fluffy = pub-v0-vqimc4s5eflwajttsofisp5st566dbq36xnpp4siz57ufdavpvlq\n"
        )
        config = config_from_string("/foo", "portnum", config_data, client_valid_config())
        self.assertEqual(
            1,
            len(config.enumerate_section("grid_managers"))
        )
