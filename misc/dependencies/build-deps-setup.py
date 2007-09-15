#! /usr/bin/python

from ez_setup import use_setuptools
import sys
if 'cygwin' in sys.platform.lower():
    min_version='0.6c6'
else:
    min_version='0.6a9'
use_setuptools(min_version=min_version, download_base="file:misc/dependencies/")

from setuptools import setup

setup(name='tahoe-deps',
      version="1",
      install_requires=["zfec >= 1.0.3",
                        "foolscap >= 0.1.6", "simplejson >= 1.4",
                        #"nevow", # we need nevow, but it doesn't seem to be
                                  # installable by easy_install
                        ],
      )
