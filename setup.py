#! /usr/bin/python

#from setuptools import setup, find_packages
from distutils.core import setup

setup(
    name="AllMyData",
    version="0.0.1",
    #packages=find_packages('.'),
    packages=["allmydata", "allmydata/test", "allmydata/util"],
    package_data={ 'allmydata': ['web/*.xhtml'] },
    description="AllMyData (tahoe2)",
    )

