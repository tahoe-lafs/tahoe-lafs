#! /usr/bin/python

import sys
from twisted.internet import reactor
from foolscap import Referenceable, Tub

class Observer(Referenceable):
    def remote_event(self, msg):
        print "event:", msg

def printResult(number):
    print "the result is", number
def gotError(err):
    print "got an error:", err
def gotRemote(remote):
    o = Observer()
    d = remote.callRemote("addObserver", observer=o)
    d.addCallback(lambda res: remote.callRemote("push", num=2))
    d.addCallback(lambda res: remote.callRemote("push", num=3))
    d.addCallback(lambda res: remote.callRemote("add"))
    d.addCallback(lambda res: remote.callRemote("pop"))
    d.addCallback(printResult)
    d.addCallback(lambda res: remote.callRemote("removeObserver", observer=o))
    d.addErrback(gotError)
    d.addCallback(lambda res: reactor.stop())
    return d

url = sys.argv[1]
tub = Tub()
d = tub.getReference(url)
d.addCallback(gotRemote)

tub.startService()
reactor.run()
