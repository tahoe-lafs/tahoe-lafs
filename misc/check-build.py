#! /usr/bin/env python

# This helper script is used with the 'test-desert-island' Makefile target.

import sys

good = True
build_out = sys.argv[1]
mode = sys.argv[2]

for line in open(build_out, "r"):
    if mode == "no-downloads" and "downloading" in line.lower():
        print line,
        good = False
if good:
    if mode == "no-downloads":
        print "Good: build did not try to download any files"
    sys.exit(0)
else:
    if mode == "no-downloads":
        print "Failed: build tried to download files"
    sys.exit(1)
