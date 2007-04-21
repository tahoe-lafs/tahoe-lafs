#!/usr/bin/env python

# zfec -- a fast C implementation of Reed-Solomon erasure coding with
# command-line, C, and Python interfaces
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# mailto:zooko@zooko.com
# 
# This file is part of zfec.
# 
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.  This package also comes with the added permission that,
# in the case that you are obligated to release a derived work under this
# licence (as per section 2.b of the GPL), you may delay the fulfillment of
# this obligation for up to 12 months.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

from setuptools import Extension, setup

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
    "Development Status :: 4 - Beta", 
    "Environment :: No Input/Output (Daemon)", 
    "Intended Audience :: Developers", 
    "License :: OSI Approved :: GNU General Public License (GPL)", 
    "Natural Language :: English", 
    "Operating System :: OS Independent", 
    "Programming Language :: C", 
    "Programming Language :: Python", 
    "Topic :: System :: Archiving :: Backup", 
    ]

setup(name='zfec',
      version='1.0.0a3',
      summary='a fast erasure code with command-line, C, and Python interfaces',
      description='Fast, portable, programmable erasure coding a.k.a. "forward error correction": the generation of redundant blocks of information such that if some blocks are lost then the original data can be recovered from the remaining blocks.',
      author='Zooko O\'Whielacronx',
      author_email='zooko@zooko.com',
      url='http://allmydata.com/source/zfec',
      license='GNU GPL',
      platform='Any',
      packages=['zfec', 'zfec.cmdline', 'zfec.util', 'zfec.test'],
      classifiers=trove_classifiers,
      entry_points = { 'console_scripts': [ 'zfec = zfec.cmdline.zfec:main', 'zunfec = zfec.cmdline.zunfec:main' ] },
      ext_modules=[Extension('_fec', ['zfec/fec.c', 'zfec/_fecmodule.c',], extra_link_args=extra_link_args, extra_compile_args=extra_compile_args, undef_macros=undef_macros),],
      )
