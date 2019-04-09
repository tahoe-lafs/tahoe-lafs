from __future__ import print_function

import sys
import json
from os.path import exists, join

from twisted.python import usage
#from allmydata.node import read_config
from allmydata.client import read_config
from allmydata.scripts.cli import _default_nodedir
from allmydata.scripts.common import BaseOptions
from allmydata.util.encodingutil import argv_to_abspath
from allmydata.util import fileutil


class GenerateKeypairOptions(BaseOptions):

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
    from allmydata.util import keyutil
    privkey_vs = options.privkey
    sk, pubkey_vs = keyutil.parse_privkey(privkey_vs)
    print("private:", privkey_vs, file=out)
    print("public:", pubkey_vs, file=out)
    return 0


class AddGridManagerCertOptions(BaseOptions):

    optParameters = [
        ['filename', 'f', None, "Filename of the certificate ('-', a dash, for stdin)"],
        ['name', 'n', "default", "Name to give this certificate"],
    ]

    def getSynopsis(self):
        return "Usage: tahoe [global-options] admin add-grid-manager-cert [options]"

    def postOptions(self):
        if self['filename'] is None:
            raise usage.UsageError(
                "Must provide --filename option"
            )
        if self['filename'] == '-':
            print("reading certificate from stdin", file=self.parent.parent.stderr)
            data = sys.stdin.read()
            if len(data) == 0:
                raise usage.UsageError(
                    "Reading certificate from stdin failed"
                )
            from allmydata.storage_client import parse_grid_manager_data
            try:
                self.certificate_data = parse_grid_manager_data(data)
            except ValueError as e:
                print("Error parsing certificate: {}".format(e), file=self.parent.parent.stderr)
                self.certificate_data = None
        else:
            with open(self['filename'], 'r') as f:
                self.certificate_data = f.read()

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


def add_grid_manager_cert(options):
    """
    Add a new Grid Manager certificate to our config
    """
    if options.certificate_data is None:
        return 1
    # XXX is there really not already a function for this?
    if options.parent.parent['node-directory']:
        nd = argv_to_abspath(options.parent.parent['node-directory'])
    else:
        nd = _default_nodedir

    config = read_config(nd, "portnum")
    config_path = join(nd, "tahoe.cfg")
    cert_fname = "{}.cert".format(options['name'])
    cert_path = config.get_config_path(cert_fname)
    cert_bytes = json.dumps(options.certificate_data, indent=4) + '\n'
    cert_name = options['name']

    if exists(cert_path):
        print("Already have file '{}'".format(cert_path), file=options.parent.parent.stderr)
        return 1

    config.set_config("storage", "grid_management", "True")
    config.add_section("grid_manager_certificates")
    config.set_config("grid_manager_certificates", cert_name, cert_fname)

    # write all the data out

    fileutil.write(cert_path, cert_bytes)
    # print("created {}: {} bytes".format(cert_fname, len(cert_bytes)))
    with open(config_path, "w") as f:
        # XXX probably want a _Config.write_tahoe_cfg() or something? or just set_config() does that automagically
        config.config.write(f)
    # print("wrote {}".format(config_fname))

    cert_count = len(config.enumerate_section("grid_manager_certificates"))
    print("There are now {} certificates".format(cert_count), file=options.parent.parent.stderr)

    return 0


class AdminCommand(BaseOptions):
    subCommands = [
        ("generate-keypair", None, GenerateKeypairOptions,
         "Generate a public/private keypair, write to stdout."),
        ("derive-pubkey", None, DerivePubkeyOptions,
         "Derive a public key from a private key."),
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
    "add-grid-manager-cert": add_grid_manager_cert,
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
