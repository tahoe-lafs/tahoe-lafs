.. -*- coding: utf-8-with-signature -*-

========================
Tahoe-LAFS SFTP Frontend
========================

1.  `SFTP Background`_
2.  `Tahoe-LAFS Support`_
3.  `Creating an Account File`_
4.  `Configuring SFTP Access`_
5.  `Dependencies`_
6.  `Immutable and Mutable Files`_
7.  `Known Issues`_


SFTP Background
===============

FTP is the venerable internet file-transfer protocol, first developed in
1971. The FTP server usually listens on port 21. A separate connection is
used for the actual data transfers, either in the same direction as the
initial client-to-server connection (for PORT mode), or in the reverse
direction (for PASV) mode. Connections are unencrypted, so passwords, file
names, and file contents are visible to eavesdroppers.

SFTP is the modern replacement, developed as part of the SSH "secure shell"
protocol, and runs as a subchannel of the regular SSH connection. The SSH
server usually listens on port 22. All connections are encrypted.

Both FTP and SFTP were developed assuming a UNIX-like server, with accounts
and passwords, octal file modes (user/group/other, read/write/execute), and
ctime/mtime timestamps.

Previous versions of Tahoe-LAFS supported FTP, but now only the superior SFTP
frontend is supported. See `Known Issues`_, below, for details on the
limitations of SFTP.

Tahoe-LAFS Support
==================

All Tahoe-LAFS client nodes can run a frontend SFTP server, allowing regular
SFTP clients (like ``/usr/bin/sftp``, the ``sshfs`` FUSE plugin, and many
others) to access the file store.

Since Tahoe-LAFS does not use user accounts or passwords, the SFTP
servers must be configured with a way to first authenticate a user (confirm
that a prospective client has a legitimate claim to whatever authorities we
might grant a particular user), and second to decide what directory cap
should be used as the root directory for a log-in by the authenticated user.
As of Tahoe-LAFS v1.17,
RSA/DSA public key authentication is the only supported mechanism.

Tahoe-LAFS provides two mechanisms to perform this user-to-cap mapping.
The first (recommended) is a simple flat file with one account per line.
The second is an HTTP-based login mechanism.

Creating an Account File
========================

To use the first form, create a file (for example ``BASEDIR/private/accounts``)
in which each non-comment/non-blank line is a space-separated line of
(USERNAME, KEY-TYPE, PUBLIC-KEY, ROOTCAP), like so::

 % cat BASEDIR/private/accounts
 # This is a public key line: username keytype pubkey cap
 # (Tahoe-LAFS v1.11 or later)
 carol ssh-rsa AAAA... URI:DIR2:ovjy4yhylqlfoqg2vcze36dhde:4d4f47qko2xm5g7osgo2yyidi5m4muyo2vjjy53q4vjju2u55mfa

The key type may be either "ssh-rsa" or "ssh-dsa".

Now add an ``accounts.file`` directive to your ``tahoe.cfg`` file, as described in
the next sections.

Configuring SFTP Access
=======================

The Tahoe-LAFS SFTP server requires a host keypair, just like the regular SSH
server. It is important to give each server a distinct keypair, to prevent
one server from masquerading as different one. The first time a client
program talks to a given server, it will store the host key it receives, and
will complain if a subsequent connection uses a different key. This reduces
the opportunity for man-in-the-middle attacks to just the first connection.

Exercise caution when connecting to the SFTP server remotely. The AES
implementation used by the SFTP code does not have defenses against timing
attacks. The code for encrypting the SFTP connection was not written by the
Tahoe-LAFS team, and we have not reviewed it as carefully as we have reviewed
the code for encrypting files and directories in Tahoe-LAFS itself. (See
`Twisted ticket #4633`_ for a possible fix to this issue.)

.. _Twisted ticket #4633: https://twistedmatrix.com/trac/ticket/4633

If you can connect to the SFTP server (which is provided by the Tahoe-LAFS
gateway) only from a client on the same host, then you would be safe from any
problem with the SFTP connection security. The examples given below enforce
this policy by including ":interface=127.0.0.1" in the "port" option, which
causes the server to only accept connections from localhost.

You will use directives in the tahoe.cfg file to tell the SFTP code where to
find these keys. To create one, use the ``ssh-keygen`` tool (which comes with
the standard OpenSSH client distribution)::

 % cd BASEDIR
 % ssh-keygen -f private/ssh_host_rsa_key

The server private key file must not have a passphrase.

Then, to enable the SFTP server with an accounts file, add the following
lines to the BASEDIR/tahoe.cfg file::

 [sftpd]
 enabled = true
 port = tcp:8022:interface=127.0.0.1
 host_pubkey_file = private/ssh_host_rsa_key.pub
 host_privkey_file = private/ssh_host_rsa_key
 accounts.file = private/accounts

The SFTP server will listen on the given port number and on the loopback
interface only. The "accounts.file" pathname will be interpreted relative to
the node's BASEDIR.

Or, to use an account server instead, do this::

 [sftpd]
 enabled = true
 port = tcp:8022:interface=127.0.0.1
 host_pubkey_file = private/ssh_host_rsa_key.pub
 host_privkey_file = private/ssh_host_rsa_key
 accounts.url = https://example.com/login

You can provide both accounts.file and accounts.url, although it probably
isn't very useful except for testing.

For further information on SFTP compatibility and known issues with various
clients and with the sshfs filesystem, see wiki:SftpFrontend_

.. _wiki:SftpFrontend: https://tahoe-lafs.org/trac/tahoe-lafs/wiki/SftpFrontend

Dependencies
============

The Tahoe-LAFS SFTP server requires the Twisted "Conch" component (a "conch"
is a twisted shell, get it?). Many Linux distributions package the Conch code
separately: debian puts it in the "python-twisted-conch" package.

Immutable and Mutable Files
===========================

All files created via SFTP are immutable files. However, files can
only be created in writeable directories, which allows the directory entry to
be relinked to a different file. Normally, when the path of an immutable file
is opened for writing by SFTP, the directory entry is relinked to another
file with the newly written contents when the file handle is closed. The old
file is still present on the grid, and any other caps to it will remain
valid. (See :doc:`../garbage-collection` for how to reclaim the space used by
files that are no longer needed.)

The 'no-write' metadata field of a directory entry can override this
behaviour. If the 'no-write' field holds a true value, then a permission
error will occur when trying to write to the file, even if it is in a
writeable directory. This does not prevent the directory entry from being
unlinked or replaced.

When using sshfs, the 'no-write' field can be set by clearing the 'w' bits in
the Unix permissions, for example using the command ``chmod 444 path/to/file``.
Note that this does not mean that arbitrary combinations of Unix permissions
are supported. If the 'w' bits are cleared on a link to a mutable file or
directory, that link will become read-only.

If SFTP is used to write to an existing mutable file, it will publish a new
version when the file handle is closed.

Known Issues
============

Known Issues in the SFTP Frontend
---------------------------------

Upload errors may not be reported when writing files using SFTP via sshfs
(`ticket #1059`_).

Non-ASCII filenames are supported with SFTP only if the client encodes
filenames as UTF-8 (`ticket #1089`_).

See also wiki:SftpFrontend_.

.. _ticket #1059: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1059
.. _ticket #1089: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1089
