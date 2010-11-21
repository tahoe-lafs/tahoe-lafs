#!/usr/bin/env python

import os, sys, re, glob

def read_version_py(infname):
    try:
        verstrline = open(infname, "rt").read()
    except EnvironmentError:
        return None
    else:
        VSRE = r"^verstr = ['\"]([^'\"]*)['\"]"
        mo = re.search(VSRE, verstrline, re.M)
        if mo:
            return mo.group(1)

version = read_version_py(os.path.join('..', 'src', 'allmydata', '_version.py'))

if version is None:
    raise AssertionError("We don't know which version we're supposed to be testing.")

APPNAME='allmydata-tahoe'

adglobals = {}
execfile(os.path.join('..', 'src', 'allmydata', '_auto_deps.py'), adglobals)
install_requires = adglobals['install_requires']
test_requires = adglobals.get('test_requires', ['mock'])

# setuptools/zetuptoolz looks in __main__.__requires__ for a list of
# requirements.

__requires__ = [APPNAME + '==' + version] + install_requires + test_requires

print "Requirements: %r" % (__requires__,)

eggz = glob.glob(os.path.join('..', 'setuptools-*.egg'))
if len(eggz) > 0:
   egg = os.path.realpath(eggz[0])
   print "Inserting egg on sys.path: %r" % (egg,)
   sys.path.insert(0, egg)

import pkg_resources

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

components = modulename.split('.')
leaf = os.path.normcase(components[-1])
if os.path.normcase(os.path.basename(srcfile)) in (leaf + '.py', leaf + '.pyc'):
    # strip the leaf module name
    components = components[:-1]

for i in components:
    srcdir = os.path.dirname(srcdir)

if os.path.normcase(srcdir).endswith('.egg'):
    srcdir = os.path.dirname(srcdir)
elif os.path.normcase(os.path.basename(srcdir)) == 'site-packages':
    srcdir = os.path.dirname(srcdir)
    if re.search(r'python.+\..+', os.path.normcase(os.path.basename(srcdir))):
        srcdir = os.path.dirname(srcdir)
    if os.path.normcase(os.path.basename(srcdir)) == 'lib':
        srcdir = os.path.dirname(srcdir)

rootdir = os.path.normcase(os.path.normpath(srcdir))
if os.path.basename(rootdir) == 'src':
    rootdir = os.path.dirname(rootdir)

root_from_cwd = os.path.normcase(os.path.normpath(os.getcwd()))
if os.path.basename(root_from_cwd) == 'src':
    root_from_cwd = os.path.dirname(root_from_cwd)

same = (root_from_cwd == rootdir)
if not same:
    try:
        same = os.path.samefile(root_from_cwd, rootdir)
    except AttributeError, e:
        e  # hush pyflakes

if not same:
    msg = ("We seem to be testing the code at %r\n"
           "(according to the source filename %r),\n"
           "but expected to be testing the code at %r.\n"
           % (rootdir, srcfile, root_from_cwd))

    root_from_cwdu = os.path.normcase(os.path.normpath(os.getcwdu()))
    if os.path.basename(root_from_cwdu) == u'src':
        root_from_cwdu = os.path.dirname(root_from_cwdu)

    if not isinstance(root_from_cwd, unicode) and root_from_cwd.decode(sys.getfilesystemencoding(), 'replace') != root_from_cwdu:
        msg += ("However, this may be a false alarm because the current directory path\n"
                "is not representable in the filesystem encoding. This script needs to be\n"
                "run from the source directory to be tested, at a non-Unicode path.")
    else:
        msg += "This script needs to be run from the source directory to be tested."

    raise AssertionError(msg)

from twisted.scripts.trial import run
run()