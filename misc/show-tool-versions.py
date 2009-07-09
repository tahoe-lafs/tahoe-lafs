#! /usr/bin/env python

import sys
import subprocess

print "python:", sys.version.replace("\n", " ") + ', maxunicode: ' + str(sys.maxunicode)

try:
    out = subprocess.Popen(["buildbot", "--version"],
                           stdout=subprocess.PIPE).communicate()[0]
    print "buildbot:", out.replace("\n", " ")
except EnvironmentError:
    pass

try:
    out = subprocess.Popen(["darcs", "--version"],
                           stdout=subprocess.PIPE).communicate()[0]
    full = subprocess.Popen(["darcs", "--exact-version"],
                            stdout=subprocess.PIPE).communicate()[0]
    print
    print "darcs:", out.replace("\n", " ")
    print full.rstrip()
except EnvironmentError:
    pass

try:
    import platform
    out = platform.platform()
    print
    print "platform:", out.replace("\n", " ")
except EnvironmentError:
    pass

try:
    import pkg_resources
    out = str(pkg_resources.require("setuptools"))
    print
    print "setuptools:", out.replace("\n", " ")
except (ImportError, EnvironmentError):
    pass
