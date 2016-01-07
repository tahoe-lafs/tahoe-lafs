Building pyOpenSSL on Windows
=============================

This document details the steps to build an pyOpenSSL egg with embedded
OpenSSL library, for use by Tahoe-LAFS on Windows.

The instructions were tried on Windows 7 64-bit and Windows XP 32-bit.
They should work on other versions of Windows, maybe with minor variations.


Download and install Microsoft Visual C++ compiler for Python 2.7
-----------------------------------------------------------------

For reasons detailed in `the Python documentation`_, Python extension modules
need to be built using a compiler compatible with the same version of Visual C++
that was used to build Python itself. Until recently, this meant downloading
Microsoft Visual Studio 2008 Express Edition and Windows SDK 3.5. The recent
release of the Microsoft Visual C++ compiler for Python 2.7 made things a lot
simpler.

So, the first step is to download and install the C++ compiler from Microsoft
from `this link`_.

Find the location where it installed the ``vcvarsall.bat`` file; depending on
the version of Windows it could be either
``"%USERPROFILE%\AppData\Local\Programs\Common\Microsoft\Visual C++ for Python\9.0"``
or ``"%CommonProgramFiles%\Microsoft\Visual C++ for Python\9.0"``, for example.
We'll call this ``%VCDIR%`` below.

.. _the Python documentation: https://docs.python.org/2/extending/windows.html
.. _this link: https://www.microsoft.com/en-us/download/details.aspx?id=44266


Download and install Perl
-------------------------

Download and install ActiveState Perl:

* go to `the ActiveState Perl download page`_.
* identify the correct link and manually change it from http to https.

.. _the ActiveState Perl download page: https://www.activestate.com/activeperl/downloads


Download and install the latest OpenSSL version
-----------------------------------------------

* Download the latest OpenSSL from `the OpenSSL source download page`_ and untar it.
  At the time of writing, the latest version was OpenSSL 1.0.1m.

* Set up the build environment. For 64-bit Windows::

    "%VCDIR%\vcvarsall.bat" amd64

  or for 32-bit Windows::

    "%VCDIR%\vcvarsall.bat" x86

* Go to the untar'ed OpenSSL source base directory. For 64-bit Windows, run::

    mkdir c:\dist
    perl Configure VC-WIN64A --prefix=c:\dist\openssl no-asm enable-tlsext
    ms\do_win64a.bat
    nmake -f ms\ntdll.mak
    nmake -f ms\ntdll.mak install

  or for 32-bit Windows, run::

    mkdir c:\dist
    perl Configure VC-WIN32 --prefix=c:\dist\openssl no-asm enable-tlsext
    ms\do_ms.bat
    nmake -f ms\ntdll.mak
    nmake -f ms\ntdll.mak install


To check that it is working, run ``c:\dist\openssl\bin\openssl version``.

.. _the OpenSSL source download page: https://www.openssl.org/source/


Building PyOpenSSL
------------------

* Download and untar pyOpenSSL 0.13.1 (see `ticket #2221`_ for why we
  currently use this version). The MD5 hash of pyOpenSSL-0.13.1.tar.gz is
  e27a3b76734c39ea03952ca94cc56715.

* Set up the build environment by running ``vcvarsall.bat`` as for building
  OpenSSL above.

* Set OpenSSL ``LIB``, ``INCLUDE`` and ``PATH``::

    set LIB=c:\dist\openssl\lib;%LIB%
    set INCLUDE=c:\dist\openssl\include;%INCLUDE%
    set PATH=c:\dist\openssl\bin;%PATH%

* A workaround is needed to ensure that the setuptools ``bdist_egg`` command
  is available. Edit pyOpenSSL's ``setup.py`` around line 13 as follows::

    < from distutils.core import Extension, setup
    ---
    > from setuptools import setup
    > from distutils.core import Extension

* Run ``python setup.py bdist_egg``

The generated egg will be in the ``dist`` directory. It is a good idea
to check that Tahoe-LAFS is able to use it before uploading the egg to
tahoe-lafs.org. This can be done by putting it in the ``tahoe-deps`` directory
of a Tahoe-LAFS checkout or release, then running ``python setup.py test``.

.. _ticket #2221: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2221
