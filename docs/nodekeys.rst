=======================
Node Keys in Tahoe-LAFS
=======================

"Node Keys" are cryptographic signing/verifying keypairs used to
identify Tahoe-LAFS nodes (client-only and client+server). The private
signing key is stored in NODEDIR/private/node.privkey , and is used to
sign the announcements that are distributed to all nodes by the
Introducer. The public verifying key is used to identify the sending
node from those other systems: it is displayed as a "Node ID" that looks
like "v0-abc234xyz567..", which ends with a long base32-encoded string.

These node keys were introduced in the 1.10 release (April 2013), as
part of ticket #466. In previous releases, announcements were unsigned,
and nodes were identified by their Foolscap "Tub ID" (a somewhat shorter
base32 string, with no "v0-" prefix).

Why Announcements Are Signed
----------------------------

All nodes (both client-only and client+server) publish announcements to
the Introducer, which then relays them to all other nodes. These
announcements contain information about the publishing node's nickname,
how to reach the node, what services it offers, and what version of code
it is running.

The new private node key is used to sign these announcements, preventing
the Introducer from modifying their contents en-route. This will enable
future versions of Tahoe-LAFS to use other forms of introduction
(gossip, multiple introducers) without weakening the security model.

The Node ID is useful as a handle with which to talk about a node. For
example, when clients eventually gain the ability to control which
storage servers they are willing to use (#467), the configuration file
might simply include a list of Node IDs for the approved servers.

TubIDs are currently also suitable for this job, but they depend upon
having a Foolscap connection to the server. Since our goal is to move
away from Foolscap towards a simpler (faster and more portable)
protocol, we want to reduce our dependence upon TubIDs. Node IDs and
Ed25519 signatures can be used for non-Foolscap non-SSL based protocols.

How The Node ID Is Computed
---------------------------

The long-form Node ID is the Ed25519 public verifying key, 256 bits (32
bytes) long, base32-encoded, with a "v0-" prefix appended, and the
trailing "=" padding removed, like so:

  v0-rlj3jnxqv4ee5rtpyngvzbhmhuikjfenjve7j5mzmfcxytwmyf6q

The Node ID is displayed in this long form on the node's front Welcome
page, and on the Introducer's status page. In most other places
(share-placement lists, file health displays), the "short form" is used
instead. This is simply the first 8 characters of the base32 portion,
frequently enclosed in square brackets, like this:

  [rlj3jnxq]

In contrast, old-style TubIDs are usually displayed with just 6 base32
characters.

Version Compatibility, Fallbacks For Old Versions
-------------------------------------------------

Since Tahoe-LAFS 1.9 does not know about signed announcements, 1.10
includes backwards-compatibility code to allow old and new versions to
interoperate. There are three relevant participants: the node publishing
an announcement, the Introducer which relays them, and the node
receiving the (possibly signed) announcement.

When a 1.10 node connects to an old Introducer (version 1.9 or earlier),
it sends downgraded non-signed announcements. It likewise accepts
non-signed announcements from the Introducer. The non-signed
announcements use TubIDs to identify the sending node. The new 1.10
Introducer, when it connects to an old node, downgrades any signed
announcements to non-signed ones before delivery.

As a result, the only way to receive signed announcements is for all
three systems to be running the new 1.10 code. In a grid with a mixture
of old and new nodes, if the Introducer is old, then all nodes will see
unsigned TubIDs. If the Introducer is new, then nodes will see signed
Node IDs whenever possible.

Share Placement
---------------

Tahoe-LAFS uses a "permuted ring" algorithm to decide where to place
shares for any given file. For each potential server, it uses that
server's "permutation seed" to compute a pseudo-random but deterministic
location on a ring, then walks the ring in clockwise order, asking each
server in turn to hold a share until all are placed. When downloading a
file, the servers are accessed in the same order. This minimizes the
number of queries that must be done to download a file, and tolerates
"churn" (nodes being added and removed from the grid) fairly well.

This property depends upon server nodes having a stable permutation
seed. If a server's permutation seed were to change, it would
effectively wind up at a randomly selected place on the permuted ring.
Downloads would still complete, but clients would spend more time asking
other servers before querying the correct one.

In the old 1.9 code, the permutation-seed was always equal to the TubID.
In 1.10, servers include their permutation-seed as part of their
announcement. To improve stability for existing grids, if an old server
(one with existing shares) is upgraded to run the 1.10 codebase, it will
use its old TubID as its permutation-seed. When a new empty server runs
the 1.10 code, it will use its Node ID instead. In both cases, once the
node has picked a permutation-seed, it will continue using that value
forever.

To be specific, when a node wakes up running the 1.10 code, it will look
for a recorded NODEDIR/permutation-seed file, and use its contents if
present. If that file does not exist, it creates it (with the TubID if
it has any shares, otherwise with the Node ID), and uses the contents as
the permutation-seed.

There is one unfortunate consequence of this pattern. If new 1.10 server
is created in a grid that has an old client, or has a new client but an
old Introducer, then that client will see downgraded non-signed
announcements, and thus will first upload shares with the TubID-based
permutation-seed. Later, when the client and/or Introducer is upgraded,
the client will start seeing signed announcements with the NodeID-based
permutation-seed, and will then look for shares in the wrong place. This
will hurt performance in a large grid, but should not affect
reliability. This effect shouldn't even be noticeable in grids for which
the number of servers is close to the "N" shares.total number (e.g.
where num-servers < 3*N). And the as-yet-unimplemented "share
rebalancing" feature should repair the misplacement.

If you wish to avoid this effect, try to upgrade both Introducers and
clients at about the same time. (Upgrading servers does not matter: they
will continue to use the old permutation-seed).
