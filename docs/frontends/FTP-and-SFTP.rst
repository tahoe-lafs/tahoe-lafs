=================================
Tahoe-LAFS FTP and SFTP Frontends
=================================

1.  `FTP/SFTP Background`_
2.  `Tahoe-LAFS Support`_
3.  `Creating an Account File`_
4.  `Configuring FTP Access`_
5.  `Configuring SFTP Access`_
6.  `Dependencies`_
7.  `Immutable and mutable files`_
8.  `Known Issues`_


FTP/SFTP Background
===================

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

Tahoe-LAFS Support
==================

All Tahoe-LAFS client nodes can run a frontend FTP server, allowing regular FTP
clients (like /usr/bin/ftp, ncftp, and countless others) to access the
virtual filesystem. They can also run an SFTP server, so SFTP clients (like
/usr/bin/sftp, the sshfs FUSE plugin, and others) can too. These frontends
sit at the same level as the web-API interface.

Since Tahoe-LAFS does not use user accounts or passwords, the FTP/SFTP servers
must be configured with a way to first authenticate a user (confirm that a
prospective client has a legitimate claim to whatever authorities we might
grant a particular user), and second to decide what root directory cap should
be granted to the authenticated username. A username and password is used
for this purpose. (The SFTP protocol is also capable of using client
RSA or DSA public keys, but this is not currently implemented.)

Tahoe-LAFS provides two mechanisms to perform this user-to-rootcap mapping. The
first is a simple flat file with one account per line. The second is an
HTTP-based login mechanism, backed by simple PHP script and a database. The
latter form is used by allmydata.com to provide secure access to customer
rootcaps.

Creating an Account File
========================

To use the first form, create a file (probably in
BASEDIR/private/ftp.accounts) in which each non-comment/non-blank line is a
space-separated line of (USERNAME, PASSWORD, ROOTCAP), like so::

 % cat BASEDIR/private/ftp.accounts
 # This is a password line, (username, password, rootcap)
 alice password URI:DIR2:ioej8xmzrwilg772gzj4fhdg7a:wtiizszzz2rgmczv4wl6bqvbv33ag4kvbr6prz3u6w3geixa6m6a
 bob sekrit URI:DIR2:6bdmeitystckbl9yqlw7g56f4e:serp5ioqxnh34mlbmzwvkp3odehsyrr7eytt5f64we3k9hhcrcja

Future versions of Tahoe-LAFS may support using client public keys for SFTP.
The words "ssh-rsa" and "ssh-dsa" after the username are reserved to specify
the public key format, so users cannot have a password equal to either of
these strings.

Now add an 'accounts.file' directive to your tahoe.cfg file, as described
in the next sections.

Configuring FTP Access
======================

To enable the FTP server with an accounts file, add the following lines to
the BASEDIR/tahoe.cfg file::

 [ftpd]
 enabled = true
 port = tcp:8021:interface=127.0.0.1
 accounts.file = private/ftp.accounts

The FTP server will listen on the given port number and on the loopback 
interface only. The "accounts.file" pathname will be interpreted 
relative to the node's BASEDIR.

To enable the FTP server with an account server instead, provide the URL of
that server in an "accounts.url" directive::

 [ftpd]
 enabled = true
 port = tcp:8021:interface=127.0.0.1
 accounts.url = https://example.com/login

You can provide both accounts.file and accounts.url, although it probably
isn't very useful except for testing.

FTP provides no security, and so your password or caps could be eavesdropped
if you connect to the FTP server remotely. The examples above include
":interface=127.0.0.1" in the "port" option, which causes the server to only
accept connections from localhost.

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
the code for encrypting files and directories in Tahoe-LAFS itself. If you
can connect to the SFTP server (which is provided by the Tahoe-LAFS gateway)
only from a client on the same host, then you would be safe from any problem
with the SFTP connection security. The examples given below enforce this
policy by including ":interface=127.0.0.1" in the "port" option, which
causes the server to only accept connections from localhost.

You will use directives in the tahoe.cfg file to tell the SFTP code where to
find these keys. To create one, use the ``ssh-keygen`` tool (which comes with
the standard openssh client distribution)::

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
 accounts.file = private/ftp.accounts

The SFTP server will listen on the given port number and on the loopback
interface only. The "accounts.file" pathname will be interpreted
relative to the node's BASEDIR.

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
clients and with the sshfs filesystem, see
http://tahoe-lafs.org/trac/tahoe-lafs/wiki/SftpFrontend .

Dependencies
============

The Tahoe-LAFS SFTP server requires the Twisted "Conch" component (a "conch" is
a twisted shell, get it?). Many Linux distributions package the Conch code
separately: debian puts it in the "python-twisted-conch" package. Conch
requires the "pycrypto" package, which is a Python+C implementation of many
cryptographic functions (the debian package is named "python-crypto").

Note that "pycrypto" is different than the "pycryptopp" package that Tahoe-LAFS
uses (which is a Python wrapper around the C++ -based Crypto++ library, a
library that is frequently installed as /usr/lib/libcryptopp.a, to avoid
problems with non-alphanumerics in filenames).

The FTP server requires code in Twisted that enables asynchronous closing of
file-upload operations. This code was landed to Twisted's SVN trunk in r28453
on 23-Feb-2010, slightly too late for the Twisted-10.0 release, but it should
be present in the next release after that. To use Tahoe-LAFS's FTP server with
Twisted-10.0 or earlier, you will need to apply the patch attached to
http://twistedmatrix.com/trac/ticket/3462 . The Tahoe-LAFS node will refuse to
start the FTP server unless it detects the necessary support code in Twisted.
This patch is not needed for SFTP.

Immutable and Mutable Files
===========================

All files created via SFTP (and FTP) are immutable files. However, files
can only be created in writeable directories, which allows the directory
entry to be relinked to a different file. Normally, when the path of an
immutable file is opened for writing by SFTP, the directory entry is
relinked to another file with the newly written contents when the file
handle is closed. The old file is still present on the grid, and any other
caps to it will remain valid. (See `docs/garbage-collection.rst
<../garbage-collection.rst>`_ for how to reclaim the space used by files
that are no longer needed.)

The 'no-write' metadata field of a directory entry can override this
behaviour. If the 'no-write' field holds a true value, then a permission
error will occur when trying to write to the file, even if it is in a
writeable directory. This does not prevent the directory entry from being
unlinked or replaced.

When using sshfs, the 'no-write' field can be set by clearing the 'w'
bits in the Unix permissions, for example using the command
'chmod 444 path/to/file'. Note that this does not mean that arbitrary
combinations of Unix permissions are supported. If the 'w' bits are
cleared on a link to a mutable file or directory, that link will become
read-only.

If SFTP is used to write to an existing mutable file, it will publish a
new version when the file handle is closed.

Known Issues
============

Mutable files are not supported by the FTP frontend (`ticket #680
<http://tahoe-lafs.org/trac/tahoe-lafs/ticket/680>`_). Currently, a directory
containing mutable files cannot even be listed over FTP.

The FTP frontend sometimes fails to report errors, for example if an upload
fails because it does meet the "servers of happiness" threshold (`ticket #1081
<http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1081>`_). Upload errors also may not
be reported when writing files using SFTP via sshfs (`ticket #1059
<http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1059>`_).

Non-ASCII filenames are not supported by FTP (`ticket #682
<http://tahoe-lafs.org/trac/tahoe-lafs/ticket/682>`_). They can be used
with SFTP only if the client encodes filenames as UTF-8 (`ticket #1089
<http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1089>`_).

The gateway node may incur a memory leak when accessing many files via SFTP
(`ticket #1045 <http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1045>`_).

For other known issues in SFTP, see
<http://tahoe-lafs.org/trac/tahoe-lafs/wiki/SftpFrontend>.
