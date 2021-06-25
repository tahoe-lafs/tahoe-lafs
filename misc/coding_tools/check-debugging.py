#! /usr/bin/python

"""
Checks for defer.setDebugging().

Runs on Python 3.

Usage: ./check-debugging.py src
"""

from __future__ import print_function

import sys, re, os

ok = True

for starting_point in sys.argv[1:]:
    for root, dirs, files in os.walk(starting_point):
        for f in files:
            if not f.endswith(".py"):
                continue
            if f == "check-debugging.py":
                continue
            fn = os.path.join(root, f)
            for lineno,line in enumerate(open(fn, "r").readlines()):
                lineno = lineno+1
                mo = re.search(r"\.setDebugging\(True\)", line)
                if mo:
                   print("Do not use defer.setDebugging(True) in production")
                   print("First used here: %s:%d" % (fn, lineno))
                   sys.exit(1)
print("No cases of defer.setDebugging(True) were found, good!")
sys.exit(0)
