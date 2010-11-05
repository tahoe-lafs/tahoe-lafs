#! /usr/bin/env python
# -*- coding: utf-8 -*-

# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2008-2010 Allmydata, Inc.
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.html file for licensing information.

import glob, os, shutil, stat, subprocess, sys, zipfile, re

##### sys.path management

def pylibdir(prefixdir):
    pyver = "python%d.%d" % (sys.version_info[:2])
    if sys.platform == "win32":
        return os.path.join(prefixdir, "Lib", "site-packages")
    else:
        return os.path.join(prefixdir, "lib", pyver, "site-packages")

basedir = os.path.dirname(os.path.abspath(__file__))
supportlib = pylibdir(os.path.join(basedir, "support"))

# locate our version number

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

version = read_version_py("src/allmydata/_version.py")

APPNAME='allmydata-tahoe'
APPNAMEFILE = os.path.join('src', 'allmydata', '_appname.py')
APPNAMEFILESTR = "__appname__ = '%s'" % (APPNAME,)
try:
    curappnamefilestr = open(APPNAMEFILE, 'rU').read()
except EnvironmentError:
    # No file, or unreadable or something, okay then let's try to write one.
    open(APPNAMEFILE, "w").write(APPNAMEFILESTR)
else:
    if curappnamefilestr.strip() != APPNAMEFILESTR:
        print "Error -- this setup.py file is configured with the 'application name' to be '%s', but there is already a file in place in '%s' which contains the contents '%s'.  If the file is wrong, please remove it and setup.py will regenerate it and write '%s' into it." % (APPNAME, APPNAMEFILE, curappnamefilestr, APPNAMEFILESTR)
        sys.exit(-1)

# setuptools/zetuptoolz looks in __main__.__requires__ for a list of
# requirements. When running "python setup.py test", __main__ is
# setup.py, so we put the list here so that the requirements will be
# available for tests:

# Tahoe's dependencies are managed by the find_links= entry in setup.cfg and
# the _auto_deps.install_requires list, which is used in the call to setup()
# below.
adglobals = {}
execfile('src/allmydata/_auto_deps.py', adglobals)
install_requires = adglobals['install_requires']

if ('trial' in sys.argv or 'test' in sys.argv) and version is not None:
    __requires__ = [APPNAME + '==' + version] + install_requires

egg = os.path.realpath(glob.glob('setuptools-*.egg')[0])
sys.path.insert(0, egg)
egg = os.path.realpath(glob.glob('darcsver-*.egg')[0])
sys.path.insert(0, egg)
import setuptools; setuptools.bootstrap_install_from = egg

from setuptools import find_packages, setup
from setuptools.command import sdist
from setuptools import Command

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
    "Programming Language :: Python :: 2.6",
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


setup_requires = []

# The darcsver command from the darcsver plugin is needed to initialize the
# distribution's .version attribute correctly. (It does this either by
# examining darcs history, or if that fails by reading the
# src/allmydata/_version.py file). darcsver will also write a new version
# stamp in src/allmydata/_version.py, with a version number derived from
# darcs history. Note that the setup.cfg file has an "[aliases]" section
# which enumerates commands that you might run and specifies that it will run
# darcsver before each one. If you add different commands (or if I forgot
# some that are already in use), you may need to add it to setup.cfg and
# configure it to run darcsver before your command, if you want the version
# number to be correct when that command runs.
# http://pypi.python.org/pypi/darcsver
setup_requires.append('darcsver >= 1.2.0')

# Nevow requires Twisted to setup, but doesn't declare that requirement in a
# way that enables setuptools to satisfy that requirement before Nevow's
# setup.py tried to "import twisted". Fortunately we require setuptools_trial
# to setup and setuptools_trial requires Twisted to install, so hopefully
# everything will work out until the Nevow issue is fixed:
# http://divmod.org/trac/ticket/2629 setuptools_trial is needed if you want
# "./setup.py trial" or "./setup.py test" to execute the tests (and in order
# to make sure Twisted is installed early enough -- see the paragraph above).
# http://pypi.python.org/pypi/setuptools_trial
setup_requires.extend(['setuptools_trial >= 0.5'])

# setuptools_darcs is required to produce complete distributions (such as
# with "sdist" or "bdist_egg") (unless there is a PKG-INFO file present which
# shows that this is itself a source distribution). For simplicity, and
# because there is some unknown error with setuptools_darcs when building and
# testing tahoe all in one python command on some platforms, we always add it
# to setup_requires. http://pypi.python.org/pypi/setuptools_darcs
setup_requires.append('setuptools_darcs >= 1.1.0')

# trialcoverage is required if you want the "trial" unit test runner to have a
# "--reporter=bwverbose-coverage" option which produces code-coverage results.
# The required version is 0.3.3, because that is the latest version that only
# depends on a version of pycoverage for which binary packages are available.
if "--reporter=bwverbose-coverage" in sys.argv:
    setup_requires.append('trialcoverage >= 0.3.3')

# stdeb is required to produce Debian files with the "sdist_dsc" command.
if "sdist_dsc" in sys.argv:
    setup_requires.append('stdeb >= 0.3')

tests_require=[
    # Mock - Mocking and Testing Library
    # http://www.voidspace.org.uk/python/mock/
    "mock",
    ]

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
        print "PYTHONPATH=%s" % os.environ.get("PYTHONPATH", '')

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
        oldpp = os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if oldpp == [""]:
            # grr silly split() behavior
            oldpp = []
        os.environ['PYTHONPATH'] = os.pathsep.join(oldpp + [supportlib,])

        # We must require the command to be safe to split on
        # whitespace, and have --python and --directory to make it
        # easier to achieve this.

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

class TestMacDiskImage(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        import sys
        sys.path.append(os.path.join('misc', 'build_helpers'))
        import test_mac_diskimage
        return test_mac_diskimage.test_mac_diskimage('Allmydata', version=self.distribution.metadata.version)

class CheckAutoDeps(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        adglobals = {}
        execfile('src/allmydata/_auto_deps.py', adglobals)
        adglobals['require_auto_deps']()


class MakeExecutable(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        bin_tahoe_template = os.path.join("bin", "tahoe-script.template")

        if sys.platform == 'win32':
            # 'tahoe' script is needed for cygwin
            script_names = ["tahoe.pyscript", "tahoe"]
        else:
            script_names = ["tahoe"]

        # Create the tahoe script file under the 'bin' directory. This
        # file is exactly the same as the 'tahoe-script.template' script
        # except that the shebang line is rewritten to use our sys.executable
        # for the interpreter.
        f = open(bin_tahoe_template, "rU")
        script_lines = f.readlines()
        f.close()
        script_lines[0] = '#!%s\n' % (sys.executable,)
        for script_name in script_names:
            tahoe_script = os.path.join("bin", script_name)
            try:
                os.remove(tahoe_script)
            except Exception:
                if os.path.exists(tahoe_script):
                   raise
            f = open(tahoe_script, "wb")
            for line in script_lines:
                f.write(line)
            f.close()

            # chmod +x
            old_mode = stat.S_IMODE(os.stat(tahoe_script)[stat.ST_MODE])
            new_mode = old_mode | (stat.S_IXUSR | stat.S_IRUSR |
                                   stat.S_IXGRP | stat.S_IRGRP |
                                   stat.S_IXOTH | stat.S_IROTH )
            os.chmod(tahoe_script, new_mode)

        old_tahoe_exe = os.path.join("bin", "tahoe.exe")
        try:
            os.remove(old_tahoe_exe)
        except Exception:
            if os.path.exists(old_tahoe_exe):
                raise


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

setup_args = {}
if version:
    setup_args["version"] = version

setup(name=APPNAME,
      description='secure, decentralized, fault-tolerant filesystem',
      long_description=open('README.txt', 'rU').read(),
      author='the Tahoe-LAFS project',
      author_email='tahoe-dev@tahoe-lafs.org',
      url='http://tahoe-lafs.org/',
      license='GNU GPL', # see README.txt -- there is an alternative licence
      cmdclass={"show_supportlib": ShowSupportLib,
                "show_pythonpath": ShowPythonPath,
                "run_with_pythonpath": RunWithPythonPath,
                "check_auto_deps": CheckAutoDeps,
                "test_mac_diskimage": TestMacDiskImage,
                "make_executable": MakeExecutable,
                "sdist": MySdist,
                },
      package_dir = {'':'src'},
      packages=find_packages("src"),
      classifiers=trove_classifiers,
      test_suite="allmydata.test",
      install_requires=install_requires,
      tests_require=tests_require,
      include_package_data=True,
      setup_requires=setup_requires,
      entry_points = { 'console_scripts': [ 'tahoe = allmydata.scripts.runner:run' ] },
      zip_safe=False, # We prefer unzipped for easier access.
      **setup_args
      )
