
from twisted.python import usage

class GenerateKeypairOptions(usage.Options):
    def getSynopsis(self):
        return "Usage: tahoe admin generate-keypair"

    def getUsage(self, width=None):
        t = usage.Options.getUsage(self, width)
        t += """
Generate an ECDSA192 public/private keypair, dumped to stdout as two lines of
base32-encoded text.

"""
        return t

def make_keypair():
    from pycryptopp.publickey import ecdsa
    from allmydata.util import base32
    privkey = ecdsa.generate(192)
    privkey_vs = "priv-v0-%s" % base32.b2a(privkey.serialize())
    pubkey = privkey.get_verifying_key()
    pubkey_vs = "pub-v0-%s" % base32.b2a(pubkey.serialize())
    return privkey_vs, pubkey_vs

def print_keypair(options):
    out = options.stdout
    privkey_vs, pubkey_vs = make_keypair()
    print >>out, "private:", privkey_vs
    print >>out, "public:", pubkey_vs

class AdminCommand(usage.Options):
    subCommands = [
        ["generate-keypair", None, GenerateKeypairOptions,
         "Generate a public/private keypair, write to stdout."],
        ]
    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    def getSynopsis(self):
        return "Usage: tahoe admin SUBCOMMAND"
    def getUsage(self, width=None):
        #t = usage.Options.getUsage(self, width)
        t = """
Subcommands:
    tahoe admin generate-keypair    Generate a public/private keypair,
                                    write to stdout.

Please run e.g. 'tahoe admin generate-keypair --help' for more details on
each subcommand.
"""
        return t

subDispatch = {
    "generate-keypair": print_keypair,
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
