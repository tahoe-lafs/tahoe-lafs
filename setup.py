#! /usr/bin/env python

# Allmydata Tahoe -- secure, distributed storage grid
#
# Copyright (C) 2008 Allmydata, Inc.
#
# This file is part of tahoe.
#
# See the docs/about.html file for licensing information.

import os, re, sys, subprocess

##### sys.path management

basedir = os.path.dirname(os.path.abspath(__file__))
pyver = "python%d.%d" % (sys.version_info[:2])
if sys.platform == "win32":
    supportlib = os.path.join("support", "Lib", "site-packages")
else:
    supportlib = os.path.join("support", "lib", pyver, "site-packages")
supportlib = os.path.join(basedir, supportlib)

def add_tahoe_paths():
    """Modify sys.path and PYTHONPATH to include Tahoe and supporting libraries

    The first step towards building Tahoe is to run::

      python setup.py build_tahoe

    which is the equivalent of::

      mkdir -p $(BASEDIR)/support/lib/python2.5/site-packages
       (or cygpath equivalent)
      setup.py develop --prefix=$(BASEDIR)/support

    This installs .eggs for any dependent libraries that aren't already
    available on the system, into support/lib/pythonN.N/site-packages (or
    support/Lib/site-packages on windows). It also adds an .egg-link for
    Tahoe itself into the same directory.

    We add this directory to os.environ['PYTHONPATH'], so that any child
    processes we spawn will be able to use these packages.

    When the setuptools site.py sees that supportlib in PYTHONPATH, it scans
    through it for .egg and .egg-link entries, and adds them to sys.path .
    Since python has already processed all the site.py files by the time we
    get here, we perform this same sort of processing ourselves: this makes
    tahoe (and dependency libraries) available to code within setup.py
    itself. This is used by the 'setup.py trial' subcommand, which invokes
    trial directly rather than spawning a subprocess (this is easier than
    locating the 'trial' executable, especially when Twisted was installed as
    a dependent library).

    We'll need to add these .eggs to sys.path before importing anything that
    isn't a part of stdlib. All the directories that we add this way are put
    at the start of sys.path, so they will override anything that was present
    on the system (and perhaps found lacking by the setuptools requirements
    expressed in _auto_deps.py).
    """

    extra_syspath_items = []
    extra_pythonpath_items = []

    extra_syspath_items.append(supportlib)
    extra_pythonpath_items.append(supportlib)

    # Since we use setuptools to populate that directory, there will be a
    # number of .egg and .egg-link entries there. Add all of them to
    # sys.path, since that what the setuptools site.py would do if it
    # encountered them at process start time. Without this step, the rest of
    # this process would be unable to use the packages installed there. We
    # don't need to add them to PYTHONPATH, since the site.py present there
    # will add them when the child process starts up.

    if os.path.isdir(supportlib):
        for fn in os.listdir(supportlib):
            if fn.endswith(".egg"):
                extra_syspath_items.append(os.path.join(supportlib, fn))

    # We also add our src/ directory, since that's where all the Tahoe code
    # lives. This matches what site.py does when it sees the .egg-link file
    # that is written to the support dir by an invocation of our 'setup.py
    # develop' command.
    extra_syspath_items.append(os.path.join(basedir, "src"))

    # and we put an extra copy of everything from PYTHONPATH in front, so
    # that it is possible to override the packages that setuptools downloads
    # with alternate versions, by doing e.g. "PYTHONPATH=foo python setup.py
    # trial"
    oldpp = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    if oldpp == [""]:
        # grr silly split() behavior
        oldpp = []
    extra_syspath_items = oldpp + extra_syspath_items

    sys.path = extra_syspath_items + sys.path

    # We also provide it to any child processes we spawn, via
    # os.environ["PYTHONPATH"]
    os.environ["PYTHONPATH"] = os.pathsep.join(oldpp + extra_pythonpath_items)

# add_tahoe_paths() must be called before use_setuptools() is called. I don't
# know why. If it isn't, then a later pkg_resources.requires(pycryptopp) call
# fails because an old version (in /usr/lib) was already loaded.
add_tahoe_paths()

try:
    from ez_setup import use_setuptools
except ImportError:
    pass
else:
    # This invokes our own customized version of ez_setup.py to make sure that
    # setuptools >= v0.6c8 (a.k.a. v0.6-final) is installed.

    # setuptools < v0.6c8 doesn't handle eggs which get installed into the CWD
    # as a result of being transitively depended on in a setup_requires, but
    # then are needed for the installed code to run, i.e. in an
    # install_requires.
    use_setuptools(download_delay=0, min_version="0.6c8")

from setuptools import Extension, find_packages, setup
from setuptools.command import sdist
from distutils.core import Command

# Make the dependency-version-requirement, which is used by the Makefile at
# build-time, also available to the app at runtime:
import shutil
try:
    shutil.copyfile("_auto_deps.py", os.path.join("src", "allmydata", "_auto_deps.py"))
except EnvironmentError:
    # Nevermind then -- perhaps it is already in place and in any case we can do
    # without it.
    pass

trove_classifiers=[
    "Development Status :: 5 - Production/Stable",
    "Environment :: Console",
    "Environment :: Web Environment",
    "License :: OSI Approved :: GNU General Public License (GPL)",
    "License :: DFSG approved",
    "License :: Other/Proprietary License",
    "Intended Audience :: Developers",
    "Intended Audience :: End Users/Desktop",
    "Intended Audience :: System Administrators",
    "Operating System :: Microsoft",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: Unix",
    "Operating System :: POSIX :: Linux",
    "Operating System :: POSIX",
    "Operating System :: MacOS :: MacOS X",
    "Operating System :: Microsoft :: Windows :: Windows NT/2000",
    "Operating System :: OS Independent",
    "Natural Language :: English",
    "Programming Language :: C",
    "Programming Language :: Python",
    "Topic :: Utilities",
    "Topic :: System :: Systems Administration",
    "Topic :: System :: Filesystems",
    "Topic :: System :: Distributed Computing",
    "Topic :: Software Development :: Libraries",
    "Topic :: Communications :: Usenet News",
    "Topic :: System :: Archiving :: Backup",
    "Topic :: System :: Archiving :: Mirroring",
    "Topic :: System :: Archiving",
    ]


VERSIONFILE = "src/allmydata/_version.py"
verstr = "unknown"
try:
    verstrline = open(VERSIONFILE, "rt").read()
except EnvironmentError:
    pass # Okay, there is no version file.
else:
    VSRE = r"^verstr = ['\"]([^'\"]*)['\"]"
    mo = re.search(VSRE, verstrline, re.M)
    if mo:
        verstr = mo.group(1)
    else:
        print "unable to find version in %s" % (VERSIONFILE,)
        raise RuntimeError("if %s.py exists, it is required to be well-formed" % (VERSIONFILE,))

LONG_DESCRIPTION=\
"""Welcome to the Tahoe project, a secure, decentralized, fault-tolerant
filesystem.  All of the source code is available under a Free Software, Open
Source licence.

This filesystem is encrypted and spread over multiple peers in such a way that
it remains available even when some of the peers are unavailable,
malfunctioning, or malicious."""

# For Desert Island builds, assume that the user has extracted the dependency
# tarball into a directory named 'misc/dependencies'.
dependency_links=[os.path.join(os.getcwd(), 'misc', 'dependencies')]

# By adding a web page to the dependency_links we are able to put new packages
# up there and have them be automatically discovered by existing copies of the
# tahoe source when that source was built.
dependency_links.append("http://allmydata.org/trac/tahoe/wiki/Dependencies")

# Default setup_requires are pyutil for the Windows installer builder(see
# misc/sub-ver.py) and Twisted for the tests.
#setup_requires = ['pyutil >= 1.3.16', 'Twisted >= 2.4.0']
setup_requires = []
# darcsver is needed only if you want "./setup.py darcsver" to write a new
# version stamp in src/allmydata/_version.py, with a version number derived from
# darcs history.
# http://pypi.python.org/pypi/darcsver
if 'darcsver' in sys.argv[1:]:
    setup_requires.append('darcsver >= 1.1.5')

# setuptools_darcs is required to produce complete distributions (such as with
# "sdist" or "bdist_egg"), unless there is a PKG-INFO file present which shows
# that this is itself a source distribution.
# http://pypi.python.org/pypi/setuptools_darcs
if not os.path.exists('PKG-INFO'):
    setup_requires.append('setuptools_darcs >= 1.1.0')

class ShowSupportLib(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        # TODO: --quiet suppresses the 'running show_supportlib' message.
        # Find a way to do this all the time.
        print supportlib # TODO windowsy

class ShowPythonPath(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        # TODO: --quiet suppresses the 'running show_supportlib' message.
        # Find a way to do this all the time.
        print "PYTHONPATH=%s" % os.environ["PYTHONPATH"]

class BuildTahoe(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        # 'setup.py develop --prefix SUPPORT' will complain if SUPPORTLIB is
        # not on PYTHONPATH, because it thinks you are installing to a place
        # that will not be searched at runtime (which is true, except that we
        # add SUPPORTLIB to PYTHONPATH to run tests, etc). So set up
        # PYTHONPATH now, then spawn a 'setup.py develop' command. Also, we
        # have to create the directory ourselves.
        if not os.path.isdir(supportlib):
            os.makedirs(supportlib)

        command = [sys.executable, "setup.py", "develop", "--prefix", "support"]
        if sys.platform == "linux2":
            # workaround for tahoe #229 / setuptools #17, on debian
            command.extend(["--site-dirs", "/var/lib/python-support/" + pyver])
        print "Command:", " ".join(command)
        rc = subprocess.call(command)
        if rc < 0:
            print >>sys.stderr, "'setup.py develop' terminated by signal", -rc
            sys.exit(1)
        elif rc > 0:
            print >>sys.stderr, "'setup.py develop' exited with rc", rc
            sys.exit(rc)

class Trial(Command):
    # Unlike 'build' and 'bdist_egg', the 'trial' subcommand cannot be run in
    # conjunction with other subcommands.

    # The '-a' argument is split on whitespace and passed into trial. (the
    # distutils parser does not give subcommands access to the rest of
    # sys.argv, so unfortunately we cannot just do something like:
    #   setup.py trial --reporter=text allmydata.test.test_util

    # Examples:
    #  setup.py trial    # run all tests
    #  setup.py trial -a allmydata.test.test_util   # run some tests
    #  setup.py trial -a '--reporter=text allmydata.test.test_util' #other args

    description = "Run unit tests via trial"

    user_options = [ ("args=", "a", "Argument string to pass to trial: setup.py trial -a allmydata.test.test_util"),
                     ]
    def initialize_options(self):
        self.args = "allmydata"
    def finalize_options(self):
        pass

    def run(self):
        # make sure Twisted is available (for trial itself), and both the
        # Tahoe source code and our dependent libraries are available (so
        # that trial has some test code to work with)

        from twisted.scripts import trial

        args = self.args.strip().split()

        # one wrinkle: we want to set the reactor here, because of bug #402
        # (twisted bug #3218). We just jam in a "--reactor poll" at the start
        # of the arglist. This does not permit the reactor to be overridden,
        # unfortunately.
        if sys.platform in ("linux2", "cygwin"):
            # poll on linux2 to avoid #402 problems with select
            # poll on cygwin since selectreactor runs out of fds
            args = ["--reactor", "poll"] + args

        # zooko also had os.environ["PYTHONUNBUFFERED"]="1" and
        # args.append("--rterrors")

        sys.argv = ["trial"] + args
        if self.verbose > 1:
            print "To run this test directly, use:"
            print "PYTHONPATH=%s %s" % (os.environ["PYTHONPATH"],
                                        " ".join(sys.argv))
        else:
            print "(run with -vv for trial command-line details)"
        trial.run() # this does sys.exit
        # NEVER REACHED

class MySdist(sdist.sdist):
    """ A hook in the sdist command so that we can determine whether this the
    tarball should be 'SUMO' or not, i.e. whether or not to include the
    external dependency tarballs.
    """

    # Add our own sumo option to the sdist command, which toggles the
    # external dependencies being included in the sdist.
    user_options = sdist.sdist.user_options + \
        [('sumo', 's', "create a 'sumo' sdist which includes the external " \
          "dependencies")]
    boolean_options = ['sumo']

    def initialize_options(self):
        sdist.sdist.initialize_options(self)
        self.sumo = None

    def run(self):
        self.run_command('egg_info')
        ei_cmd = self.get_finalized_command('egg_info')
        self.filelist = ei_cmd.filelist
        self.filelist.append(os.path.join(ei_cmd.egg_info,'SOURCES.txt'))

        # If '--sumo' wasn't specified in the arguments, do not include
        # the external dependency tarballs in the sdist.
        if not self.sumo:
            self.filelist.exclude_pattern(None, prefix='misc/dependencies')

        print self.filelist.files
        self.check_readme()
        self.check_metadata()
        self.make_distribution()

        dist_files = getattr(self.distribution,'dist_files',[])
        for file in self.archive_files:
            data = ('sdist', '', file)
            if data not in dist_files:
                dist_files.append(data)

# get a list of the libraries that we depend upon, for use in the call to
# setup() at the end of this file
from _auto_deps import install_requires

setup(name='allmydata-tahoe',
      version=verstr,
      description='secure, decentralized, fault-tolerant filesystem',
      long_description=LONG_DESCRIPTION,
      author='the allmydata.org Tahoe project',
      author_email='tahoe-dev@allmydata.org',
      url='http://allmydata.org/',
      license='GNU GPL',
      cmdclass={"show_supportlib": ShowSupportLib,
                "show_pythonpath": ShowPythonPath,
                "build_tahoe": BuildTahoe,
                "trial": Trial,
                "sdist": MySdist,
                },
      package_dir = {'':'src'},
      packages=find_packages("src"),
      classifiers=trove_classifiers,
      test_suite="allmydata.test",
      install_requires=install_requires,
      include_package_data=True,
      setup_requires=setup_requires,
      dependency_links=dependency_links,
      entry_points = { 'console_scripts': [ 'tahoe = allmydata.scripts.runner:run' ] },
      zip_safe=False, # We prefer unzipped for easier access.
      )
