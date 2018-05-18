
import os
import sys
import json

from pycryptopp.publickey import ed25519  # perhaps NaCl instead? other code uses this though

from allmydata.scripts.common import BasedirOptions
from twisted.scripts import twistd
from twisted.python import usage
from twisted.python.reflect import namedAny
from twisted.python.filepath import FilePath
from allmydata.scripts.default_nodedir import _default_nodedir
from allmydata.util import fileutil
from allmydata.util import base32
from allmydata.util import keyutil
from allmydata.node import read_config
from allmydata.util.encodingutil import listdir_unicode, quote_local_unicode_path
from twisted.application.service import Service
from twisted.internet.defer import inlineCallbacks, returnValue


class CreateOptions(BasedirOptions):
    description = (
        "Create a new identity key and configuration of a Grid Manager"
    )


class ShowIdentityOptions(BasedirOptions):
    description = (
        "Show the public identity key of a Grid Manager\n"
        "\n"
        "This is what you give to clients to add to their configuration"
        " so they use announcements from this Grid Manager"
    )


class AddOptions(BasedirOptions):
    description = (
        "Add a new storage-server's key to a Grid Manager configuration"
    )

    def parseArgs(self, *args, **kw):
        BasedirOptions.parseArgs(self, **kw)
        if len(args) != 2:
            raise usage.UsageError(
                "Requires two arguments: name public_key"
            )
        self['name'] = unicode(args[0])
        try:
            # WTF?! why does it want 'str' and not six.text_type?
            self['storage_public_key'] = keyutil.parse_public_key(args[1])
        except Exception as e:
            raise usage.UsageError(
                "Invalid public_key argument: {}".format(e)
            )


class SignOptions(BasedirOptions):
    description = (
        "Create and sign a new certificate for a storage-server"
    )

    def parseArgs(self, *args, **kw):
        BasedirOptions.parseArgs(self, **kw)
        if len(args) != 1:
            raise usage.UsageError(
                "Requires one argument: name"
            )
        self['name'] = unicode(args[0])


class GridManagerOptions(BasedirOptions):
    subCommands = [
        ["create", None, CreateOptions, "Create a Grid Manager."],
        ["public-identity", None, ShowIdentityOptions, "Get the public-key for this Grid Manager."],
        ["add", None, AddOptions, "Add a storage server to this Grid Manager."],
        ["sign", None, SignOptions, "Create and sign a new Storage Certificate."],
    ]

    optParameters = [
        ("config", "c", None, "How to find the Grid Manager's configuration")
    ]

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
        if self['config'] is None:
            raise usage.UsageError("Must supply configuration with --config")

    description = (
        'A "grid-manager" consists of some data defining a keypair (along with '
        'some other details) and Tahoe sub-commands to manipulate the data and '
        'produce certificates to give to storage-servers. Certificates assert '
        'the statement: "Grid Manager X suggests you use storage-server Y to '
        'upload shares to" (X and Y are public-keys).'
        '\n\n'
        'Clients can use Grid Managers to decide which storage servers to '
        'upload shares to.'
    )


def _create_gridmanager():
    return {
        "grid_manager_config_version": 0,
        "private_key": ed25519.SigningKey(os.urandom(32)),
    }

def _create(gridoptions, options):
    gm_config = gridoptions['config']
    assert gm_config is not None

    # pre-conditions check
    fp = None
    if gm_config.strip() != '-':
        fp = FilePath(gm_config.strip())
        if fp.exists():
            raise usage.UsageError(
                "The directory '{}' already exists.".format(gm_config)
            )

    gm = _create_gridmanager()
    _save_gridmanager_config(fp, gm)


def _save_gridmanager_config(file_path, grid_manager):
    """
    Writes a Grid Manager configuration to the place specified by
    'file_path' (if None, stdout is used).
    """
    # FIXME probably want a GridManagerConfig class or something with
    # .save and .load instead of this crap
    raw_data = {
        k: v
        for k, v in grid_manager.items()
    }
    raw_data['private_key'] = base32.b2a(raw_data['private_key'].sk_and_vk[:32])
    data = json.dumps(raw_data, indent=4)

    if file_path is None:
        print("{}\n".format(data))
    else:
        fileutil.make_dirs(file_path.path, mode=0o700)
        with file_path.child("config.json").open("w") as f:
            f.write("{}\n".format(data))
    return 0


# XXX should take a FilePath or None
def _load_gridmanager_config(gm_config):
    """
    Loads a Grid Manager configuration and returns it (a dict) after
    validating. Exceptions if the config can't be found, or has
    problems.
    """
    fp = None
    if gm_config.strip() != '-':
        fp = FilePath(gm_config.strip())
        if not fp.exists():
            raise RuntimeError(
                "No such directory '{}'".format(gm_config)
            )

    if fp is None:
        gm = json.load(sys.stdin)
    else:
        with fp.child("config.json").open("r") as f:
            gm = json.load(f)

    if 'private_key' not in gm:
        raise RuntimeError(
            "Grid Manager config from '{}' requires a 'private_key'".format(
                gm_config
            )
        )

    private_key_str = gm['private_key']
    try:
        private_key_bytes = base32.a2b(private_key_str.encode('ascii'))  # WTF?! why is a2b requiring "str", not "unicode"?
        gm['private_key'] = ed25519.SigningKey(private_key_bytes)
    except Exception as e:
        raise RuntimeError(
            "Invalid Grid Manager private_key: {}".format(e)
        )

    gm_version = gm.get('grid_manager_config_version', None)
    if gm_version != 0:
        raise RuntimeError(
            "Missing or unknown version '{}' of Grid Manager config".format(
                gm_version
            )
        )
    return gm


def _show_identity(gridoptions, options):
    """
    Output the public-key of a Grid Manager
    """
    gm_config = gridoptions['config'].strip()
    assert gm_config is not None

    gm = _load_gridmanager_config(gm_config)
    verify_key_bytes = gm['private_key'].get_verifying_key_bytes()
    print(base32.b2a(verify_key_bytes))


def _add(gridoptions, options):
    """
    Add a new storage-server by name to a Grid Manager
    """
    gm_config = gridoptions['config'].strip()
    assert gm_config is not None
    fp = FilePath(gm_config) if gm_config.strip() != '-' else None

    gm = _load_gridmanager_config(gm_config)
    if options['name'] in gm.get('storage_severs', set()):
        raise usage.UsageError(
            "A storage-server called '{}' already exists".format(options['name'])
        )
    if 'storage_servers' not in gm:
        gm['storage_servers'] = dict()
    gm['storage_servers'][options['name']] = base32.b2a(options['storage_public_key'].vk_bytes)
    _save_gridmanager_config(fp, gm)


def _sign(gridoptions, options):
    """
    sign a new certificate
    """
    gm_config = gridoptions['config'].strip()
    assert gm_config is not None
    fp = FilePath(gm_config) if gm_config.strip() != '-' else None
    gm = _load_gridmanager_config(gm_config)

    if options['name'] not in gm.get('storage_servers', dict()):
        raise usage.UsageError(
            "No storage-server called '{}' exists".format(options['name'])
        )

    public_key = gm['storage_servers'][options['name']]
    import time
    cert_info = {
        "expires": int(time.time() + 86400),  # XXX FIXME
        "public_key": public_key,
        "version": 1,
    }
    cert_data = json.dumps(cert_info, separators=(',',':'), sort_keys=True)
    sig = gm['private_key'].sign(cert_data)
    certificate = {
        "certificate": cert_data,
        "signature": base32.b2a(sig),
    }
    certificate_data = json.dumps(certificate, indent=4)
    print(certificate_data)
    if fp is not None:
        with fp.child('{}.cert'.format(options['name'])).open('w') as f:
            f.write(certificate_data)


grid_manager_commands = {
    CreateOptions: _create,
    ShowIdentityOptions: _show_identity,
    AddOptions: _add,
    SignOptions: _sign,
}

@inlineCallbacks
def gridmanager(config):
    """
    Runs the 'tahoe grid-manager' command.
    """
    if config.subCommand is None:
        print(config)
        returnValue(1)

    try:
        f = grid_manager_commands[config.subOptions.__class__]
    except KeyError:
        print(config.subOptions, grid_manager_commands.keys())
        print("Unknown command 'tahoe grid-manager {}': no such grid-manager subcommand".format(config.subCommand))
        returnValue(2)

    x = yield f(config, config.subOptions)
    returnValue(x)

subCommands = [
    ["grid-manager", None, GridManagerOptions,
     "Grid Manager subcommands: use 'tahoe grid-manager' for a list."],
]

dispatch = {
    "grid-manager": gridmanager,
}
