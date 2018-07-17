.. -*- coding: utf-8 -*-

Random Notes (remove from final)
================================

SDMF notes:

 (actually "H" is a tagged-hash, see hashutil)

 - each SDMF Slot has keypair
   * pubkey = "verification key"
   * privkey = "signature key"
 - "write-key" = first 16 bytes of H(privkey)
 - "read-key" = first 16 bytes of H(write-key)
 - WEM = "write enabler master"
   write-enabler = H(WEM + server-node-id)
    * "write-enabler associated w/ bucket, not shares" (not 100% sure i understand)
 - first 16 bytes of H(privkey) = "write-key" (AES-CTR)
    * "The write key is not used to encrypt anything else, and the
      private key never changes, so we do not need an IV for this
      purpose."
 - first 16 bytes of H(read-key) = "storage-index"
 - privkey stored, encrypted by write-key, in the slot
 - "The actual data is encrypted (using AES in counter mode) with a
   key derived by concatenating the readkey with the IV, then hashing
   the results and truncating to 16 bytes. The IV is randomly
   generated each time the slot is updated, and stored next to the
   encrypted data."
 - "The read-write URI consists of the write key and the verification
   key hash. The read-only URI contains the read key and the
   verification key hash. The verify-only URI contains the storage
   index and the verification key hash."

 - slots are "allocated" using: (size, storage-index, write-enabler)


Pub/Sub for Tahoe-LAFS
======================

There are various use-cases in Tahoe-LAFS where it would be useful to
send messages to particular clients (or for particular topics). A
well-known pattern called "Publish/Subscribe" or "PubSub" would answer
many of these use-cases, but Tahoe-LAFS does not include such
messaging.


Use Cases
---------

Many of these use-cases are special-cases of a general want:

 - there exists a mutable capability;
 - one client updates content in this capability;
 - ..and one or more (different) clients wishes to know when it changes

Thus, a "pub/sub" pattern could be implemented by adding a mechanism
to notify clients that a particular mutable (to which they possess a
valid read-capability) has changed. This would maintain many of the
existing properties of capabilities.


Magic Folders Updates
`````````````````````

Magic Folders in Tahoe currently poll, looking for updates. This
involves periodically downloading some mutable directory-capabilities
and determining if anything has changed (wasting bandwidth). It would
be nice if a client, Alice, could tell other participants in the
magic-folder that she's added some content. More generally, it would
be useful for a client to be able to ask a storage-server to tell it
whenever a particular (mutable) capability has changed. (For the Magic
Folders use-case, this would mean Alice simply makes changes as now,
and relies on the server to update interested clients).


Grid -> Client Communication
````````````````````````````

Allowing the operator(s) of a Grid to communicate with clients is
often desired. For example, notifying users of upcoming maintenance,
new terms or general news. There is currently no mechanism to do this.

An obvious way to implement this right now would be with a mechanism
like Magic Folders uses: a mutable capability containing news which
clients poll (and which the operators can update). This would suffer
the same polling problems described above.


Some Required Definitions
=========================

Slot -- unit of storage for SDMF and MDMF mutable files, referenced by
        a Storage Index.

Storage Index -- first 16 bytes of SHA256d("allmydata_mutable_readkey_to_storage_index_v1" + Read Key)

Read Key -- first 16 bytes of SHA256d("allmydata_mutable_writekey_to_readkey_v1" + Write Key)

Write Key -- first 16 bytes of SH256d("allmydata_mutable_privkey_to_writekey_v1" + Signature Key)

Signature Key -- private part of SDMF keypair

Verification Key -- public part of SDMF keypair

SDMF keypair -- random public/private keypair (RSA 2048-bit)


Proposed Solution
=================

Anything wanting a "broadcast" or "pub-sub" like pattern can implement
it using the following mechanism:

 - the publisher creates a mutable directory, yielding a write-cap, "W"
 - anyone wishing to subscribe is given a read-cap, "R"
   - anyone possessing a valid read-cap "R" (including the publisher)
     can obviously "invite" further clients
   - nobody else may subscribe
 - to add information, the publisher adds a new file to the mutable
   directory
 - old information can be removed (or not) as the publisher wishes

Without further changes to Tahoe-LAFS, this can already be achieved:
clients can "poll" the read-capability and determine if anything new
exists.


Improvements Over Polling
-------------------------

The above mechanism would scale somewhat poorly as the number of
"broadcast" capabilities increased. It would be better if the
storage-server could *tell* clients when a mutable Slot has changed.

Storage servers implement a new feature, based on WebSockets. See also
:ref:`http-storage-node-protocol` with which this protocol aims to be
compatible. A single WebSocket endpoint exists:

 - <server>/v1/mutable_updates

After connecting to this endpoint, a client may send any number of
messages (encoded using JSON) asking for updates to mutable
Slots:

    {
        "tahoe-mutable-notification-version": 1,
        "subscribe": [
            "storage-index-0",
            "storage-index-1",
        }
    }

..where "storage-index-0" corresponds to an actual Storage Index of an
existing mutable file. A client computes this from "R" (a read
capability). The client must keep a mapping of `Storage Index ->
read-capability` so it can match subsequent notifications.

The server replies with a message like:

    {
        "tahoe-mutable-notification-version": 1,
        "status": {
            "storage-index-0": true,
            "storage-index-1": "some kind of error message"
        }
    }

That is, a "status" dict containing each requested Slot's
storage-index with either "true" if the subscribe was successful or a
string with an error-message if it was not possible to subscribe
(e.g. the capability wasn't a mutable one, or wasn't found on this
server). Every storage-index requested in the initial list will have
an entry in this dict. The client should remember a map of all
*successful* subscriptions back to the corresponding read-capability.

TODO:

 - for a given Slot, does a client just ask every storage-server for
   the corresponding Storage Index? (are there information-disclosure
   issues here?)

       meejah: a storage-server can learn which Slots a client is
       interested in, but it learns this information with the "polling"
       mechanism too -- so i think the answer is, "to storage-server
       learns the same information as with the polling method"

 - do we want a more-structured response for errors, e.g. with
   machine-readable "code" and a human-readable "message"?

All subscriptions for a particular client shall be removed when that
client's WebSocket connection goes away.

The server will send a single message for each update:

    {
        "tahoe-mutable-notification-version": 1,
        "update": "storage-index-1"
    }

**Note:** A client must track mappings of Storage Indexes to read-
capabilities because only the client has the read-capability. There's
no easy way to go from a Storage Index back to a read-capability (the
Storage Index is just a hash from part of the read-capablity).
