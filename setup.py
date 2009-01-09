#! /usr/bin/env python

# Allmydata Tahoe -- secure, distributed storage grid
#
# Copyright (C) 2008 Allmydata, Inc.
#
# This file is part of tahoe.
#
# See the docs/about.html file for licensing information.

import os, re, shutil, stat, subprocess, sys, zipfile

##### sys.path management

def pylibdir(prefixdir):
    pyver = "python%d.%d" % (sys.version_info[:2])
    if sys.platform == "win32":
        return os.path.join(prefixdir, "Lib", "site-packages")
    else:
        return os.path.join(prefixdir, "lib", pyver, "site-packages")

basedir = os.path.dirname(os.path.abspath(__file__))
supportlib = pylibdir(os.path.join(basedir, "support"))

for i in range(len(sys.argv)):
    arg = sys.argv[i]
    if arg == "build_tahoe":
        del sys.argv[i]
        sys.argv.extend(["develop", "--prefix=support", "--script-dir=support/bin"])

for i in range(len(sys.argv)):
    arg = sys.argv[i]
    prefixdir = None
    if arg.startswith("--prefix="):
        prefixdir = arg[len("--prefix="):]
    if arg == "--prefix":
        if len(sys.argv) > i+1:
            prefixdir = sys.argv[i+1]

    if prefixdir:
        libdir = pylibdir(prefixdir)
        try:
            os.makedirs(libdir)
        except EnvironmentError, le:
            # Okay, maybe the dir was already there.
            pass
        sys.path.append(libdir)
        pp = os.environ.get('PYTHONPATH','').split(os.pathsep)
        pp.append(libdir)
        os.environ['PYTHONPATH'] = os.pathsep.join(pp)

    if arg.startswith("build"):
        # chmod +x bin/tahoe
        bin_tahoe = os.path.join("bin", "tahoe")
        old_mode = stat.S_IMODE(os.stat(bin_tahoe)[stat.ST_MODE])
        new_mode = old_mode | (stat.S_IXUSR | stat.S_IRUSR |
                               stat.S_IXGRP | stat.S_IRGRP |
                               stat.S_IXOTH | stat.S_IROTH )
        os.chmod(bin_tahoe, new_mode)

    if arg.startswith("install") or arg.startswith("develop"):
        if sys.platform == "linux2":
            # workaround for tahoe #229 / setuptools #17, on debian
            sys.argv.extend(["--site-dirs", "/var/lib/python-support/python%d.%d" % (sys.version_info[:2])])
        elif sys.platform == "darwin":
            # this probably only applies to leopard 10.5, possibly only 10.5.5
            sd = "/System/Library/Frameworks/Python.framework/Versions/%d.%d/Extras/lib/python" % (sys.version_info[:2])
            sys.argv.extend(["--site-dirs", sd])

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
    use_setuptools(download_delay=0, min_version="0.6c10dev")

from setuptools import find_packages, setup
from setuptools.command import sdist
from distutils.core import Command
from pkg_resources import require

import pkg_resources
pkg_resources.require('setuptools_trial')
from setuptools_trial.setuptools_trial import TrialTest

# Make the dependency-version-requirement, which is used by the Makefile at
# build-time, also available to the app at runtime:
import shutil
shutil.copyfile("_auto_deps.py", os.path.join("src", "allmydata", "_auto_deps.py"))

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
    "Operating System :: Microsoft :: Windows :: Windows NT/2000",
    "Operating System :: Unix",
    "Operating System :: POSIX :: Linux",
    "Operating System :: POSIX",
    "Operating System :: MacOS :: MacOS X",
    "Operating System :: OS Independent",
    "Natural Language :: English",
    "Programming Language :: C",
    "Programming Language :: Python",
    "Programming Language :: Python :: 2",
    "Programming Language :: Python :: 2.4",
    "Programming Language :: Python :: 2.5",
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


setup_requires = []

# Nevow requires Twisted to setup, but doesn't declare that requirement in a way that enables
# setuptools to satisfy that requirement before Nevow's setup.py tried to "import twisted".
setup_requires.extend(['Twisted >= 2.4.0', 'setuptools_trial'])

# darcsver is needed only if you want "./setup.py darcsver" to write a new
# version stamp in src/allmydata/_version.py, with a version number derived from
# darcs history.
# http://pypi.python.org/pypi/darcsver
if 'darcsver' in sys.argv[1:]:
    setup_requires.append('darcsver >= 1.1.5')

# setuptools_trial is needed only if you want "./setup.py trial" to execute the tests.
# http://pypi.python.org/pypi/setuptools_trial
if 'trial' in sys.argv[1:]:
    setup_requires.append('setuptools_trial >= 0.2')

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

class RunWithPythonPath(Command):
    description = "Run a subcommand with PYTHONPATH set appropriately"

    user_options = [ ("python", "p",
                      "Treat command string as arguments to a python executable"),
                     ("command=", "c", "Command to be run"),
                     ("directory=", "d", "Directory to run the command in"),
                     ]
    boolean_options = ["python"]

    def initialize_options(self):
        self.command = None
        self.python = False
        self.directory = None
    def finalize_options(self):
        pass
    def run(self):
        # os.environ['PYTHONPATH'] is already set by add_tahoe_paths, so we
        # just need to exec() their command. We must require the command to
        # be safe to split on whitespace, and have --python and --directory
        # to make it easier to achieve this.
        command = []
        if self.python:
            command.append(sys.executable)
        if self.command:
            command.extend(self.command.split())
        if not command:
            raise RuntimeError("The --command argument is mandatory")
        if self.directory:
            os.chdir(self.directory)
        if self.verbose:
            print "command =", " ".join(command)
        rc = subprocess.call(command)
        sys.exit(rc)

class CheckAutoDeps(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        import _auto_deps
        _auto_deps.require_auto_deps()


class BuildTahoe(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        # On Windows, create the 'tahoe-script.py' file based on the 'tahoe'
        # executable script under the 'bin' directory so that the tahoe.exe
        # will work correctly.  The 'tahoe-script.py' file is exactly the same
        # as the 'tahoe' script except that we need to update the she-bang
        # line.  The tahoe.exe will be copied from the setuptools egg's cli.exe
        # and this will work from a zip-safe and non-zip-safe setuptools egg.
        if sys.platform == "win32":
            setuptools_egg = require("setuptools")[0].location
            if os.path.isfile(setuptools_egg):
                z = zipfile.ZipFile(setuptools_egg, 'r')
                for filename in z.namelist():
                    if 'cli.exe' in filename:
                        cli_exe = z.read(filename)
            else:
                cli_exe = os.path.join(setuptools_egg, 'setuptools', 'cli.exe')
            tahoe_exe = os.path.join("bin", "tahoe.exe")
            if os.path.isfile(setuptools_egg):
                f = open(tahoe_exe, 'wb')
                f.write(cli_exe)
                f.close()
            else:
                shutil.copy(cli_exe, tahoe_exe)
            bin_tahoe = os.path.join("bin", "tahoe")
            f = open(bin_tahoe, "r")
            script_lines = f.readlines()
            f.close()
            script_lines[0] = "#!%s\n" % sys.executable
            tahoe_script = os.path.join("bin", "tahoe-script.py")
            f = open(tahoe_script, "w")
            for line in script_lines:
                f.write(line)
            f.close()

        command = [sys.executable, "setup.py", "develop", "--prefix", "support"]
        print "Command:", " ".join(command)
        rc = subprocess.call(command)
        if rc < 0:
            print >>sys.stderr, "'setup.py develop' terminated by signal", -rc
            sys.exit(1)
        elif rc > 0:
            print >>sys.stderr, "'setup.py develop' exited with rc", rc
            sys.exit(rc)

class Trial(TrialTest):
    # Custom sub-class of the TrialTest class from the setuptools_trial
    # plugin so that we can ensure certain options are set by default.
    #
    # Examples:
    #  setup.py trial    # run all tests
    #  setup.py trial -a allmydata.test.test_util   # run some tests
    #  setup.py trial -a '--reporter=text allmydata.test.test_util' #other args


    def initialize_options(self):
        TrialTest.initialize_options(self)

        # We want to set the reactor to 'poll', because of bug #402
        # (twisted bug #3218).
        if sys.platform in ("linux2", "cygwin"):
            # poll on linux2 to avoid #402 problems with select
            # poll on cygwin since selectreactor runs out of fds
            self.reactor = "poll"


class MySdist(sdist.sdist):
    """ A hook in the sdist command so that we can determine whether this the
    tarball should be 'SUMO' or not, i.e. whether or not to include the
    external dependency tarballs. Note that we always include
    misc/dependencies/* in the tarball; --sumo controls whether tahoe-deps/*
    is included as well.
    """

    user_options = sdist.sdist.user_options + \
        [('sumo', 's',
          "create a 'sumo' sdist which includes the contents of tahoe-deps/*"),
         ]
    boolean_options = ['sumo']

    def initialize_options(self):
        sdist.sdist.initialize_options(self)
        self.sumo = False

    def make_distribution(self):
        # add our extra files to the list just before building the
        # tarball/zipfile. We override make_distribution() instead of run()
        # because setuptools.command.sdist.run() does not lend itself to
        # easy/robust subclassing (the code we need to add goes right smack
        # in the middle of a 12-line method). If this were the distutils
        # version, we'd override get_file_list().

        if self.sumo:
            # If '--sumo' was specified, include tahoe-deps/* in the sdist.
            # We assume that the user has fetched the tahoe-deps.tar.gz
            # tarball and unpacked it already.
            self.filelist.extend([os.path.join("tahoe-deps", fn)
                                  for fn in os.listdir("tahoe-deps")])
            # In addition, we want the tarball/zipfile to have -SUMO in the
            # name, and the unpacked directory to have -SUMO too. The easiest
            # way to do this is to patch self.distribution and override the
            # get_fullname() method. (an alternative is to modify
            # self.distribution.metadata.version, but that also affects the
            # contents of PKG-INFO).
            fullname = self.distribution.get_fullname()
            def get_fullname():
                return fullname + "-SUMO"
            self.distribution.get_fullname = get_fullname

        return sdist.sdist.make_distribution(self)

# Tahoe's dependencies are managed by the find_links= entry in setup.cfg and
# the _auto_deps.install_requires list, which is used in the call to setup()
# at the end of this file
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
                "run_with_pythonpath": RunWithPythonPath,
                "check_auto_deps": CheckAutoDeps,
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
      entry_points = { 'console_scripts': [ 'tahoe = allmydata.scripts.runner:run' ] },
      zip_safe=False, # We prefer unzipped for easier access.
      )
