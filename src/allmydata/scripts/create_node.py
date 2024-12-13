
from __future__ import annotations

from typing import Optional

import io
import os

from allmydata.scripts.types_ import (
    SubCommands,
    Parameters,
    Flags,
)

from twisted.internet import reactor, defer
from twisted.python.usage import UsageError
from twisted.python.filepath import (
    FilePath,
)

from allmydata.scripts.common import (
    BasedirOptions,
    NoDefaultBasedirOptions,
    write_introducer,
)
from allmydata.scripts.default_nodedir import _default_nodedir
from allmydata.util import dictutil
from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, argv_to_unicode, quote_local_unicode_path, get_io_encoding

i2p_provider: Listener
tor_provider: Listener

from allmydata.util import fileutil, i2p_provider, tor_provider, jsonbytes as json

from ..listeners import ListenerConfig, Listener, TCPProvider, StaticProvider

def _get_listeners() -> dict[str, Listener]:
    """
    Get all of the kinds of listeners we might be able to use.
    """
    return {
        "tor": tor_provider,
        "i2p": i2p_provider,
        "tcp": TCPProvider(),
        "none": StaticProvider(
            available=True,
            hide_ip=False,
            config=defer.succeed(None),
            # This is supposed to be an IAddressFamily but we have none for
            # this kind of provider.  We could implement new client and server
            # endpoint types that always fail and pass an IAddressFamily here
            # that uses those.  Nothing would ever even ask for them (at
            # least, yet), let alone try to use them, so that's a lot of extra
            # work for no practical result so I'm not doing it now.
            address=None, # type: ignore[arg-type]
        ),
    }

_LISTENERS = _get_listeners()

dummy_tac = """
import sys
print("Nodes created by Tahoe-LAFS v1.11.0 or later cannot be run by")
print("releases of Tahoe-LAFS before v1.10.0.")
sys.exit(1)
"""

def write_tac(basedir, nodetype):
    fileutil.write(os.path.join(basedir, "tahoe-%s.tac" % (nodetype,)), dummy_tac)


WHERE_OPTS : Parameters = [
    ("location", None, None,
     "Server location to advertise (e.g. tcp:example.org:12345)"),
    ("port", None, None,
     "Server endpoint to listen on (e.g. tcp:12345, or tcp:12345:interface=127.0.0.1."),
    ("hostname", None, None,
     "Hostname to automatically set --location/--port when --listen=tcp"),
    ("listen", None, "tcp",
     "Comma-separated list of listener types (tcp,tor,i2p,none)."),
]

TOR_OPTS : Parameters = [
    ("tor-control-port", None, None,
     "Tor's control port endpoint descriptor string (e.g. tcp:127.0.0.1:9051 or unix:/var/run/tor/control)"),
    ("tor-executable", None, None,
     "The 'tor' executable to run (default is to search $PATH)."),
]

TOR_FLAGS : Flags = [
    ("tor-launch", None, "Launch a tor instead of connecting to a tor control port."),
]

I2P_OPTS : Parameters = [
    ("i2p-sam-port", None, None,
     "I2P's SAM API port endpoint descriptor string (e.g. tcp:127.0.0.1:7656)"),
    ("i2p-executable", None, None,
     "(future) The 'i2prouter' executable to run (default is to search $PATH)."),
]

I2P_FLAGS : Flags = [
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
        if o['listen'] != "none" and o.get('join', None) is None:
            listeners = o['listen'].split(",")
            for l in listeners:
                if l not in _LISTENERS:
                    raise UsageError(
                        "--listen= must be one/some of: "
                        f"{', '.join(sorted(_LISTENERS))}",
                    )
            if 'tcp' in listeners and not o['hostname']:
                raise UsageError("--listen=tcp requires --hostname=")
            if 'tcp' not in listeners and o['hostname']:
                raise UsageError("--listen= must be tcp to use --hostname")

def validate_tor_options(o):
    use_tor = "tor" in o["listen"].split(",")
    if use_tor or any((o["tor-launch"], o["tor-control-port"])):
        if not _LISTENERS["tor"].is_available():
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
        if not _LISTENERS["i2p"].is_available():
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
            ip_hiders = dictutil.filter(lambda v: v.can_hide_ip(), _LISTENERS)
            available = dictutil.filter(lambda v: v.is_available(), ip_hiders)
            if not available:
                raise UsageError(
                    "--hide-ip was specified but no IP-hiding listener is installed.\n"
                    "Try one of these:\n" +
                    "".join([
                        f"\tpip install tahoe-lafs[{name}]\n"
                        for name
                        in ip_hiders
                    ])
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
        ("join", None, None, "Join a grid with the given Invite Code."),
        ] # type: Parameters

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
        ("helper", None, "Enable helper"),
    ] + TOR_FLAGS + I2P_FLAGS

    synopsis = "[options] [NODEDIR]"
    description = "Create a full Tahoe-LAFS node (client+server)."

    optParameters = [
        ("storage-dir", None, None, "Path where the storage will be placed."),
    ] + CreateClientOptions.optParameters + WHERE_OPTS + TOR_OPTS + I2P_OPTS

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


def merge_config(
        left: Optional[ListenerConfig],
        right: Optional[ListenerConfig],
) -> Optional[ListenerConfig]:
    """
    Merge two listener configurations into one configuration representing
    both of them.

    If either is ``None`` then the result is ``None``.  This supports the
    "disable listeners" functionality.

    :raise ValueError: If the keys in the node configs overlap.
    """
    if left is None or right is None:
        return None

    overlap = set(left.node_config) & set(right.node_config)
    if overlap:
        raise ValueError(f"Node configs overlap: {overlap}")

    return ListenerConfig(
        list(left.tub_ports) + list(right.tub_ports),
        list(left.tub_locations) + list(right.tub_locations),
        dict(list(left.node_config.items()) + list(right.node_config.items())),
    )


async def write_node_config(c, config):
    # this is shared between clients and introducers
    c.write("# -*- mode: conf; coding: {c.encoding} -*-\n".format(c=c))
    c.write("\n")
    c.write("# This file controls the configuration of the Tahoe node that\n")
    c.write("# lives in this directory. It is only read at node startup.\n")
    c.write("# For details about the keys that can be set here, please\n")
    c.write("# read the 'docs/configuration.rst' file that came with your\n")
    c.write("# Tahoe installation.\n")
    c.write("\n\n")

    if config["hide-ip"]:
        c.write("[connections]\n")
        if _LISTENERS["tor"].is_available():
            c.write("tcp = tor\n")
        else:
            # XXX What about i2p?
            c.write("tcp = disabled\n")
        c.write("\n")

    c.write("[node]\n")
    nickname = argv_to_unicode(config.get("nickname") or "")
    c.write("nickname = %s\n" % (nickname,))
    if config["hide-ip"]:
        c.write("reveal-IP-address = false\n")
    else:
        c.write("reveal-IP-address = true\n")

    # TODO: validate webport
    webport = argv_to_unicode(config.get("webport") or "none")
    if webport.lower() == "none":
        webport = ""
    c.write("web.port = %s\n" % (webport,))
    c.write("web.static = public_html\n")

    listener_config = ListenerConfig([], [], {})
    for listener_name in config['listen'].split(","):
        listener = _LISTENERS[listener_name]
        listener_config = merge_config(
            (await listener.create_config(reactor, config)),
            listener_config,
        )

    if listener_config is None:
        tub_ports = ["disabled"]
        tub_locations = ["disabled"]
    else:
        tub_ports = listener_config.tub_ports
        tub_locations = listener_config.tub_locations

    c.write("tub.port = %s\n" % ",".join(tub_ports))
    c.write("tub.location = %s\n" % ",".join(tub_locations))
    c.write("\n")

    c.write("#log_gatherer.furl =\n")
    c.write("#timeout.keepalive =\n")
    c.write("#timeout.disconnect =\n")
    c.write("#ssh.port = 8022\n")
    c.write("#ssh.authorized_keys_file = ~/.ssh/authorized_keys\n")
    c.write("\n")

    if listener_config is not None:
        for section, items in listener_config.node_config.items():
            c.write(f"[{section}]\n")
            for k, v in items:
                c.write(f"{k} = {v}\n")
            c.write("\n")


def write_client_config(c, config):
    introducer = config.get("introducer", None)
    if introducer is not None:
        write_introducer(
            FilePath(config["basedir"]),
            "default",
            introducer,
        )

    c.write("[client]\n")
    c.write("helper.furl =\n")
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
    storage_dir = config.get("storage-dir")
    if storage_dir:
        c.write("storage_dir = %s\n" % (storage_dir,))
    else:
        c.write("#storage_dir =\n")
    c.write("#expire.enabled =\n")
    c.write("#expire.mode =\n")
    c.write("\n")

    c.write("[helper]\n")
    c.write("# Shall this node run a helper service that clients can use?\n")
    if config.get("helper"):
        c.write("enabled = true\n")
    else:
        c.write("enabled = false\n")
    c.write("\n")


@defer.inlineCallbacks
def _get_config_via_wormhole(config):
    out = config.stdout
    print("Opening wormhole with code '{}'".format(config['join']), file=out)
    relay_url = config.parent['wormhole-server']
    print("Connecting to '{}'".format(relay_url), file=out)

    wh = config.parent.wormhole.create(
        appid=config.parent['wormhole-invite-appid'],
        relay_url=relay_url,
        reactor=reactor,
    )
    code = str(config['join'])
    wh.set_code(code)
    yield wh.get_welcome()
    print("Connected to wormhole server", file=out)

    intro = {
        u"abilities": {
            "client-v1": {},
        }
    }
    wh.send_message(json.dumps_bytes(intro))

    server_intro = yield wh.get_message()
    server_intro = json.loads(server_intro)

    print("  received server introduction", file=out)
    if u'abilities' not in server_intro:
        raise RuntimeError("  Expected 'abilities' in server introduction")
    if u'server-v1' not in server_intro['abilities']:
        raise RuntimeError("  Expected 'server-v1' in server abilities")

    remote_data = yield wh.get_message()
    print("  received configuration", file=out)
    defer.returnValue(json.loads(remote_data))


@defer.inlineCallbacks
def create_node(config):
    out = config.stdout
    err = config.stderr
    basedir = config['basedir']
    # This should always be called with an absolute Unicode basedir.
    precondition(isinstance(basedir, str), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print("The base directory %s is not empty." % quote_local_unicode_path(basedir), file=err)
            print("To avoid clobbering anything, I am going to quit now.", file=err)
            print("Please use a different directory, or empty this one.", file=err)
            defer.returnValue(-1)
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    write_tac(basedir, "client")

    # if we're doing magic-wormhole stuff, do it now
    if config['join'] is not None:
        try:
            remote_config = yield _get_config_via_wormhole(config)
        except RuntimeError as e:
            print(str(e), file=err)
            defer.returnValue(1)

        # configuration we'll allow the inviter to set
        whitelist = [
            'shares-happy', 'shares-needed', 'shares-total',
            'introducer', 'nickname',
        ]
        sensitive_keys = ['introducer']

        print("Encoding: {shares-needed} of {shares-total} shares, on at least {shares-happy} servers".format(**remote_config), file=out)
        print("Overriding the following config:", file=out)

        for k in whitelist:
            v = remote_config.get(k, None)
            if v is not None:
                # we're faking usually argv-supplied options :/
                v_orig = v
                if isinstance(v, str):
                    v = v.encode(get_io_encoding())
                config[k] = v
                if k not in sensitive_keys:
                    if k not in ['shares-happy', 'shares-total', 'shares-needed']:
                        print("  {}: {}".format(k, v_orig), file=out)
                else:
                    print("  {}: [sensitive data; see tahoe.cfg]".format(k), file=out)

    fileutil.make_dirs(os.path.join(basedir, "private"), 0o700)
    cfg_name = os.path.join(basedir, "tahoe.cfg")
    with io.open(cfg_name, "w", encoding='utf-8') as c:
        yield defer.Deferred.fromCoroutine(write_node_config(c, config))
        write_client_config(c, config)

    print("Node created in %s" % quote_local_unicode_path(basedir), file=out)
    tahoe_cfg = quote_local_unicode_path(os.path.join(basedir, "tahoe.cfg"))
    introducers_yaml = quote_local_unicode_path(
        os.path.join(basedir, "private", "introducers.yaml"),
    )
    if not config.get("introducer", ""):
        print(" Please add introducers to %s!" % (introducers_yaml,), file=out)
        print(" The node cannot connect to a grid without it.", file=out)
    if not config.get("nickname", ""):
        print(" Please set [node]nickname= in %s" % tahoe_cfg, file=out)
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
    precondition(isinstance(basedir, str), basedir)

    if os.path.exists(basedir):
        if listdir_unicode(basedir):
            print("The base directory %s is not empty." % quote_local_unicode_path(basedir), file=err)
            print("To avoid clobbering anything, I am going to quit now.", file=err)
            print("Please use a different directory, or empty this one.", file=err)
            defer.returnValue(-1)
        # we're willing to use an empty directory
    else:
        os.mkdir(basedir)
    write_tac(basedir, "introducer")

    fileutil.make_dirs(os.path.join(basedir, "private"), 0o700)
    cfg_name = os.path.join(basedir, "tahoe.cfg")
    with io.open(cfg_name, "w", encoding='utf-8') as c:
        yield defer.Deferred.fromCoroutine(write_node_config(c, config))

    print("Introducer created in %s" % quote_local_unicode_path(basedir), file=out)
    defer.returnValue(0)


subCommands : SubCommands = [
    ("create-node", None, CreateNodeOptions, "Create a node that acts as a client, server or both."),
    ("create-client", None, CreateClientOptions, "Create a client node (with storage initially disabled)."),
    ("create-introducer", None, CreateIntroducerOptions, "Create an introducer node."),
]

dispatch = {
    "create-node": create_node,
    "create-client": create_client,
    "create-introducer": create_introducer,
    }
