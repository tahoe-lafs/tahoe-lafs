#! /usr/bin/python

import sys
from twisted.internet import reactor
from foolscap import RemoteCopy, Tub

# the receiving side defines the RemoteCopy
class RemoteUserRecord(RemoteCopy):
    copytype = "unique-string-UserRecord" # this matches the sender

    def __init__(self):
        # note: our __init__ must take no arguments
        pass

    def setCopyableState(self, d):
        self.name = d['name']
        self.age = d['age']
        self.shoe_size = "they wouldn't tell us"

    def display(self):
        print "Name:", self.name
        print "Age:", self.age
        print "Shoe Size:", self.shoe_size

def getRecord(rref, name):
    d = rref.callRemote("getuser", name=name)
    def _gotRecord(r):
        # r is an instance of RemoteUserRecord
        r.display()
        reactor.stop()
    d.addCallback(_gotRecord)


from foolscap import Tub
tub = Tub()
tub.startService()

d = tub.getReference(sys.argv[1])
d.addCallback(getRecord, "alice")

reactor.run()
