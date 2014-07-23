﻿#! /usr/bin/env python
# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

from foolscap import Tub
from foolscap.eventual import eventually
import sys
from twisted.internet import reactor

def go():
    t = Tub()
    d = t.getReference(sys.argv[1])
    d.addCallback(lambda rref: rref.callRemote("get_memory_usage"))
    def _got(res):
        print res
        reactor.stop()
    d.addCallback(_got)

eventually(go)
reactor.run()
