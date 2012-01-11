==================
Getting Tahoe-LAFS
==================

Welcome to `the Tahoe-LAFS project <https://tahoe-lafs.org>`_, a secure,
decentralized, fault-tolerant storage system. `About Tahoe-LAFS
<about.rst>`_.

How To Get Tahoe-LAFS
=====================

This procedure has been verified to work on Windows, Mac, OpenSolaris,
and too many flavors of Linux and of BSD to list. It's likely to work
on other platforms.

In Case Of Trouble
------------------

There are a few 3rd party libraries that Tahoe-LAFS depends on that
might not be easy to set up on your platform. If the following
instructions don't Just Work without any further effort on your part,
then please write to `the tahoe-dev mailing list
<https://tahoe-lafs.org/cgi-bin/mailman/listinfo/tahoe-dev>`_ where
friendly hackers will help you out.

Install Python
--------------

Check if you already have an adequate version of Python installed by
running ``python -V``. Python v2.4 (v2.4.4 or greater), Python v2.5,
Python v2.6, or Python v2.7 will work. Python v3 does not work. On
Windows, we recommend the use of Python v2.6 (native, not Cygwin). If
you don't have one of these versions of Python installed, then follow
the instructions on `the Python download page
<http://www.python.org/download/releases/2.6.6/>`_ to download and
install Python v2.6. Make sure that the path to the installation
directory has no spaces in it (e.g. on Windows, do not install Python
in the "Program Files" directory).

Get Tahoe-LAFS
--------------

`Download the latest stable release, v1.9.1
<https://tahoe-lafs.org/source/tahoe-lafs/releases/allmydata-tahoe-1.9.1.zip>`_

Set Up Tahoe-LAFS
-----------------

Unpack the zip file and cd into the top-level directory.

Run ``python setup.py build`` to generate the ``tahoe`` executable in a
subdirectory of the current directory named ``bin``. This will download
and build anything you need from various websites.

On Windows, the ``build`` step might tell you to open a new Command
Prompt (or, on XP and earlier, to log out and back in again). This is
needed the first time you set up Tahoe-LAFS on a particular
installation of Windows.

Optionally run ``python setup.py test`` to verify that it passes all of
its self-tests.

Run ``bin/tahoe --version`` (on Windows, ``bin\tahoe --version``) to
verify that the executable tool prints out the right version number.

Run Tahoe-LAFS
--------------

Now you are ready to deploy a decentralized filesystem. The ``tahoe``
executable in the ``bin`` directory can configure and launch your
Tahoe-LAFS nodes. See `running.rst <running.rst>`_ for instructions on
how to do that.

Advanced Installation
---------------------

For optional features such as tighter integration with your operating
system's package manager, you can see the `AdvancedInstall
<https://tahoe-lafs.org/trac/tahoe-lafs/wiki/AdvancedInstall>`_ wiki page.
The options on that page are not necessary to use Tahoe-LAFS and can be
complicated, so we do not recommend following that page unless you have
unusual requirements for advanced optional features. For most people,
you should first follow the instructions on this page, and if that
doesn't work then ask for help by writing to `the tahoe-dev mailing
list <https://tahoe-lafs.org/cgi-bin/mailman/listinfo/tahoe-dev>`_.

