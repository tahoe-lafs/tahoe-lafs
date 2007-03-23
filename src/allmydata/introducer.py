from foolscap import Referenceable, DeadReferenceError
from twisted.application import service
from twisted.python import log
from twisted.internet.error import ConnectionLost, ConnectionDone
from zope.interface import implements
from allmydata.interfaces import RIIntroducer


def sendOnly(call, methname, *args, **kwargs):
    d = call(methname, *args, **kwargs)
    def _trap(f):
        f.trap(DeadReferenceError, ConnectionLost, ConnectionDone)
    d.addErrback(_trap)

class Introducer(service.MultiService, Referenceable):
    implements(RIIntroducer)

    def __init__(self):
        service.MultiService.__init__(self)
        self.nodes = set()
        self.pburls = set()

    def remote_hello(self, node, pburl):
        log.msg("roster: new contact at %s, node is %s" % (pburl, node))
        def _remove():
            log.msg(" roster: removing %s %s" % (node, pburl))
            self.nodes.remove(node)
            self.pburls.remove(pburl)
        node.notifyOnDisconnect(_remove)
        self.pburls.add(pburl)
        node.callRemote("new_peers", self.pburls)
        for othernode in self.nodes:
            othernode.callRemote("new_peers", set([pburl]))
        self.nodes.add(node)
