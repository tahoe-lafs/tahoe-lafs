#! /usr/bin/env python
# -*- coding: utf-8 -*-
import sys

# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2006-2012 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

import os, subprocess, re

basedir = os.path.dirname(os.path.abspath(__file__))

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

# Tahoe's dependencies are managed by the find_links= entry in setup.cfg and
# the _auto_deps.install_requires list, which is used in the call to setup()
# below.
adglobals = {}
auto_deps_fn = "src/allmydata/_auto_deps.py"
if sys.version_info[0] >= 3:
    exec(compile(open(auto_deps_fn, 'rb').read(), auto_deps_fn, "exec"),
         adglobals, adglobals)
else:
    execfile(auto_deps_fn, adglobals)
install_requires = adglobals['install_requires']
setup_requires = adglobals['setup_requires']

if len(sys.argv) > 1 and sys.argv[1] == '--fakedependency':
    del sys.argv[1]
    install_requires += ["fakedependency >= 1.0.0"]

from setuptools import setup
from setuptools import Command
from setuptools.command import install


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


GIT_VERSION_BODY = '''
# This _version.py is generated from git metadata by the tahoe setup.py.

__pkgname__ = "%(pkgname)s"
real_version = "%(version)s"
full_version = "%(full)s"
branch = "%(branch)s"
verstr = "%(normalized)s"
__version__ = verstr
'''

def run_command(args, cwd=None):
    use_shell = sys.platform == "win32"
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, cwd=cwd, shell=use_shell)
    except EnvironmentError as e:  # if this gives a SyntaxError, note that Tahoe-LAFS requires Python 2.7+
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
    stdout = stdout.decode("ascii")
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
    full = stdout.decode("ascii").strip()
    if version.endswith("-dirty"):
        full += "-dirty"
        normalized_version += ".dev0"

    # Thanks to Jistanidiot at <http://stackoverflow.com/questions/6245570/get-current-branch-name>.
    stdout = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=source_dir)
    branch = (stdout or b"unknown").decode("ascii").strip()

    # this returns native strings (bytes on py2, unicode on py3)
    return {"version": version, "normalized": normalized_version,
            "full": full, "branch": branch}

# setup.cfg has an [aliases] section which runs "update_version" before many
# commands (like "build" and "sdist") that need to know our package version
# ahead of time. If you add different commands (or if we forgot some), you
# may need to add it to setup.cfg and configure it to run update_version
# before your command.

class UpdateVersion(Command):
    description = "update _version.py from revision-control metadata"
    user_options = install.install.user_options

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
        # If we change the release tag names, we must change this too
        versions = versions_from_git("tahoe-lafs-")

        # setup.py might be run by either py2 or py3 (when run by tox, which
        # uses py3 on modern debian/ubuntu distros). We want this generated
        # file to contain native strings on both (str=bytes in py2,
        # str=unicode in py3)
        if versions:
            body = GIT_VERSION_BODY % {
                "pkgname": self.distribution.get_name(),
                "version": versions["version"],
                "normalized": versions["normalized"],
                "full": versions["full"],
                "branch": versions["branch"],
                }
            f = open(VERSION_PY_FILENAME, "wb")
            f.write(body.encode("ascii"))
            f.close()
            print("Wrote normalized version %r into '%s'" % (versions["normalized"], VERSION_PY_FILENAME))

        return versions.get("normalized", None)

class PleaseUseTox(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass

    def run(self):
        print("ERROR: Please use 'tox' to run the test suite.")
        sys.exit(1)

setup_args = {}
if version:
    setup_args["version"] = version

setup(name="tahoe-lafs", # also set in __init__.py
      description='secure, decentralized, fault-tolerant file store',
      long_description=open('README.rst', 'rU').read(),
      author='the Tahoe-LAFS project',
      author_email='tahoe-dev@tahoe-lafs.org',
      url='https://tahoe-lafs.org/',
      license='GNU GPL', # see README.rst -- there is an alternative licence
      cmdclass={"update_version": UpdateVersion,
                "test": PleaseUseTox,
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
                'allmydata.test.mutable',
                'allmydata.test.cli',
                'allmydata.util',
                'allmydata.web',
                'allmydata.windows',
                ],
      classifiers=trove_classifiers,
      install_requires=install_requires,
      extras_require={
          "test": [
              "pyflakes",
              "coverage",
              "mock",
              "tox",
              "foolscap[tor] >= 0.12.5",
              "txtorcon >= 0.17.0", # in case pip's resolver doesn't work
              "foolscap[i2p]",
              "txi2p >= 0.3.1", # in case pip's resolver doesn't work
              "pytest",
              "pytest-twisted",
          ],
          "tor": [
              "foolscap[tor] >= 0.12.5",
              "txtorcon >= 0.17.0", # in case pip's resolver doesn't work
          ],
          "i2p": [
              "foolscap[i2p]",
              "txi2p >= 0.3.1", # in case pip's resolver doesn't work
          ],
      },
      package_data={"allmydata.web": ["*.xhtml",
                                      "static/*.js", "static/*.png", "static/*.css",
                                      "static/img/*.png",
                                      "static/css/*.css",
                                      ]
                    },
      setup_requires=setup_requires,
      entry_points = { 'console_scripts': [ 'tahoe = allmydata.scripts.runner:run' ] },
      **setup_args
      )
