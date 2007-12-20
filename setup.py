#! /usr/bin/env python

# Allmydata Tahoe -- secure, distributed storage grid
# 
# Copyright (C) 2007 Allmydata, Inc.
# 
# This file is part of tahoe.
# 
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version, with the added permission that, if you become obligated
# to release a derived work under this licence (as per section 2.b), you may
# delay the fulfillment of this obligation for up to 12 months.  If you are
# obligated to release code under section 2.b of this licence, you are
# obligated to release it under these same terms, including the 12-month grace
# period clause.  See the COPYING file for details.
#
# If you would like to inquire about a commercial relationship with Allmydata,
# Inc., please contact partnerships@allmydata.com and visit
# http://allmydata.com/.

import sys, re, os.path
try:
    from ez_setup import use_setuptools
except ImportError:
    pass
else:
    if 'cygwin' in sys.platform.lower():
        min_version='0.6c6'
    else:
        # foolscap uses a module-level os.urandom() during import, which
        # breaks inside older setuptools' sandboxing. 0.6c4 is the first
        # version which fixed this problem.
        min_version='0.6c4'
    download_base = os.path.join('misc', 'dependencies')+os.path.sep
    use_setuptools(min_version=min_version,
                   download_base=download_base,
                   download_delay=0)

from setuptools import Extension, find_packages, setup

from calcdeps import install_requires, dependency_links

trove_classifiers=[
    "Development Status :: 3 - Alpha", 
    "Environment :: Console",
    "Environment :: Web Environment",
    # "License :: Free Software (GPL variant)", # Not a real acceptable value.  I guess this means we really need to get our licence DFSG/OSI approved.
    # "License :: Open Source (GPL variant)", # Not a real acceptable value.  I guess this means we really need to get our licence DFSG/OSI approved.
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


# Build _version.py before trying to extract a version from it. If we aren't
# running from a darcs checkout, this will leave any pre-existing _version.py
# alone.
try:
    os.system(" ".join([sys.executable,
                       "misc/make-version.py",
                       "allmydata-tahoe",
                       '"src/allmydata/_version.py"', # cygwin vs slashes
                        ]))
except Exception, le:
    pass
VERSIONFILE = "src/allmydata/_version.py"
verstr = "unknown"
if os.path.exists(VERSIONFILE):
    VSRE = re.compile("^verstr = ['\"]([^'\"]*)['\"]", re.M)
    verstrline = open(VERSIONFILE, "rt").read()
    mo = VSRE.search(verstrline)
    if mo:
        verstr = mo.group(1)
    else:
        print "unable to find version in src/allmydata/_version.py"
        raise RuntimeError("if _version.py exists, it must be well-formed")


LONG_DESCRIPTION=\
"""Welcome to the AllMyData "tahoe" project. This project implements a secure,
distributed, fault-tolerant storage grid under a Free Software licence.

The basic idea is that the data in this storage grid is spread over all
participating nodes, using an algorithm that can recover the data even if a
majority of the nodes are no longer available."""


setup(name='allmydata-tahoe',
      version=verstr,
      description='secure, distributed storage grid',
      long_description=LONG_DESCRIPTION,
      author='Allmydata, Inc.',
      author_email='tahoe-dev@allmydata.org',
      url='http://allmydata.org/',
      license='GNU GPL v2 or later, plus transitive 12 month grace period; http://allmydata.org/trac/tahoe/browser/COPYING',
      package_dir = {'':'src'},
      packages=find_packages("src"),
      classifiers=trove_classifiers,
      test_suite="allmydata.test",
      install_requires=install_requires,
      include_package_data=True,
      setup_requires=["setuptools_darcs >= 1.0.5",],
      dependency_links=dependency_links,
      entry_points = { 'console_scripts': [ 'tahoe = allmydata.scripts.runner:run' ] },
      zip_safe=False, # We prefer unzipped for easier access.
      )
