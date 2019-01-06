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
That is, writes that came after read of particular state will always be
observed by all nodes as happening after given state.
This model has the advantage of being very easily representable as a directed
graph of nodes encoding events and vertices the happened-after relationship,
which is the same model underlying commits and branches in most DVCSes such as
git.
This in turn has fairly natural encoding using immutable files and directories
in Tahoe-LAFS so the full log of events can be stored in the grid and
deduplicated easily, or stored at each node for full availability.
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

would represent single update with a single parent, the directory entry `0`
referring to another such update recursively until initial update with no
parents is encountered.
This way full modification graph will be referencable by just holding the
reference to the set of latest updates and periodic renewal would prevent it
from being garbage collected.

Pruning and compaction of history is possible but out of scope of this proposal.

Rationale for consistency level chosen
--------------------------------------

There are many ways in which multi-writer concurrency can be handled in a
distributed system.
Choosing the correct one comes down to trade-off between availability under
partition and consistency level offered.
One of the goals of Tahoe-LAFS is to provide storage grid that can retain data
that is accessible even when large amount of nodes go down and is able to work
with little to no central coordination.
Working with data store though should come with as little surprises as possible
and the behaviour should be predictable to humans, requiring easy to understand
model.

The causal consistency model offers for availability under partition and
generates partial order so each node can follow causal order of operations,
resulting fairly predictable behaviour. It can't prevent concurrent updates to
single entry, but overlaying such model with convergent function to resolve
conflicts
(sometimes called *causal+ consistency*)
and requiring that there are no rollbacks to past states
(aka *real time causal consistency*, the strongest higly-available model)
allows us to arrive at the same result at each participating node without
loosing any update.

Causal model is *sticky-available* meaning it requires each client to go
through only one node to access the data for both reads and writes. This is
expected to be true under Tahoe-LAFS as running a node on each client to
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

.. todo:: To be extended

Providing consistency over data structure hierarchy
---------------------------------------------------

The directory structure is of high importance for addressing individual pieces
of data.

.. todo:: To be written

Communicating data updates
--------------------------

.. todo:: To be written

..  vim:  sts=3 sw=3 et tw=79
