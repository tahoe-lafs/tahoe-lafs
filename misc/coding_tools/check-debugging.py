#! /usr/bin/python

# ./check-debugging.py src

import sys, re, os

ok = True
umids = {}

for starting_point in sys.argv[1:]:
    for root, dirs, files in os.walk(starting_point):
        for fn in [f for f in files if f.endswith(".py")]:
            fn = os.path.join(root, fn)
            for lineno,line in enumerate(open(fn, "r").readlines()):
                lineno = lineno+1
                mo = re.search(r"\.setDebugging\(True\)", line)
                if mo:
                   print "Do not use defer.setDebugging(True) in production"
                   print "First used here: %s:%d" % (fn, lineno)
                   sys.exit(1)
print "No cases of defer.setDebugging(True) were found, good!"
sys.exit(0)
