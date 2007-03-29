#! /usr/bin/env python

from distutils.core import setup
from distutils.core import Extension
from distutils.command.build_ext import build_ext
import os, sys


setup(
    name="AllMyData",
    version="0.0.1",
    packages=["allmydata", "allmydata.test", "allmydata.util",
              "allmydata.filetree", "allmydata.scripts",
              ],
    package_dir={ "allmydata": "src/allmydata",
                  },
    scripts = ["bin/allmydata-tahoe"],
    package_data={ 'allmydata': ['web/*.xhtml'] },

    description="AllMyData (tahoe2)",
    )

