#! /usr/bin/python

from setuptools import setup

setup(name='tahoe-deps',
      version="1",
      install_requires=["zfec >= 1.0.3",
                        "foolscap >= 0.1.6", "simplejson >= 1.4",
                        #"nevow", # we need nevow, but it doesn't seem to be
                                  # installable by easy_install
                        ],
      )
