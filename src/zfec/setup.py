#!/usr/bin/env python

# zfec -- fast forward error correction library with Python interface
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# 
# This file is part of zfec.
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

from ez_setup import use_setuptools
import sys
if 'cygwin' in sys.platform.lower():
    min_version='0.6c6'
else:
    min_version='0.6a9'
use_setuptools(min_version=min_version)

from setuptools import Extension, find_packages, setup

DEBUGMODE=False
# DEBUGMODE=True

extra_compile_args=[]
extra_link_args=[]

extra_compile_args.append("-std=c99")

undef_macros=[]

if DEBUGMODE:
    extra_compile_args.append("-O0")
    extra_compile_args.append("-g")
    extra_compile_args.append("-Wall")
    extra_link_args.append("-g")
    undef_macros.append('NDEBUG')

trove_classifiers=[
    "Development Status :: 5 - Production/Stable",
    "Environment :: Console",
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

setup(name='zfec',
      version='1.0.0',
      description='a fast erasure code with command-line, C, and Python interfaces',
      long_description='Fast, portable, programmable erasure coding a.k.a. "forward error correction": the generation of redundant blocks of information such that if some blocks are lost then the original data can be recovered from the remaining blocks.',
      author='Zooko O\'Whielacronx',
      author_email='zooko@zooko.com',
      url='http://allmydata.org/source/zfec',
      license='GNU GPL',
      packages=find_packages(),
      classifiers=trove_classifiers,
      entry_points = { 'console_scripts': [ 'zfec = zfec.cmdline_zfec:main', 'zunfec = zfec.cmdline_zunfec:main' ] },
      ext_modules=[Extension('_fec', ['zfec/fec.c', 'zfec/_fecmodule.c',], extra_link_args=extra_link_args, extra_compile_args=extra_compile_args, undef_macros=undef_macros),],
      test_suite="zfec.test",
      )
