
What Does It Do
---------------

Create _version.py, based upon the latest darcs release tag.

If your source tree is coming from darcs (i.e. it is in a darcs repository),
this tool will determine the most recent release tag, count the patches that
have been applied since then, and compute a version number to be written into
_version.py . This version number will be available by doing:

 from your_package_name import __version__

Source trees that do not come from darcs (e.g. release tarballs, nightly
tarballs) and are not within a darcs repository should instead, come with a
_version.py that was generated before the tarball was produced. In this case,
this tool will quietly exit without modifying the existing _version.py .

'release tags' are tags in the source repository that match the following
regexp:

 ^your_package_name-\d+\.\d+(\.\d+)?((a|b|c)(\d+)?)?\w*$


Installation
------------

With easy_install:

  easy_install darcsver

Alternative manual installation:

  tar -zxvf darcsver-X.Y.Z.tar.gz
  cd darcsver-X.Y.Z
  python setup.py install

Where X.Y.Z is a version number.

Alternative to make a specific package use darcsver without installing
darcsver into the system:

  Put "setup_requires=['darcsver']" in the call to setup() in the
  package's setup.py file.


Usage
-----

There are two ways to use this: the command-line tool and the
setuptools plugin.

To use the command-line tool, execute it as:

darcsver $PACKAGE_NAME $PATH_TO_VERSION_PY


To use the setuptools plugin (which enables you to write "./setup.py
darcsver" and which cleverly figures out where the _version.py file
ought to go), you must first package your python module with
`setup.py` and use setuptools.

The former is well documented in the distutils manual:

  http://docs.python.org/dist/dist.html

To use setuptools instead of distutils, just edit `setup.py` and
change

  from distutils.core import setup

to

  from setuptools import setup


References
----------

How to distribute Python modules with Distutils:

  http://docs.python.org/dist/dist.html


Setuptools complete manual:

  http://peak.telecommunity.com/DevCenter/setuptools


Thanks to Yannick Gingras for providing the prototype for this
README.txt.
