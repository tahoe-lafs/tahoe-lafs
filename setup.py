#! /usr/bin/env python
# -*- coding: utf-8 -*-
import sys; assert sys.version_info < (3,), ur"Tahoe-LAFS does not run under Python 3. Please use a version of Python between 2.6 and 2.7.x inclusive."

# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2006-2012 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

import os, stat, subprocess, re

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

VERSION_PY_FILENAME = 'src/allmydata/_version.py'
version = read_version_py(VERSION_PY_FILENAME)

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
        print("Error -- this setup.py file is configured with the 'application name' to be '%s', but there is already a file in place in '%s' which contains the contents '%s'.  If the file is wrong, please remove it and setup.py will regenerate it and write '%s' into it." % (APPNAME, APPNAMEFILE, curappnamefilestr, APPNAMEFILESTR))
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

if len(sys.argv) > 1 and sys.argv[1] == '--fakedependency':
    del sys.argv[1]
    install_requires += ["fakedependency >= 1.0.0"]

__requires__ = install_requires[:]

egg = os.path.realpath('setuptools-0.6c16dev6.egg')
sys.path.insert(0, egg)
import setuptools; setuptools.bootstrap_install_from = egg

from setuptools import setup
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
    "Operating System :: Unix",
    "Operating System :: POSIX :: Linux",
    "Operating System :: POSIX",
    "Operating System :: MacOS :: MacOS X",
    "Operating System :: OS Independent",
    "Natural Language :: English",
    "Programming Language :: C",
    "Programming Language :: Python",
    "Programming Language :: Python :: 2",
    "Programming Language :: Python :: 2.6",
    "Programming Language :: Python :: 2.7",
    "Topic :: Utilities",
    "Topic :: System :: Systems Administration",
    "Topic :: System :: Filesystems",
    "Topic :: System :: Distributed Computing",
    "Topic :: Software Development :: Libraries",
    "Topic :: System :: Archiving :: Backup",
    "Topic :: System :: Archiving :: Mirroring",
    "Topic :: System :: Archiving",
    ]


# We no longer have any requirements specific to tests.
tests_require=[]


class Trial(Command):
    description = "run trial (use 'bin%stahoe debug trial' for the full set of trial options)" % (os.sep,)
    # This is just a subset of the most useful options, for compatibility.
    user_options = [ ("no-rterrors", None, "Don't print out tracebacks as they occur."),
                     ("rterrors", "e", "Print out tracebacks as they occur (default, so ignored)."),
                     ("until-failure", "u", "Repeat a test (specified by -s) until it fails."),
                     ("reporter=", None, "The reporter to use for this test run."),
                     ("suite=", "s", "Specify the test suite."),
                     ("quiet", None, "Don't display version numbers and paths of Tahoe dependencies."),
                     ("coverage", "c", "Collect branch coverage information."),
                   ]

    def initialize_options(self):
        self.rterrors = False
        self.no_rterrors = False
        self.until_failure = False
        self.reporter = None
        self.suite = "allmydata"
        self.quiet = False
        self.coverage = False

    def finalize_options(self):
        pass

    def run(self):
        args = [sys.executable, os.path.join('bin', 'tahoe')]

        if self.coverage:
            from errno import ENOENT
            coverage_cmd = 'coverage'
            try:
                subprocess.call([coverage_cmd, 'help'])
            except OSError as e:
                if e.errno != ENOENT:
                    raise
                coverage_cmd = 'python-coverage'
                try:
                    rc = subprocess.call([coverage_cmd, 'help'])
                except OSError as e:
                    if e.errno != ENOENT:
                        raise
                    print >>sys.stderr
                    print >>sys.stderr, "Couldn't find the command 'coverage' nor 'python-coverage'."
                    print >>sys.stderr, "coverage can be installed using 'pip install coverage', or on Debian-based systems, 'apt-get install python-coverage'."
                    sys.exit(1)

            args += ['@' + coverage_cmd, 'run', '--branch', '--source=src/allmydata', '@tahoe']

        if not self.quiet:
            args.append('--version-and-path')
        args += ['debug', 'trial']
        if self.rterrors and self.no_rterrors:
            raise AssertionError("--rterrors and --no-rterrors conflict.")
        if not self.no_rterrors:
            args.append('--rterrors')
        if self.until_failure:
            args.append('--until-failure')
        if self.reporter:
            args.append('--reporter=' + self.reporter)
        if self.suite:
            args.append(self.suite)
        rc = subprocess.call(args)
        sys.exit(rc)


class MakeExecutable(Command):
    description = "make the 'bin%stahoe' scripts" % (os.sep,)
    user_options = []

    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        bin_tahoe_template = os.path.join("bin", "tahoe-script.template")

        # tahoe.pyscript is really only necessary for Windows, but we also
        # create it on Unix for consistency.
        script_names = ["tahoe.pyscript", "tahoe"]

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
        unix_script = os.path.join("bin", "tahoe")
        old_mode = stat.S_IMODE(os.stat(unix_script)[stat.ST_MODE])
        new_mode = old_mode | (stat.S_IXUSR | stat.S_IRUSR |
                               stat.S_IXGRP | stat.S_IRGRP |
                               stat.S_IXOTH | stat.S_IROTH )
        os.chmod(unix_script, new_mode)

        old_tahoe_exe = os.path.join("bin", "tahoe.exe")
        try:
            os.remove(old_tahoe_exe)
        except Exception:
            if os.path.exists(old_tahoe_exe):
                raise


GIT_VERSION_BODY = '''
# This _version.py is generated from git metadata by the tahoe setup.py.

__pkgname__ = %(pkgname)r
real_version = %(version)r
full_version = %(full)r
branch = %(branch)r
verstr = %(normalized)r
__version__ = verstr
'''

def run_command(args, cwd=None):
    use_shell = sys.platform == "win32"
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, cwd=cwd, shell=use_shell)
    except EnvironmentError as e:  # if this gives a SyntaxError, note that Tahoe-LAFS requires Python 2.6+
        print("Warning: unable to run %r." % (" ".join(args),))
        print(e)
        return None
    stdout = p.communicate()[0].strip()
    if p.returncode != 0:
        print("Warning: %r returned error code %r." % (" ".join(args), p.returncode))
        return None
    return stdout


def versions_from_git(tag_prefix):
    # This runs 'git' from the directory that contains this file. That either
    # means someone ran a setup.py command (and this code is in
    # versioneer.py, thus the containing directory is the root of the source
    # tree), or someone ran a project-specific entry point (and this code is
    # in _version.py, thus the containing directory is somewhere deeper in
    # the source tree). This only gets called if the git-archive 'subst'
    # variables were *not* expanded, and _version.py hasn't already been
    # rewritten with a short version string, meaning we're inside a checked
    # out source tree.

    # versions_from_git (as copied from python-versioneer) returns strings
    # like "1.9.0-25-gb73aba9-dirty", which means we're in a tree with
    # uncommited changes (-dirty), the latest checkin is revision b73aba9,
    # the most recent tag was 1.9.0, and b73aba9 has 25 commits that weren't
    # in 1.9.0 . The narrow-minded NormalizedVersion parser that takes our
    # output (meant to enable sorting of version strings) refuses most of
    # that. Tahoe uses a function named suggest_normalized_version() that can
    # handle "1.9.0.post25", so dumb down our output to match.

    try:
        source_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError as e:
        # some py2exe/bbfreeze/non-CPython implementations don't do __file__
        print("Warning: unable to find version because we could not obtain the source directory.")
        print(e)
        return {}
    stdout = run_command(["git", "describe", "--tags", "--dirty", "--always"],
                         cwd=source_dir)
    if stdout is None:
        # run_command already complained.
        return {}
    if not stdout.startswith(tag_prefix):
        print("Warning: tag %r doesn't start with prefix %r." % (stdout, tag_prefix))
        return {}
    version = stdout[len(tag_prefix):]
    pieces = version.split("-")
    if len(pieces) == 1:
        normalized_version = pieces[0]
    else:
        normalized_version = "%s.post%s" % (pieces[0], pieces[1])

    stdout = run_command(["git", "rev-parse", "HEAD"], cwd=source_dir)
    if stdout is None:
        # run_command already complained.
        return {}
    full = stdout.strip()
    if version.endswith("-dirty"):
        full += "-dirty"
        normalized_version += ".dev0"

    # Thanks to Jistanidiot at <http://stackoverflow.com/questions/6245570/get-current-branch-name>.
    stdout = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=source_dir)
    branch = (stdout or "unknown").strip()

    return {"version": version, "normalized": normalized_version, "full": full, "branch": branch}

# setup.cfg has an [aliases] section which runs "update_version" before many
# commands (like "build" and "sdist") that need to know our package version
# ahead of time. If you add different commands (or if we forgot some), you
# may need to add it to setup.cfg and configure it to run update_version
# before your command.

class UpdateVersion(Command):
    description = "update _version.py from revision-control metadata"
    user_options = []

    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        global version
        verstr = version
        if os.path.isdir(os.path.join(basedir, ".git")):
            verstr = self.try_from_git()

        if verstr:
            self.distribution.metadata.version = verstr
        else:
            print("""\
********************************************************************
Warning: no version information found. This may cause tests to fail.
********************************************************************
""")

    def try_from_git(self):
        # If we change APPNAME, the release tag names should also change from then on.
        versions = versions_from_git(APPNAME + '-')
        if versions:
            f = open(VERSION_PY_FILENAME, "wb")
            f.write(GIT_VERSION_BODY %
                    { "pkgname": self.distribution.get_name(),
                      "version": versions["version"],
                      "normalized": versions["normalized"],
                      "full": versions["full"],
                      "branch": versions["branch"],
                    })
            f.close()
            print("Wrote normalized version %r into '%s'" % (versions["normalized"], VERSION_PY_FILENAME))

        return versions.get("normalized", None)


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

        try:
            old_mask = os.umask(int("022", 8))
            return sdist.sdist.make_distribution(self)
        finally:
            os.umask(old_mask)


setup_args = {}
if version:
    setup_args["version"] = version

setup(name=APPNAME,
      description='secure, decentralized, fault-tolerant filesystem',
      long_description=open('README.rst', 'rU').read(),
      author='the Tahoe-LAFS project',
      author_email='tahoe-dev@tahoe-lafs.org',
      url='https://tahoe-lafs.org/',
      license='GNU GPL', # see README.rst -- there is an alternative licence
      cmdclass={"trial": Trial,
                "make_executable": MakeExecutable,
                "update_version": UpdateVersion,
                "sdist": MySdist,
                },
      package_dir = {'':'src'},
      packages=['allmydata',
                'allmydata.frontends',
                'allmydata.immutable',
                'allmydata.immutable.downloader',
                'allmydata.introducer',
                'allmydata.mutable',
                'allmydata.scripts',
                'allmydata.storage',
                'allmydata.test',
                'allmydata.util',
                'allmydata.web',
                'allmydata.windows',
                'buildtest'],
      classifiers=trove_classifiers,
      test_suite="allmydata.test",
      install_requires=install_requires,
      tests_require=tests_require,
      package_data={"allmydata.web": ["*.xhtml",
                                      "static/*.js", "static/*.png", "static/*.css",
                                      "static/img/*.png",
                                      "static/css/*.css",
                                      ]
                    },
      setup_requires=setup_requires,
      entry_points = { 'console_scripts': [ 'tahoe = allmydata.scripts.runner:run' ] },
      zip_safe=False, # We prefer unzipped for easier access.
      **setup_args
      )
