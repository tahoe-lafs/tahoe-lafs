# -*- python -*-

"""Monitor a Tahoe grid, by playing sounds in response to remote events.

To install:
 1: install Boodler, from http://www.eblong.com/zarf/boodler/
 2: run "boodler.py -l listen.Sounds". This will run a daemon
    that listens on a network socket (31863 by default) and
    accepts commands in the form of "sound bird/crow1.aiff\n"
 3: copy this file into a new directory, which we'll call $BASEDIR
 4: write one or more logport FURLs into files named *.furl or *.furls, one
    per line. All logports from all such files will be used.
 5: launch this daemon with 'cd $BASEDIR && twistd -y boodlegrid.tac'

"""

import os, time
from zope.interface import implements
from twisted.application import service
from twisted.internet import protocol, reactor, defer
from foolscap import Tub, Referenceable
from foolscap.logging.interfaces import RILogObserver
from twisted.python import log

class Listener:

    def __init__(self):
        self.boodler = None # filled in when we connect to boodler
        self.last = {}

    def sound(self, name, slot=None, max=0.100):
        if not self.boodler:
            return
        now = time.time()
        if slot is None:
            slot = name
        if now < self.last.get(slot, 0) + max:
            return # too soon
        self.last[slot] = now
        self.boodler.write("sound %s\n" % name)

    def msg(self, m, furl):
        #print "got it", m
        message = m.get("message", m.get("format", ""))
        format = m.get("format", "")
        facility = m.get("facility", "")

        # messages emitted by the Introducer: client join/leave
        if message.startswith("introducer: subscription[storage] request"):
            print "new client"
            self.sound("voice/hooray.aiff")
        if message.startswith("introducer: unsubscribing"):
            print "unsubscribe"
            self.sound("electro/zaptrill-fade.aiff")

        # messages from the helper
        if message == "file already found in grid":
            print "already found"
            self.sound("mech/ziplash-high.aiff")
        #if message == "upload done":
        if format == "plaintext_hash=%(plaintext_hash)s, SI=%(SI)s, size=%(size)d":
            size = m.get("size")
            print "upload done, size", size
            self.sound("mech/ziplash-low.aiff")
        if "fetching " in message:
            # helper grabbing ciphertext from client
            self.sound("voice/phoneme/sh.aiff", max=0.5)

        # messages from storage servers
        if message.startswith("storage: slot_readv"):
            #self.sound("voice/phoneme/r.aiff")
            self.sound("percussion/wood-tap-hollow.aiff")

        # messages from webapi
        if message.startswith("Retrieve") and "starting" in message:
            self.sound("mech/metal-clack.aiff")
        if message.startswith("Publish") and "starting" in message:
            self.sound("mech/door-slam.aiff")
            #self.sound("mech/metal-clash.aiff")
        if ("web: %(clientip)s" in format
            and m.get("method") == "POST"
            and "t=set_children" in m.get("uri", "")):
            self.sound("mech/clock-clang.aiff")

        # generic messages
        #if m['level'] < 20:
        #    self.sound("mech/keyboard-1.aiff")
        if "_check_for_done but we're not running" in message:
            pass
        elif format == "excessive reactor delay (%ss)":
            self.sound("animal/frog-cheep.aiff")
            print "excessive delay %s: %s" % (m['args'][0], furl)
        elif format == "excessive reactor delay (%(delay)ss)":
            self.sound("animal/frog-cheep.aiff")
            print "excessive delay %s: %s" % (m['delay'], furl)
        elif facility == "foolscap.negotiation":
          if (message == "got offer for an existing connection"
              or "master told us to use a new connection" in message):
              print "foolscap: got offer for an existing connection", message, furl
          else:
              #print "foolscap:", message
              pass
        elif m['level'] > 30: # SCARY or BAD
            #self.sound("mech/alarm-bell.aiff")
            self.sound("environ/thunder-tense.aiff")
            print m, furl
        elif m['level'] == 30: # WEIRD
            self.sound("mech/glass-breaking.aiff")
            print m, furl
        elif m['level'] > 20: # UNUSUAL or INFREQUENT or CURIOUS
            self.sound("mech/telephone-ring-old.aiff")
            print m, furl

class BoodleSender(protocol.Protocol):
    def connectionMade(self):
        print "connected to boodler"
        self.factory.listener.boodler = self.transport

class Bridge(Referenceable):
    implements(RILogObserver)

    def __init__(self, furl, listener):
        self.furl = furl
        self.listener = listener

    def remote_msg(self, m):
        d = defer.maybeDeferred(self.listener.msg, m, self.furl)
        d.addErrback(log.err)
        # never send errors to the remote side

class Monitor(service.MultiService):
    def __init__(self):
        service.MultiService.__init__(self)
        self.tub = Tub()
        self.tub.setServiceParent(self)
        self.listener = Listener()
        self.targets = []
        for fn in os.listdir("."):
            if fn.endswith(".furl") or fn.endswith(".furls"):
                for i,line in enumerate(open(fn, "r").readlines()):
                    target = line.strip()
                    if target:
                        self.tub.connectTo(target, self._got_logpublisher,
                                           fn, i, target)

        cf = protocol.ClientFactory()
        cf.listener = self.listener
        cf.protocol = BoodleSender
        reactor.connectTCP("localhost", 31863, cf)

    def _got_logpublisher(self, publisher, fn, i, target):
        print "connected to %s:%d, %s" % (fn, i, target)
        b = Bridge(target, self.listener)
        publisher.callRemote("subscribe_to_all", b)


m = Monitor()
application = service.Application("boodlegrid")
m.setServiceParent(application)

