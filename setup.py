#! /usr/bin/env python

# Allmydata Tahoe -- secure, distributed storage grid
#
# Copyright (C) 2008 Allmydata, Inc.
#
# This file is part of tahoe.
#
# See the docs/about.html file for licensing information.

import os, re, sys

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

import _auto_deps

setup(name='allmydata-tahoe',
      version=verstr,
      description='secure, decentralized, fault-tolerant filesystem',
      long_description=LONG_DESCRIPTION,
      author='the allmydata.org Tahoe project',
      author_email='tahoe-dev@allmydata.org',
      url='http://allmydata.org/',
      license='GNU GPL',
      cmdclass={'sdist': MySdist},
      package_dir = {'':'src'},
      packages=find_packages("src"),
      classifiers=trove_classifiers,
      test_suite="allmydata.test",
      install_requires=_auto_deps.install_requires,
      include_package_data=True,
      setup_requires=setup_requires,
      dependency_links=dependency_links,
      entry_points = { 'console_scripts': [ 'tahoe = allmydata.scripts.runner:run' ] },
      zip_safe=False, # We prefer unzipped for easier access.
      )
