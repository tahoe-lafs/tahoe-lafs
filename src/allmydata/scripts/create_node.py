import os
from twisted.python.usage import UsageError
from twisted.internet import defer
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


WHERE_PARMS = [
    ("location", None, None,
     "Server location to advertise (e.g. tcp:example.org:12345)"),
    ("port", None, None,
     "Server endpoint to listen on (e.g. tcp:12345, or tcp:12345:interface=127.0.0.1."),
    ("hostname", None, None,
     "Hostname to automatically set --location/--port when --listen=tcp"),
    ("listen", None, "tcp",
     "Comma-separated list of listener types (tcp,tor,i2p), or none."),
    ("tor-control-port", None, None,
     "Endpoint of the Tor control port (for --listen=tor)"),
]

WHERE_FLAGS = [
    ("launch-tor", None, "Launch our own Tor (for --listen=tor)"),
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

class _CreateBaseOptions(BasedirOptions):
    optFlags = [
        ("hide-ip", None, "prohibit any configuration that would reveal the node's IP address"),
        ]

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

    # This is overridden in order to ensure we get a "Wrong number of
    # arguments." error when more than one argument is given.
    def parseArgs(self, basedir=None):
        BasedirOptions.parseArgs(self, basedir)

class CreateNodeOptions(CreateClientOptions):
    optFlags = [
        ("no-storage", None, "Do not offer storage service to other nodes."),
        ] + WHERE_FLAGS
    synopsis = "[options] [NODEDIR]"
    description = "Create a full Tahoe-LAFS node (client+server)."
    optParameters = CreateClientOptions.optParameters + WHERE_PARMS

    def parseArgs(self, basedir=None):
        CreateClientOptions.parseArgs(self, basedir)
        validate_where_options(self)

class CreateIntroducerOptions(NoDefaultBasedirOptions):
    subcommand_name = "create-introducer"
    description = "Create a Tahoe-LAFS introducer."
    optFlags = [
        ("hide-ip", None, "prohibit any configuration that would reveal the node's IP address"),
    ] + WHERE_FLAGS
    optParameters = NoDefaultBasedirOptions.optParameters + WHERE_PARMS
    def parseArgs(self, basedir=None):
        NoDefaultBasedirOptions.parseArgs(self, basedir)
        validate_where_options(self)

@defer.inlineCallbacks
def allocate_onion(config, basedir):
    raise NotImplementedError("--listen=tor is under development, "
                              "see ticket #2490 for details")
    control_port = config.get("tor-control-port")
    launch = config.get("launch-tor")
    config_lines = []
    hs_external_port = 3457
    hs_internal_port = iputil.allocate_tcp_port()
    hs_port = "%d 127.0.0.1:%d" % (hs_external_port, hs_internal_port)
    hs = txtorcon.EphemeralHiddenService([hs_port])
    d = hs.add_to_tor(CONTROL_PROTO)

    onion = hs.hostname
    privkey = hs.private_key
    relative_privkey_file = os.path.join("private", "onion.privkey")
    privkey_file = os.path.join(basedir, relative_privkey_file)
    with open(privkey_file, "w") as f:
        f.write(privkey)
    config_lines.append("onion.external_port = %d" % hs_external_port)
    config_lines.append("onion.privkey_file = %s" % relative_privkey_file)

    returnValue(onion, hs_internal_port, [])

def allocate_i2p(config):
    raise NotImplementedError("--listen=i2p is under development, "
                              "see ticket #2490 for details")

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
        c.write("tcp = tor\n")

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

    tub_ports = []
    location_hints = []
    tor_lines = []
    i2p_lines = []

    listeners = config['listen'].split(",")
    if listeners == ["none"]:
        tub_ports.append("disabled")
        location_hints.append("disabled")
    else:
        if "tor" in listeners:
            onion, localport, tor_lines = allocate_onion(config)
            tub_ports.append("tcp:%d:interface=127.0.0.1" % localport)
            location_hints.append("tor:%s:%d" % (onion, localport))
        if "i2p" in listeners:
            addr, localport, i2p_lines = allocate_i2p(config)
            tub_ports.append("tcp:%d:interface=127.0.0.1" % localport)
            location_hints.append("tor:%s:%d" % (onion, localport))
        if "tcp" in listeners:
            if config["port"]: # --port/--location are a pair
                tub_ports.append(config["port"].encode('utf-8'))
                location_hints.append(config["location"].encode('utf-8'))
            else:
                assert "hostname" in config
                hostname = config["hostname"]
                new_port = iputil.allocate_tcp_port()
                tub_ports.append("tcp:%s" % new_port)
                location_hints.append("tcp:%s:%s" % (hostname.encode('utf-8'),
                                                     new_port))
    assert len(tub_ports) == 1 # can't handle >1 yet
    c.write("tub.port = %s\n" % " ".join(tub_ports))
    c.write("tub.location = %s\n" % ",".join(location_hints))

    c.write("#log_gatherer.furl =\n")
    c.write("#timeout.keepalive =\n")
    c.write("#timeout.disconnect =\n")
    c.write("#ssh.port = 8022\n")
    c.write("#ssh.authorized_keys_file = ~/.ssh/authorized_keys\n")
    c.write("\n")

    if tor_lines:
        c.write("[tor]\n")
        for line in tor_lines:
            c.write(line)
            c.write("\n")
        c.write("\n")

    if i2p_lines:
        c.write("[i2p]\n")
        for line in i2p_lines:
            c.write(line)
            c.write("\n")
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
            return -1
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    write_tac(basedir, "client")

    with open(os.path.join(basedir, "tahoe.cfg"), "w") as c:
        yield write_node_config(c, config)
        write_client_config(c, config)

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
    config['listen'] = "none"
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
