"""
A CLI for configuring a grid manager.
"""

from typing import Optional
from datetime import (
    timedelta,
)

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
    current_datetime_with_zone,
)
from allmydata.util import jsonbytes as json


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
        Available to all sub-commands as Click's context.obj
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

    gm = create_grid_manager()
    try:
        save_grid_manager(fp, gm)
    except OSError as e:
        raise click.ClickException(
            "Can't create '{}': {}".format(config_location, e)
        )


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
@click.argument("public_key", type=click.STRING)
@click.pass_context
def add(ctx, name, public_key):
    """
    Add a new storage-server by name to a Grid Manager

    PUBLIC_KEY is the contents of a node.pubkey file from a Tahoe
    node-directory. NAME is an arbitrary label.
    """
    public_key = public_key.encode("ascii")
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
        create=False,
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

    save_grid_manager(fp, ctx.obj.grid_manager, create=False)


@grid_manager.command()  # noqa: F811
@click.pass_context
def list(ctx):
    """
    List all storage-servers known to a Grid Manager
    """
    for name in sorted(ctx.obj.grid_manager.storage_servers.keys()):
        blank_name = " " * len(name)
        click.echo("{}: {}".format(
            name,
            str(ctx.obj.grid_manager.storage_servers[name].public_key_string(), "utf-8")))
        for cert in ctx.obj.grid_manager.storage_servers[name].certificates:
            delta = current_datetime_with_zone() - cert.expires
            click.echo("{}  cert {}: ".format(blank_name, cert.index), nl=False)
            if delta.total_seconds() < 0:
                click.echo("valid until {} ({})".format(cert.expires, abbreviate_time(delta)))
            else:
                click.echo("expired {} ({})".format(cert.expires, abbreviate_time(delta)))


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
    expiry = timedelta(days=expiry_days)

    try:
        certificate = ctx.obj.grid_manager.sign(name, expiry)
    except KeyError:
        raise click.ClickException(
            "No storage-server called '{}' exists".format(name)
        )

    certificate_data = json.dumps(certificate.marshal(), indent=4)
    click.echo(certificate_data)
    if fp is not None:
        next_serial = 0
        f = None
        while f is None:
            fname = "{}.cert.{}".format(name, next_serial)
            try:
                f = fp.child(fname).create()
            except FileExistsError:
                f = None
            except OSError as e:
                raise click.ClickException(f"{fname}: {e}")
            next_serial += 1
        with f:
            f.write(certificate_data.encode("ascii"))


def _config_path_from_option(config: str) -> Optional[FilePath]:
    """
    :param str config: a path or -
    :returns: a FilePath instance or None
    """
    if config == "-":
        return None
    return FilePath(config)


if __name__ == '__main__':
    grid_manager()  # type: ignore
