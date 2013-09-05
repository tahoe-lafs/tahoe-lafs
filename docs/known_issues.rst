
============
Known Issues
============

Below is a list of known issues in recent releases of Tahoe-LAFS, and how to
manage them.  The current version of this file can be found at
https://tahoe-lafs.org/source/tahoe-lafs/trunk/docs/known_issues.rst .

If you've been using Tahoe-LAFS since v1.1 (released 2008-06-11) or if you're
just curious about what sort of mistakes we've made in the past, then you might
want to read `the "historical known issues" document`_.

.. _the "historical known issues" document: historical/historical_known_issues.txt


Known Issues in Tahoe-LAFS v1.9.1, released 12-Jan-2012
=======================================================

  *  `Potential unauthorized access by JavaScript in unrelated files`_
  *  `Potential disclosure of file through embedded hyperlinks or JavaScript in that file`_
  *  `Command-line arguments are leaked to other local users`_
  *  `Capabilities may be leaked to web browser phishing filter / "safe browsing" servers`_
  *  `Known issues in the FTP and SFTP frontends`_
  *  `Traffic analysis based on sizes of files/directories, storage indices, and timing`_

----

Potential unauthorized access by JavaScript in unrelated files
--------------------------------------------------------------

If you view a file stored in Tahoe-LAFS through a web user interface,
JavaScript embedded in that file might be able to access other files or
directories stored in Tahoe-LAFS which you view through the same web
user interface.  Such a script would be able to send the contents of
those other files or directories to the author of the script, and if you
have the ability to modify the contents of those files or directories,
then that script could modify or delete those files or directories.

*how to manage it*

For future versions of Tahoe-LAFS, we are considering ways to close off
this leakage of authority while preserving ease of use -- the discussion
of this issue is ticket `#615`_.

For the present, either do not view files stored in Tahoe-LAFS through a
web user interface, or turn off JavaScript in your web browser before
doing so, or limit your viewing to files which you know don't contain
malicious JavaScript.

.. _#615: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/615


----

Potential disclosure of file through embedded hyperlinks or JavaScript in that file
-----------------------------------------------------------------------------------

If there is a file stored on a Tahoe-LAFS storage grid, and that file
gets downloaded and displayed in a web browser, then JavaScript or
hyperlinks within that file can leak the capability to that file to a
third party, which means that third party gets access to the file.

If there is JavaScript in the file, then it could deliberately leak
the capability to the file out to some remote listener.

If there are hyperlinks in the file, and they get followed, then
whichever server they point to receives the capability to the
file. Note that IMG tags are typically followed automatically by web
browsers, so being careful which hyperlinks you click on is not
sufficient to prevent this from happening.

*how to manage it*

For future versions of Tahoe-LAFS, we are considering ways to close off
this leakage of authority while preserving ease of use -- the discussion
of this issue is ticket `#127`_.

For the present, a good work-around is that if you want to store and
view a file on Tahoe-LAFS and you want that file to remain private, then
remove from that file any hyperlinks pointing to other people's servers
and remove any JavaScript unless you are sure that the JavaScript is not
written to maliciously leak access.

.. _#127: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/127


----

Command-line arguments are leaked to other local users
------------------------------------------------------

Remember that command-line arguments are visible to other users (through
the 'ps' command, or the windows Process Explorer tool), so if you are
using a Tahoe-LAFS node on a shared host, other users on that host will
be able to see (and copy) any caps that you pass as command-line
arguments.  This includes directory caps that you set up with the "tahoe
add-alias" command.

*how to manage it*

As of Tahoe-LAFS v1.3.0 there is a "tahoe create-alias" command that does
the following technique for you.

Bypass add-alias and edit the NODEDIR/private/aliases file directly, by
adding a line like this:

  fun: URI:DIR2:ovjy4yhylqlfoqg2vcze36dhde:4d4f47qko2xm5g7osgo2yyidi5m4muyo2vjjy53q4vjju2u55mfa

By entering the dircap through the editor, the command-line arguments
are bypassed, and other users will not be able to see them. Once you've
added the alias, if you use that alias instead of a cap itself on the
command-line, then no secrets are passed through the command line.  Then
other processes on the system can still see your filenames and other
arguments you type there, but not the caps that Tahoe-LAFS uses to permit
access to your files and directories.


----

Capabilities may be leaked to web browser phishing filter / "safe browsing" servers
-----------------------------------------------------------------------------------

Firefox, Internet Explorer, and Chrome include a "phishing filter" or
"safe browing" component, which is turned on by default, and which sends
any URLs that it deems suspicious to a central server.

Microsoft gives `a brief description of their filter's operation`_. Firefox
and Chrome both use Google's `"safe browsing API"`_ (`specification`_).

This of course has implications for the privacy of general web browsing
(especially in the cases of Firefox and Chrome, which send your main
personally identifying Google cookie along with these requests without your
explicit consent, as described in `Firefox bugzilla ticket #368255`_.

The reason for documenting this issue here, though, is that when using the
Tahoe-LAFS web user interface, it could also affect confidentiality and integrity
by leaking capabilities to the filter server.

Since IE's filter sends URLs by SSL/TLS, the exposure of caps is limited to
the filter server operators (or anyone able to hack the filter server) rather
than to network eavesdroppers. The "safe browsing API" protocol used by
Firefox and Chrome, on the other hand, is *not* encrypted, although the
URL components are normally hashed.

Opera also has a similar facility that is disabled by default. A previous
version of this file stated that Firefox had abandoned their phishing
filter; this was incorrect.

.. _a brief description of their filter's operation: http://blogs.msdn.com/ie/archive/2005/09/09/463204.aspx
.. _"safe browsing API": http://code.google.com/apis/safebrowsing/
.. _specification: http://code.google.com/p/google-safe-browsing/wiki/Protocolv2Spec
.. _Firefox bugzilla ticket #368255: https://bugzilla.mozilla.org/show_bug.cgi?id=368255


*how to manage it*

If you use any phishing filter or "safe browsing" feature, consider either
disabling it, or not using the WUI via that browser. Phishing filters have
`very limited effectiveness`_ , and phishing or malware attackers have learnt
how to bypass them.

.. _very limited effectiveness: http://lorrie.cranor.org/pubs/ndss-phish-tools-final.pdf

To disable the filter in IE7 or IE8:
++++++++++++++++++++++++++++++++++++

- Click Internet Options from the Tools menu.

- Click the Advanced tab.

- If an "Enable SmartScreen Filter" option is present, uncheck it.
  If a "Use Phishing Filter" or "Phishing Filter" option is present,
  set it to Disable.

- Confirm (click OK or Yes) out of all dialogs.

If you have a version of IE that splits the settings between security
zones, do this for all zones.

To disable the filter in Firefox:
+++++++++++++++++++++++++++++++++

- Click Options from the Tools menu.

- Click the Security tab.

- Uncheck both the "Block reported attack sites" and "Block reported
  web forgeries" options.

- Click OK.

To disable the filter in Chrome:
++++++++++++++++++++++++++++++++

- Click Options from the Tools menu.

- Click the "Under the Hood" tab and find the "Privacy" section.

- Uncheck the "Enable phishing and malware protection" option.

- Click Close.


----

Known issues in the FTP and SFTP frontends
------------------------------------------

These are documented in `docs/frontends/FTP-and-SFTP.rst`_ and on `the SftpFrontend page`_ on the wiki. 

.. _docs/frontends/FTP-and-SFTP.rst: frontends/FTP-and-SFTP.rst
.. _the SftpFrontend page: https://tahoe-lafs.org/trac/tahoe-lafs/wiki/SftpFrontend


----

Traffic analysis based on sizes of files/directories, storage indices, and timing
---------------------------------------------------------------------------------

Files and directories stored by Tahoe-LAFS are encrypted, but the ciphertext
reveals the exact size of the original file or directory representation.
This information is available to passive eavesdroppers and to server operators.

For example, a large data set with known file sizes could probably be
identified with a high degree of confidence.

Uploads and downloads of the same file or directory can be linked by server
operators, even without making assumptions based on file size. Anyone who
knows the introducer furl for a grid may be able to act as a server operator.
This implies that if such an attacker knows which file/directory is being
accessed in a particular request (by some other form of surveillance, say),
then they can identify later or earlier accesses of the same file/directory.

Observing requests during a directory traversal (such as a deep-check
operation) could reveal information about the directory structure, i.e.
which files and subdirectories are linked from a given directory.

Attackers can combine the above information with inferences based on timing
correlations. For instance, two files that are accessed close together in
time are likely to be related even if they are not linked in the directory
structure. Also, users that access the same files may be related to each other.


----

Known Issues in Tahoe-LAFS v1.9.0, released 31-Oct-2011
=======================================================


Integrity Failure during Mutable Downloads
------------------------------------------

Under certain circumstances, the integrity-verification code of the mutable
downloader could be bypassed. Clients who receive carefully crafted shares
(from attackers) will emit incorrect file contents, and the usual
share-corruption errors would not be raised. This only affects mutable files
(not immutable), and only affects downloads that use doctored shares. It is
not persistent: the threat is resolved once you upgrade your client to a
version without the bug. However, read-modify-write operations (such as
directory manipulations) performed by vulnerable clients could cause the
attacker's modifications to be written back out to the mutable file, making
the corruption permanent.

The attacker's ability to manipulate the file contents is limited. They can
modify FEC-encoded ciphertext in all but one share. This gives them the
ability to blindly flip bits in roughly 2/3rds of the file (for the default
k=3 encoding parameter). Confidentiality remains intact, unless the attacker
can deduce the file's contents by observing your reactions to corrupted
downloads.

This bug was introduced in 1.9.0, as part of the MDMF-capable downloader, and
affects both SDMF and MDMF files. It was not present in 1.8.3.

*how to manage it*

There are three options:

* Upgrade to 1.9.1, which fixes the bug
* Downgrade to 1.8.3, which does not contain the bug
* If using 1.9.0, do not trust the contents of mutable files (whether SDMF or
  MDMF) that the 1.9.0 client emits, and do not modify directories (which
  could write the corrupted data back into place, making the damage
  persistent)


.. _#1654: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1654

----

Known Issues in Tahoe-LAFS v1.8.2, released 30-Jan-2011
=======================================================


Unauthorized deletion of an immutable file by its storage index
---------------------------------------------------------------

Due to a flaw in the Tahoe-LAFS storage server software in v1.3.0 through
v1.8.2, a person who knows the "storage index" that identifies an immutable
file can cause the server to delete its shares of that file.

If an attacker can cause enough shares to be deleted from enough storage
servers, this deletes the file.

This vulnerability does not enable anyone to read file contents without
authorization (confidentiality), nor to change the contents of a file
(integrity).

A person could learn the storage index of a file in several ways:

1. By being granted the authority to read the immutable file—i.e. by being
   granted a read capability to the file. They can determine the file's
   storage index from its read capability.

2. By being granted a verify capability to the file. They can determine the
   file's storage index from its verify capability. This case probably
   doesn't happen often because users typically don't share verify caps.

3. By operating a storage server, and receiving a request from a client that
   has a read cap or a verify cap. If the client attempts to upload,
   download, or verify the file with their storage server, even if it doesn't
   actually have the file, then they can learn the storage index of the file.

4. By gaining read access to an existing storage server's local filesystem,
   and inspecting the directory structure that it stores its shares in. They
   can thus learn the storage indexes of all files that the server is holding
   at least one share of. Normally only the operator of an existing storage
   server would be able to inspect its local filesystem, so this requires
   either being such an operator of an existing storage server, or somehow
   gaining the ability to inspect the local filesystem of an existing storage
   server.

*how to manage it*

Tahoe-LAFS version v1.8.3 or newer (except v1.9a1) no longer has this flaw;
if you upgrade a storage server to a fixed release then that server is no
longer vulnerable to this problem.

Note that the issue is local to each storage server independently of other
storage servers—when you upgrade a storage server then that particular
storage server can no longer be tricked into deleting its shares of the
target file.

If you can't immediately upgrade your storage server to a version of
Tahoe-LAFS that eliminates this vulnerability, then you could temporarily
shut down your storage server. This would of course negatively impact
availability—clients would not be able to upload or download shares to that
particular storage server while it was shut down—but it would protect the
shares already stored on that server from being deleted as long as the server
is shut down.

If the servers that store shares of your file are running a version of
Tahoe-LAFS with this vulnerability, then you should think about whether
someone can learn the storage indexes of your files by one of the methods
described above. A person can not exploit this vulnerability unless they have
received a read cap or verify cap, or they control a storage server that has
been queried about this file by a client that has a read cap or a verify cap.

Tahoe-LAFS does not currently have a mechanism to limit which storage servers
can connect to your grid, but it does have a way to see which storage servers
have been connected to the grid. The Introducer's front page in the Web User
Interface has a list of all storage servers that the Introducer has ever seen
and the first time and the most recent time that it saw them. Each Tahoe-LAFS
gateway maintains a similar list on its front page in its Web User Interface,
showing all of the storage servers that it learned about from the Introducer,
when it first connected to that storage server, and when it most recently
connected to that storage server. These lists are stored in memory and are
reset to empty when the process is restarted.

See ticket `#1528`_ for technical details.

.. _#1528: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1528
