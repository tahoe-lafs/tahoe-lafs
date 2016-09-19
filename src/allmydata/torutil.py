# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import with_statement

from twisted.internet import reactor, defer

import txtorcon
from txtorcon import torconfig
from txtorcon import torcontrolprotocol

@defer.inlineCallbacks
def CreateOnion(tor_provider, key_file, onion_port):
    local_port = yield txtorcon.util.available_tcp_port(reactor)
    # XXX in the future we need to make it use UNIX domain sockets instead of TCP
    hs_string = '%s 127.0.0.1:%d' % (onion_port, local_port)
    service = txtorcon.EphemeralHiddenService([hs_string])
    tor_protocol = yield tor_provider.get_control_protocol()
    yield service.add_to_tor(tor_protocol)

class TorProvider:
    def __init__(self, tor_binary=None, data_directory=None, control_endpoint=None):
        assert tor_binary is not None or control_endpoint is not None
        self.data_directory = data_directory
        self.tor_binary = tor_binary
        self.control_endpoint = control_endpoint
        self.tor_control_protocol = None

    def get_control_protocol(self):
        """
        Returns a deferred which fires with the txtorcon tor control port object
        """
        if self.tor_control_protocol is not None:
            d = defer.succeed(self.tor_control_protocol)
        else:
            if self.control_endpoint is None:
                config = torconfig.TorConfig()
                if self.data_directory is not None:
                    config['DataDirectory'] = self.data_directory
                d = torconfig.launch_tor(config, reactor, tor_binary=self.tor_binary)
                def remember_tor_protocol(result):
                    self.tor_control_protocol = result.tor_protocol
                    return result.tor_protocol
                d.addCallback(remember_tor_protocol)
            else:
                d = torcontrolprotocol.connect(self.control_endpoint) # XXX use a password_function?
                def remember_tor_protocol(result):
                    self.tor_control_protocol = result
                    return result
                d.addCallback(remember_tor_protocol)
        return d
