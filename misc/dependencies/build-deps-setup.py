#! /usr/bin/python

# N.B.: this expects to run from the top of the source tree

import sys
from ez_setup import use_setuptools
if 'cygwin' in sys.platform.lower():
    min_version='0.6c6'
else:
    min_version='0.6a9'
use_setuptools(min_version=min_version, download_base="file:misc/dependencies/")

from setuptools import setup

from calcdeps import install_requires, dependency_links

setup(name='tahoe-deps',
      version="1",
      install_requires=install_requires,
      dependency_links=dependency_links,
      )
