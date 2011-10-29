======================
Welcome to Tahoe-LAFS!
======================

Welcome to `Tahoe-LAFS <https://tahoe-lafs.org>`_, the first decentralized
storage system with *provider-independent security*.

What is "provider-independent security"?
========================================

Every seller of cloud storage services will tell you that their service is
"secure".  But what they mean by that is something fundamentally different
from what we mean.  What they mean by "secure" is that after you've given
them the power to read and modify your data, they try really hard not to let
this power be abused.  This turns out to be difficult!  Bugs,
misconfigurations, or operator error can accidentally expose your data to
another customer or to the public, or can corrupt your data.  Criminals
routinely gain illicit access to corporate servers.  Even more insidious is
the fact that the employees themselves sometimes violate customer privacy out
of carelessness, avarice, or mere curiousity.  The most conscientious of
these service providers spend considerable effort and expense trying to
mitigate these risks.

What we mean by "security" is something different.  *The service provider
never has the ability to read or modify your data in the first place --
never.* If you use Tahoe-LAFS, then all of the threats described above are
non-issues to you.  Not only is it easy and inexpensive for the service
provider to maintain the security of your data, but in fact they couldn't
violate its security if they tried.  This is what we call
*provider-independent security*.

This guarantee is integrated naturally into the Tahoe-LAFS storage system and
doesn't require you to perform a manual pre-encryption step or cumbersome key
management.  (After all, having to do cumbersome manual operations when
storing or accessing your data would nullify one of the primary benefits of
using cloud storage in the first place -- convenience.)

Here's how it works:

.. image:: https://tahoe-lafs.org/~zooko/network-and-reliance-topology.png

A "storage grid" is made up of a number of storage servers.  A storage server
has direct attached storage (typically one or more hard disks).  A "gateway"
uses the storage servers and provides access to the filesystem over HTTP(S)
or (S)FTP.

Users do not rely on storage servers to provide *confidentiality* nor
*integrity* for their data -- instead all of the data is encrypted and
integrity-checked by the gateway, so that the servers can neither read nor
modify the contents of the files.

Users do rely on storage servers for *availability*.  The ciphertext is
erasure-coded into ``N`` shares distributed across at least ``H`` distinct
storage servers (the default value for ``N`` is 10 and for ``H`` is 7) so
that it can be recovered from any ``K`` of these servers (the default
value of ``K`` is 3).  Therefore only the failure of ``H-K+1`` (with the
defaults, 5) servers can make the data unavailable.

In the typical deployment mode each user runs her own gateway on her own
machine.  This way she relies on her own machine for the confidentiality and
integrity of the data.

An alternate deployment mode is that the gateway runs on a remote machine and
the user connects to it over HTTPS or SFTP.  This means that the operator of
the gateway can view and modify the user's data (the user *relies on* the
gateway for confidentiality and integrity), but the advantage is that the
user can access the filesystem with a client that doesn't have the gateway
software installed, such as an Internet kiosk or cell phone.

Access Control
==============

There are two kinds of files: immutable and mutable.  Immutable files have
the property that once they have been uploaded to the storage grid they can't
be modified.  Mutable ones can be modified.  A user can have read-write
access to a mutable file or read-only access to it (or no access to it at
all).

A user who has read-write access to a mutable file or directory can give
another user read-write access to that file or directory, or they can give
read-only access to that file or directory.  A user who has read-only access
to a file or directory can give another user read-only access to it.

When linking a file or directory into a parent directory, you can use a
read-write link or a read-only link.  If you use a read-write link, then
anyone who has read-write access to the parent directory can gain read-write
access to the child, and anyone who has read-only access to the parent
directory can gain read-only access to the child.  If you use a read-only
link, then anyone who has either read-write or read-only access to the parent
directory can gain read-only access to the child.

For more technical detail, please see the `the doc page
<https://tahoe-lafs.org/trac/tahoe-lafs/wiki/Doc>`_ on the Wiki.

Get Started
===========

To use Tahoe-LAFS, please see `quickstart.rst <quickstart.rst>`_.

License
=======

You may use this package under the GNU General Public License, version 2 or,
at your option, any later version.  See the file `COPYING.GPL
<../COPYING.GPL>`_ for the terms of the GNU General Public License, version
2.

You may use this package under the Transitive Grace Period Public Licence,
version 1 or, at your option, any later version.  The Transitive Grace Period
Public Licence has requirements similar to the GPL except that it allows you
to wait for up to twelve months after you redistribute a derived work before
releasing the source code of your derived work. See the file `COPYING.TGGPL
<../COPYING.TGPPL.rst>`_ for the terms of the Transitive Grace Period Public
Licence, version 1.

(You may choose to use this package under the terms of either licence, at
your option.)
