
import re
from zope.interface import implements
from twisted.application import service
from twisted.python import log
from foolscap import Referenceable
from allmydata.interfaces import RIIntroducer, RIIntroducerClient
from allmydata.util import idlib, observer

def ignoreDeadRef(target, *args, **kwargs):
    from twisted.internet import error
    from foolscap import DeadReferenceError
    d = target.callRemote(*args, **kwargs)
    def _ignore(f):
        f.trap(error.ConnectionDone, error.ConnectError,
               error.ConnectionLost, DeadReferenceError)
    d.addErrback(_ignore)

class Introducer(service.MultiService, Referenceable):
    implements(RIIntroducer)

    def __init__(self):
        service.MultiService.__init__(self)
        self.nodes = set()
        self.pburls = set()

    def remote_hello(self, node, pburl):
        log.msg("introducer: new contact at %s, node is %s" % (pburl, node))
        def _remove():
            log.msg(" introducer: removing %s %s" % (node, pburl))
            self.nodes.remove(node)
            self.pburls.remove(pburl)
            for othernode in self.nodes:
                #othernode.callRemote("lost_peers", set([pburl]))
                #othernode.callRemoteOnly("lost_peers", set([pburl]))
                ignoreDeadRef(othernode, "lost_peers", set([pburl]))

        node.notifyOnDisconnect(_remove)
        self.pburls.add(pburl)
        node.callRemote("new_peers", self.pburls)
        for othernode in self.nodes:
            othernode.callRemote("new_peers", set([pburl]))
        self.nodes.add(node)


class IntroducerClient(service.Service, Referenceable):
    implements(RIIntroducerClient)

    def __init__(self, tub, introducer_pburl, my_pburl):
        self.tub = tub
        self.introducer_pburl = introducer_pburl
        self.my_pburl = my_pburl

        self.connections = {} # k: nodeid, v: ref
        self.reconnectors = {} # k: PBURL, v: reconnector

        self.change_observers = observer.ObserverList()

    def startService(self):
        self.introducer_reconnector = self.tub.connectTo(self.introducer_pburl,
                                                         self._got_introducer)

    def log(self, msg):
        self.parent.log(msg)

    def remote_new_peers(self, pburls):
        for pburl in pburls:
            self._new_peer(pburl)

    def remote_lost_peers(self, pburls):
        for pburl in pburls:
            self._lost_peer(pburl)

    def stopService(self):
        service.Service.stopService(self)
        self.introducer_reconnector.stopConnecting()
        for reconnector in self.reconnectors.itervalues():
            reconnector.stopConnecting()
        self.reconnectors = {}

    def _new_peer(self, pburl):
        if pburl in self.reconnectors:
            return
        # TODO: rather than using the TubID as a nodeid, we should use
        # something else. The thing that requires the least additional
        # mappings is to use the foolscap "identifier" (the last component of
        # the pburl), since these are unguessable. Before we can do that,
        # though, we need a way to conveniently make these identifiers
        # persist from one run of the client program to the next. Also, using
        # the foolscap identifier would mean that anyone who knows the name
        # of the node also has all the secrets they need to contact and use
        # them, which may or may not be what we want.
        m = re.match(r'pb://(\w+)@', pburl)
        assert m
        nodeid = idlib.a2b(m.group(1))
        def _got_peer(rref):
            self.log(" connected to(%s)" % idlib.b2a(nodeid))
            self.change_observers.notify("add", nodeid, rref)
            self.connections[nodeid] = rref
            def _lost():
                # TODO: notifyOnDisconnect uses eventually(), but connects do
                # not. Could this cause a problem?
                del self.connections[nodeid]
            rref.notifyOnDisconnect(_lost)
        self.log(" connecting to(%s)" % pburl)
        self.reconnectors[pburl] = self.tub.connectTo(pburl, _got_peer)

    def _got_introducer(self, introducer):
        self.log(" introducing ourselves: %s, %s" % (self, self.my_pburl))
        d = introducer.callRemote("hello",
                             node=self,
                             pburl=self.my_pburl)

    def notify_on_change(self, cb):
        """Register a callback that will be fired (with ('add',nodeid,rref)
        or ('remove',pburl) ) when a new connection is established or a peer
        is lost. This is used by the unit tests."""
        self.change_observers.subscribe(cb)

    def _lost_peer(self, pburl):
        if pburl in self.reconnectors:
            self.reconnectors[pburl].stopConnecting()
            del self.reconnectors[pburl]
            self.change_observers.notify("remove", pburl)
        # TODO: we don't currently bother to terminate any connections we
        # might have to this peer. The assumption is that, since the
        # introducer lost their connection to this peer, we'll probably lose
        # our connection too. Also, foolscap doesn't currently provide a
        # clean way to terminate a given connection.
