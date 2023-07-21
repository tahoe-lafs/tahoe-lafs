"""
Ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from six import ensure_binary

from twisted.python import usage
from twisted.python.filepath import (
    FilePath,
)
from allmydata.scripts.common import (
    BaseOptions,
    BasedirOptions,
)
from allmydata.storage import (
    crawler,
    expirer,
)
from allmydata.scripts.types_ import SubCommands
from allmydata.client import read_config
from allmydata.grid_manager import (
    parse_grid_manager_certificate,
)
from allmydata.scripts.cli import _default_nodedir
from allmydata.util.encodingutil import argv_to_abspath
from allmydata.util import jsonbytes

class GenerateKeypairOptions(BaseOptions):

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Generate a public/private keypair, dumped to stdout as two lines of ASCII..

"""
        return t

def print_keypair(options):
    from allmydata.crypto import ed25519
    out = options.stdout
    private_key, public_key = ed25519.create_signing_keypair()
    print("private:", str(ed25519.string_from_signing_key(private_key), "ascii"),
          file=out)
    print("public:", str(ed25519.string_from_verifying_key(public_key), "ascii"),
          file=out)

class DerivePubkeyOptions(BaseOptions):
    def parseArgs(self, privkey):
        self.privkey = privkey

    def getSynopsis(self):
        return "Usage: tahoe [global-options] admin derive-pubkey PRIVKEY"

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Given a private (signing) key that was previously generated with
generate-keypair, derive the public key and print it to stdout.

"""
        return t

def derive_pubkey(options):
    out = options.stdout
    from allmydata.crypto import ed25519
    privkey_vs = options.privkey
    privkey_vs = ensure_binary(privkey_vs)
    private_key, public_key = ed25519.signing_keypair_from_string(privkey_vs)
    print("private:", str(ed25519.string_from_signing_key(private_key), "ascii"), file=out)
    print("public:", str(ed25519.string_from_verifying_key(public_key), "ascii"), file=out)
    return 0


class MigrateCrawlerOptions(BasedirOptions):

    def getSynopsis(self):
        return "Usage: tahoe [global-options] admin migrate-crawler"

    def getUsage(self, width=None):
        t = BasedirOptions.getUsage(self, width)
        t += (
            "The crawler data is now stored as JSON to avoid"
            " potential security issues with pickle files.\n\nIf"
            " you are confident the state files in the 'storage/'"
            " subdirectory of your node are trustworthy, run this"
            " command to upgrade them to JSON.\n\nThe files are:"
            " lease_checker.history, lease_checker.state, and"
            " bucket_counter.state"
        )
        return t


class AddGridManagerCertOptions(BaseOptions):
    """
    Options for add-grid-manager-cert
    """

    optParameters = [
        ['filename', 'f', None, "Filename of the certificate ('-', a dash, for stdin)"],
        ['name', 'n', None, "Name to give this certificate"],
    ]

    def getSynopsis(self):
        return "Usage: tahoe [global-options] admin add-grid-manager-cert [options]"

    def postOptions(self) -> None:
        assert self.parent is not None
        assert self.parent.parent is not None

        if self['name'] is None:
            raise usage.UsageError(
                "Must provide --name option"
            )
        if self['filename'] is None:
            raise usage.UsageError(
                "Must provide --filename option"
            )

        data: str
        if self['filename'] == '-':
            print("reading certificate from stdin", file=self.parent.parent.stderr)  # type: ignore[attr-defined]
            data = self.parent.parent.stdin.read()  # type: ignore[attr-defined]
            if len(data) == 0:
                raise usage.UsageError(
                    "Reading certificate from stdin failed"
                )
        else:
            with open(self['filename'], 'r') as f:
                data = f.read()

        try:
            self.certificate_data = parse_grid_manager_certificate(data)
        except ValueError as e:
            raise usage.UsageError(
                "Error parsing certificate: {}".format(e)
            )

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += (
            "Adds a Grid Manager certificate to a Storage Server.\n\n"
            "The certificate will be copied into the base-dir and config\n"
            "will be added to 'tahoe.cfg', which will be re-written. A\n"
            "restart is required for changes to take effect.\n\n"
            "The human who operates a Grid Manager would produce such a\n"
            "certificate and communicate it securely to you.\n"
        )
        return t


def migrate_crawler(options):
    out = options.stdout
    storage = FilePath(options['basedir']).child("storage")

    conversions = [
        (storage.child("lease_checker.state"), crawler._convert_pickle_state_to_json),
        (storage.child("bucket_counter.state"), crawler._convert_pickle_state_to_json),
        (storage.child("lease_checker.history"), expirer._convert_pickle_state_to_json),
    ]

    for fp, converter in conversions:
        existed = fp.exists()
        newfp = crawler._upgrade_pickle_to_json(fp, converter)
        if existed:
            print("Converted '{}' to '{}'".format(fp.path, newfp.path), file=out)
        else:
            if newfp.exists():
                print("Already converted: '{}'".format(newfp.path), file=out)
            else:
                print("Not found: '{}'".format(fp.path), file=out)


def add_grid_manager_cert(options):
    """
    Add a new Grid Manager certificate to our config
    """
    # XXX is there really not already a function for this?
    if options.parent.parent['node-directory']:
        nd = argv_to_abspath(options.parent.parent['node-directory'])
    else:
        nd = _default_nodedir

    config = read_config(nd, "portnum")
    cert_fname = "{}.cert".format(options['name'])
    cert_path = FilePath(config.get_config_path(cert_fname))
    cert_bytes = jsonbytes.dumps_bytes(options.certificate_data, indent=4) + b'\n'
    cert_name = options['name']

    if cert_path.exists():
        msg = "Already have certificate for '{}' (at {})".format(
            options['name'],
            cert_path.path,
        )
        print(msg, file=options.stderr)
        return 1

    config.set_config("storage", "grid_management", "True")
    config.set_config("grid_manager_certificates", cert_name, cert_fname)

    # write all the data out
    with cert_path.open("wb") as f:
        f.write(cert_bytes)

    cert_count = len(config.enumerate_section("grid_manager_certificates"))
    print("There are now {} certificates".format(cert_count),
          file=options.stderr)

    return 0


class AdminCommand(BaseOptions):
    subCommands = [
        ("generate-keypair", None, GenerateKeypairOptions,
         "Generate a public/private keypair, write to stdout."),
        ("derive-pubkey", None, DerivePubkeyOptions,
         "Derive a public key from a private key."),
        ("migrate-crawler", None, MigrateCrawlerOptions,
         "Write the crawler-history data as JSON."),
        ("add-grid-manager-cert", None, AddGridManagerCertOptions,
         "Add a Grid Manager-provided certificate to a storage "
         "server's config."),
        ]
    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    def getSynopsis(self):
        return "Usage: tahoe [global-options] admin SUBCOMMAND"
    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Please run e.g. 'tahoe admin generate-keypair --help' for more details on
each subcommand.
"""
        return t


subDispatch = {
    "generate-keypair": print_keypair,
    "derive-pubkey": derive_pubkey,
    "migrate-crawler": migrate_crawler,
    "add-grid-manager-cert": add_grid_manager_cert,
}


def do_admin(options):
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    return f(so)


subCommands : SubCommands = [
    ("admin", None, AdminCommand, "admin subcommands: use 'tahoe admin' for a list"),
    ]

dispatch = {
    "admin": do_admin,
}
