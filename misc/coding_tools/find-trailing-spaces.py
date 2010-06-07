#!/usr/bin/env python

import os, sys

from twisted.python import usage

class Options(usage.Options):
    optFlags = [
        ("recursive", "r", "Search for .py files recursively"),
        ]
    def parseArgs(self, *starting_points):
        self.starting_points = starting_points

found = [False]

def check(fn):
    f = open(fn, "r")
    for i,line in enumerate(f.readlines()):
        if line == "\n":
            continue
        if line[-1] == "\n":
            line = line[:-1]
        if line.rstrip() != line:
            # the %s:%d:%d: lets emacs' compile-mode jump to those locations
            print "%s:%d:%d: trailing whitespace" % (fn, i+1, len(line)+1)
            found[0] = True
    f.close()

o = Options()
o.parseOptions()
if o['recursive']:
    for starting_point in o.starting_points:
        for root, dirs, files in os.walk(starting_point):
            for fn in [f for f in files if f.endswith(".py")]:
                fn = os.path.join(root, fn)
                check(fn)
else:
    for fn in o.starting_points:
        check(fn)
if found[0]:
    sys.exit(1)
sys.exit(0)
