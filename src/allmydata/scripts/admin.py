from __future__ import print_function

from twisted.python import usage
from allmydata.scripts.common import BaseOptions

class GenerateKeypairOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-opts] admin generate-keypair"

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Generate a public/private keypair, dumped to stdout as two lines of ASCII..

"""
        return t

def print_keypair(options):
    from allmydata.util.keyutil import make_keypair
    out = options.stdout
    privkey_vs, pubkey_vs = make_keypair()
    print("private:", privkey_vs, file=out)
    print("public:", pubkey_vs, file=out)

class DerivePubkeyOptions(BaseOptions):
    def parseArgs(self, privkey):
        self.privkey = privkey

    def getSynopsis(self):
        return "Usage: tahoe [global-opts] admin derive-pubkey PRIVKEY"

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Given a private (signing) key that was previously generated with
generate-keypair, derive the public key and print it to stdout.

"""
        return t

def derive_pubkey(options):
    out = options.stdout
    from allmydata.util import keyutil
    privkey_vs = options.privkey
    sk, pubkey_vs = keyutil.parse_privkey(privkey_vs)
    print("private:", privkey_vs, file=out)
    print("public:", pubkey_vs, file=out)
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
        return "Usage: tahoe [global-opts] admin SUBCOMMAND"
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
    ["admin", None, AdminCommand, "admin subcommands: use 'tahoe admin' for a list"],
    ]

dispatch = {
    "admin": do_admin,
    }
