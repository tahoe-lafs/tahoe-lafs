#!/usr/bin/env python

from setuptools import setup
import py2app

import glob
import os
import sys

# pull in formless, as best way to grab its .css file depenedency
import formless

def find_formless_css():
    fpath = formless.__path__[0]
    # first look for it from a regular package install
    f = os.path.join(fpath, 'freeform-default.css')
    if os.path.exists(f):
        return f
    # then try looking within .egg structured files
    pyver = 'python%s.%s' % (sys.version_info[0], sys.version_info[1])
    f = os.path.join(fpath, '../lib', pyver, 'site-packages/formless/freeform-default.css')
    if os.path.exists(f):
        return f
    raise RuntimeError("Can't find formless .css file")

data_files = [
     ('pkg_resources/allmydata/web', glob.glob('../src/allmydata/web/*')),
     ('pkg_resources/formless', [find_formless_css()]),
     ]

from setuptools import find_packages

packages = find_packages('../src')

py2app_options = {
    'argv_emulation': True,
    'iconfile': 'allmydata.icns',
    'plist': { 'CFBundleIconFile': 'allmydata.icns', },
    }

setup_args = {
    'name': 'Allmydata Tahoe',
    'description': 'The various parts of the Allmydata Tahoe system',
    'author': 'Allmydata, Inc.',
    'app': [ 'allmydata_tahoe.py' ],
    'options': { 'py2app': py2app_options },
    'data_files': data_files,
    'setup_requires': [ 'py2app' ],
    'packages': packages,
}


if __name__ == '__main__':
    if not os.path.exists('allmydata'):
        os.symlink('../src/allmydata', 'allmydata')
    setup(**setup_args)

junk = [formless, py2app]
del junk




