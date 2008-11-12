
import os, sys
import pkg_resources
from twisted.python import usage
from allmydata.scripts.common import BasedirMixin, NoDefaultBasedirMixin

class CreateClientOptions(BasedirMixin, usage.Options):
    optParameters = [
        ("basedir", "C", None, "which directory to create the client in"),
        # we provide create-client -time options for the most common
        # configuration knobs. The rest can be controlled by editing
        # tahoe.cfg before node startup.
        ("nickname", "n", None, "nickname for this node"),
        ("introducer", "i", None, "introducer FURL to use"),
        ("webport", "p", "tcp:8123:interface=127.0.0.1",
         "which TCP port to run the HTTP interface on. Use 'none' to disable."),
        ]
    optFlags = [
        ("no-storage", None, "do not offer storage service to other nodes"),
        ]

class CreateIntroducerOptions(NoDefaultBasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "which directory to create the introducer in"],
        ]

client_tac = """
# -*- python -*-

import pkg_resources
pkg_resources.require('allmydata-tahoe')
pkg_resources.require('twisted')
from allmydata import client
from twisted.application import service

c = client.Client()

application = service.Application("allmydata_client")
c.setServiceParent(application)
"""

introducer_tac = """
# -*- python -*-

import pkg_resources
pkg_resources.require('allmydata-tahoe')
pkg_resources.require('twisted')
from allmydata import introducer
from twisted.application import service

c = introducer.IntroducerNode()

application = service.Application("allmydata_introducer")
c.setServiceParent(application)
"""

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
    c.write("nickname = %s\n" % config.get("nickname", "")) #TODO: utf8 in argv?
    webport = config.get("webport", "none")
    if webport.lower() == "none":
        webport = ""
    c.write("web.port = %s\n" % webport)
    c.write("web.static = public_html\n")
    c.write("#tub.port =\n")
    c.write("#advertised_ip_addresses =\n")
    c.write("#log_gatherer.furl =\n")
    c.write("#timeout.keepalive =\n")
    c.write("#timeout.disconnect =\n")
    c.write("#ssh.port = 8022\n")
    c.write("#ssh.authorized_keys_file = ~/.ssh/authorized_keys\n")
    c.write("\n")


def create_client(basedir, config, out=sys.stdout, err=sys.stderr):
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print >>err, "The base directory \"%s\", which is \"%s\" is not empty." % (basedir, os.path.abspath(basedir))
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
    c.write("\n")

    boolstr = {True:"true", False:"false"}
    c.write("[storage]\n")
    storage_enabled = not config.get("no-storage", None)
    c.write("enabled = %s\n" % boolstr[storage_enabled])
    c.write("#readonly =\n")
    c.write("#sizelimit =\n")
    c.write("\n")

    c.write("[helper]\n")
    c.write("enabled = false\n")
    c.write("\n")

    c.close()

    from allmydata.util import fileutil
    fileutil.make_dirs(os.path.join(basedir, "private"), 0700)
    print >>out, "client created in %s" % basedir
    if not config.get("introducer", ""):
        print >>out, " Please set [client]introducer.furl= in tahoe.cfg!"
        print >>out, " The node cannot connect to a grid without it."
    if not config.get("nickname", ""):
        print >>out, " Please set [node]nickname= in tahoe.cfg"

def create_introducer(basedir, config, out=sys.stdout, err=sys.stderr):
    if os.path.exists(basedir):
        if os.listdir(basedir):
            print >>err, "The base directory \"%s\", which is \"%s\" is not empty." % (basedir, os.path.abspath(basedir))
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

    print >>out, "introducer created in %s" % basedir

subCommands = [
    ["create-client", None, CreateClientOptions, "Create a client node."],
    ["create-introducer", None, CreateIntroducerOptions, "Create a introducer node."],

]

dispatch = {
    "create-client": create_client,
    "create-introducer": create_introducer,
    }
