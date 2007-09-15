#! /usr/bin/python

# N.B.: this expects to run from the top of the source tree

import os, sys
from ez_setup import use_setuptools
if 'cygwin' in sys.platform.lower():
    min_version='0.6c6'
else:
    min_version='0.6a9'
use_setuptools(min_version=min_version, download_base="file:misc/dependencies/")

from setuptools import setup

dependency_tarballs=[ os.path.join("misc", "dependencies", fn)
                      for fn in os.listdir(os.path.join("misc", "dependencies"))
                      if fn.endswith(".tar.gz") ]
dependency_links=["http://allmydata.org/trac/tahoe/wiki/Dependencies"] + dependency_tarballs

setup(name='tahoe-deps',
      version="1",
      install_requires=["zfec >= 1.0.3",
                        "foolscap >= 0.1.6",
                        "simplejson >= 1.4",
                        "nevow",
                        ],
      dependency_links=dependency_links,
      )
