Building Tahoe-LAFS on Windows-7 (64-bit)
=========================================

This document details the steps to build Tahoe-LAFS on Windows. The instructions
were tried on Windows-7 64-bit. Building on a 32-bit machine shouldn't be too
different.

Download and install Microsoft Visual C++ compiler for Python 2.7
-----------------------------------------------------------------

For reasons detailed in this page <https://docs.python.org/2/extending/windows.html>, we the
same version of VC++that was used to build Python itself. Until recently, this meant, downloading
Microsoft Visual Studio 2008 Express Edition and Windows SDK 3.5. The recent release of the Microsoft
Visual C++ compiler for Python 2.7 made things a lot simpler.

So, the first step is to download and install the C++ compiler from Microsoft from `this link`_.

.. _this link: http://www.microsoft.com/en-us/download/details.aspx?id=44266

Download and install Perl
-------------------------

Download and install ActiveState Perl.

Download and install OpenSSL 1.0.1j
-----------------------------------

*  Untar openssl-1.0.1j
*  Invoke ``"C:\Users\UserName\AppData\Local\Programs\Common\Microsoft\Visual C++ for Python\9.0\vcvarsall.bat" x86_amd64``
*  Go to the untar'ed openssl source base directory and run the following commands.
*  ``perl Configure VC-WIN64A --prefix=c:\dist\openssl64 no-asm enable-tlsext``
*  ``ms\do_win64a.mak``
*  ``nmake -f ms\ntdll.bat``
*  ``nmake -f ms\ntdll.bat install``

Building PyOpenSSL
------------------

*  Setup the build env:

``"C:\Users\UserName\AppData\Local\Programs\Common\Microsoft\Visual C++ for Python\9.0\vcvarsall.bat" x86_amd64``

*  download and untar pyopenssl 0.14
*  Set OpenSSL ``LIB``, ``INCLUDE`` and ``PATH``.

``set PYCA_WINDOWS_LINK_TYPE=dynamic``
``set LIB=c:\dist\openssl64\lib;%LIB%``
``set INCLUDE=c:\dist\openssl64\include;%INCLUDE%``
``set PATH=c:\dist\openssl64\bin;%PATH%``

*  ``python setup.py build``
*  ``python setup.py install``

Building Tahoe-LAFS
-------------------

Now, with all the prerequisites built and installed, we can proceed to build Tahoe-LAFS.

*  Download and install ``git``.
*  Download and install Python 2.7.x.
*  ``git clone https://github.com/tahoe-lafs/tahoe-lafs.git``
*  ``cd tahoe-lafs``
*  ``python setup.py build``
*  Test the build by invoking the ``bin\tahoe`` script.


