=========================
Debian and Ubuntu Support
=========================

1.  `Overview`_
2.  `Dependency Packages`_


Overview
========

Tahoe-LAFS is provided as a ``.deb`` package in current Debian (>= wheezy)
and Ubuntu (>= lucid) releases. Before official packages were added, the Tahoe
source tree provided support for building unofficial packages for a variety
of popular Debian/Ubuntu versions. The project also ran buildbots to create
``.debs`` of current trunk for ease of testing.

As of version 1.9, the source tree no longer provides these tools. To
construct a ``.deb`` from current trunk, your best bet is to apply the current
Debian diff from the latest upstream package and invoke the ``debian/rules``
as usual. Debian's standard ``apt-get`` tool can be used to fetch the current
source package (including the Debian-specific diff): run
"``apt-get source tahoe-lafs``". That will fetch three files: the ``.dsc``
control file, the main Tahoe tarball, and the Debian-specific
``.debian.tar.gz`` file. Just unpack the ``.debian.tar.gz`` file inside
your Tahoe source tree, modify the version number in ``debian/changelog``,
then run "``fakeroot ./debian/rules binary``", and a new ``.deb`` will be
placed in the parent directory.


Dependency Packages
===================

Tahoe depends upon a number of additional libraries. When building Tahoe from
source, any dependencies that are not already present in the environment will
be downloaded (via ``easy_install``) and stored in the ``support/lib``
directory.

The ``.deb`` packages, of course, rely solely upon other ``.deb`` packages.
For reference, here is a list of the debian package names that provide Tahoe's
dependencies as of the 1.9 release:

* python
* python-zfec
* python-pycryptopp
* python-foolscap
* python-openssl (needed by foolscap)
* python-twisted
* python-nevow
* python-mock
* python-simplejson
* python-setuptools
* python-support (for Debian-specific install-time tools)

When building your own Debian packages, a convenient way to get all these
dependencies installed is to first install the official "tahoe-lafs" package,
then uninstall it, leaving the dependencies behind. You may also find it
useful to run "``apt-get build-dep tahoe-lafs``" to make sure all the usual
build-essential tools are installed.
