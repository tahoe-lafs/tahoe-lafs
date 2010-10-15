==============
Debian Support
==============

1.  `Overview`_
2.  `TL;DR supporting package building instructions`_
3.  `TL;DR package building instructions for Tahoe`_
4.  `Building Debian Packages`_
5.  `Using Pre-Built Debian Packages`_
6.  `Building From Source on Debian Systems`_

Overview
========

One convenient way to install Tahoe-LAFS is with debian packages.
This document attempts to explain how to complete a desert island build for
people in a hurry. It also attempts to explain more about our Debian packaging
for those willing to read beyond the simple pragmatic packaging exercises.

TL;DR supporting package building instructions
==============================================

There are only four supporting packages that are currently not available from
the debian apt repositories in Debian Lenny::

    python-foolscap python-zfec argparse zbase32

First, we'll install some common packages for development::

    sudo apt-get install -y build-essential debhelper cdbs python-central \
                    python-setuptools python python-dev python-twisted-core \
                    fakeroot darcs python-twisted python-nevow \
                    python-simplejson  python-pycryptopp devscripts \
                    apt-file
    sudo apt-file update


To create packages for Lenny, we'll also install stdeb::  

    sudo apt-get install python-all-dev
    STDEB_VERSION="0.5.1"
    wget http://pypi.python.org/packages/source/s/stdeb/stdeb-$STDEB_VERSION.tar.gz
    tar xzf stdeb-$STDEB_VERSION.tar.gz
    cd stdeb-$STDEB_VERSION
    python setup.py --command-packages=stdeb.command bdist_deb
    sudo dpkg -i deb_dist/python-stdeb_$STDEB_VERSION-1_all.deb

Now we're ready to build and install the zfec Debian package::

    darcs get http://allmydata.org/source/zfec/trunk zfac
    cd zfac/zfec/
    python setup.py sdist_dsc
    cd `find deb_dist -mindepth 1 -maxdepth 1 -type d` && \
    dpkg-buildpackage -rfakeroot -uc -us
    sudo dpkg -i ../python-zfec_1.4.6-r333-1_amd64.deb

We need to build a pyutil package::

    wget http://pypi.python.org/packages/source/p/pyutil/pyutil-1.6.1.tar.gz
    tar -xvzf pyutil-1.6.1.tar.gz 
    cd pyutil-1.6.1/
    python setup.py --command-packages=stdeb.command sdist_dsc
    cd deb_dist/pyutil-1.6.1/
    dpkg-buildpackage -rfakeroot -uc -us
    sudo dpkg -i ../python-pyutil_1.6.1-1_all.deb

We also need to install argparse and zbase32::

    sudo easy_install argparse # argparse won't install with stdeb (!) :-(
    sudo easy_install zbase32 # XXX TODO: package with stdeb

Finally, we'll fetch, unpack, build and install foolscap::

    # You may not already have Brian's key:
    # gpg --recv-key 0x1514A7BD
    wget http://foolscap.lothar.com/releases/foolscap-0.5.0.tar.gz.asc
    wget http://foolscap.lothar.com/releases/foolscap-0.5.0.tar.gz
    gpg --verify foolscap-0.5.0.tar.gz.asc
    tar -xvzf foolscap-0.5.0.tar.gz 
    cd foolscap-0.5.0/
    python setup.py --command-packages=stdeb.command sdist_dsc
    cd deb_dist/foolscap-0.5.0/
    dpkg-buildpackage -rfakeroot -uc -us
    sudo dpkg -i ../python-foolscap_0.5.0-1_all.deb 

TL;DR package building instructions for Tahoe
=============================================

If you want to build your own Debian packages from the darcs tree or from 
a source release, do the following::

    cd ~/
    mkdir src && cd src/
    darcs get --lazy http://allmydata.org/source/tahoe-lafs/trunk tahoe-lafs
    cd tahoe-lafs
    # set this for your Debian release name (lenny, sid, etc)
    make deb-lenny-head
    # You must have your dependency issues worked out by hand for this to work
    sudo dpkg -i ../allmydata-tahoe_1.6.1-r4262_all.deb

You should now have a functional desert island build of Tahoe with all of the
supported libraries as .deb packages. You'll need to edit the Debian specific
/etc/defaults/allmydata-tahoe file to get Tahoe started. Data is by default
stored in /var/lib/tahoelafsd/ and Tahoe runs as the 'tahoelafsd' user.

Building Debian Packages
========================

The Tahoe source tree comes with limited support for building debian packages
on a variety of Debian and Ubuntu platforms. For each supported platform,
there is a "deb-PLATFORM-head" target in the Makefile that will produce a
debian package from a darcs checkout, using a version number that is derived
from the most recent darcs tag, plus the total number of revisions present in
the tree (e.g. "1.1-r2678").

To create debian packages from a Tahoe tree, you will need some additional
tools installed. The canonical list of these packages is in the
"Build-Depends" clause of misc/sid/debian/control , and includes::

 build-essential
 debhelper
 cdbs
 python-central
 python-setuptools
 python
 python-dev
 python-twisted-core

In addition, to use the "deb-$PLATFORM-head" target, you will also need the
"debchange" utility from the "devscripts" package, and the "fakeroot" package.

Some recent platforms can be handled by using the targets for the previous
release, for example if there is no "deb-hardy-head" target, try building
"deb-gutsy-head" and see if the resulting package will work.

Note that we haven't tried to build source packages (.orig.tar.gz + dsc) yet,
and there are no such source packages in our APT repository.

Using Pre-Built Debian Packages
===============================

The allmydata.org site hosts an APT repository with debian packages that are
built after each checkin. `This wiki page
<http://allmydata.org/trac/tahoe/wiki/DownloadDebianPackages>`_ describes this
repository.

The allmydata.org APT repository also includes debian packages of support
libraries, like Foolscap, zfec, pycryptopp, and everything else you need that
isn't already in debian.

Building From Source on Debian Systems
======================================

Many of Tahoe's build dependencies can be satisfied by first installing
certain debian packages: simplejson is one of these. Some debian/ubuntu
platforms do not provide the necessary .egg-info metadata with their
packages, so the Tahoe build process may not believe they are present. Some
Tahoe dependencies are not present in most debian systems (such as foolscap
and zfec): debs for these are made available in the APT repository described
above.

The Tahoe build process will acquire (via setuptools) most of the libraries
that it needs to run and which are not already present in the build
environment).

We have observed occasional problems with this acquisition process. In some
cases, setuptools will only be half-aware of an installed debian package,
just enough to interfere with the automatic download+build of the dependency.
For example, on some platforms, if Nevow-0.9.26 is installed via a debian
package, setuptools will believe that it must download Nevow anyways, but it
will insist upon downloading that specific 0.9.26 version. Since the current
release of Nevow is 0.9.31, and 0.9.26 is no longer available for download,
this will fail.

The Tahoe source tree currently ships with a directory full of tarballs of
dependent libraries (misc/dependencies/), to enable a "desert-island build".
There are plans to remove these tarballs from the source repository (but
still provide a way to get Tahoe source plus dependencies). This Nevow-0.9.26
-type problem can be mitigated by putting the right dependency tarball in
misc/dependencies/ .
