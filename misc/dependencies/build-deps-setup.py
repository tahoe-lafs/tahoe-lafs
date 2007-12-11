#! /usr/bin/python

# N.B.: this expects to run from the top of the source tree

import sys
from ez_setup import use_setuptools
if 'cygwin' in sys.platform.lower():
    min_version='0.6c6'
else:
    # foolscap uses a module-level os.urandom() during import, which
    # breaks inside older setuptools' sandboxing. 0.6c4 is the first
    # version which fixed this problem.
    min_version='0.6c4'
use_setuptools(min_version=min_version,
               download_base="file:misc/dependencies/",
               download_delay=0)

from setuptools import setup

from calcdeps import install_requires, dependency_links

setup(name='tahoe-deps',
      version="1",
      install_requires=install_requires,
      dependency_links=dependency_links,
      )
