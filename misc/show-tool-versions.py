#! /usr/bin/env python

import os, subprocess, sys

try:
    import platform
    out = platform.platform()
    print
    print "platform:", out.replace("\n", " ")
except EnvironmentError, le:
    sys.stderr.write("Got exception using 'platform': %s" % (le,))
    pass

print "python:", sys.version.replace("\n", " ") + ', maxunicode: ' + str(sys.maxunicode)

try:
    import pkg_resources
    out = str(pkg_resources.require("setuptools"))
    print
    print "setuptools:", out.replace("\n", " ")
except (ImportError, EnvironmentError), le:
    sys.stderr.write("Got exception using 'pkg_resources' to get the version of setuptools: %s" % (le,))
    pass

try:
    out = subprocess.Popen(["buildbot", "--version"],
                           stdout=subprocess.PIPE).communicate()[0]
    print "buildbot:", out.replace("\n", " ")
except EnvironmentError, le:
    sys.stderr.write("Got exception invoking 'buildbot': %s" % (le,))
    pass

try:
    out = subprocess.Popen(["cl"],
                           stdout=subprocess.PIPE).communicate()[0]
    print "cl:", out.replace("\n", " ")
except EnvironmentError, le:
    sys.stderr.write("Got exception invoking 'cl': %s" % (le,))
    pass

try:
    out = subprocess.Popen(["g++", "--version"],
                           stdout=subprocess.PIPE).communicate()[0]
    print "g++:", out.replace("\n", " ")
except EnvironmentError, le:
    sys.stderr.write("Got exception invoking 'g++': %s" % (le,))
    pass

try:
    out = subprocess.Popen(["gcc", "--version"],
                           stdout=subprocess.PIPE).communicate()[0]
    print "gcc:", out.replace("\n", " ")
except EnvironmentError, le:
    sys.stderr.write("Got exception invoking 'gcc': %s" % (le,))
    pass

try:
    out = subprocess.Popen(["as", "-version"], stdin=open(os.devnull),
                           stdout=subprocess.PIPE).communicate()[0]
    print "as:", out.replace("\n", " ")
except EnvironmentError, le:
    sys.stderr.write("Got exception invoking 'as': %s" % (le,))
    pass

try:
    out = subprocess.Popen(["darcs", "--version"],
                           stdout=subprocess.PIPE).communicate()[0]
    full = subprocess.Popen(["darcs", "--exact-version"],
                            stdout=subprocess.PIPE).communicate()[0]
    print
    print "darcs:", out.replace("\n", " ")
    print full.rstrip()
except EnvironmentError, le:
    sys.stderr.write("Got exception invoking 'darcs': %s" % (le,))
    pass
