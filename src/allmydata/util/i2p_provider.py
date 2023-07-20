# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any
from typing_extensions import Literal

import os

from zope.interface import (
    implementer,
)

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.endpoints import clientFromString
from twisted.internet.error import ConnectionRefusedError, ConnectError
from twisted.application import service
from twisted.python.usage import Options

from ..listeners import ListenerConfig
from ..interfaces import (
    IAddressFamily,
)
from ..node import _Config

def create(reactor: Any, config: _Config) -> IAddressFamily:
    """
    Create a new Provider service (this is an IService so must be
    hooked up to a parent or otherwise started).

    If foolscap.connections.i2p or txi2p are not installed, then
    Provider.get_i2p_handler() will return None. If 'tahoe.cfg' wants
    to start an I2P Destination too, then this `create()` method will
    throw a nice error (and startService will throw an ugly error).
    """
    provider = _Provider(config, reactor)
    provider.check_dest_config()
    return provider


def _import_i2p():
    # this exists to be overridden by unit tests
    try:
        from foolscap.connections import i2p
        return i2p
    except ImportError: # pragma: no cover
        return None

def _import_txi2p():
    try:
        import txi2p
        return txi2p
    except ImportError: # pragma: no cover
        return None

def is_available() -> bool:
    """
    Can this type of listener actually be used in this runtime
    environment?

    If its dependencies are missing then it cannot be.
    """
    return not (_import_i2p() is None or _import_txi2p() is None)

def can_hide_ip() -> Literal[True]:
    """
    Can the transport supported by this type of listener conceal the
    node's public internet address from peers?
    """
    return True

def _try_to_connect(reactor, endpoint_desc, stdout, txi2p):
    # yields True or None
    ep = clientFromString(reactor, endpoint_desc)
    d = txi2p.testAPI(reactor, 'SAM', ep)
    def _failed(f):
        # depending upon what's listening at that endpoint, we might get
        # various errors. If this list is too short, we might expose an
        # exception to the user (causing "tahoe create-node" to fail messily)
        # when we're supposed to just try the next potential port instead.
        # But I don't want to catch everything, because that may hide actual
        # coding errors.
        f.trap(ConnectionRefusedError, # nothing listening on TCP
               ConnectError, # missing unix socket, or permission denied
               #ValueError,
               # connecting to e.g. an HTTP server causes an
               # UnhandledException (around a ValueError) when the handshake
               # fails to parse, but that's not something we can catch. The
               # attempt hangs, so don't do that.
               RuntimeError, # authentication failure
               )
        if stdout:
            stdout.write("Unable to reach I2P SAM API at '%s': %s\n" %
                         (endpoint_desc, f.value))
        return None
    d.addErrback(_failed)
    return d

@inlineCallbacks
def _connect_to_i2p(reactor, cli_config, txi2p):
    # we assume i2p is already running
    ports_to_try = ["tcp:127.0.0.1:7656"]
    if cli_config["i2p-sam-port"]:
        ports_to_try = [cli_config["i2p-sam-port"]]
    for port in ports_to_try:
        accessible = yield _try_to_connect(reactor, port, cli_config.stdout,
                                           txi2p)
        if accessible:
            returnValue(port) ; break # helps editor
    else:
        raise ValueError("unable to reach any default I2P SAM port")

async def create_config(reactor: Any, cli_config: Options) -> ListenerConfig:
    """
    For a given set of command-line options, construct an I2P listener.

    This includes allocating a new I2P address.
    """
    txi2p = _import_txi2p()
    if not txi2p:
        raise ValueError("Cannot create I2P Destination without txi2p. "
                         "Please 'pip install tahoe-lafs[i2p]' to fix this.")
    tahoe_config_i2p = [] # written into tahoe.cfg:[i2p]
    private_dir = os.path.abspath(os.path.join(cli_config["basedir"], "private"))
    # XXX We shouldn't carry stdout around by jamming it into the Options
    # value.  See https://tahoe-lafs.org/trac/tahoe-lafs/ticket/4048
    stdout = cli_config.stdout # type: ignore[attr-defined]
    if cli_config["i2p-launch"]:
        raise NotImplementedError("--i2p-launch is under development.")
    else:
        print("connecting to I2P (to allocate .i2p address)..", file=stdout)
        sam_port = await _connect_to_i2p(reactor, cli_config, txi2p)
        print("I2P connection established", file=stdout)
        tahoe_config_i2p.append(("sam.port", sam_port))

    external_port = 3457 # TODO: pick this randomly? there's no contention.

    privkeyfile = os.path.join(private_dir, "i2p_dest.privkey")
    sam_endpoint = clientFromString(reactor, sam_port)
    print("allocating .i2p address...", file=stdout)
    dest = await txi2p.generateDestination(reactor, privkeyfile, 'SAM', sam_endpoint)
    print(".i2p address allocated", file=stdout)
    i2p_port = "listen:i2p" # means "see [i2p]", calls Provider.get_listener()
    i2p_location = "i2p:%s:%d" % (dest.host, external_port)

    # in addition to the "how to launch/connect-to i2p" keys above, we also
    # record information about the I2P service into tahoe.cfg.
    # * "port" is the random "public Destination port" (integer), which
    #   (when combined with the .i2p address) should match "i2p_location"
    #   (which will be added to tub.location)
    # * "private_key_file" points to the on-disk copy of the private key
    #   material (although we always write it to the same place)

    tahoe_config_i2p.extend([
        ("dest", "true"),
        ("dest.port", str(external_port)),
        ("dest.private_key_file", os.path.join("private", "i2p_dest.privkey")),
    ])

    # tahoe_config_i2p: this is a dictionary of keys/values to add to the
    # "[i2p]" section of tahoe.cfg, which tells the new node how to launch
    # I2P in the right way.

    # i2p_port: a server endpoint string, it will be added to tub.port=

    # i2p_location: a foolscap connection hint, "i2p:B32_ADDR:PORT"

    # We assume/require that the Node gives us the same data_directory=
    # at both create-node and startup time. The data directory is not
    # recorded in tahoe.cfg

    return ListenerConfig([i2p_port], [i2p_location], {"i2p": tahoe_config_i2p})


@implementer(IAddressFamily)
class _Provider(service.MultiService):
    def __init__(self, config, reactor):
        service.MultiService.__init__(self)
        self._config = config
        self._i2p = _import_i2p()
        self._txi2p = _import_txi2p()
        self._reactor = reactor

    def _get_i2p_config(self, *args, **kwargs):
        return self._config.get_config("i2p", *args, **kwargs)

    def get_listener(self):
        # this is relative to BASEDIR, and our cwd should be BASEDIR
        privkeyfile = self._get_i2p_config("dest.private_key_file")
        external_port = self._get_i2p_config("dest.port")
        sam_port = self._get_i2p_config("sam.port")
        escaped_sam_port = sam_port.replace(':', '\:')
        # for now, this returns a string, which then gets passed to
        # endpoints.serverFromString . But it can also return an Endpoint
        # directly, which means we don't need to encode all these options
        # into a string
        i2p_port = "i2p:%s:%s:api=SAM:apiEndpoint=%s" % \
                   (privkeyfile, external_port, escaped_sam_port)
        return i2p_port

    def get_client_endpoint(self):
        """
        Get an ``IStreamClientEndpoint`` which will set up a connection to an I2P
        address.

        If I2P is not enabled or the dependencies are not available, return
        ``None`` instead.
        """
        enabled = self._get_i2p_config("enabled", True, boolean=True)
        if not enabled:
            return None
        if not self._i2p:
            return None

        sam_port = self._get_i2p_config("sam.port", None)
        launch = self._get_i2p_config("launch", False, boolean=True)
        configdir = self._get_i2p_config("i2p.configdir", None)
        keyfile = self._get_i2p_config("dest.private_key_file", None)

        if sam_port:
            if launch:
                raise ValueError("tahoe.cfg [i2p] must not set both "
                                 "sam.port and launch")
            ep = clientFromString(self._reactor, sam_port)
            return self._i2p.sam_endpoint(ep, keyfile=keyfile)

        if launch:
            executable = self._get_i2p_config("i2p.executable", None)
            return self._i2p.launch(i2p_configdir=configdir, i2p_binary=executable)

        if configdir:
            return self._i2p.local_i2p(configdir)

        return self._i2p.default(self._reactor, keyfile=keyfile)

    # Backwards compatibility alias
    get_i2p_handler = get_client_endpoint

    def check_dest_config(self):
        if self._get_i2p_config("dest", False, boolean=True):
            if not self._txi2p:
                raise ValueError("Cannot create I2P Destination without txi2p. "
                                 "Please 'pip install tahoe-lafs[i2p]' to fix.")

            # to start an I2P server, we either need an I2P SAM port, or
            # we need to launch I2P
            sam_port = self._get_i2p_config("sam.port", None)
            launch = self._get_i2p_config("launch", False, boolean=True)
            configdir = self._get_i2p_config("i2p.configdir", None)
            if not sam_port and not launch and not configdir:
                raise ValueError("[i2p] dest = true, but we have neither "
                                 "sam.port= nor launch=true nor configdir=")
            if sam_port and launch:
                raise ValueError("tahoe.cfg [i2p] must not set both "
                                 "sam.port and launch")
            if launch:
                raise NotImplementedError("[i2p] launch is under development.")
            # check that all the expected Destination-specific keys are present
            def require(name):
                if not self._get_i2p_config("dest.%s" % name, None):
                    raise ValueError("[i2p] dest = true,"
                                     " but dest.%s= is missing" % name)
            require("port")
            require("private_key_file")

    def startService(self):
        service.MultiService.startService(self)
        # if we need to start I2P, now is the time
        # TODO: implement i2p launching

    @inlineCallbacks
    def stopService(self):
        # TODO: can we also stop i2p?
        yield service.MultiService.stopService(self)
