
import re
from base64 import b32encode, b32decode
from zope.interface import implements
from twisted.application import service
from twisted.internet import defer
from twisted.python import log
from foolscap import Referenceable
from allmydata import node
from allmydata.interfaces import RIIntroducer, RIIntroducerClient
from allmydata.util import observer

class IntroducerNode(node.Node):
    PORTNUMFILE = "introducer.port"
    NODETYPE = "introducer"
    ENCODING_PARAMETERS_FILE = "encoding_parameters"
    DEFAULT_K, DEFAULT_DESIRED, DEFAULT_N = 3, 7, 10

    def tub_ready(self):
        k, desired, n = self.DEFAULT_K, self.DEFAULT_DESIRED, self.DEFAULT_N
        data = self.get_config("encoding_parameters")
        if data is not None:
            k,desired,n = data.split()
            k = int(k); desired = int(desired); n = int(n)
        introducerservice = IntroducerService(self.basedir, (k, desired, n))
        self.add_service(introducerservice)
        self.introducer_url = self.tub.registerReference(introducerservice, "introducer")
        self.log(" introducer is at %s" % self.introducer_url)
        self.write_config("introducer.furl", self.introducer_url + "\n")

class IntroducerService(service.MultiService, Referenceable):
    implements(RIIntroducer)
    name = "introducer"

    def __init__(self, basedir=".", encoding_parameters=None):
        service.MultiService.__init__(self)
        self.introducer_url = None
        self.nodes = set()
        self.furls = set()
        self._encoding_parameters = encoding_parameters

    def remote_hello(self, node, furl):
        log.msg("introducer: new contact at %s, node is %s" % (furl, node))
        def _remove():
            log.msg(" introducer: removing %s %s" % (node, furl))
            self.nodes.remove(node)
            if furl is not None:
                self.furls.remove(furl)
        node.notifyOnDisconnect(_remove)
        if furl is not None:
            self.furls.add(furl)
            for othernode in self.nodes:
                othernode.callRemote("new_peers", set([furl]))
        node.callRemote("new_peers", self.furls)
        if self._encoding_parameters is not None:
            node.callRemote("set_encoding_parameters",
                            self._encoding_parameters)
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

        # The N'th element of _observers_of_enough_peers is None if nobody has
        # asked to be informed when N peers become connected, it is a
        # OneShotObserverList if someone has asked to be informed, and that list
        # is fired when N peers next become connected (or immediately if N peers
        # are already connected when they asked), and the N'th element is
        # replaced by None when the number of connected peers falls below N.
        # _observers_of_enough_peers is always just long enough to hold the
        # highest-numbered N that anyone is interested in (i.e., there are never
        # trailing Nones in _observers_of_enough_peers).
        self._observers_of_enough_peers = []
        # The N'th element of _observers_of_fewer_than_peers is None if nobody
        # has asked to be informed when we become connected to fewer than N
        # peers, it is a OneShotObserverList if someone has asked to be
        # informed, and that list is fired when we become connected to fewer
        # than N peers (or immediately if we are already connected to fewer than
        # N peers when they asked).  _observers_of_fewer_than_peers is always
        # just long enough to hold the highest-numbered N that anyone is
        # interested in (i.e., there are never trailing Nones in
        # _observers_of_fewer_than_peers).
        self._observers_of_fewer_than_peers = []

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

    def _notify_observers_of_enough_peers(self, numpeers):
        if len(self._observers_of_enough_peers) > numpeers:
            osol = self._observers_of_enough_peers[numpeers]
            if osol:
                osol.fire(None)

    def _remove_observers_of_enough_peers(self, numpeers):
        if len(self._observers_of_enough_peers) > numpeers:
            self._observers_of_enough_peers[numpeers] = None
            while self._observers_of_enough_peers and (not self._observers_of_enough_peers[-1]):
                self._observers_of_enough_peers.pop()

    def _notify_observers_of_fewer_than_peers(self, numpeers):
        if len(self._observers_of_fewer_than_peers) > numpeers:
            osol = self._observers_of_fewer_than_peers[numpeers]
            if osol:
                osol.fire(None)
                self._observers_of_fewer_than_peers[numpeers] = None
                while len(self._observers_of_fewer_than_peers) > numpeers and (not self._observers_of_fewer_than_peers[-1]):
                    self._observers_of_fewer_than_peers.pop()

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
            self.log("connected to %s" % b32encode(nodeid).lower()[:8])
            self.connection_observers.notify(nodeid, rref)
            self.connections[nodeid] = rref
            self._notify_observers_of_enough_peers(len(self.connections))
            self._notify_observers_of_fewer_than_peers(len(self.connections))
            def _lost():
                # TODO: notifyOnDisconnect uses eventually(), but connects do
                # not. Could this cause a problem?

                # We know that this observer list must have been fired, since we
                # had enough peers before this one was lost.
                self._remove_observers_of_enough_peers(len(self.connections))
                self._notify_observers_of_fewer_than_peers(len(self.connections)+1)

                del self.connections[nodeid]

            rref.notifyOnDisconnect(_lost)
        self.log("connecting to %s" % b32encode(nodeid).lower()[:8])
        self.reconnectors[furl] = self.tub.connectTo(furl, _got_peer)

    def _got_introducer(self, introducer):
        if self.my_furl:
            my_furl_s = self.my_furl[6:13]
        else:
            my_furl_s = "<none>"
        self.log("introducing ourselves: %s, %s" % (self, my_furl_s))
        self._connected = True
        d = introducer.callRemote("hello",
                                  node=self,
                                  furl=self.my_furl)
        introducer.notifyOnDisconnect(self._disconnected)

    def _disconnected(self):
        self.log("bummer, we've lost our connection to the introducer")
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

    def when_enough_peers(self, numpeers):
        """
        I return a deferred that fires the next time that at least
        numpeers are connected, or fires immediately if numpeers are
        currently connected.
        """
        self._observers_of_enough_peers.extend([None]*(numpeers+1-len(self._observers_of_enough_peers)))
        if not self._observers_of_enough_peers[numpeers]:
            self._observers_of_enough_peers[numpeers] = observer.OneShotObserverList()
            if len(self.connections) >= numpeers:
                self._observers_of_enough_peers[numpeers].fire(self)
        return self._observers_of_enough_peers[numpeers].when_fired()

    def when_fewer_than_peers(self, numpeers):
        """
        I return a deferred that fires the next time that fewer than numpeers
        are connected, or fires immediately if fewer than numpeers are currently
        connected.
        """
        if len(self.connections) < numpeers:
            return defer.succeed(None)
        else:
            self._observers_of_fewer_than_peers.extend([None]*(numpeers+1-len(self._observers_of_fewer_than_peers)))
            if not self._observers_of_fewer_than_peers[numpeers]:
                self._observers_of_fewer_than_peers[numpeers] = observer.OneShotObserverList()
            return self._observers_of_fewer_than_peers[numpeers].when_fired()
