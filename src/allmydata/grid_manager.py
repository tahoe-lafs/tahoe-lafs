import sys
import json
from datetime import (
    datetime,
    timedelta,
)

from allmydata.crypto import (
    ed25519,
)
from allmydata.util import (
    fileutil,
    base32,
)


class _GridManagerStorageServer(object):
    """
    A Grid Manager's notion of a storage server
    """

    def __init__(self, name, public_key, certificates):
        self.name = name
        self._public_key = public_key
        self._certificates = [] if certificates is None else certificates

    def add_certificate(self, certificate):
        self._certificates.append(certificate)

    def public_key(self):
        return ed25519.string_from_verifying_key(self._public_key)

    def marshal(self):
        return {
            u"public_key": self.public_key(),
        }


def create_grid_manager():
    """
    Create a new Grid Manager with a fresh keypair
    """
    private_key, public_key = ed25519.create_signing_keypair()
    return _GridManager(
        ed25519.string_from_signing_key(private_key),
        {},
    )


def load_grid_manager(config_path, config_location):
    """
    Load a Grid Manager from existing configuration.

    :param FilePath config_path: the configuration location (or None for
        stdin)

    :param str config_location: a string describing the config's location

    :returns: a GridManager instance
    """
    if config_path is None:
        config_file = sys.stdin
    else:
        try:
            config_file = config_path.child("config.json").open("r")
        except IOError:
            raise ValueError(
                "'{}' is not a Grid Manager config-directory".format(config)
            )
    with config_file:
        config = json.load(config_file)

    if not config:
        raise ValueError(
            "Invalid Grid Manager config in '{}'".format(config_location)
        )
    if 'private_key' not in config:
        raise ValueError(
            "Grid Manager config from '{}' requires a 'private_key'".format(
                config_location,
            )
        )

    private_key_bytes = config['private_key'].encode('ascii')
    try:
        private_key, public_key = ed25519.signing_keypair_from_string(private_key_bytes)
    except Exception as e:
        raise ValueError(
            "Invalid Grid Manager private_key: {}".format(e)
        )

    storage_servers = dict()
    for name, srv_config in config.get(u'storage_servers', {}).items():
        if not 'public_key' in srv_config:
            raise ValueError(
                "No 'public_key' for storage server '{}'".format(name)
            )
        storage_servers[name] = _GridManagerStorageServer(
            name,
            ed25519.verifying_key_from_string(srv_config['public_key'].encode('ascii')),
            None,
        )

    gm_version = config.get(u'grid_manager_config_version', None)
    if gm_version != 0:
        raise ValueError(
            "Missing or unknown version '{}' of Grid Manager config".format(
                gm_version
            )
        )
    return _GridManager(private_key_bytes, storage_servers)


class _GridManager(object):
    """
    A Grid Manager's configuration.
    """

    def __init__(self, private_key_bytes, storage_servers):
        self._storage_servers = dict() if storage_servers is None else storage_servers
        self._private_key_bytes = private_key_bytes
        self._private_key, self._public_key = ed25519.signing_keypair_from_string(self._private_key_bytes)
        self._version = 0

    @property
    def storage_servers(self):
        return self._storage_servers

    def public_identity(self):
        return ed25519.string_from_verifying_key(self._public_key)

    def sign(self, name, expiry_seconds):
        try:
            srv = self._storage_servers[name]
        except KeyError:
            raise KeyError(
                u"No storage server named '{}'".format(name)
            )
        expiration = datetime.utcnow() + timedelta(seconds=expiry_seconds)
        epoch_offset = (expiration - datetime(1970, 1, 1)).total_seconds()
        cert_info = {
            "expires": epoch_offset,
            "public_key": srv.public_key(),
            "version": 1,
        }
        cert_data = json.dumps(cert_info, separators=(',',':'), sort_keys=True).encode('utf8')
        sig = ed25519.sign_data(self._private_key, cert_data)
        certificate = {
            u"certificate": cert_data,
            u"signature": base32.b2a(sig),
        }

        vk = ed25519.verifying_key_from_signing_key(self._private_key)
        ed25519.verify_signature(vk, sig, cert_data)

        return certificate

    def add_storage_server(self, name, public_key):
        """
        :param name: a user-meaningful name for the server
        :param public_key: ed25519.VerifyingKey the public-key of the
            storage provider (e.g. from the contents of node.pubkey
            for the client)
        """
        if name in self._storage_servers:
            raise KeyError(
                "Already have a storage server called '{}'".format(name)
            )
        ss = _GridManagerStorageServer(name, public_key, None)
        self._storage_servers[name] = ss
        return ss

    def remove_storage_server(self, name):
        """
        :param name: a user-meaningful name for the server
        """
        try:
            del self._storage_servers[name]
        except KeyError:
            raise KeyError(
                "No storage server called '{}'".format(name)
            )

    def marshal(self):
        data = {
            u"grid_manager_config_version": self._version,
            u"private_key": self._private_key_bytes.decode('ascii'),
        }
        if self._storage_servers:
            data[u"storage_servers"] = {
                name: srv.marshal()
                for name, srv
                in self._storage_servers.items()
            }
        return data


def save_grid_manager(file_path, grid_manager):
    """
    Writes a Grid Manager configuration.

    :param file_path: a FilePath specifying where to write the config
        (if None, stdout is used)

    :param grid_manager: a _GridManager instance
    """
    data = json.dumps(
        grid_manager.marshal(),
        indent=4,
    )

    if file_path is None:
        print("{}\n".format(data))
    else:
        fileutil.make_dirs(file_path.path, mode=0o700)
        with file_path.child("config.json").open("w") as f:
            f.write("{}\n".format(data))
