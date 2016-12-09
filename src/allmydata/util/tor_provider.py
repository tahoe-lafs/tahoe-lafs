# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, with_statement
import os

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.endpoints import clientFromString
from twisted.internet.error import ConnectionRefusedError, ConnectError
from twisted.application import service

from .observer import OneShotObserverList
from .iputil import allocate_tcp_port

def _import_tor():
    # this exists to be overridden by unit tests
    try:
        from foolscap.connections import tor
        return tor
    except ImportError: # pragma: no cover
        return None

def _import_txtorcon():
    try:
        import txtorcon
        return txtorcon
    except ImportError: # pragma: no cover
        return None

def data_directory(private_dir):
    return os.path.join(private_dir, "tor-statedir")

# different ways we might approach this:

# 1: get an ITorControlProtocol, make a
# txtorcon.EphemeralHiddenService(ports), yield ehs.add_to_tor(tcp), store
# ehs.hostname and ehs.private_key, yield ehs.remove_from_tor(tcp)

def _try_to_connect(reactor, endpoint_desc, stdout, txtorcon):
    # yields a TorState, or None
    ep = clientFromString(reactor, endpoint_desc)
    d = txtorcon.build_tor_connection(ep)
    def _failed(f):
        # depending upon what's listening at that endpoint, we might get
        # various errors. If this list is too short, we might expose an
        # exception to the user (causing "tahoe create-node" to fail messily)
        # when we're supposed to just try the next potential port instead.
        # But I don't want to catch everything, because that may hide actual
        # coding errrors.
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
            stdout.write("Unable to reach Tor at '%s': %s\n" %
                         (endpoint_desc, f.value))
        return None
    d.addErrback(_failed)
    return d

@inlineCallbacks
def _launch_tor(reactor, tor_executable, private_dir, txtorcon):
    # TODO: handle default tor-executable
    # TODO: it might be a good idea to find exactly which Tor we used,
    # and record it's absolute path into tahoe.cfg . This would protect
    # us against one Tor being on $PATH at create-node time, but then a
    # different Tor being present at node startup. OTOH, maybe we don't
    # need to worry about it.
    tor_config = txtorcon.TorConfig()
    tor_config.DataDirectory = data_directory(private_dir)

    if True: # unix-domain control socket
        tor_config.ControlPort = "unix:" + os.path.join(private_dir, "tor.control")
        tor_control_endpoint_desc = tor_config.ControlPort
    else:
        # we allocate a new TCP control port each time
        tor_config.ControlPort = allocate_tcp_port()
        tor_control_endpoint_desc = "tcp:127.0.0.1:%d" % tor_config.ControlPort

    tor_config.SOCKSPort = allocate_tcp_port()

    tpp = yield txtorcon.launch_tor(
        tor_config, reactor,
        tor_binary=tor_executable,
        # can be useful when debugging; mirror Tor's output to ours
        # stdout=sys.stdout,
        # stderr=sys.stderr,
    )

    # now tor is launched and ready to be spoken to
    # as a side effect, we've got an ITorControlProtocol ready to go
    tor_control_proto = tpp.tor_protocol

    # How/when to shut down the new process? for normal usage, the child
    # tor will exit when it notices its parent (us) quit. Unit tests will
    # mock out txtorcon.launch_tor(), so there will never be a real Tor
    # process. So I guess we don't need to track the process.

    # If we do want to do anything with it, we can call tpp.quit()
    # (because it's a TorProcessProtocol) which returns a Deferred
    # that fires when Tor has actually exited.

    returnValue((tor_control_endpoint_desc, tor_control_proto))

@inlineCallbacks
def _connect_to_tor(reactor, cli_config, txtorcon):
    # we assume tor is already running
    ports_to_try = ["unix:/var/run/tor/control",
                    "tcp:127.0.0.1:9051",
                    "tcp:127.0.0.1:9151", # TorBrowserBundle
                    ]
    if cli_config["tor-control-port"]:
        ports_to_try = [cli_config["tor-control-port"]]
    for port in ports_to_try:
        tor_state = yield _try_to_connect(reactor, port, cli_config.stdout,
                                          txtorcon)
        if tor_state:
            tor_control_proto = tor_state.protocol
            returnValue((port, tor_control_proto)) ; break # helps editor
    else:
        raise ValueError("unable to reach any default Tor control port")

@inlineCallbacks
def create_onion(reactor, cli_config):
    txtorcon = _import_txtorcon()
    if not txtorcon:
        raise ValueError("Cannot create onion without txtorcon. "
                         "Please 'pip install tahoe-lafs[tor]' to fix this.")
    tahoe_config_tor = {} # written into tahoe.cfg:[tor]
    private_dir = os.path.abspath(os.path.join(cli_config["basedir"], "private"))
    stdout = cli_config.stdout
    if cli_config["tor-launch"]:
        tahoe_config_tor["launch"] = "true"
        tor_executable = cli_config["tor-executable"]
        if tor_executable:
            tahoe_config_tor["tor.executable"] = tor_executable
        print("launching Tor (to allocate .onion address)..", file=stdout)
        (_, tor_control_proto) = yield _launch_tor(
            reactor, tor_executable, private_dir, txtorcon)
        print("Tor launched", file=stdout)
    else:
        print("connecting to Tor (to allocate .onion address)..", file=stdout)
        (port, tor_control_proto) = yield _connect_to_tor(
            reactor, cli_config, txtorcon)
        print("Tor connection established", file=stdout)
        tahoe_config_tor["control.port"] = port

    external_port = 3457 # TODO: pick this randomly? there's no contention.

    local_port = allocate_tcp_port()
    ehs = txtorcon.EphemeralHiddenService(
        "%d 127.0.0.1:%d" % (external_port, local_port)
    )
    print("allocating .onion address (takes ~40s)..", file=stdout)
    yield ehs.add_to_tor(tor_control_proto)
    print(".onion address allocated", file=stdout)
    tor_port = "tcp:%d:interface=127.0.0.1" % local_port
    tor_location = "tor:%s:%d" % (ehs.hostname, external_port)
    privkey = ehs.private_key
    yield ehs.remove_from_tor(tor_control_proto)

    # in addition to the "how to launch/connect-to tor" keys above, we also
    # record information about the onion service into tahoe.cfg.
    # * "local_port" is a server endpont string, which should match
    #   "tor_port" (which will be added to tahoe.cfg [node] tub.port)
    # * "external_port" is the random "public onion port" (integer), which
    #   (when combined with the .onion address) should match "tor_location"
    #   (which will be added to tub.location)
    # * "private_key_file" points to the on-disk copy of the private key
    #   material (although we always write it to the same place)

    tahoe_config_tor["onion"] = "true"
    tahoe_config_tor["onion.local_port"] = str(local_port)
    tahoe_config_tor["onion.external_port"] = str(external_port)
    assert privkey
    tahoe_config_tor["onion.private_key_file"] = os.path.join("private",
                                                              "tor_onion.privkey")
    privkeyfile = os.path.join(private_dir, "tor_onion.privkey")
    with open(privkeyfile, "wb") as f:
        f.write(privkey)

    # tahoe_config_tor: this is a dictionary of keys/values to add to the
    # "[tor]" section of tahoe.cfg, which tells the new node how to launch
    # Tor in the right way.

    # tor_port: a server endpoint string, it will be added to tub.port=

    # tor_location: a foolscap connection hint, "tor:ONION:EXTERNAL_PORT"

    # We assume/require that the Node gives us the same data_directory=
    # at both create-node and startup time. The data directory is not
    # recorded in tahoe.cfg

    returnValue((tahoe_config_tor, tor_port, tor_location))

# we can always create a Provider. If foolscap.connections.tor or txtorcon
# are not installed, then get_tor_handler() will return None. If tahoe.cfg
# wants to start an onion service too, then check_onion_config() will throw a
# nice error, and startService will throw an ugly error.

class Provider(service.MultiService):
    def __init__(self, basedir, node_for_config, reactor):
        service.MultiService.__init__(self)
        self._basedir = basedir
        self._node_for_config = node_for_config
        self._tor_launched = None
        self._onion_ehs = None
        self._onion_tor_control_proto = None
        self._tor = _import_tor()
        self._txtorcon = _import_txtorcon()
        self._reactor = reactor

    def _get_tor_config(self, *args, **kwargs):
        return self._node_for_config.get_config("tor", *args, **kwargs)

    def get_tor_handler(self):
        enabled = self._get_tor_config("enabled", True, boolean=True)
        if not enabled:
            return None
        if not self._tor:
            return None

        if self._get_tor_config("launch", False, boolean=True):
            if not self._txtorcon:
                return None
            return self._tor.control_endpoint_maker(self._make_control_endpoint,
                                                    takes_status=True)

        socks_endpoint_desc = self._get_tor_config("socks.port", None)
        if socks_endpoint_desc:
            socks_ep = clientFromString(self._reactor, socks_endpoint_desc)
            return self._tor.socks_endpoint(socks_ep)

        controlport = self._get_tor_config("control.port", None)
        if controlport:
            ep = clientFromString(self._reactor, controlport)
            return self._tor.control_endpoint(ep)

        return self._tor.default_socks()

    @inlineCallbacks
    def _make_control_endpoint(self, reactor, update_status):
        # this will only be called when tahoe.cfg has "[tor] launch = true"
        update_status("launching Tor")
        with self._tor.add_context(update_status, "launching Tor"):
            (endpoint_desc, _) = yield self._get_launched_tor(reactor)
        tor_control_endpoint = clientFromString(reactor, endpoint_desc)
        returnValue(tor_control_endpoint)

    def _get_launched_tor(self, reactor):
        # this fires with a tuple of (control_endpoint, tor_protocol)
        if not self._tor_launched:
            self._tor_launched = OneShotObserverList()
            private_dir = os.path.join(self._basedir, "private")
            tor_binary = self._get_tor_config("tor.executable", None)
            d = _launch_tor(reactor, tor_binary, private_dir, self._txtorcon)
            d.addBoth(self._tor_launched.fire)
        return self._tor_launched.when_fired()

    def check_onion_config(self):
        if self._get_tor_config("onion", False, boolean=True):
            if not self._txtorcon:
                raise ValueError("Cannot create onion without txtorcon. "
                                 "Please 'pip install tahoe-lafs[tor]' to fix.")

            # to start an onion server, we either need a Tor control port, or
            # we need to launch tor
            launch = self._get_tor_config("launch", False, boolean=True)
            controlport = self._get_tor_config("control.port", None)
            if not launch and not controlport:
                raise ValueError("[tor] onion = true, but we have neither "
                                 "launch=true nor control.port=")
            # check that all the expected onion-specific keys are present
            def require(name):
                if not self._get_tor_config("onion.%s" % name, None):
                    raise ValueError("[tor] onion = true,"
                                     " but onion.%s= is missing" % name)
            require("local_port")
            require("external_port")
            require("private_key_file")

    @inlineCallbacks
    def _start_onion(self, reactor):
        # launch tor, if necessary
        if self._get_tor_config("launch", False, boolean=True):
            (_, tor_control_proto) = yield self._get_launched_tor(reactor)
        else:
            controlport = self._get_tor_config("control.port", None)
            tcep = clientFromString(reactor, controlport)
            tor_state = yield self._txtorcon.build_tor_connection(tcep)
            tor_control_proto = tor_state.protocol

        local_port = int(self._get_tor_config("onion.local_port"))
        external_port = int(self._get_tor_config("onion.external_port"))

        fn = self._get_tor_config("onion.private_key_file")
        privkeyfile = os.path.join(self._basedir, fn)
        with open(privkeyfile, "rb") as f:
            privkey = f.read()
        ehs = self._txtorcon.EphemeralHiddenService(
            "%d 127.0.0.1:%d" % (external_port, local_port), privkey)
        yield ehs.add_to_tor(tor_control_proto)
        self._onion_ehs = ehs
        self._onion_tor_control_proto = tor_control_proto


    def startService(self):
        service.MultiService.startService(self)
        # if we need to start an onion service, now is the time
        if self._get_tor_config("onion", False, boolean=True):
            return self._start_onion(self._reactor) # so tests can synchronize

    @inlineCallbacks
    def stopService(self):
        if self._onion_ehs and self._onion_tor_control_proto:
            yield self._onion_ehs.remove_from_tor(self._onion_tor_control_proto)
        # TODO: can we also stop tor?
        yield service.MultiService.stopService(self)
