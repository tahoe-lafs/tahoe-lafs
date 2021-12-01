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

try:
    from allmydata.scripts.types_ import SubCommands
except ImportError:
    pass

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


class AdminCommand(BaseOptions):
    subCommands = [
        ("generate-keypair", None, GenerateKeypairOptions,
         "Generate a public/private keypair, write to stdout."),
        ("derive-pubkey", None, DerivePubkeyOptions,
         "Derive a public key from a private key."),
        ("migrate-crawler", None, MigrateCrawlerOptions,
         "Write the crawler-history data as JSON."),
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
    }

def do_admin(options):
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    return f(so)


subCommands = [
    ("admin", None, AdminCommand, "admin subcommands: use 'tahoe admin' for a list"),
    ]  # type: SubCommands

dispatch = {
    "admin": do_admin,
    }
