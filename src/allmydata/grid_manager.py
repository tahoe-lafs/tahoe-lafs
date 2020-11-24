
import sys
import json
from datetime import (
    datetime,
)

from allmydata.crypto import (
    ed25519,
)
from allmydata.util import (
    base32,
)

import attr


@attr.s
class _GridManagerStorageServer(object):
    """
    A Grid Manager's notion of a storage server
    """

    name = attr.ib()
    public_key = attr.ib(validator=attr.validators.instance_of(ed25519.Ed25519PublicKey))
    certificates = attr.ib(
        default=attr.Factory(list),
        validator=attr.validators.instance_of(list),
    )

    def add_certificate(self, certificate):
        self.certificates.append(certificate)

    def public_key_string(self):
        return ed25519.string_from_verifying_key(self.public_key)

    def marshal(self):
        return {
            u"public_key": self.public_key_string(),
        }


@attr.s
class _GridManagerCertificate(object):
    """
    Represents a single certificate for a single storage-server
    """

    filename = attr.ib()
    index = attr.ib(validator=attr.validators.instance_of(int))
    expires = attr.ib(validator=attr.validators.instance_of(datetime))
    public_key = attr.ib(validator=attr.validators.instance_of(ed25519.Ed25519PublicKey))


def create_grid_manager():
    """
    Create a new Grid Manager with a fresh keypair
    """
    private_key, public_key = ed25519.create_signing_keypair()
    return _GridManager(
        ed25519.string_from_signing_key(private_key),
        {},
    )


def _load_certificates_for(config_path, name, gm_key=None):
    """
    Load any existing certificates for the given storage-server.

    :param FilePath config_path: the configuration location (or None for
        stdin)

    :param str name: the name of an existing storage-server

    :param ed25519.VerifyingKey gm_key: an optional Grid Manager
        public key. If provided, certificates will be verified against it.

    :returns: list containing any known certificates (may be empty)

    :raises: ed25519.BadSignature if any certificate signature fails to verify
    """
    if config_path is None:
        return []
    cert_index = 0
    cert_path = config_path.child('{}.cert.{}'.format(name, cert_index))
    certificates = []
    while cert_path.exists():
        container = json.load(cert_path.open('r'))
        if gm_key is not None:
            validate_grid_manager_certificate(gm_key, container)
        cert_data = json.loads(container['certificate'])
        if cert_data['version'] != 1:
            raise ValueError(
                "Unknown certificate version '{}' in '{}'".format(
                    cert_data['version'],
                    cert_path.path,
                )
            )
        certificates.append(
            _GridManagerCertificate(
                filename=cert_path.path,
                index=cert_index,
                expires=datetime.utcfromtimestamp(cert_data['expires']),
                public_key=ed25519.verifying_key_from_string(cert_data['public_key'].encode('ascii')),
            )
        )
        cert_index += 1
        cert_path = config_path.child('{}.cert.{}'.format(name, cert_index))
    return certificates


def load_grid_manager(config_path):
    """
    Load a Grid Manager from existing configuration.

    :param FilePath config_path: the configuration location (or None for
        stdin)

    :returns: a GridManager instance

    :raises: ValueError if the confguration is invalid or IOError if
        expected files can't be opened.
    """
    if config_path is None:
        config_file = sys.stdin
    else:
        # this might raise IOError or similar but caller must handle it
        config_file = config_path.child("config.json").open("r")

    with config_file:
        config = json.load(config_file)

    gm_version = config.get(u'grid_manager_config_version', None)
    if gm_version != 0:
        raise ValueError(
            "Missing or unknown version '{}' of Grid Manager config".format(
                gm_version
            )
        )
    if 'private_key' not in config:
        raise ValueError(
            "'private_key' required in config"
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
        if 'public_key' not in srv_config:
            raise ValueError(
                "No 'public_key' for storage server '{}'".format(name)
            )
        storage_servers[name] = _GridManagerStorageServer(
            name,
            ed25519.verifying_key_from_string(srv_config['public_key'].encode('ascii')),
            _load_certificates_for(config_path, name, public_key),
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

    def sign(self, name, expiry):
        """
        Create a new signed certificate for a particular server

        :param str name: the server to create a certificate for

        :param timedelta expiry: how far in the future the certificate
            should expire.

        :returns: a dict defining the certificate (it has
            "certificate" and "signature" keys).
        """
        try:
            srv = self._storage_servers[name]
        except KeyError:
            raise KeyError(
                "No storage server named '{}'".format(name)
            )
        expiration = datetime.utcnow() + expiry
        epoch_offset = (expiration - datetime(1970, 1, 1)).total_seconds()
        cert_info = {
            "expires": epoch_offset,
            "public_key": srv.public_key_string(),
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

        srv.add_certificate(certificate)
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
        ss = _GridManagerStorageServer(name, public_key, [])
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


def save_grid_manager(file_path, grid_manager, create=True):
    """
    Writes a Grid Manager configuration.

    :param file_path: a FilePath specifying where to write the config
        (if None, stdout is used)

    :param grid_manager: a _GridManager instance

    :param bool create: if True (the default) we are creating a new
        grid-manager and will fail if the directory already exists.
    """
    data = json.dumps(
        grid_manager.marshal(),
        indent=4,
    )

    if file_path is None:
        print("{}\n".format(data))
    else:
        try:
            file_path.makedirs()
            file_path.chmod(0o700)
        except OSError:
            if create:
                raise
        with file_path.child("config.json").open("w") as f:
            f.write("{}\n".format(data))


def parse_grid_manager_certificate(gm_data):
    """
    :param gm_data: some data that might be JSON that might be a valid
       Grid Manager Certificate

    :returns: json data of a valid Grid Manager certificate, or an
        exception if the data is not valid.
    """

    required_keys = {
        'certificate',
        'signature',
    }

    js = json.loads(gm_data)

    if not isinstance(js, dict):
        raise ValueError(
            "Grid Manager certificate must be a dict"
        )
    if set(js.keys()) != required_keys:
            raise ValueError(
                "Grid Manager certificate must contain: {}".format(
                    ", ".join("'{}'".format(k) for k in js.keys()),
                )
            )
    return js


def validate_grid_manager_certificate(gm_key, alleged_cert):
    """
    :param gm_key: a VerifyingKey instance, a Grid Manager's public
        key.

    :param alleged_cert: dict with "certificate" and "signature" keys, where
        "certificate" contains a JSON-serialized certificate for a Storage
        Server (comes from a Grid Manager).

    :return: a dict consisting of the deserialized certificate data or
        None if the signature is invalid. Note we do NOT check the
        expiry time in this function.
    """
    try:
        ed25519.verify_signature(
            gm_key,
            base32.a2b(alleged_cert['signature'].encode('ascii')),
            alleged_cert['certificate'].encode('ascii'),
        )
    except ed25519.BadSignature:
        return None
    # signature is valid; now we can load the actual data
    cert = json.loads(alleged_cert['certificate'])
    return cert


def create_grid_manager_verifier(keys, certs, public_key, now_fn=None, bad_cert=None):
    """
    Creates a predicate for confirming some Grid Manager-issued
    certificates against Grid Manager keys. A predicate is used
    (instead of just returning True/False here) so that the
    expiry-time can be tested on each call.

    :param list keys: 0 or more `VerifyingKey` instances

    :param list certs: 1 or more Grid Manager certificates each of
        which is a `dict` containing 'signature' and 'certificate' keys.

    :param str public_key: the identifier of the server we expect
        certificates for.

    :param callable now_fn: a callable which returns the current UTC
        timestamp (or datetime.utcnow if None).

    :param callable bad_cert: a two-argument callable which is invoked
        when a certificate verification fails. The first argument is
        the verifying key and the second is the certificate. If None
        (the default) errors are print()-ed. Note that we may have
        several certificates and only one must be valid, so this may
        be called (multiple times) even if the function ultimately
        returns successfully.

    :returns: a callable which will return True only-if there is at
        least one valid certificate (that has not at this moment
        expired) in `certs` signed by one of the keys in `keys`.
    """

    now_fn = datetime.utcnow if now_fn is None else now_fn
    valid_certs = []

    # if we have zero grid-manager keys then everything is valid
    if not keys:
        return lambda: True

    if bad_cert is None:

        def bad_cert(key, alleged_cert):
            """
            We might want to let the user know about this failed-to-verify
            certificate .. but also if you have multiple grid-managers
            then a bunch of these messages would appear. Better would
            be to bubble this up to some sort of status API (or maybe
            on the Welcome page?)

            The only thing that might actually be interesting, though,
            is whether this whole function returns false or not..
            """
            print(
                "Grid Manager certificate signature failed. Certificate: "
                "\"{cert}\" for key \"{key}\".".format(
                    cert=alleged_cert,
                    key=ed25519.string_from_verifying_key(key),
                )
            )

    # validate the signatures on any certificates we have (not yet the expiry dates)
    for alleged_cert in certs:
        for key in keys:
            cert = validate_grid_manager_certificate(key, alleged_cert)
            if cert is not None:
                valid_certs.append(cert)
            else:
                bad_cert(key, alleged_cert)

    def validate():
        now = now_fn()
        # if *any* certificate is still valid then we consider the server valid
        for cert in valid_certs:
            expires = datetime.utcfromtimestamp(cert['expires'])
            cert_pubkey = ed25519.verifying_key_from_string(cert['public_key'].encode('ascii'))
            if cert['public_key'] == public_key:
                if expires > now:
                    # not-expired
                    return True
        return False

    return validate
