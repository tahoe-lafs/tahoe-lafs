
import os, sys
from allmydata.scripts.common import BasedirMixin, BaseOptions
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, argv_to_unicode, quote_output
import allmydata

class CreateClientOptions(BasedirMixin, BaseOptions):
    optParameters = [
        # we provide 'create-node'-time options for the most common
        # configuration knobs. The rest can be controlled by editing
        # tahoe.cfg before node startup.
        ("nickname", "n", None, "Specify the nickname for this node."),
        ("introducer", "i", None, "Specify the introducer FURL to use."),
        ("webport", "p", "tcp:3456:interface=127.0.0.1",
         "Specify which TCP port to run the HTTP interface on. Use 'none' to disable."),
        ]

class CreateNodeOptions(CreateClientOptions):
    optFlags = [
        ("no-storage", None, "Do not offer storage service to other nodes."),
        ]

class CreateIntroducerOptions(BasedirMixin, BaseOptions):
    default_nodedir = None

    optParameters = [
        ["node-directory", "d", None, "Specify which directory the introducer should be created in. [no default]"],
    ]

client_tac = """
# -*- python -*-

import pkg_resources
pkg_resources.require('%s')
pkg_resources.require('twisted')
from allmydata import client
from twisted.application import service

c = client.Client()

application = service.Application("allmydata_client")
c.setServiceParent(application)
""" % (allmydata.__appname__,)

introducer_tac = """
# -*- python -*-

import pkg_resources
pkg_resources.require('%s')
pkg_resources.require('twisted')
from allmydata import introducer
from twisted.application import service

c = introducer.IntroducerNode()

application = service.Application("allmydata_introducer")
c.setServiceParent(application)
""" % (allmydata.__appname__,)

def write_node_config(c, config):
    # this is shared between clients and introducers
    c.write("# -*- mode: conf; coding: utf-8 -*-\n")
    c.write("\n")
    c.write("# This file controls the configuration of the Tahoe node that\n")
    c.write("# lives in this directory. It is only read at node startup.\n")
    c.write("# For details about the keys that can be set here, please\n")
    c.write("# read the 'docs/configuration.txt' file that came with your\n")
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
    # This should always be called with an absolute Unicode basedir.
    precondition(isinstance(basedir, unicode), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print >>err, "The base directory %s is not empty." % quote_output(basedir)
            print >>err, "To avoid clobbering anything, I am going to quit now."
            print >>err, "Please use a different directory, or empty this one."
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "tahoe-client.tac"), "w")
    f.write(client_tac)
    f.close()

    c = open(os.path.join(basedir, "tahoe.cfg"), "w")

    write_node_config(c, config)

    c.write("[client]\n")
    c.write("introducer.furl = %s\n" % config.get("introducer", ""))
    c.write("helper.furl =\n")
    c.write("#key_generator.furl =\n")
    c.write("#stats_gatherer.furl =\n")
    c.write("#shares.needed = 3\n")
    c.write("#shares.happy = 7\n")
    c.write("#shares.total = 10\n")
    c.write("\n")

    boolstr = {True:"true", False:"false"}
    c.write("[storage]\n")
    storage_enabled = not config.get("no-storage", None)
    c.write("enabled = %s\n" % boolstr[storage_enabled])
    c.write("#readonly =\n")
    c.write("#reserved_space =\n")
    c.write("#expire.enabled =\n")
    c.write("#expire.mode =\n")
    c.write("\n")

    c.write("[helper]\n")
    c.write("enabled = false\n")
    c.write("\n")

    c.close()

    from allmydata.util import fileutil
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
    # This should always be called with an absolute Unicode basedir.
    precondition(isinstance(basedir, unicode), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print >>err, "The base directory %s is not empty." % quote_output(basedir)
            print >>err, "To avoid clobbering anything, I am going to quit now."
            print >>err, "Please use a different directory, or empty this one."
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    f = open(os.path.join(basedir, "tahoe-introducer.tac"), "w")
    f.write(introducer_tac)
    f.close()

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
