from datetime import (
    datetime,
)
import json

import click

from twisted.python.filepath import (
    FilePath,
)

from allmydata.crypto import (
    ed25519,
)
from allmydata.util.abbreviate import (
    abbreviate_time,
)
from allmydata.grid_manager import (
    create_grid_manager,
    save_grid_manager,
    load_grid_manager,
)


@click.group()
@click.option(
    '--config', '-c',
    type=click.Path(),
    help="Configuration directory (or - for stdin)",
    required=True,
)
@click.pass_context
def grid_manager(ctx, config):
    """
    A Tahoe Grid Manager issues certificates to storage-servers

    A Tahoe client with one or more Grid Manager public keys
    configured will only upload to a Storage Server that presents a
    valid certificate signed by one of the configured Grid
    Manager keys.

    Grid Manager configuration can be in a local directory or given
    via stdin. It contains long-term secret information (a private
    signing key) and should be kept safe.
    """

    class Config(object):
        """
        Availble to all sub-commands as Click's context.obj
        """
        _grid_manager = None

        @property
        def grid_manager(self):
            if self._grid_manager is None:
                config_path = _config_path_from_option(config)
                try:
                    self._grid_manager = load_grid_manager(config_path)
                except ValueError as e:
                    raise click.ClickException(
                        "Error loading Grid Manager from '{}': {}".format(config, e)
                    )
            return self._grid_manager

    ctx.obj = Config()


@grid_manager.command()
@click.pass_context
def create(ctx):
    """
    Make a new Grid Manager
    """
    config_location = ctx.parent.params["config"]
    fp = None
    if config_location != '-':
        fp = FilePath(config_location)
        if fp.exists():
            raise click.ClickException(
                "The directory '{}' already exists.".format(config_location)
            )

    gm = create_grid_manager()
    save_grid_manager(fp, gm)


@grid_manager.command()
@click.pass_obj
def public_identity(config):
    """
    Show the public identity key of a Grid Manager

    This is what you give to clients to add to their configuration so
    they use announcements from this Grid Manager
    """
    click.echo(config.grid_manager.public_identity())


@grid_manager.command()
@click.argument("name")
@click.argument("public_key", type=click.UNPROCESSED)
@click.pass_context
def add(ctx, name, public_key):
    """
    Add a new storage-server by name to a Grid Manager

    PUBLIC_KEY is the contents of a node.pubkey file from a Tahoe
    node-directory. NAME is an arbitrary label.
    """
    try:
        ctx.obj.grid_manager.add_storage_server(
            name,
            ed25519.verifying_key_from_string(public_key),
        )
    except KeyError:
        raise click.ClickException(
            "A storage-server called '{}' already exists".format(name)
        )
    save_grid_manager(
        _config_path_from_option(ctx.parent.params["config"]),
        ctx.obj.grid_manager,
    )
    return 0


@grid_manager.command()
@click.argument("name")
@click.pass_context
def remove(ctx, name):
    """
    Remove an existing storage-server by name from a Grid Manager
    """
    fp = _config_path_from_option(ctx.parent.params["config"])
    try:
        ctx.obj.grid_manager.remove_storage_server(name)
    except KeyError:
        raise click.ClickException(
            "No storage-server called '{}' exists".format(name)
        )
    cert_count = 0
    if fp is not None:
        while fp.child('{}.cert.{}'.format(name, cert_count)).exists():
            fp.child('{}.cert.{}'.format(name, cert_count)).remove()
            cert_count += 1

    save_grid_manager(fp, ctx.obj.grid_manager)


@grid_manager.command()
@click.pass_context
def list(ctx):
    """
    List all storage-servers known to a Grid Manager
    """
    fp = _config_path_from_option(ctx.parent.params["config"])
    for name in sorted(ctx.obj.grid_manager.storage_servers.keys()):
        blank_name = " " * len(name)
        click.echo("{}: {}".format(name, ctx.obj.grid_manager.storage_servers[name].public_key()))
        if fp:
            cert_count = 0
            while fp.child('{}.cert.{}'.format(name, cert_count)).exists():
                container = json.load(fp.child('{}.cert.{}'.format(name, cert_count)).open('r'))
                cert_data = json.loads(container['certificate'])
                expires = datetime.utcfromtimestamp(cert_data['expires'])
                delta = datetime.utcnow() - expires
                click.echo("{}  cert {}: ".format(blank_name, cert_count), nl=False)
                if delta.total_seconds() < 0:
                    click.echo("valid until {} ({})".format(expires, abbreviate_time(delta)))
                else:
                    click.echo("expired {} ({})".format(expires, abbreviate_time(delta)))
                cert_count += 1


@grid_manager.command()
@click.argument("name")
@click.argument(
    "expiry_days",
    type=click.IntRange(1, 5*365),  # XXX is 5 years a good maximum?
)
@click.pass_context
def sign(ctx, name, expiry_days):
    """
    sign a new certificate
    """
    fp = _config_path_from_option(ctx.parent.params["config"])
    expiry_seconds = int(expiry_days) * 86400

    try:
        certificate = ctx.obj.grid_manager.sign(name, expiry_seconds)
    except KeyError:
        raise click.ClickException(
            "No storage-server called '{}' exists".format(name)
        )

    certificate_data = json.dumps(certificate, indent=4)
    click.echo(certificate_data)
    if fp is not None:
        next_serial = 0
        while fp.child("{}.cert.{}".format(name, next_serial)).exists():
            next_serial += 1
        with fp.child('{}.cert.{}'.format(name, next_serial)).open('w') as f:
            f.write(certificate_data)


def _config_path_from_option(config):
    """
    :param string config: a path or -
    :returns: a FilePath instance or None
    """
    if config == "-":
        return None
    return FilePath(config)
