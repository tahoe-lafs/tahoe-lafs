#! /usr/bin/env python

import os, subprocess, sys

def print_platform():
    try:
        import platform
        out = platform.platform()
        print
        print "platform:", out.replace("\n", " ")
    except EnvironmentError, le:
         sys.stderr.write("Got exception using 'platform': %s\n" % (le,))
         pass

def print_python_ver():
    print "python:", sys.version.replace("\n", " "),
    print ', maxunicode: ' + str(sys.maxunicode),
    print ', stdout.encoding: ' + str(sys.stdout.encoding),
    print ', stdin.encoding: ' + str(sys.stdin.encoding),
    print ', filesystem.encoding: ' + str(sys.getfilesystemencoding())

def print_cmd_ver(cmdlist, label=None):
    try:
        res = subprocess.Popen(cmdlist, stdin=open(os.devnull),
                               stdout=subprocess.PIPE).communicate()[0]
        if label is None:
            label = cmdlist[0]
        print
        print label + ': ' + res.replace("\n", " ")
    except EnvironmentError, le:
        sys.stderr.write("Got exception invoking '%s': %s\n" % (cmdlist[0], le,))
        pass

def print_as_ver():
    if os.path.exists('a.out'):
        print
        print "WARNING: a file named a.out exists, and getting the version of the 'as' assembler writes to that filename, so I'm not attempting to get the version of 'as'."
        return
    try:
        res = subprocess.Popen(['as', '-version'], stdin=open(os.devnull),
                               stderr=subprocess.PIPE).communicate()[1]
        print
        print 'as: ' + res.replace("\n", " ")
        os.remove('a.out')
    except EnvironmentError, le:
        sys.stderr.write("Got exception invoking '%s': %s\n" % ('as', le,))
        pass

def print_setuptools_ver():
    try:
        import pkg_resources
        out = str(pkg_resources.require("setuptools"))
        print
        print "setuptools:", out.replace("\n", " ")
    except (ImportError, EnvironmentError), le:
        sys.stderr.write("Got exception using 'pkg_resources' to get the version of setuptools: %s\n" % (le,))
        pass

def print_py_pkg_ver(pkgname):
    try:
        import pkg_resources
        out = str(pkg_resources.require(pkgname))
        print
        print pkgname + ': ' + out.replace("\n", " ")
    except (ImportError, EnvironmentError), le:
        sys.stderr.write("Got exception using 'pkg_resources' to get the version of %s: %s\n" % (pkgname, le,))
        pass
    except pkg_resources.DistributionNotFound, le:
        sys.stderr.write("pkg_resources reported no %s package installed: %s\n" % (pkgname, le,))
        pass

print_platform()

print_python_ver()

print_cmd_ver(['buildbot', '--version'])
print_cmd_ver(['cl'])
print_cmd_ver(['gcc', '--version'])
print_cmd_ver(['g++', '--version'])
print_cmd_ver(['cryptest', 'V'])
print_cmd_ver(['darcs', '--version'])
print_cmd_ver(['darcs', '--exact-version'], label='darcs-exact-version')
print_cmd_ver(['7za'])

print_as_ver()

print_setuptools_ver()

print_py_pkg_ver('coverage')
print_py_pkg_ver('trialcoverage')
print_py_pkg_ver('setuptools_trial')
