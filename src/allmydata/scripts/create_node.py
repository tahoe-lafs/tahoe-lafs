
import os, sys
from allmydata.scripts.common import BasedirOptions, NoDefaultBasedirOptions, create_basedir, \
    NonEmptyBasedirException
from allmydata.util.encodingutil import argv_to_unicode, quote_output
from allmydata.util import fileutil


class CreateClientOptions(BasedirOptions):
    optParameters = [
        # we provide 'create-node'-time options for the most common
        # configuration knobs. The rest can be controlled by editing
        # tahoe.cfg before node startup.
        ("nickname", "n", None, "Specify the nickname for this node."),
        ("introducer", "i", None, "Specify the introducer FURL to use."),
        ("webport", "p", "tcp:3456:interface=127.0.0.1",
         "Specify which TCP port to run the HTTP interface on. Use 'none' to disable."),
        ]

    def getSynopsis(self):
        return "Usage:  %s [global-opts] create-client [options] [NODEDIR]" % (self.command_name,)

    # This is overridden in order to ensure we get a "Wrong number of arguments."
    # error when more than one argument is given.
    def parseArgs(self, basedir=None):
        BasedirOptions.parseArgs(self, basedir)


class CreateNodeOptions(CreateClientOptions):
    optFlags = [
        ("no-storage", None, "Do not offer storage service to other nodes."),
        ]

    def getSynopsis(self):
        return "Usage:  %s [global-opts] create-node [options] [NODEDIR]" % (self.command_name,)


class CreateIntroducerOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-introducer"


def write_node_config(c, config):
    # this is shared between clients and introducers
    c.write("# -*- mode: conf; coding: utf-8 -*-\n")
    c.write("\n")
    c.write("# This file controls the configuration of the Tahoe node that\n")
    c.write("# lives in this directory. It is only read at node startup.\n")
    c.write("# For details about the keys that can be set here, please\n")
    c.write("# read the 'docs/configuration.rst' file that came with your\n")
    c.write("# Tahoe installation.\n")
    c.write("\n\n")

    c.write("[node]\n")
    nickname = argv_to_unicode(config.get("nickname") or "")
    c.write("nickname = %s\n" % (nickname.encode('utf-8'),))

    # TODO: validate webport
    webport = argv_to_unicode(config.get("webport") or "none")
    if webport.lower() == "none":
        webport = ""
    c.write("web.port = %s\n" % (webport.encode('utf-8'),))
    c.write("web.static = public_html\n")
    c.write("#tub.port =\n")
    c.write("#tub.location = \n")
    c.write("#log_gatherer.furl =\n")
    c.write("#timeout.keepalive =\n")
    c.write("#timeout.disconnect =\n")
    c.write("#ssh.port = 8022\n")
    c.write("#ssh.authorized_keys_file = ~/.ssh/authorized_keys\n")
    c.write("\n")


def create_node(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
    try:
        create_basedir(basedir, "client", err=err)
    except NonEmptyBasedirException:
        return -1

    c = open(os.path.join(basedir, "tahoe.cfg"), "w")

    write_node_config(c, config)

    c.write("[client]\n")
    c.write("# Which services should this client connect to?\n")
    c.write("introducer.furl = %s\n" % config.get("introducer", ""))
    c.write("helper.furl =\n")
    c.write("#key_generator.furl =\n")
    c.write("#stats_gatherer.furl =\n")
    c.write("\n")
    c.write("# What encoding parameters should this client use for uploads?\n")
    c.write("#shares.needed = 3\n")
    c.write("#shares.happy = 7\n")
    c.write("#shares.total = 10\n")
    c.write("\n")

    boolstr = {True:"true", False:"false"}
    c.write("[storage]\n")
    c.write("# Shall this node provide storage service?\n")
    storage_enabled = not config.get("no-storage", None)
    c.write("enabled = %s\n" % boolstr[storage_enabled])
    c.write("#readonly =\n")
    c.write("reserved_space = 1G\n")
    c.write("#expire.enabled =\n")
    c.write("#expire.mode =\n")
    c.write("\n")

    c.write("[helper]\n")
    c.write("# Shall this node run a helper service that clients can use?\n")
    c.write("enabled = false\n")
    c.write("\n")

    c.write("[drop_upload]\n")
    c.write("# Shall this node automatically upload files created or modified in a local directory?\n")
    c.write("enabled = false\n")
    c.write("# To specify the target of uploads, a mutable directory writecap URI must be placed\n"
            "# in 'private/drop_upload_dircap'.\n")
    c.write("local.directory = ~/drop_upload\n")
    c.write("\n")

    c.close()

    fileutil.make_dirs(os.path.join(basedir, "private"), 0700)
    print >>out, "Node created in %s" % quote_output(basedir)
    if not config.get("introducer", ""):
        print >>out, " Please set [client]introducer.furl= in tahoe.cfg!"
        print >>out, " The node cannot connect to a grid without it."
    if not config.get("nickname", ""):
        print >>out, " Please set [node]nickname= in tahoe.cfg"
    return 0

def create_client(config, out=sys.stdout, err=sys.stderr):
    config['no-storage'] = True
    return create_node(config, out=out, err=err)


def create_introducer(config, out=sys.stdout, err=sys.stderr):
    basedir = config['basedir']
    try:
        create_basedir(basedir, "introducer", err=err)
    except NonEmptyBasedirException:
        return -1

    c = open(os.path.join(basedir, "tahoe.cfg"), "w")
    write_node_config(c, config)
    c.close()

    print >>out, "Introducer created in %s" % quote_output(basedir)
    return 0


subCommands = [
    ["create-node", None, CreateNodeOptions, "Create a node that acts as a client, server or both."],
    ["create-client", None, CreateClientOptions, "Create a client node (with storage initially disabled)."],
    ["create-introducer", None, CreateIntroducerOptions, "Create an introducer node."],
]

dispatch = {
    "create-node": create_node,
    "create-client": create_client,
    "create-introducer": create_introducer,
    }
