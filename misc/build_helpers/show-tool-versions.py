#! /usr/bin/env python

import locale, os, subprocess, sys

def foldlines(s):
    return s.replace("\n", " ").replace("\r", "")

def print_platform():
    print
    try:
        import platform
        out = platform.platform()
        print "platform:", foldlines(out)
        if hasattr(platform, 'linux_distribution'):
            print "linux_distribution:", repr(platform.linux_distribution())
    except EnvironmentError, le:
         sys.stderr.write("Got exception using 'platform': %s\n" % (le,))
         pass

def print_python_ver():
    print
    print "python:", foldlines(sys.version)
    print 'maxunicode: ' + str(sys.maxunicode)

def print_python_encoding_settings():
    print_stderr([sys.executable, '-c', 'import sys; print >>sys.stderr, sys.stdout.encoding'], label='sys.stdout.encoding')
    print_stdout([sys.executable, '-c', 'import sys; print sys.stderr.encoding'], label='sys.stderr.encoding')
    print
    print 'filesystem.encoding: ' + str(sys.getfilesystemencoding())
    print 'locale.getpreferredencoding: ' + str(locale.getpreferredencoding())
    print 'os.path.supports_unicode_filenames: ' + str(os.path.supports_unicode_filenames)
    print 'locale.defaultlocale: ' + str(locale.getdefaultlocale())
    print 'locale.locale: ' + str(locale.getlocale())

def print_stdout(cmdlist, label=None):
    print
    try:
        res = subprocess.Popen(cmdlist, stdin=open(os.devnull),
                               stdout=subprocess.PIPE).communicate()[0]
        if label is None:
            label = cmdlist[0]
        print label + ': ' + foldlines(res)
    except EnvironmentError, le:
        sys.stderr.write("Got exception invoking '%s': %s\n" % (cmdlist[0], le,))
        pass

def print_stderr(cmdlist, label=None):
    print
    try:
        res = subprocess.Popen(cmdlist, stdin=open(os.devnull),
                               stderr=subprocess.PIPE).communicate()[1]
        if label is None:
            label = cmdlist[0]
        print label + ': ' + foldlines(res)
    except EnvironmentError, le:
        sys.stderr.write("Got exception invoking '%s': %s\n" % (cmdlist[0], le,))
        pass

def print_as_ver():
    print
    if os.path.exists('a.out'):
        print "WARNING: a file named a.out exists, and getting the version of the 'as' assembler writes to that filename, so I'm not attempting to get the version of 'as'."
        return
    try:
        res = subprocess.Popen(['as', '-version'], stdin=open(os.devnull),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        print 'as: ' + foldlines(res[0]+' '+res[1])
        if os.path.exists('a.out'):
            os.remove('a.out')
    except EnvironmentError, le:
        sys.stderr.write("Got exception invoking '%s': %s\n" % ('as', le,))
        pass

def print_setuptools_ver():
    print
    try:
        import pkg_resources
        out = str(pkg_resources.require("setuptools"))
        print "setuptools:", foldlines(out)
    except (ImportError, EnvironmentError), le:
        sys.stderr.write("Got exception using 'pkg_resources' to get the version of setuptools: %s\n" % (le,))
        pass

def print_py_pkg_ver(pkgname):
    print
    try:
        import pkg_resources
        out = str(pkg_resources.require(pkgname))
        print pkgname + ': ' + foldlines(out)
    except (ImportError, EnvironmentError), le:
        sys.stderr.write("Got exception using 'pkg_resources' to get the version of %s: %s\n" % (pkgname, le,))
        pass
    except pkg_resources.DistributionNotFound, le:
        sys.stderr.write("pkg_resources reported no %s package installed: %s\n" % (pkgname, le,))
        pass

print_platform()

print_python_ver()

print_stdout(['locale'])
print_python_encoding_settings()

print_stdout(['buildbot', '--version'])
print_stdout(['cl'])
print_stdout(['gcc', '--version'])
print_stdout(['g++', '--version'])
print_stdout(['cryptest', 'V'])
print_stdout(['darcs', '--version'])
print_stdout(['darcs', '--exact-version'], label='darcs-exact-version')
print_stdout(['7za'])

print_as_ver()

print_setuptools_ver()

print_py_pkg_ver('coverage')
print_py_pkg_ver('trialcoverage')
print_py_pkg_ver('setuptools_trial')
print_py_pkg_ver('pyflakes')
print_py_pkg_ver('zope.interface')
print_py_pkg_ver('setuptools_darcs')
print_py_pkg_ver('darcsver')
