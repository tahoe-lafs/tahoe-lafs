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
    # On cygwin there was a permissions error that was fixed in 0.6c6.  (Also
    # foolscap uses a module-level os.urandom() during import, which breaks
    # inside older setuptools' sandboxing. 0.6c4 is the first version which
    # fixed this problem.)
    use_setuptools(min_version='0.6c6')

from setuptools import Extension, find_packages, setup

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
    "Development Status :: 4 - Beta", 
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

miscdeps=os.path.join(os.getcwd(), 'misc', 'dependencies')
dependency_links=[os.path.join(miscdeps, t) for t in os.listdir(miscdeps) if t.endswith(".tar")]

# By adding a web page to the dependency_links we are able to put new packages
# up there and have them be automatically discovered by existing copies of the
# tahoe source when that source was built.
dependency_links.append("http://allmydata.org/trac/tahoe/wiki/Dependencies")

setup_requires = []

# darcsver is needed only if you want "./setup.py darcsver" to write a new
# version stamp in src/allmydata/_version.py, with a version number derived from
# darcs history.
# http://pypi.python.org/pypi/darcsver
setup_requires.append('darcsver >= 1.1.1')

# setuptools_darcs is required to produce complete distributions (such as with
# "sdist" or "bdist_egg"), unless there is a PKG-INFO file present which shows
# that this is itself a source distribution.
# http://pypi.python.org/pypi/setuptools_darcs
if not os.path.exists('PKG-INFO'):
    setup_requires.append('setuptools_darcs >= 1.1.0')

import _auto_deps

setup(name='allmydata-tahoe',
      version=verstr,
      description='secure, decentralized, fault-tolerant filesystem',
      long_description=LONG_DESCRIPTION,
      author='Allmydata, Inc.',
      author_email='tahoe-dev@allmydata.org',
      url='http://allmydata.org/',
      license='GNU GPL',
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
