
from base64 import b32encode, b32decode

import re
from zope.interface import implements
from twisted.application import service
from twisted.python import log
from foolscap import Referenceable
from allmydata.interfaces import RIIntroducer, RIIntroducerClient
from allmydata.util import observer

class Introducer(service.MultiService, Referenceable):
    implements(RIIntroducer)
    name = "introducer"

    def __init__(self):
        service.MultiService.__init__(self)
        self.nodes = set()
        self.furls = set()
        self._encoding_parameters = None

    def set_encoding_parameters(self, parameters):
        self._encoding_parameters = parameters

    def remote_hello(self, node, furl):
        log.msg("introducer: new contact at %s, node is %s" % (furl, node))
        def _remove():
            log.msg(" introducer: removing %s %s" % (node, furl))
            self.nodes.remove(node)
            self.furls.remove(furl)
        node.notifyOnDisconnect(_remove)
        self.furls.add(furl)
        node.callRemote("new_peers", self.furls)
        if self._encoding_parameters is not None:
            node.callRemote("set_encoding_parameters",
                            self._encoding_parameters)
        for othernode in self.nodes:
            othernode.callRemote("new_peers", set([furl]))
        self.nodes.add(node)


class IntroducerClient(service.Service, Referenceable):
    implements(RIIntroducerClient)

    def __init__(self, tub, introducer_furl, my_furl):
        self.tub = tub
        self.introducer_furl = introducer_furl
        self.my_furl = my_furl

        self.connections = {} # k: nodeid, v: ref
        self.reconnectors = {} # k: FURL, v: reconnector
        self._connected = False

        self.connection_observers = observer.ObserverList()
        self.encoding_parameters = None

    def startService(self):
        service.Service.startService(self)
        self.introducer_reconnector = self.tub.connectTo(self.introducer_furl,
                                                         self._got_introducer)
        def connect_failed(failure):
            self.log("\n\nInitial Introducer connection failed: "
                     "perhaps it's down\n")
            self.log(str(failure))
        d = self.tub.getReference(self.introducer_furl)
        d.addErrback(connect_failed)

    def log(self, msg):
        self.parent.log(msg)

    def remote_new_peers(self, furls):
        for furl in furls:
            self._new_peer(furl)

    def remote_set_encoding_parameters(self, parameters):
        self.encoding_parameters = parameters

    def stopService(self):
        service.Service.stopService(self)
        self.introducer_reconnector.stopConnecting()
        for reconnector in self.reconnectors.itervalues():
            reconnector.stopConnecting()

    def _new_peer(self, furl):
        if furl in self.reconnectors:
            return
        # TODO: rather than using the TubID as a nodeid, we should use
        # something else. The thing that requires the least additional
        # mappings is to use the foolscap "identifier" (the last component of
        # the furl), since these are unguessable. Before we can do that,
        # though, we need a way to conveniently make these identifiers
        # persist from one run of the client program to the next. Also, using
        # the foolscap identifier would mean that anyone who knows the name
        # of the node also has all the secrets they need to contact and use
        # them, which may or may not be what we want.
        m = re.match(r'pb://(\w+)@', furl)
        assert m
        nodeid = b32decode(m.group(1).upper())
        def _got_peer(rref):
            self.log(" connected to(%s)" % b32encode(nodeid).lower())
            self.connection_observers.notify(nodeid, rref)
            self.connections[nodeid] = rref
            def _lost():
                # TODO: notifyOnDisconnect uses eventually(), but connects do
                # not. Could this cause a problem?
                del self.connections[nodeid]
            rref.notifyOnDisconnect(_lost)
        self.log(" connecting to(%s)" % furl)
        self.reconnectors[furl] = self.tub.connectTo(furl, _got_peer)

    def _got_introducer(self, introducer):
        self.log(" introducing ourselves: %s, %s" % (self, self.my_furl))
        self._connected = True
        d = introducer.callRemote("hello",
                             node=self,
                             furl=self.my_furl)
        introducer.notifyOnDisconnect(self._disconnected)

    def _disconnected(self):
        self._connected = False

    def notify_on_new_connection(self, cb):
        """Register a callback that will be fired (with nodeid, rref) when
        a new connection is established."""
        self.connection_observers.subscribe(cb)

    def connected_to_introducer(self):
        return self._connected

    def get_all_peerids(self):
        return self.connections.iterkeys()

    def get_all_peers(self):
        return self.connections.iteritems()
