﻿#!/usr/bin/env python
# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

from foolscap import Tub, eventual
from twisted.internet import reactor
import sys
import pprint

def oops(f):
    print "ERROR"
    print f

def fetch(furl):
    t = Tub()
    t.startService()
    d = t.getReference(furl)
    d.addCallback(lambda rref: rref.callRemote("get_averages"))
    d.addCallback(pprint.pprint)
    return d

d = eventual.fireEventually(sys.argv[1])
d.addCallback(fetch)
d.addErrback(oops)
d.addBoth(lambda res: reactor.stop())
reactor.run()
