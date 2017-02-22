import os
from twisted.internet import reactor, defer
from twisted.python.usage import UsageError
from allmydata.scripts.common import BasedirOptions, NoDefaultBasedirOptions
from allmydata.scripts.default_nodedir import _default_nodedir
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, argv_to_unicode, quote_local_unicode_path
from allmydata.util import fileutil, i2p_provider, iputil, tor_provider


dummy_tac = """
import sys
print("Nodes created by Tahoe-LAFS v1.11.0 or later cannot be run by")
print("releases of Tahoe-LAFS before v1.10.0.")
sys.exit(1)
"""

def write_tac(basedir, nodetype):
    fileutil.write(os.path.join(basedir, "tahoe-%s.tac" % (nodetype,)), dummy_tac)


WHERE_OPTS = [
    ("location", None, None,
     "Server location to advertise (e.g. tcp:example.org:12345)"),
    ("port", None, None,
     "Server endpoint to listen on (e.g. tcp:12345, or tcp:12345:interface=127.0.0.1."),
    ("hostname", None, None,
     "Hostname to automatically set --location/--port when --listen=tcp"),
    ("listen", None, "tcp",
     "Comma-separated list of listener types (tcp,tor,i2p,none)."),
]

TOR_OPTS = [
    ("tor-control-port", None, None,
     "Tor's control port endpoint descriptor string (e.g. tcp:127.0.0.1:9051 or unix:/var/run/tor/control)"),
    ("tor-executable", None, None,
     "The 'tor' executable to run (default is to search $PATH)."),
]

TOR_FLAGS = [
    ("tor-launch", None, "Launch a tor instead of connecting to a tor control port."),
]

I2P_OPTS = [
    ("i2p-sam-port", None, None,
     "I2P's SAM API port endpoint descriptor string (e.g. tcp:127.0.0.1:7656)"),
    ("i2p-executable", None, None,
     "(future) The 'i2prouter' executable to run (default is to search $PATH)."),
]

I2P_FLAGS = [
    ("i2p-launch", None, "(future) Launch an I2P router instead of connecting to a SAM API port."),
]

def validate_where_options(o):
    if o['listen'] == "none":
        # no other arguments are accepted
        if o['hostname']:
            raise UsageError("--hostname cannot be used when --listen=none")
        if o['port'] or o['location']:
            raise UsageError("--port/--location cannot be used when --listen=none")
    # --location and --port: overrides all others, rejects all others
    if o['location'] and not o['port']:
        raise UsageError("--location must be used with --port")
    if o['port'] and not o['location']:
        raise UsageError("--port must be used with --location")

    if o['location'] and o['port']:
        if o['hostname']:
            raise UsageError("--hostname cannot be used with --location/--port")
        # TODO: really, we should reject an explicit --listen= option (we
        # want them to omit it entirely, because --location/--port would
        # override anything --listen= might allocate). For now, just let it
        # pass, because that allows us to use --listen=tcp as the default in
        # optParameters, which (I think) gets included in the rendered --help
        # output, which is useful. In the future, let's reconsider the value
        # of that --help text (or achieve that documentation in some other
        # way), change the default to None, complain here if it's not None,
        # then change parseArgs() to transform the None into "tcp"
    else:
        # no --location and --port? expect --listen= (maybe the default), and
        # --listen=tcp requires --hostname. But --listen=none is special.
        if o['listen'] != "none":
            listeners = o['listen'].split(",")
            for l in listeners:
                if l not in ["tcp", "tor", "i2p"]:
                    raise UsageError("--listen= must be none, or one/some of: tcp, tor, i2p")
            if 'tcp' in listeners and not o['hostname']:
                raise UsageError("--listen=tcp requires --hostname=")
            if 'tcp' not in listeners and o['hostname']:
                raise UsageError("--listen= must be tcp to use --hostname")

def validate_tor_options(o):
    use_tor = "tor" in o["listen"].split(",")
    if use_tor or any((o["tor-launch"], o["tor-control-port"])):
        if tor_provider._import_txtorcon() is None:
            raise UsageError(
                "Specifying any Tor options requires the 'txtorcon' module"
            )
    if not use_tor:
        if o["tor-launch"]:
            raise UsageError("--tor-launch requires --listen=tor")
        if o["tor-control-port"]:
            raise UsageError("--tor-control-port= requires --listen=tor")
    if o["tor-launch"] and o["tor-control-port"]:
        raise UsageError("use either --tor-launch or --tor-control-port=, not both")

def validate_i2p_options(o):
    use_i2p = "i2p" in o["listen"].split(",")
    if use_i2p or any((o["i2p-launch"], o["i2p-sam-port"])):
        if i2p_provider._import_txi2p() is None:
            raise UsageError(
                "Specifying any I2P options requires the 'txi2p' module"
            )
    if not use_i2p:
        if o["i2p-launch"]:
            raise UsageError("--i2p-launch requires --listen=i2p")
        if o["i2p-sam-port"]:
            raise UsageError("--i2p-sam-port= requires --listen=i2p")
    if o["i2p-launch"] and o["i2p-sam-port"]:
        raise UsageError("use either --i2p-launch or --i2p-sam-port=, not both")
    if o["i2p-launch"]:
        raise UsageError("--i2p-launch is under development")

class _CreateBaseOptions(BasedirOptions):
    optFlags = [
        ("hide-ip", None, "prohibit any configuration that would reveal the node's IP address"),
        ]

    def postOptions(self):
        super(_CreateBaseOptions, self).postOptions()
        if self['hide-ip']:
            if tor_provider._import_txtorcon() is None and i2p_provider._import_txi2p() is None:
                raise UsageError(
                    "--hide-ip was specified but neither 'txtorcon' nor 'txi2p' "
                    "are installed.\nTo do so:\n   pip install tahoe-lafs[tor]\nor\n"
                    "   pip install tahoe-lafs[i2p]"
                )

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
        ("shares-needed", None, 3, "Needed shares required for uploaded files."),
        ("shares-happy", None, 7, "How many servers new files must be placed on."),
        ("shares-total", None, 10, "Total shares required for uploaded files."),
        ]

    # This is overridden in order to ensure we get a "Wrong number of
    # arguments." error when more than one argument is given.
    def parseArgs(self, basedir=None):
        BasedirOptions.parseArgs(self, basedir)
        for name in ["shares-needed", "shares-happy", "shares-total"]:
            try:
                int(self[name])
            except ValueError:
                raise UsageError(
                    "--{} must be an integer".format(name)
                )


class CreateNodeOptions(CreateClientOptions):
    optFlags = [
        ("no-storage", None, "Do not offer storage service to other nodes."),
        ] + TOR_FLAGS + I2P_FLAGS

    synopsis = "[options] [NODEDIR]"
    description = "Create a full Tahoe-LAFS node (client+server)."
    optParameters = CreateClientOptions.optParameters + WHERE_OPTS + TOR_OPTS + I2P_OPTS

    def parseArgs(self, basedir=None):
        CreateClientOptions.parseArgs(self, basedir)
        validate_where_options(self)
        validate_tor_options(self)
        validate_i2p_options(self)


class CreateIntroducerOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-introducer"
    description = "Create a Tahoe-LAFS introducer."
    optFlags = [
        ("hide-ip", None, "prohibit any configuration that would reveal the node's IP address"),
    ] + TOR_FLAGS + I2P_FLAGS
    optParameters = NoDefaultBasedirOptions.optParameters + WHERE_OPTS + TOR_OPTS + I2P_OPTS
    def parseArgs(self, basedir=None):
        NoDefaultBasedirOptions.parseArgs(self, basedir)
        validate_where_options(self)
        validate_tor_options(self)
        validate_i2p_options(self)


@defer.inlineCallbacks
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

    if config["hide-ip"]:
        c.write("[connections]\n")
        if tor_provider._import_txtorcon():
            c.write("tcp = tor\n")
        else:
            c.write("tcp = disabled\n")
        c.write("\n")

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

    listeners = config['listen'].split(",")

    tor_config = {}
    i2p_config = {}
    tub_ports = []
    tub_locations = []
    if listeners == ["none"]:
        c.write("tub.port = disabled\n")
        c.write("tub.location = disabled\n")
    else:
        if "tor" in listeners:
            (tor_config, tor_port, tor_location) = \
                         yield tor_provider.create_onion(reactor, config)
            tub_ports.append(tor_port)
            tub_locations.append(tor_location)
        if "i2p" in listeners:
            (i2p_config, i2p_port, i2p_location) = \
                         yield i2p_provider.create_dest(reactor, config)
            tub_ports.append(i2p_port)
            tub_locations.append(i2p_location)
        if "tcp" in listeners:
            if config["port"]: # --port/--location are a pair
                tub_ports.append(config["port"].encode('utf-8'))
                tub_locations.append(config["location"].encode('utf-8'))
            else:
                assert "hostname" in config
                hostname = config["hostname"]
                new_port = iputil.allocate_tcp_port()
                tub_ports.append("tcp:%s" % new_port)
                tub_locations.append("tcp:%s:%s" % (hostname.encode('utf-8'),
                                                    new_port))
        c.write("tub.port = %s\n" % ",".join(tub_ports))
        c.write("tub.location = %s\n" % ",".join(tub_locations))
    c.write("\n")

    c.write("#log_gatherer.furl =\n")
    c.write("#timeout.keepalive =\n")
    c.write("#timeout.disconnect =\n")
    c.write("#ssh.port = 8022\n")
    c.write("#ssh.authorized_keys_file = ~/.ssh/authorized_keys\n")
    c.write("\n")

    if tor_config:
        c.write("[tor]\n")
        for key, value in tor_config.items():
            c.write("%s = %s\n" % (key, value))
        c.write("\n")

    if i2p_config:
        c.write("[i2p]\n")
        for key, value in i2p_config.items():
            c.write("%s = %s\n" % (key, value))
        c.write("\n")


def write_client_config(c, config):
    # note, config can be a plain dict, it seems -- see
    # test_configutil.py in test_create_client_config
    c.write("[client]\n")
    c.write("# Which services should this client connect to?\n")
    introducer = config.get("introducer", None) or ""
    c.write("introducer.furl = %s\n" % introducer)
    c.write("helper.furl =\n")
    c.write("#stats_gatherer.furl =\n")
    c.write("\n")
    c.write("# Encoding parameters this client will use for newly-uploaded files\n")
    c.write("# This can be changed at any time: the encoding is saved in\n")
    c.write("# each filecap, and we can download old files with any encoding\n")
    c.write("# settings\n")
    c.write("shares.needed = {}\n".format(config['shares-needed']))
    c.write("shares.happy = {}\n".format(config['shares-happy']))
    c.write("shares.total = {}\n".format(config['shares-total']))
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

@defer.inlineCallbacks
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
            defer.returnValue(-1)
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    write_tac(basedir, "client")

    fileutil.make_dirs(os.path.join(basedir, "private"), 0700)
    with open(os.path.join(basedir, "tahoe.cfg"), "w") as c:
        yield write_node_config(c, config)
        write_client_config(c, config)

    print >>out, "Node created in %s" % quote_local_unicode_path(basedir)
    if not config.get("introducer", ""):
        print >>out, " Please set [client]introducer.furl= in tahoe.cfg!"
        print >>out, " The node cannot connect to a grid without it."
    if not config.get("nickname", ""):
        print >>out, " Please set [node]nickname= in tahoe.cfg"
    defer.returnValue(0)

def create_client(config):
    config['no-storage'] = True
    config['listen'] = "none"
    return create_node(config)


@defer.inlineCallbacks
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
            defer.returnValue(-1)
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    write_tac(basedir, "introducer")

    fileutil.make_dirs(os.path.join(basedir, "private"), 0700)
    with open(os.path.join(basedir, "tahoe.cfg"), "w") as c:
        yield write_node_config(c, config)

    print >>out, "Introducer created in %s" % quote_local_unicode_path(basedir)
    defer.returnValue(0)


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
