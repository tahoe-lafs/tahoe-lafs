#!/usr/bin/env python

# zfec -- fast forward error correction library with Python interface
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# mailto:zooko@zooko.com
# 
# This file is part of zfec.
# 
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

from distutils.core import Extension, setup


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
      version='1.0.0a1',
      summary='Provides a fast C implementation of Reed-Solomon erasure coding with a Python interface.',
      description='Erasure coding is the generation of redundant blocks of information such that if some blocks are lost ("erased") then the original data can be recovered from the remaining blocks.  This package contains an optimized implementation along with a Python interface.',
      author='Zooko O\'Whielacronx',
      author_email='zooko@zooko.com',
      url='http://www.allmydata.com/source/zfec',
      license='GNU GPL',
      platform='Any',
      packages=['fec', 'fec.util', 'fec.test'],
      classifiers=trove_classifiers,
      ext_modules=[Extension('_fec', ['fec/fec.c', 'fec/_fecmodule.c',], extra_link_args=extra_link_args, extra_compile_args=extra_compile_args, undef_macros=undef_macros),],
      )
