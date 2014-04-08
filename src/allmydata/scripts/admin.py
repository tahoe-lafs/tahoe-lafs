
import os

from twisted.python import usage
from allmydata.util.encodingutil import quote_output
from allmydata.scripts.common import BaseOptions, BasedirOptions

class GenerateKeypairOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: %s [global-opts] admin generate-keypair" % (self.command_name,)

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Generate a public/private keypair, dumped to stdout as two lines of ASCII.
"""
        return t

def print_keypair(options):
    from allmydata.util.keyutil import make_keypair
    out = options.stdout
    privkey_vs, pubkey_vs = make_keypair()
    print >>out, "private:", privkey_vs
    print >>out, "public:", pubkey_vs

class DerivePubkeyOptions(BaseOptions):
    def parseArgs(self, privkey):
        self.privkey = privkey

    def getSynopsis(self):
        return "Usage: %s [global-opts] admin derive-pubkey PRIVKEY" % (self.command_name,)

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
    print >>out, "private:", privkey_vs
    print >>out, "public:", pubkey_vs
    return 0


class CreateContainerOptions(BasedirOptions):
    def getSynopsis(self):
        return "Usage: %s [global-opts] admin create-container [NODEDIR]" % (self.command_name,)

    def getUsage(self, width=None):
        t = BasedirOptions.getUsage(self, width)
        t += """
Create a storage container, using the name and credentials configured in
tahoe.cfg. This is needed only for the cloud backend, and only if the
container has not already been created. See <docs/backends/cloud.rst>
for more details.
"""
        return t

def create_container(options):
    from twisted.internet import reactor, defer

    d = defer.maybeDeferred(do_create_container, options)
    d.addCallbacks(lambda ign: os._exit(0), lambda ign: os._exit(1))
    reactor.run()

def do_create_container(options):
    from twisted.internet import defer
    from allmydata.node import ConfigOnly
    from allmydata.client import Client

    out = options.stdout
    err = options.stderr

    d = defer.succeed(None)
    def _do_create(ign):
        config = ConfigOnly(options['basedir'])
        (backend, _) = Client.configure_backend(config)

        d2 = backend.create_container()
        def _done(res):
            if res is False:
                print >>out, ("It is not necessary to create a container for this backend type (%s)."
                              % (backend.__class__.__name__,))
            else:
                print >>out, "The container was successfully created."
            print >>out
        d2.addCallback(_done)
        return d2
    d.addCallback(_do_create)
    def _failed(f):
        print >>err, "Container creation failed."
        print >>err, "%s: %s" % (f.value.__class__.__name__, f.value)
        print >>err
        return f
    d.addErrback(_failed)
    return d


class ListContainerOptions(BasedirOptions):
    def getSynopsis(self):
        return "Usage: %s [global-opts] admin ls-container [NODEDIR]" % (self.command_name,)

    def getUsage(self, width=None):
        t = BasedirOptions.getUsage(self, width)
        t += """
List the contents of a storage container, using the name and credentials
configured in tahoe.cfg. This currently works only for the cloud backend.
"""
        return t

def ls_container(options):
    from twisted.internet import reactor, defer

    d = defer.maybeDeferred(do_ls_container, options)
    d.addCallbacks(lambda ign: os._exit(0), lambda ign: os._exit(1))
    reactor.run()

def format_date(date):
    datestr = str(date)
    if datestr.endswith('+00:00'):
        datestr = datestr[: -6] + 'Z'
    return datestr

def do_ls_container(options):
    from twisted.internet import defer
    from allmydata.node import ConfigOnly
    from allmydata.client import Client

    out = options.stdout
    err = options.stderr

    d = defer.succeed(None)
    def _do_create(ign):
        config = ConfigOnly(options['basedir'])
        if not config.get_config("storage", "enabled", True, boolean=True):
            raise AssertionError("'tahoe admin ls-container' is intended for administration of nodes running a storage service.\n"
                                 "The node with base directory %s is not configured to provide storage."
                                 % quote_output(options['basedir']))

        (backend, _) = Client.configure_backend(config)

        d2 = backend.list_container()
        def _done(items):
            print >>out, "Listing %d object(s):" % len(items)
            print >>out, "    Size  Last modified         Key"
            for item in items:
                print >>out, "% 8s  %20s  %s" % (item.size, format_date(item.modification_date), item.key)
        d2.addCallback(_done)
        return d2
    d.addCallback(_do_create)
    def _failed(f):
        print >>err, "Container listing failed."
        print >>err, "%s: %s" % (f.value.__class__.__name__, f.value)
        print >>err
        return f
    d.addErrback(_failed)
    return d


class AdminCommand(BaseOptions):
    subCommands = [
        ("generate-keypair", None, GenerateKeypairOptions,
         "Generate a public/private keypair, write to stdout."),
        ("derive-pubkey", None, DerivePubkeyOptions,
         "Derive a public key from a private key."),
        ("create-container", None, CreateContainerOptions,
         "Create a container for the configured cloud backend."),
        ("ls-container", None, ListContainerOptions,
         "List the contents of the configured backend container."),
        ]
    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    def getSynopsis(self):
        return "Usage: %s [global-opts] admin SUBCOMMAND" % (self.command_name,)
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
    "create-container": create_container,
    "ls-container": ls_container,
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
