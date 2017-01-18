# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, with_statement
import os

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.endpoints import clientFromString
from twisted.internet.error import ConnectionRefusedError, ConnectError
from twisted.application import service

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

@inlineCallbacks
def create_dest(reactor, cli_config):
    txi2p = _import_txi2p()
    if not txi2p:
        raise ValueError("Cannot create I2P Destination without txi2p. "
                         "Please 'pip install tahoe-lafs[i2p]' to fix this.")
    tahoe_config_i2p = {} # written into tahoe.cfg:[i2p]
    private_dir = os.path.abspath(os.path.join(cli_config["basedir"], "private"))
    stdout = cli_config.stdout
    if cli_config["i2p-launch"]:
        raise NotImplementedError("--i2p-launch is under development.")
    else:
        print("connecting to I2P (to allocate .i2p address)..", file=stdout)
        sam_port = yield _connect_to_i2p(reactor, cli_config, txi2p)
        print("I2P connection established", file=stdout)
        tahoe_config_i2p["sam.port"] = sam_port

    external_port = 3457 # TODO: pick this randomly? there's no contention.

    privkeyfile = os.path.join(private_dir, "i2p_dest.privkey")
    sam_endpoint = clientFromString(reactor, sam_port)
    print("allocating .i2p address...", file=stdout)
    dest = yield txi2p.generateDestination(reactor, privkeyfile, 'SAM', sam_endpoint)
    print(".i2p address allocated", file=stdout)
    escaped_sam_port = sam_port.replace(':', '\:')
    i2p_port = "i2p:%s:%d:api=SAM:apiEndpoint=%s" % \
        (privkeyfile, external_port, escaped_sam_port)
    i2p_location = "i2p:%s:%d" % (dest.host, external_port)

    # in addition to the "how to launch/connect-to i2p" keys above, we also
    # record information about the I2P service into tahoe.cfg.
    # * "port" is the random "public Destination port" (integer), which
    #   (when combined with the .i2p address) should match "i2p_location"
    #   (which will be added to tub.location)
    # * "private_key_file" points to the on-disk copy of the private key
    #   material (although we always write it to the same place)

    tahoe_config_i2p["dest"] = "true"
    tahoe_config_i2p["dest.port"] = str(external_port)
    tahoe_config_i2p["dest.private_key_file"] = os.path.join("private",
                                                             "i2p_dest.privkey")

    # tahoe_config_i2p: this is a dictionary of keys/values to add to the
    # "[i2p]" section of tahoe.cfg, which tells the new node how to launch
    # I2P in the right way.

    # i2p_port: a server endpoint string, it will be added to tub.port=

    # i2p_location: a foolscap connection hint, "i2p:B32_ADDR:PORT"

    # We assume/require that the Node gives us the same data_directory=
    # at both create-node and startup time. The data directory is not
    # recorded in tahoe.cfg

    returnValue((tahoe_config_i2p, i2p_port, i2p_location))

# we can always create a Provider. If foolscap.connections.i2p or txi2p
# are not installed, then get_i2p_handler() will return None. If tahoe.cfg
# wants to start an I2P Destination too, then check_dest_config() will throw
# a nice error, and startService will throw an ugly error.

class Provider(service.MultiService):
    def __init__(self, basedir, node_for_config, reactor):
        service.MultiService.__init__(self)
        self._basedir = basedir
        self._node_for_config = node_for_config
        self._i2p = _import_i2p()
        self._txi2p = _import_txi2p()
        self._reactor = reactor

    def _get_i2p_config(self, *args, **kwargs):
        return self._node_for_config.get_config("i2p", *args, **kwargs)

    def get_i2p_handler(self):
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
