
setuptools_darcs Manual
=======================

About
-----

This is a plugin for setuptools that integrates darcs.  Once
installed, Setuptools can be told to include in a package distribution
all the files tracked by darcs.  This is an alternative to explicit
inclusion specifications with `MANIFEST.in`.

A distribution here refers to a package that you create using
setup.py, ex:

  python setup.py sdist
  python setup.py bdist_egg
  python setup.py bdist_rpm

This package was formerly known as setuptools_darcs_plugin.  The name
change is the result of an agreement by the setuptools plugin
developers to provide a uniform naming convention.


Installation
------------

With easy_install:

  easy_install setuptools_darcs

Alternative manual installation:

  tar -zxvf setuptools_darcs-X.Y.Z.tar.gz
  cd setuptools_darcs-X.Y.Z
  python setup.py install

Where X.Y.Z is a version number.

Alternative to make a specific package use setuptools_darcs without
installing setuptools_darcs into the system:

  Put "setup_requires=['setuptools_darcs']" in the call to setup() in
  the package's setup.py file.


Usage
-----

To use this plugin, you must first package your python module with
`setup.py` and use setuptools.  The former is well documented in the
distutils manual:

  http://docs.python.org/dist/dist.html

To use setuptools instead of distutils, just edit `setup.py` and
change

  from distutils.core import setup

to

  from setuptools import setup

When setuptools builds a source package, it always includes all files
tracked by your revision control system, if it knows how to learn what
those files are.

When setuptools builds a binary package, you can ask it to include all
files tracked by your revision control system, by adding this argument
to your invocation of `setup()`:

  setup(...,
    include_package_data=True,
    ...)

This plugin lets setuptools know what files are tracked by your darcs
revision control tool.  setuptools ships with support for cvs and
subversion.  Other plugins like this one are available for bzr, git,
monotone, and mercurial, at least.

It might happen that you track files with your revision control system
that you don't want to include in your packages.  In that case, you
can prevent setuptools from packaging those files with a directive in
your `MANIFEST.in`, ex:

  exclude .darcs-boringfile
  recursive-exclude images *.xcf *.blend

In this example, we prevent setuptools from packaging
`.darcs-boringfile` and the Gimp and Blender source files found under
the `images` directory.

Alternatively, files to exclude from the package can be listed in the
`setup()` directive:

  setup(...,
    exclude_package_data = {'': ['.darcs-boringfile'],
    			    'images': ['*.xcf', '*.blend']},
    ...)


Gotchas
-------

If someone clones your darcs repository using darcs but does not
install this plugin, then when they run a package building command
they will not get all the right files.  On the other hand if someone
gets a source distribution that was created by "./setup.py sdist",
then it will come with a list of all files, so they will not need
darcs in order to build a distribution themselves.

You can make sure that anyone who uses your setup.py file has this
plugin by adding a `setup_requires` argument.

  setup_requires=[]
  # setuptools_darcs is required to produce complete distributions (such as with
  # "sdist" or "bdist_egg"), unless there is a ${PKG}.egg-info/SOURCES.txt file
  # present which contains a complete list of files that should be included in
  # distributions.
  # http://pypi.python.org/pypi/setuptools_darcs
  setup_requires.append('setuptools_darcs >= 1.1.0')

  setup(...,
    setup_requires = setup_requires,
    ...)


References
----------

How to distribute Python modules with Distutils:

  http://docs.python.org/dist/dist.html


Setuptools complete manual:

  http://peak.telecommunity.com/DevCenter/setuptools


Thanks to Yannick Gingras for providing the prototype for this
README.txt.
