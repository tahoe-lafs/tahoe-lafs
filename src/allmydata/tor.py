# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import with_statement

from twisted.internet import reactor, defer

from txtorcon import torconfig
from txtorcon import torcontrolprotocol


class TorProvider:

    def __init__(self, tor_binary=None, control_endpoint=None):
        assert tor_binary is not None or control_endpoint is not None

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
