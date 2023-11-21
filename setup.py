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
from io import open

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

install_requires = [
    # importlib.resources.files and friends are new in Python 3.9.
    "importlib_resources; python_version < '3.9'",

    "zfec >= 1.1.0",

    # zope.interface >= 3.6.0 is required for Twisted >= 12.1.0.
    "zope.interface >= 3.6.0",

    # * foolscap < 0.5.1 had a performance bug which spent O(N**2) CPU for
    #   transferring large mutable files of size N.
    # * foolscap < 0.6 is incompatible with Twisted 10.2.0.
    # * foolscap 0.6.1 quiets a DeprecationWarning.
    # * foolscap < 0.6.3 is incompatible with Twisted 11.1.0 and newer.
    # * foolscap 0.8.0 generates 2048-bit RSA-with-SHA-256 signatures,
    #   rather than 1024-bit RSA-with-MD5. This also allows us to work
    #   with a FIPS build of OpenSSL.
    # * foolscap >= 0.12.3 provides tcp/tor/i2p connection handlers we need,
    #   and allocate_tcp_port
    # * foolscap >= 0.12.5 has ConnectionInfo and ReconnectionInfo
    # * foolscap >= 0.12.6 has an i2p.sam_endpoint() that takes kwargs
    # * foolscap 0.13.2 drops i2p support completely
    # * foolscap >= 21.7 is necessary for Python 3 with i2p support.
    # * foolscap >= 23.3 is necessary for Python 3.11.
    "foolscap >= 21.7.0",
    "foolscap >= 23.3.0; python_version > '3.10'",

    # * cryptography 2.6 introduced some ed25519 APIs we rely on.  Note that
    #   Twisted[conch] also depends on cryptography and Twisted[tls]
    #   transitively depends on cryptography.  So it's anyone's guess what
    #   version of cryptography will *really* be installed.
    "cryptography >= 2.6",

    # * Used for custom HTTPS validation
    "pyOpenSSL >= 23.2.0",

    # * The SFTP frontend depends on Twisted 11.0.0 to fix the SSH server
    #   rekeying bug <https://twistedmatrix.com/trac/ticket/4395>
    # * The SFTP frontend and manhole depend on the conch extra. However, we
    #   can't explicitly declare that without an undesirable dependency on gmpy,
    #   as explained in ticket #2740.
    # * Due to a setuptools bug, we need to declare a dependency on the tls
    #   extra even though we only depend on it via foolscap.
    # * Twisted >= 15.1.0 is the first version that provided the [tls] extra.
    # * Twisted-16.1.0 fixes https://twistedmatrix.com/trac/ticket/8223,
    #   which otherwise causes test_system to fail (DirtyReactorError, due to
    #   leftover timers)
    # * Twisted-16.4.0 introduces `python -m twisted.trial` which is needed
    #   for coverage testing
    # * Twisted 16.6.0 drops the undesirable gmpy dependency from the conch
    #   extra, letting us use that extra instead of trying to duplicate its
    #   dependencies here.  Twisted[conch] >18.7 introduces a dependency on
    #   bcrypt.  It is nice to avoid that if the user ends up with an older
    #   version of Twisted.  That's hard to express except by using the extra.
    #
    # * Twisted 18.4.0 adds `client` and `host` attributes to `Request` in the
    # * initializer, needed by logic in our custom `Request` subclass.
    #
    #   In a perfect world, Twisted[conch] would be a dependency of an "sftp"
    #   extra.  However, pip fails to resolve the dependencies all
    #   dependencies when asked for Twisted[tls] *and* Twisted[conch].
    #   Specifically, "Twisted[conch]" (as the later requirement) is ignored.
    #   If there were an Tahoe-LAFS sftp extra that dependended on
    #   Twisted[conch] and install_requires only included Twisted[tls] then
    #   `pip install tahoe-lafs[sftp]` would not install requirements
    #   specified by Twisted[conch].  Since this would be the *whole point* of
    #   an sftp extra in Tahoe-LAFS, there is no point in having one.
    # * Twisted 19.10 introduces Site.getContentFile which we use to get
    #   temporary upload files placed into a per-node temporary directory.
    # * Twisted 22.8.0 added support for coroutine-returning functions in many
    #   places (mainly via `maybeDeferred`)
    "Twisted[tls,conch] >= 22.8.0",

    "PyYAML >= 3.11",

    "six >= 1.10.0",

    # for 'tahoe invite' and 'tahoe join'
    "magic-wormhole >= 0.10.2",

    # We want a new enough version to support custom JSON encoders.
    "eliot >= 1.14.0",

    "pyrsistent",

    # A great way to define types of values.
    "attrs >= 20.1.0",

    # WebSocket library for twisted and asyncio
    "autobahn >= 22.4.3",

    # Support for Python 3 transition
    "future >= 0.18.2",

    # Discover local network configuration
    "netifaces",

    # Utility code:
    "pyutil >= 3.3.0",

    # Linux distribution detection:
    "distro >= 1.4.0",

    # For the RangeMap datastructure. Need 2.0.2 at least for bugfixes.
    "collections-extended >= 2.0.2",

    # HTTP server and client
    # Latest version is necessary to work with latest werkzeug:
    "klein >= 23.5.0",
    # 2.2.0 has a bug: https://github.com/pallets/werkzeug/issues/2465
    "werkzeug != 2.2.0",
    "treq",
    "cbor2",

    # 0.4 adds the ability to pass in mmap() values which greatly reduces the
    # amount of copying involved.
    "pycddl >= 0.4",

    # Command-line parsing
    "click >= 8.1.1",

    # for pid-file support
    "psutil",
    "filelock",
]

tor_requires = [
    # 23.5 added support for custom TLS contexts in web_agent(), which is
    # needed for the HTTP storage client to run over Tor.
    "txtorcon >= 23.5.0",
]

i2p_requires = [
    # txi2p has Python 3 support in master branch, but it has not been
    # released -- see https://github.com/str4d/txi2p/issues/10.  We
    # could use a fork for Python 3 until txi2p's maintainers are back
    # in action.  For Python 2, we could continue using the txi2p
    # version about which no one has complained to us so far.
    "txi2p; python_version < '3.0'",
    "txi2p-tahoe >= 0.3.5; python_version > '3.0'",
]

if len(sys.argv) > 1 and sys.argv[1] == '--fakedependency':
    del sys.argv[1]
    install_requires += ["fakedependency >= 1.0.0"]

from setuptools import find_packages, setup
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
    "Programming Language :: Python :: 3",
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
    except EnvironmentError as e:  # if this gives a SyntaxError, note that Tahoe-LAFS requires Python 3.8+
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
      long_description=open('README.rst', 'r', encoding='utf-8').read(),
      author='the Tahoe-LAFS project',
      author_email='tahoe-dev@lists.tahoe-lafs.org',
      url='https://tahoe-lafs.org/',
      license='GNU GPL', # see README.rst -- there is an alternative licence
      cmdclass={"update_version": UpdateVersion,
                "test": PleaseUseTox,
                },
      package_dir = {'':'src'},
      packages=find_packages('src') + ['allmydata.test.plugins'],
      classifiers=trove_classifiers,
      # We support Python 3.8 or later, 3.13 is untested for now
      python_requires=">=3.8, <3.13",
      install_requires=install_requires,
      extras_require={
          # Duplicate the Twisted pywin32 dependency here.  See
          # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2392 for some
          # discussion.
          ':sys_platform=="win32"': ["pywin32 != 226"],
          "build": [
              "dulwich",
              "gpg",
          ],

          # Here are the dependencies required to set up a reproducible test
          # environment.  This could be for CI or local development.  These
          # are *not* library dependencies of the test suite itself.  They are
          # the tools we use to run the test suite at all.
          "testenv": [
              # Pin all of these versions for the same reason you ever want to
              # pin anything: to prevent new releases with regressions from
              # introducing spurious failures into CI runs for whatever
              # development work is happening at the time.  The versions
              # selected here are just the current versions at the time.
              # Bumping them to keep up with future releases is fine as long
              # as those releases are known to actually work.
              "pip==23.3.1",
              "wheel==0.41.3",
              "subunitreporter==23.8.0",
              "python-subunit==1.4.2",
              "junitxml==0.7",
              "coverage==7.2.5",
          ],

          # Here are the library dependencies of the test suite.
          "test": [
              "mock",
              "pytest",
              "pytest-twisted",
              "hypothesis >= 3.6.1",
              "towncrier",
              "testtools",
              "fixtures",
              "beautifulsoup4",
              "html5lib",
              # Pin old version until
              # https://github.com/paramiko/paramiko/issues/1961 is fixed.
              "paramiko < 2.9",
              "pytest-timeout",
              # Does our OpenMetrics endpoint adhere to the spec:
              "prometheus-client == 0.11.0",
          ] + tor_requires + i2p_requires,
          "tor": tor_requires,
          "i2p": i2p_requires,
      },
      package_data={"allmydata.web": ["*.xhtml",
                                      "static/*.js", "static/*.png", "static/*.css",
                                      "static/img/*.png",
                                      "static/css/*.css",
                                      ],
                    "allmydata": ["ported-modules.txt"],
                    },
      include_package_data=True,
      entry_points={
          'console_scripts': [
              'tahoe = allmydata.scripts.runner:run',
              'grid-manager = allmydata.cli.grid_manager:grid_manager',
          ]
      },
      **setup_args
      )
