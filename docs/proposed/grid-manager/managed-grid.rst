(This document is "in-progress", with feedback and input from two
devchats with Brain Warner and exarkun as well as other input,
discussion and edits from exarkun. It is NOT done). Search for
"DECIDE" for open questions.


Managed Grid
============

In a grid using an Introducer, a client will use any storage-server
the Introducer announces. This means that anyone with the Introducer
fURL can connect storage to the grid.

Sometimes, this is just what you want!

For some use-cases, though, you want to have clients only use certain
servers. One case might be a "managed" grid, where some entity runs
the grid; clients of this grid don't want their uploads to go to
"unmanaged" storage if some other client decides to provide storage.

One way to limit which storage servers a client connects to is via the
"server list" (:ref:`server_list`) (aka "Introducer-less"
mode). Clients are given static lists of storage-servers, and connect
only to those. This means manually updating these lists if the storage
servers change, however.

Another method is for clients to use `[client] peers.preferred=`
configuration option (XXX link? appears undocumented), which suffers
from a similar disadvantage.


Grid Manager
------------

A "grid-manager" consists of some data defining a keypair (along with
some other details) and Tahoe sub-commands to manipulate the data and
produce certificates to give to storage-servers. Certificates assert
the statement: "Grid Manager X suggests you use storage-server Y to
upload shares to" (X and Y are public-keys). Such a certificate
consists of:

 - a version (currently 1)
 - the public-key of a storage-server
 - an expiry timestamp
 - a signature of the above

A client will always use any storage-server for downloads (expired
certificate, or no certificate) because clients check the ciphertext
and re-assembled plaintext against the keys in the capability;
"grid-manager" certificates only control uploads.


Grid Manager Data Storage
-------------------------

The data defining the grid-manager is stored in an arbitrary
directory, which you indicate with the ``--config`` option (in the
future, we may add the ability to store the data directly in a grid,
at which time you may be able to pass a directory-capability to this
option).

If you don't want to store the configuration on disk at all, you may
use ``--config -`` (that's a dash) and write a valid JSON (YAML? I'm
guessing JSON is easier to deal with here, more-interoperable?)
configuration to stdin.

All commands take the ``--config`` option, and they all behave
similarly for "data from stdin" versus "data from disk".

DECIDE:
 - config is YAML or JSON?


tahoe grid-manager create
`````````````````````````

Create a new grid-manager.

If you specify ``--config -`` then a new grid-manager configuration is
written to stdout. Otherwise, a new grid-manager is created in the
directory specified by the ``--config`` option. It is an error if the
directory already exists.


tahoe grid-manager public-identity
``````````````````````````````````

Print out a grid-manager's public key. This key is derived from the
private-key of the grid-manager, so a valid grid-manager config must
be given via ``--config``

This public key is what is put in clients' configuration to actually
validate and use grid-manager certificates.


tahoe grid-manager add
``````````````````````

Takes two args: ``name pubkey``. The ``name`` is an arbitrary local
identifier and the pubkey is the encoded key from a ``node.pubkey``
file in the storage-server's node directory (with no whitespace).

This adds a new storage-server to a Grid Manager's
configuration. (Since it mutates the configuration, if you used
``--config -`` the new configuration will be printed to stdout).


tahoe grid-manager list
```````````````````````

Lists all storage-servers that have previously been added using
``tahoe grid-manager add``.


tahoe grid-manager sign
```````````````````````

Takes one arg: ``name``, the petname used previous in a ``tahoe
grid-manager add`` command.

Note that this mutates the state of the grid-manager if it is on disk,
by adding this certificate to our collection of issued
certificates. If you used ``--config -``, the certificate isn't
persisted anywhere except to stdout (so if you wish to keep it
somewhere, that is up to you).

This command creates a new "version 1" certificate for a
storage-server (identified by its public key). The new certificate is
printed to stdout. If you stored the config on disk, the new
certificate will (also) be in a file named like ``alice.cert.0``.


Enrolling a Storage Server
--------------------------

tahoe admin add-grid-manager-cert
`````````````````````````````````

- `--filename`: the file to read the cert from (default: stdin)
- `--name`: the name of this certificate (default: "default")

Import a "version 1" storage-certificate produced by a grid-manager
(probably: a storage server may have zero or more such certificates
installed; for now just one is sufficient). You will have to re-start
your node after this. Subsequent announcements to the Introducer will
include this certificate.


Enrolling a Client
------------------

tahoe add-grid-manager
``````````````````````

- ``--name``: a petname to call this Grid Manager (default: "default")

For clients to start using a Grid Manager, they must add a
public-key. A client may have any number of grid-managers, so each one
has a name. If you don't supply ``--name`` then ``"default"`` is used.

This command takes a single argument, which is the hex-encoded public
key of the Grid Manager. The client will have to be re-started once
this change is made.


Example Setup of a New Managed Grid
-----------------------------------

We'll store our Grid Manager configuration on disk, in
``~/grid-manager``. To initialize this directory::

    tahoe grid-manager create --config ~/grid-manager

This example creates an actual grid, but it's all just on one machine
with different "node directories". Usually of course each one would be
on a separate computer.

(If you already have a grid, you can :ref:`skip ahead <skip_ahead>`.)

First of all, create an Introducer. Note that we actually have to run
it briefly before it creates the "Introducer fURL" we want for the
next steps.

    tahoe create-introducer --listen=tcp --port=5555 --location=tcp:localhost:5555 ./introducer
    tahoe -d introducer run
    (Ctrl-C to stop it after a bit)

Next, we attach a couple of storage nodes::

    tahoe create-node --introducer $(cat introducer/private/introducer.furl) --nickname storage0 --webport 6001 --webport 6002 --location tcp:localhost:6003 --port 6003 ./storage0
    tahoe create-node --introducer $(cat introducer/private/introducer.furl) --nickname storage1 --webport 6101 --webport 6102 --location tcp:localhost:6103 --port 6103 ./storage1
    daemonize tahoe -d storage0 run
    daemonize tahoe -d storage1 run

.. _skip_ahead:

We can now ask the Grid Manager to create certificates for our new
storage servers::

    tahoe grid-manager --config ~/grid-manager add-storage --pubkey $(cat storage0/node.pubkey) > storage0.cert
    tahoe grid-manager --config ~/grid-manager add-storage --pubkey $(cat storage1/node.pubkey) > storage1.cert

    # enroll server0 (using file)
    kill $(cat storage0/twistd.pid)
    tahoe -d storage0 admin add-grid-manager-cert --filename storage0.cert
    daemonize tahoe -d storage0 run

    # enroll server1 (using stdin)
    kill $(cat storage1/twistd.pid)
    cat storage1.cert | tahoe -d storage1 admin add-grid-manager-cert
    daemonize tahoe -d storage1 run

Now try adding a new storage server ``storage2``. This client can join
the grid just fine, and announce itself to the Introducer as providing
storage::

    tahoe create-node --introducer $(cat introducer/private/introducer.furl) --nickname storage2 --webport 6301 --webport 6302 --location tcp:localhost:6303 --port 6303 ./storage2
    daemonize tahoe -d storage2 run

At this point any client will upload to any of these three
storage-servers. Make a client "alice" and try!

::

    tahoe create-client --introducer $(cat introducer/private/introducer.furl) --nickname alice --webport 6301 --shares-total=3 --shares-needed=2 --shares-happy=3 ./alice
    daemonize tahoe -d alice run
    tahoe -d alice mkdir  # prints out a dir-cap
    find storage2/storage/shares  # confirm storage2 has a share

Now we want to make Alice only upload to the storage servers that the
grid-manager has given certificates to (``storage0`` and
``storage1``). We need the grid-manager's public key to put in Alice's
configuration::

    kill $(cat alice/twistd.pid)
    tahoe -d alice add-grid-manager --name work-grid $(tahoe grid-manager --config ~/grid-manager show-identity)
    daemonize tahoe -d alice start

DECIDE:
 - should the grid-manager be identified by a certificate? exarkun
   points out: --name seems like the hint of the beginning of a
   use-case for certificates rather than bare public keys?).

Since we made Alice's parameters require 3 storage servers to be
reachable (`--happy=3`), all their uploads should now fail (so `tahoe
mkdir` will fail) because they won't use storage2 and can't "achieve
happiness".

You can check Alice's "Welcome" page (where the list of connected servers
is) at http://localhost:6301/ and should be able to see details about
the "work-grid" Grid Manager that you added. When any Grid Managers
are enabled, each storage-server line will show whether it has a valid
cerifiticate or not (and how much longer it's valid until).
