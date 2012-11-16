
=====================
Lease database design
=====================

The target audience for this document is developers who wish to understand
the new lease database (leasedb) planned to be added in Tahoe-LAFS v1.11.0.


Introduction
------------

A "lease" is a request by an account that a share not be deleted before a
specified time. Each storage server stores leases in order to know which
shares to spare from garbage collection.

Motivation
----------

The leasedb will replace the current design in which leases are stored in
the storage server's share container files. That design has several
disadvantages:

- Updating a lease requires modifying a share container file (even for
  immutable shares). This complicates the implementation of share classes.
  The mixing of share contents and lease data in share files also led to a
  security bug (ticket `#1528`_).

- When only the disk backend is supported, it is possible to read and
  update leases synchronously because the share files are stored locally
  to the storage server. For the cloud backend, accessing share files
  requires an HTTP request, and so must be asynchronous. Accepting this
  asynchrony for lease queries would be both inefficient and complex.
  Moving lease information out of shares and into a local database allows
  lease queries to stay synchronous.

Also, the current cryptographic protocol for renewing and cancelling leases
(based on shared secrets derived from secure hash functions) is complex,
and the cancellation part was never used.

The leasedb solves the first two problems by storing the lease information in
a local database instead of in the share container files. The share data
itself is still held in the share container file.

At the same time as implementing leasedb, we devised a simpler protocol for
allocating and cancelling leases: a client can use a public key digital
signature to authenticate access to a foolscap object representing the
authority of an account. This protocol is not yet implemented; at the time
of writing, only an "anonymous" account is supported.

The leasedb also provides an efficient way to get summarized information,
such as total space usage of shares leased by an account, for accounting
purposes.

.. _`#1528`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1528


Design constraints
------------------

A share is stored as a collection of objects. The persistent storage may be
remote from the server (for example, cloud storage).

Writing to the persistent store objects is in general not an atomic
operation. So the leasedb also keeps track of which shares are in an
inconsistent state because they have been partly written. (This may
change in future when we implement a protocol to improve atomicity of
updates to mutable shares.)

Leases are no longer stored in shares. The same share format is used as
before, but the lease slots are ignored, and are cleared when rewriting a
mutable share. The new design also does not use lease renewal or cancel
secrets. (They are accepted as parameters in the storage protocol interfaces
for backward compatibility, but are ignored. Cancel secrets were already
ignored due to the fix for `#1528`_.)

The new design needs to be fail-safe in the sense that if the lease database
is lost or corruption is detected, no share data will be lost (even though
the metadata about leases held by particular accounts has been lost).


Accounting crawler
------------------

A "crawler" is a long-running process that visits share container files at a
slow rate, so as not to overload the server by trying to visit all share
container files one after another immediately.

The accounting crawler replaces the previous "lease crawler". It examines
each share container file and compares it with the state of the leasedb, and
may update the state of the share and/or the leasedb.

The accounting crawler performs the following functions:

- Remove leases that are past their expiration time. (Currently, this is
  done automatically before deleting shares, but we plan to allow expiration
  to be performed separately for individual accounts in future.)

- Delete the objects containing unleased shares — that is, shares that have
  stable entries in the leasedb but no current leases (see below for the
  definition of "stable" entries).

- Discover shares that have been manually added to storage, via ``scp`` or
  some other out-of-band means.

- Discover shares that are present when a storage server is upgraded to
  a leasedb-supporting version from a previous version, and give them
  "starter leases".

- Recover from a situation where the leasedb is lost or detectably
  corrupted. This is handled in the same way as upgrading from a previous
  version.

- Detect shares that have unexpectedly disappeared from storage.  The
  disappearance of a share is logged, and its entry and leases are removed
  from the leasedb.


Accounts
--------

An account holds leases for some subset of shares stored by a server. The
leasedb schema can handle many distinct accounts, but for the time being we
create only two accounts: an anonymous account and a starter account. The
starter account is used for leases on shares discovered by the accounting
crawler; the anonymous account is used for all other leases.

The leasedb has at most one lease entry per account per (storage_index,
shnum) pair. This entry stores the times when the lease was last renewed and
when it is set to expire (if the expiration policy does not force it to
expire earlier), represented as Unix UTC-seconds-since-epoch timestamps.

For more on expiration policy, see `docs/garbage-collection.rst
<../garbage-collection.rst>`__.


Share states
------------

The leasedb holds an explicit indicator of the state of each share.

The diagram and descriptions below give the possible values of the "state"
indicator, what that value means, and transitions between states, for any
(storage_index, shnum) pair on each server::


  #        STATE_STABLE -------.
  #         ^   |   ^ |         |
  #         |   v   | |         v
  #    STATE_COMING | |    STATE_GOING
  #         ^       | |         |
  #         |       | v         |
  #         '----- NONE <------'


**NONE**: There is no entry in the ``shares`` table for this (storage_index,
shnum) in this server's leasedb. This is the initial state.

**STATE_COMING**: The share is being created or (if a mutable share)
updated. The store objects may have been at least partially written, but
the storage server doesn't have confirmation that they have all been
completely written.

**STATE_STABLE**: The store objects have been completely written and are
not in the process of being modified or deleted by the storage server. (It
could have been modified or deleted behind the back of the storage server,
but if it has, the server has not noticed that yet.) The share may or may not
be leased.

**STATE_GOING**: The share is being deleted.

State transitions
-----------------

• **STATE_GOING** → **NONE**

    trigger: The storage server gains confidence that all store objects for
    the share have been removed.

    implementation:

    1. Remove the entry in the leasedb.

• **STATE_STABLE** → **NONE**
	
    trigger: The accounting crawler noticed that all the store objects for
    this share are gone.

    implementation:

    1. Remove the entry in the leasedb.

• **NONE** → **STATE_COMING**

    triggers: A new share is being created, as explicitly signalled by a
    client invoking a creation command, *or* the accounting crawler discovers
    an incomplete share.

    implementation:

    1. Add an entry to the leasedb with **STATE_COMING**.

    2. (In case of explicit creation) begin writing the store objects to hold
       the share.

• **STATE_STABLE** → **STATE_COMING**

    trigger: A mutable share is being modified, as explicitly signalled by a
    client invoking a modification command.

    implementation:

    1. Add an entry to the leasedb with **STATE_COMING**.

    2. Begin updating the store objects.

• **STATE_COMING** → **STATE_STABLE**

    trigger: All store objects have been written.

    implementation:

    1. Change the state value of this entry in the leasedb from
       **STATE_COMING** to **STATE_STABLE**.

• **NONE** → **STATE_STABLE**

    trigger: The accounting crawler discovers a complete share.

    implementation:

    1. Add an entry to the leasedb with **STATE_STABLE**.

• **STATE_STABLE** → **STATE_GOING**

    trigger: The share should be deleted because it is unleased.

    implementation:

    1. Change the state value of this entry in the leasedb from
       **STATE_STABLE** to **STATE_GOING**.

    2. Initiate removal of the store objects.


The following constraints are needed to avoid race conditions:

- While a share is being deleted (entry in **STATE_GOING**), we do not accept
  any requests to recreate it. That would result in add and delete requests
  for store objects being sent concurrently, with undefined results.

- While a share is being added or modified (entry in **STATE_COMING**), we
  treat it as leased.

- Creation or modification requests for a given mutable share are serialized.


Unresolved design issues
------------------------

- What happens if a write to store objects for a new share fails
  permanently?  If we delete the share entry, then the accounting crawler
  will eventually get to those store objects and see that their lengths
  are inconsistent with the length in the container header. This will cause
  the share to be treated as corrupted. Should we instead attempt to
  delete those objects immediately? If so, do we need a direct
  **STATE_COMING** → **STATE_GOING** transition to handle this case?

- What happens if only some store objects for a share disappear
  unexpectedly?  This case is similar to only some objects having been
  written when we get an unrecoverable error during creation of a share, but
  perhaps we want to treat it differently in order to preserve information
  about the storage service having lost data.

- Does the leasedb need to track corrupted shares?


Future directions
-----------------

Clients will have key pairs identifying accounts, and will be able to add
leases for a specific account. Various space usage policies can be defined.

Better migration tools ('tahoe storage export'?) will create export files
that include both the share data and the lease data, and then an import tool
will both put the share in the right place and update the recipient node's
leasedb.
