import os
from twisted.python import usage
from allmydata.scripts.common import BasedirOptions, NoDefaultBasedirOptions
from allmydata.scripts.default_nodedir import _default_nodedir
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, argv_to_unicode, quote_local_unicode_path
from allmydata.util import fileutil, iputil


dummy_tac = """
import sys
print("Nodes created by Tahoe-LAFS v1.11.0 or later cannot be run by")
print("releases of Tahoe-LAFS before v1.10.0.")
sys.exit(1)
"""

def write_tac(basedir, nodetype):
    fileutil.write(os.path.join(basedir, "tahoe-%s.tac" % (nodetype,)), dummy_tac)


WHERE_OPTS = [
    ("location", None, None, "Specify the location to advertise for this node."),
    ("port", None, None, "Specify the server endpoint to listen on for this node."),
]

def validate_where_options(options):
    if options['hostname'] and options['port']:
        raise usage.UsageError("The --hostname option cannot be used with the --port option.")
    if options['hostname'] and options['location']:
        raise usage.UsageError("The --hostname option cannot be used with the --location option.")
    if not options['hostname'] and (options['location'] and not options['port']):
        raise usage.UsageError("The --location option must be used with the --port option.")
    if not options['hostname'] and (options['port'] and not options['location']):
        raise usage.UsageError("The --port option must be used with the --location option.")
    if (options['listen'] != "tcp") and options['hostname']:
        raise usage.UsageError("The listener type must be TCP to use --hostname option.")

class _CreateBaseOptions(BasedirOptions):
    optFlags = [
        ("hide-ip", None, "prohibit any configuration that would reveal the node's IP address"),
        ]

    # This is overridden in order to ensure we get a "Wrong number of
    # arguments." error when more than one argument is given.
    def parseArgs(self, basedir=None):
        BasedirOptions.parseArgs(self, basedir)


class CreateClientOptions(_CreateBaseOptions):
    synopsis = "[options] [NODEDIR]"
    description = "Create a client-only Tahoe-LAFS node (no storage server)."

    optParameters = [
        # we provide 'create-node'-time options for the most common
        # configuration knobs. The rest can be controlled by editing
        # tahoe.cfg before node startup.
        ("nickname", "n", None, "Specify the nickname for this node."),
        ("introducer", "i", None, "Specify the introducer FURL to use."),
        ("webport", "p", "tcp:3456:interface=127.0.0.1",
         "Specify which TCP port to run the HTTP interface on. Use 'none' to disable."),
        ("basedir", "C", None, "Specify which Tahoe base directory should be used. This has the same effect as the global --node-directory option. [default: %s]"
         % quote_local_unicode_path(_default_nodedir)),
        ]

    def parseArgs(self, basedir=None):
        BasedirOptions.parseArgs(self, basedir)

class CreateNodeOptions(CreateClientOptions):
    optFlags = [
        ("no-storage", None, "Do not offer storage service to other nodes."),
        ]
    synopsis = "[options] [NODEDIR]"
    description = "Create a full Tahoe-LAFS node (client+server)."
    optParameters = WHERE_OPTS + [
        # we provide 'create-node'-time options for the most common
        # configuration knobs. The rest can be controlled by editing
        # tahoe.cfg before node startup.
        ("hostname", None, None, "Specify the hostname for listening and advertising for this node."),
        ("listen", None, "tcp", "Specify the listener type for this node."),
        ("nickname", "n", None, "Specify the nickname for this node."),
        ("introducer", "i", None, "Specify the introducer FURL to use."),
        ("webport", "p", "tcp:3456:interface=127.0.0.1",
         "Specify which TCP port to run the HTTP interface on. Use 'none' to disable."),
        ("basedir", "C", None, "Specify which Tahoe base directory should be used. This has the same effect as the global --node-directory option. [default: %s]"
         % quote_local_unicode_path(_default_nodedir)),

        ]

    def parseArgs(self, basedir=None):
        CreateClientOptions.parseArgs(self, basedir)
        validate_where_options(self)

class CreateIntroducerOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-introducer"
    description = "Create a Tahoe-LAFS introducer."
    optFlags = [
        ("hide-ip", None, "prohibit any configuration that would reveal the node's IP address"),
    ]
    optParameters = WHERE_OPTS + [("listen", None, "tcp", "Specify the listener type for this node."),
        ("hostname", None, None, "Specify the hostname for listening and advertising for this node."),
    ]
    def parseArgs(self, basedir=None):
        NoDefaultBasedirOptions.parseArgs(self, basedir)
        validate_where_options(self)

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
    if config["hide-ip"]:
        c.write("reveal-IP-address = false\n")
    else:
        c.write("reveal-IP-address = true\n")

    # TODO: validate webport
    webport = argv_to_unicode(config.get("webport") or "none")
    if webport.lower() == "none":
        webport = ""
    c.write("web.port = %s\n" % (webport.encode('utf-8'),))
    c.write("web.static = public_html\n")

    if 'hostname' in config and config['hostname'] is not None:
        print "HOSTNAME"
        new_port = iputil.allocate_tcp_port()
        c.write("tub.port = tcp:%s\n" % new_port)
        c.write("tub.location = tcp:%s:%s\n" % (config.get('hostname').encode('utf-8'), new_port))
    elif 'listen' in config and config['listen'] == "tor":
        raise NotImplementedError("This feature addition is being tracked by this ticket:" +
                                  "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2490")
    elif 'listen' in config and config['listen'] == "i2p":
        raise NotImplementedError("This feature addition is being tracked by this ticket:" +
                                  "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2490")
    elif config.get('port') is not None:
        c.write("tub.port = %s\n" % config.get('port').encode('utf-8'))
        c.write("tub.location = %s\n" % config.get('location').encode('utf-8'))
    else:
        c.write("tub.port = disabled\n")
        c.write("tub.location = disabled\n")

    if ('hostname' in config and config['hostname']) or ('listen' in config and config['listen']):
        c.write("# to prevent the Tub from listening at all, use this:\n")
        c.write("#  tub.port = disabled\n")
        c.write("#  tub.location = disabled\n")

    c.write("#log_gatherer.furl =\n")
    c.write("#timeout.keepalive =\n")
    c.write("#timeout.disconnect =\n")
    c.write("#ssh.port = 8022\n")
    c.write("#ssh.authorized_keys_file = ~/.ssh/authorized_keys\n")
    c.write("\n")

def write_client_config(c, config):
    c.write("[client]\n")
    c.write("# Which services should this client connect to?\n")
    c.write("introducer.furl = %s\n" % config.get("introducer", ""))
    c.write("helper.furl =\n")
    c.write("#stats_gatherer.furl =\n")
    c.write("\n")
    c.write("# Encoding parameters this client will use for newly-uploaded files\n")
    c.write("# This can be changed at any time: the encoding is saved in\n")
    c.write("# each filecap, and we can download old files with any encoding\n")
    c.write("# settings\n")
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

def create_node(config):
    out = config.stdout
    err = config.stderr
    basedir = config['basedir']
    # This should always be called with an absolute Unicode basedir.
    precondition(isinstance(basedir, unicode), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print >>err, "The base directory %s is not empty." % quote_local_unicode_path(basedir)
            print >>err, "To avoid clobbering anything, I am going to quit now."
            print >>err, "Please use a different directory, or empty this one."
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    write_tac(basedir, "client")

    c = open(os.path.join(basedir, "tahoe.cfg"), "w")

    write_node_config(c, config)
    write_client_config(c, config)

    c.close()

    from allmydata.util import fileutil
    fileutil.make_dirs(os.path.join(basedir, "private"), 0700)
    print >>out, "Node created in %s" % quote_local_unicode_path(basedir)
    if not config.get("introducer", ""):
        print >>out, " Please set [client]introducer.furl= in tahoe.cfg!"
        print >>out, " The node cannot connect to a grid without it."
    if not config.get("nickname", ""):
        print >>out, " Please set [node]nickname= in tahoe.cfg"
    return 0

def create_client(config):
    config['no-storage'] = True
    return create_node(config)


def create_introducer(config):
    out = config.stdout
    err = config.stderr
    basedir = config['basedir']
    # This should always be called with an absolute Unicode basedir.
    precondition(isinstance(basedir, unicode), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print >>err, "The base directory %s is not empty." % quote_local_unicode_path(basedir)
            print >>err, "To avoid clobbering anything, I am going to quit now."
            print >>err, "Please use a different directory, or empty this one."
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    write_tac(basedir, "introducer")

    c = open(os.path.join(basedir, "tahoe.cfg"), "w")
    write_node_config(c, config)
    c.close()

    print >>out, "Introducer created in %s" % quote_local_unicode_path(basedir)
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
