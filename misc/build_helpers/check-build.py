#! /usr/bin/env python

# This helper script is used with the 'test-desert-island' Makefile target.

import sys

good = True
build_out = sys.argv[1]
mode = sys.argv[2]

print

for line in open(build_out, "r"):
    if mode == "no-downloads":
        # when setup_requires= uses
        # misc/dependencies/setuptools-0.6c8.egg, it causes a
        # "Downloading: misc/dependencies/.." line to be emitted,
        # which doesn't count as a network download.  Lines that start
        # with "Reading" indicate that it is fetching web pages in
        # order to check for newer versions of packages. As long as it
        # doesn't actually download any packages then it still passes
        # this test. That is: it *would* have succeeded if you were on
        # a Desert Island, an airplane with no network, behind a
        # corporate firewall that disallows such connections, or if
        # you had turned off your network prior to running "python
        # setup.py build". A stronger requirement would be that it
        # doesn't even try to check for new packages on remote hosts
        # if it has all the packages that it needs locally, but we
        # currently don't enforce that stronger requirement.
        if line.startswith("Downloading http:"):
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
