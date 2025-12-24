.. -*- coding: utf-8 -*-

================================
Causally-consistent data storage
================================

This document outlines how a causally-consistent data structure may be
implemented using immutable resources written to Tahoe-LAFS and a secure
communication channel (that may be facilitated by per-writer-node writable
directories).

Further it describes a conflict-free replicated data type that may be used for
encoding and reconstructing concurrent changes to directory structure and
extension mechanism for different CRDTs to be added either to resolve file
content or as alternative directory implementation.

Motivation
----------

Currently Tahoe-LAFS doesn't guarantee any consistency on writes to mutable
files or directories by multiple writer nodes. This is an obstacle to
providing reliable collaborative environment given each participant may want
to run their own node.

Representing dependency graph
-----------------------------

Causal consistency model is a stronger form of eventual consistency that
guarantees ordering between modifications that may have causal relationship.
That is, writes that came after reading of a particular state will always be
observed by all nodes as happening after a given state.

This model has the advantage of being very easily representable as a
directed graph of nodes (encoding events) and vertices (encoding the
"happened-after" relationship). This is the same model underlying
commits and branches in most Distributed Version Control Systems
(DVCSes) such as git.

This in turn has fairly natural encoding using immutable files and directories
in Tahoe-LAFS so the full log of events can be stored in the grid and
de-duplicated easily, or stored at each node for full availability.

For each data update (commit) we need to store the difference to original state
(such as the operation performed on a CRDT) and the list of previous states it
has seen.

I propose a format that represents it as an immutable directory structure, such
that the data difference itself is plain file and the rest are directory
references.
For example::

   Directory({
      'parents': Directory({
         '0': ...,
      }),
      'data': File(...),
   })

would represent a single update with a single parent, the directory
entry ``0`` referring to another such update recursively until the
initial update (with no parents) is encountered.

This way a full modification graph will be referencable by just holding the
reference to the set of latest updates and periodic renewal would prevent it
from being garbage collected.

Pruning and compaction of history is currently not addressed by this
proposal and would likely benefit from a different encoding scheme
that does not transitively reference the whole history using directory
references.


Rationale for consistency level chosen
--------------------------------------

There are many ways in which multi-writer concurrency can be handled in a
distributed system.

Choosing the correct one comes down to a trade-off between availability under
partition and consistency level offered.

One of the goals of Tahoe-LAFS is to provide a storage grid that can
retain data that is accessible even when large amount of nodes go down
and is able to work with little to no central coordination.

Working with data storage should come with as few surprises as
possible and the behaviour should be predictable to humans.
This requires an easy-to-understand model.

The causal consistency model offers availability under partition and
generates partial order so each node can follow causal order of operations,
resulting in fairly predictable behaviour. It can't prevent concurrent updates to
a single entry, but overlaying such a model with a convergent function to resolve
conflicts
(sometimes called *causal+ consistency*)
and requiring that there are no rollbacks to past states
(aka *real time causal consistency*, the strongest highly-available model)
allows us to arrive at the same result at each participating node without
losing any update.

Causal model is *sticky-available* meaning it requires each client to go
through only one node to access the data for both reads and writes. This is
expected to be true under Tahoe-LAFS when running a node on each client to
preserve the end-to-end encryption guarantees.


Convergence for directory structures
------------------------------------

To handle conflicting updates without requiring administrative intervention we
need to define a merge function above the states
(resulting in *convergent replicated data type*)
or define the update function to be commutative
(resulting in *commutative replicated data type*).
The distinction between the two is mostly formal, as emulation can be provided
both ways.

For representing directory contents an *observed-removed map*
(extension of OR-set)
may be used.
The observed-removed set and map data structures use unique tags when adding
new entries that is used for any subsequent operation on them.
This avoids conflicting concurrent writes to the same key being conflated with each
other and allows us to implement deterministic renaming scheme to handle such
collisions without data loss (as opposed to a "last write wins" type of structure).
It also allows items to be added and removed an arbitrary amount of times as
opposed to grow-only or tombstone-based sets and maps.

The core operations on OR-map are::

   add(tag, key, value)
   remove(tag)

This alone ought to be enough to encode arbitrary changes to the map structure
(such as renames or updates)
given that the update operations may be batched as units to be performed at
once (transactions).

For directory operation the ``tag`` should be invisible, the ``key`` would be the
name of directory entry and ``value`` would be a reference to *immutable* data
(such as directory or file)
or another CRDT
(convergent subdirectory or, provided those will be defined, convergent file).

The data update structure is identical with OR-set where the elements of the
set are two-element tuples.
The crucial distinction that we want to be able to alter the ``key`` part on
conflicting ``add`` operations with different tags.
There are several designs we may consider.

Firstly there is a question on how to decide which of two concurrent updates
should be renamed.
Since the process needs to be deterministic we need a simple algorithm that
uses just the operation data.
As such, simple comparison of the two ``tag`` value can be used, which brings
us to question of how to generate such unique tags.
I present following options:

1) random fixed-length byte string
2) timestamp + random string (older gets renamed)
3) timestamp + random string (newer gets renamed)

The timestamp would be encoded some reasonably universal format
(eg. tai64n or UNIX-epoch based timestamps)
and would give us predictable behaviour given the nodes operating on the data
have access to reasonably accurate clock.

The other question is persistence of such renaming.
Consider following sequence of operations::

   1: add("t1", "parrot", "is no more")
   2: add("t2", "parrot", "ceased to be")
   3: remove("t2")

Given that under our renaming criteria ``"t1"`` would be the one to get
renamed, we could see following behaviours:

If the implementation treated the data structure as OR-set with additional
name-mapping layer then we would see the original ``"parrot"`` item to be
renamed to (eg.) ``"parrot.renamed.t1"`` after completion of step 2 and then
returning back to name of ``"parrot"`` after completion of step 3.

Conversely if the renaming is made persistent, then the entry will be visible
under the new name of ``"parrot.renamed.t1"`` even if steps 2 and 3 are
performed as atomic operation with the value under tag ``"t2"`` never being
visible.


Providing consistency over data structure hierarchy
---------------------------------------------------

The consistency of directory structure is of high importance for addressing
individual pieces of data.
That means that when several data items are to be updated atomically
(eg. if we wanted atomic rename/move across directory boundaries, like most
UNIX filesystems support)
we need to make sure that those updates are to be distributed as one single
update with causality relationship spanning the whole hierarchy.
On the other hand we don't want to give up the ability to create fine-grained
attenuated capabilities for viewing or updating parts of the hierarchy.

One possible way to address that is to do what snapshotting copy-on-write
filesystems generally do: recursively create new modified copy of each parent
directory for each data update.
This should be easily encodable by making the ``value`` field of the directory
CRDT a reference to specific data state
(which in turn is a set of read-only directory capabilities)
as opposed to referring to the data structure itself.

This would have the disadvantage of significantly higher data overhead consumed
by old and redundant metadata.
It should also be noted that if specific subdirectories are exported as
capabilities and then made into a filesystem hierarchy again by adding them
to a different directory, the same consistency guarantees will not apply.

Such approach would need modification to the directory CRDT; either by adding
an ``update(tag, value)`` operation or by changing conflict resolution
semantics for compatible types to not perform renaming but instead merge their
values. The latter would allow for a mechanism similar to union or overlay
filesystems if propagation of updates was restricted to one direction, with
some caveats about absence of propagation of renames.

.. note:: TODO: To be extended

Referring to convergent data and communicating updates
------------------------------------------------------

For nodes to be able to synchronize with each other there needs to be a
mechanism for broadcasting the updates among the participating nodes.
The way we may want to refer to the data structure may depend on which
communication protocol we choose to use, but likely we will want to layer a
capability system for attenuated revokable usage.
Specifically, here are some capabilities to consider:

* Create bidirectional link between nodes for purpose of updating data
  structure both ways.
* Create unidirectional link that allows node to observe future changes but not
  update the data itself.
* Get just the current state with history, but no future updates.

Another need for communication would come from implementation of history
compaction and pruning, which may be necessary for keeping the metadata
overhead to a reasonable level.
In that case we would need to know which updates did each node already read and
merge into its internal state, so they don't have to be kept from garbage
collection anymore.

Ideally we would want an end-to-end encrypted publish-subscribe protocol
between the nodes, but if we are content with high latency of polling we may
use one mutable directory or file per node and avoid the need for adding any
additional or external protocol.
Notably the requirements of end-to-end encrypted publish-subscribe system are
those of secure group messaging software and despite recent surge in demand of
such systems there is still significant lack of reliable open-source implementations.

From the options that seem the most tenable to me there is:

* Just using mutable capabilities and polling.
* Creating a custom capability-based pub/sub protocol inside Tahoe-LAFS
  (Worthy goal, but out of scope of this document).
* OMEMO running over XMPP.
* Using regular authenticated encryption channel and connecting each
  participating node to each other, not using any way to multicast the
  messages.

Durability, latency and data locality
-------------------------------------

Literature about CRDTs and consistency of distributed systems generally assumes
that each node retains its own copy of all relevant data.
This is not true in Tahoe-LAFS and sometimes not even possible for the
workloads it's meant for, but we may want to consider this for the special case
of filesystem metadata.

Tahoe-LAFS's concept of durable uploads (servers of happiness) is not
inherently available.
Thus any partitions smaller than this number cannot reach full availability
offered by the above model.
Any upload into such partition would likewise fail to be considered durable
though, so it's of little concern that the following metadata updates wouldn't
upload either.

Causal model maintains consistency by delaying updates until all their
dependencies have been processed.
Causal relationships can not be sharded and generally span across all nodes'
data.
This creates tradeoff where
(for systems which maintain separate copy of relevant data per node)
one has to choose between write throughput and visibility latency.
Specifically to handle updates at low latency the rate of the writes may not
exceed the capabilities of the slowest node and demands on each node grow
linearly with number of nodes writing at a constant pace
(that is, the total processing requirements grow quadratically with the amount
of participating nodes).

This document assumes unbounded visibility latency and does not try to address
write saturation.
It is expected that the amount of nodes participating in maintaining each
consistent dataset will be fairly small - only the ones trusted to decrypt the
data by the user.
It is also expected that the metadata modification will come in bursts but the
total amount of metadata updates will be dwarfed by the actual data.

.. note:: TODO: To be extended

..  vim:  sts=3 sw=3 et tw=79
