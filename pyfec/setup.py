#!/usr/bin/env python

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
    extra_link_args.append("-g")
    undef_macros.append('NDEBUG')

trove_classifiers="""
XYZ insert trove classifiers here.
"""

setup(name='pyfec',
      versions='0.9',
      summary='Provides a fast C implementation of Reed-Solomon erasure coding with a Python interface.',
      description='Erasure coding is the generation of extra redundant packets of information such that if some packets are lost ("erased") then the original data can be recovered from the remaining packets.  This package contains an optimized implementation along with a Python interface.',
      author='Zooko O\'Whielacronx',
      author_email='zooko@zooko.com',
      url='http://zooko.com/repos/pyfec',
      license='GNU GPL',
      platform='Any',
      packages=['fec'],
      classifiers=trove_classifiers.split("\n"),
      ext_modules=[Extension('fec', ['fec/fec.c', 'fec/fecmodule.c',], extra_link_args=extra_link_args, extra_compile_args=extra_compile_args, undef_macros=undef_macros),],
      )
