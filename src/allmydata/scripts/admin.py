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
from allmydata.scripts.common import BaseOptions

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

class AdminCommand(BaseOptions):
    subCommands = [
        ("generate-keypair", None, GenerateKeypairOptions,
         "Generate a public/private keypair, write to stdout."),
        ("derive-pubkey", None, DerivePubkeyOptions,
         "Derive a public key from a private key."),
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
