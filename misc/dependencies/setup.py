#! /usr/bin/env python

# N.B.: this expects to run from the top of the source tree

import sys, os

miscdeps=os.path.join('misc', 'dependencies')

# Dapper ships with older versions of Twisted (2.2.0) and Nevow (0.6.0), and
# (unlike newer distributions) they are not installed with .egg meta-data
# directories. As a result, they are invisible to setuptools. When the
# 'build-deps' target thus builds Nevow, it will fail unless Twisted-2.4.0 or
# newer is available, so Dapper users must install a newer Twisted before
# running 'make build-deps'. In addition, through some not-yet-understood
# quirk of setuptools, if that newer Twisted is in /usr/local/lib , somehow
# the build still manages to pick up the old version from /usr/lib . It turns
# out that importing twisted now, before use_setuptools() is called, causes
# setuptools to stick with the correct (newer) Twisted. This causes an error
# if Twisted is not installed before you run 'make build-deps', but having
# Twisted at this point is a requirement anyways.

import twisted

try:
    from ez_setup import use_setuptools
except ImportError:
    pass
else:
    if 'cygwin' in sys.platform.lower():
        min_version='0.6c6'
    else:
        # foolscap uses a module-level os.urandom() during import, which
        # breaks inside older setuptools' sandboxing. 0.6c4 is the first
        # version which fixed this problem.
        min_version='0.6c4'
    download_base = "file:"+os.path.join('misc', 'dependencies')+os.path.sep
    use_setuptools(min_version=min_version,
                   download_base=download_base,
                   download_delay=0, to_dir=miscdeps)

from setuptools import setup

from calcdeps import install_requires, dependency_links

setup(name='tahoe-deps',
      version="1",
      install_requires=install_requires,
      dependency_links=dependency_links,
      zip_safe=False
      )
