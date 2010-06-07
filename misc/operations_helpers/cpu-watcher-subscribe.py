# -*- python -*-

from twisted.internet import reactor
import sys

import os.path, pprint
from twisted.application import service
from twisted.python import log
from foolscap import Tub, Referenceable, RemoteInterface
from foolscap.schema import ListOf, TupleOf
from zope.interface import implements

Averages = ListOf( TupleOf(str, float, float, float) )
class RICPUWatcherSubscriber(RemoteInterface):
    def averages(averages=Averages):
        return None

class CPUWatcherSubscriber(service.MultiService, Referenceable):
    implements(RICPUWatcherSubscriber)
    def __init__(self, furlthing):
        service.MultiService.__init__(self)
        if furlthing.startswith("pb://"):
            furl = furlthing
        else:
            furlfile = os.path.expanduser(furlthing)
            if os.path.isdir(furlfile):
                furlfile = os.path.join(furlfile, "watcher.furl")
            furl = open(furlfile, "r").read().strip()
        tub = Tub()
        tub.setServiceParent(self)
        tub.connectTo(furl, self.connected)

    def connected(self, rref):
        print "subscribing"
        d = rref.callRemote("get_averages")
        d.addCallback(self.remote_averages)
        d.addErrback(log.err)

        d = rref.callRemote("subscribe", self)
        d.addErrback(log.err)

    def remote_averages(self, averages):
        pprint.pprint(averages)


c = CPUWatcherSubscriber(sys.argv[1])
c.startService()
reactor.run()

