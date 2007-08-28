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
# delay the fulfillment of this obligation for up to 12 months.  See the file
# COPYING for details.
#
# If you would like to inquire about a commercial relationship with Allmydata,
# Inc., please contact partnerships@allmydata.com and visit
# http://allmydata.com/.

import re, os.path
from distutils.core import Extension, setup

trove_classifiers=[
    "Development Status :: 3 - Alpha", 
    "Environment :: Console",
    "Environment :: Web Environment",
    "License :: OSI Approved :: GNU General Public License (GPL)", 
    "License :: DFSG approved",
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


VERSIONFILE = "src/allmydata/version.py"
verstr = "unknown"
if os.path.exists(VERSIONFILE):
    VSRE = re.compile("^verstr = ['\"]([^'\"]*)['\"]", re.M)
    verstrline = open(VERSIONFILE, "rt").read()
    mo = VSRE.search(verstrline)
    if mo:
        verstr = mo.group(1)
    else:
        print "unable to find version in version.py"
        raise RuntimeError("if version.py exists, it must be well-formed")


LONG_DESCRIPTION=\
"""Welcome to the AllMyData "tahoe" project. This project implements a
secure, distributed, fault-tolerant storage grid.

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
      license='GNU GPL',
      packages=["allmydata", "allmydata.test", "allmydata.util",
                "allmydata.scripts",
                "allmydata.Crypto", "allmydata.Crypto.Cipher",
                "allmydata.Crypto.Hash", "allmydata.Crypto.Util",
                #"allmydata.Crypto.PublicKey",
                ],
      package_dir={ "allmydata": "src/allmydata",},
      scripts = ["bin/allmydata-tahoe"],
      package_data={ 'allmydata': ['web/*.xhtml', 'web/*.html', 'web/*.css',
                                   'owner.sql'] },
      classifiers=trove_classifiers,
      test_suite="allmydata.test",
      ext_modules=[
          Extension("allmydata.Crypto.Cipher.AES",
                    include_dirs=["src/allmydata/Crypto"],
                    sources=["src/allmydata/Crypto/AES.c"]),
          Extension("allmydata.Crypto.Hash.SHA256",
                    include_dirs=["src/allmydata/Crypto"],
                    sources=["src/allmydata/Crypto/SHA256.c"]),
          # _fastmath requires gmp. Since we're not using rsa yet, hold off
          # on requiring this. (note that RSA.py doesn't require _fastmath,
          # but I doubt we'd want to use the pure-python version).
#          Extension("allmydata.Crypto.PublicKey._fastmath",
#                    sources=["src/allmydata/Crypto/_fastmath.c"]),
          ],
      )
