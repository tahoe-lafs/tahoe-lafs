
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

def generate_keypair(options):
    from pycryptopp.publickey import ecdsa
    from allmydata.util import base32
    out = options.stdout
    privkey = ecdsa.generate(192)
    print >>out, "private: priv-v0-%s" % base32.b2a(privkey.serialize())
    pubkey = privkey.get_verifying_key()
    print >>out, "public: pub-v0-%s" % base32.b2a(pubkey.serialize())

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
    "generate-keypair": generate_keypair,
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
