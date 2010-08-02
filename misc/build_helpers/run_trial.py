#!/usr/bin/env python

import os, sys, re

modulename = None
for i in xrange(1, len(sys.argv)):
    if not sys.argv[i].startswith('-'):
        modulename = sys.argv[i]
        break

if modulename is None:
    raise AssertionError("no test module specified")

__import__(modulename)
srcfile = sys.modules[modulename].__file__
srcdir = os.path.dirname(os.path.realpath(srcfile))
for i in modulename.split('.'):
    srcdir = os.path.dirname(srcdir)

if os.path.normcase(srcdir).endswith('.egg'):
    srcdir = os.path.dirname(srcdir)
elif os.path.normcase(os.path.basename(srcdir)) == 'site-packages':
    srcdir = os.path.dirname(srcdir)
    if re.search(r'python.+\..+', os.path.normcase(os.path.basename(srcdir))):
        srcdir = os.path.dirname(srcdir)
    if os.path.normcase(os.path.basename(srcdir)) == 'lib':
        srcdir = os.path.dirname(srcdir)

srcdir = os.path.normcase(os.path.normpath(srcdir))
cwd = os.path.normcase(os.path.normpath(os.getcwd()))

same = (srcdir == cwd)
if not same:
    try:
        same = os.path.samefile(srcdir, cwd)
    except AttributeError, e:
        e  # hush pyflakes

if not same:
    msg = ("We seem to be testing the code at %r\n"
           "(according to the source filename %r),\n"
           "but expected to be testing the code at %r.\n"
           % (srcdir, srcfile, cwd))
    if (not isinstance(cwd, unicode) and
        cwd.decode(sys.getfilesystemencoding(), 'replace') != os.path.normcase(os.path.normpath(os.getcwdu()))):
        msg += ("However, this may be a false alarm because the current directory path\n"
                "is not representable in the filesystem encoding. This script needs to be\n"
                "run from the source directory to be tested, at a non-Unicode path.")
    else:
        msg += "This script needs to be run from the source directory to be tested."

    raise AssertionError(msg)

from twisted.scripts.trial import run
run()