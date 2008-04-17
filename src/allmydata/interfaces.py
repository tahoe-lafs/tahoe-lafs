
from zope.interface import Interface
from foolscap.schema import StringConstraint, ListOf, TupleOf, SetOf, DictOf, \
     ChoiceOf, IntegerConstraint
from foolscap import RemoteInterface, Referenceable

HASH_SIZE=32

Hash = StringConstraint(maxLength=HASH_SIZE,
                        minLength=HASH_SIZE)# binary format 32-byte SHA256 hash
Nodeid = StringConstraint(maxLength=20,
                          minLength=20) # binary format 20-byte SHA1 hash
FURL = StringConstraint(1000)
StorageIndex = StringConstraint(16)
URI = StringConstraint(300) # kind of arbitrary

MAX_BUCKETS = 200  # per peer

ShareData = StringConstraint(None)
URIExtensionData = StringConstraint(1000)
Number = IntegerConstraint(8) # 2**(8*8) == 16EiB ~= 18e18 ~= 18 exabytes
Offset = Number
ReadSize = int # the 'int' constraint is 2**31 == 2Gib
LeaseRenewSecret = Hash # used to protect bucket lease renewal requests
LeaseCancelSecret = Hash # used to protect bucket lease cancellation requests

# Announcements are (FURL, service_name, remoteinterface_name,
#                    nickname, my_version, oldest_supported)
#  the (FURL, service_name, remoteinterface_name) refer to the service being
#  announced. The (nickname, my_version, oldest_supported) refer to the
#  client as a whole. The my_version/oldest_supported strings can be parsed
#  by an allmydata.util.version.Version instance, and then compared. The
#  first goal is to make sure that nodes are not confused by speaking to an
#  incompatible peer. The second goal is to enable the development of
#  backwards-compatibility code.

Announcement = TupleOf(FURL, str, str,
                       str, str, str)

class RIIntroducerSubscriberClient(RemoteInterface):
    __remote_name__ = "RIIntroducerSubscriberClient.tahoe.allmydata.com"

    def announce(announcements=SetOf(Announcement)):
        """I accept announcements from the publisher."""
        return None

    def set_encoding_parameters(parameters=(int, int, int)):
        """Advise the client of the recommended k-of-n encoding parameters
        for this grid. 'parameters' is a tuple of (k, desired, n), where 'n'
        is the total number of shares that will be created for any given
        file, while 'k' is the number of shares that must be retrieved to
        recover that file, and 'desired' is the minimum number of shares that
        must be placed before the uploader will consider its job a success.
        n/k is the expansion ratio, while k determines the robustness.

        Introducers should specify 'n' according to the expected size of the
        grid (there is no point to producing more shares than there are
        peers), and k according to the desired reliability-vs-overhead goals.

        Note that setting k=1 is equivalent to simple replication.
        """
        return None

# When Foolscap can handle multiple interfaces (Foolscap#17), the
# full-powered introducer will implement both RIIntroducerPublisher and
# RIIntroducerSubscriberService. Until then, we define
# RIIntroducerPublisherAndSubscriberService as a combination of the two, and
# make everybody use that.

class RIIntroducerPublisher(RemoteInterface):
    """To publish a service to the world, connect to me and give me your
    announcement message. I will deliver a copy to all connected subscribers."""
    __remote_name__ = "RIIntroducerPublisher.tahoe.allmydata.com"

    def publish(announcement=Announcement):
        # canary?
        return None

class RIIntroducerSubscriberService(RemoteInterface):
    __remote_name__ = "RIIntroducerSubscriberService.tahoe.allmydata.com"

    def subscribe(subscriber=RIIntroducerSubscriberClient, service_name=str):
        """Give me a subscriber reference, and I will call its new_peers()
        method will any announcements that match the desired service name. I
        will ignore duplicate subscriptions.
        """
        return None

class RIIntroducerPublisherAndSubscriberService(RemoteInterface):
    __remote_name__ = "RIIntroducerPublisherAndSubscriberService.tahoe.allmydata.com"
    def publish(announcement=Announcement):
        return None
    def subscribe(subscriber=RIIntroducerSubscriberClient, service_name=str):
        return None

class IIntroducerClient(Interface):
    """I provide service introduction facilities for a node. I help nodes
    publish their services to the rest of the world, and I help them learn
    about services available on other nodes."""

    def publish(furl, service_name, remoteinterface_name):
        """Once you call this, I will tell the world that the Referenceable
        available at FURL is available to provide a service named
        SERVICE_NAME. The precise definition of the service being provided is
        identified by the Foolscap 'remote interface name' in the last
        parameter: this is supposed to be a globally-unique string that
        identifies the RemoteInterface that is implemented."""

    def subscribe_to(service_name):
        """Call this if you will eventually want to use services with the
        given SERVICE_NAME. This will prompt me to subscribe to announcements
        of those services. You can pick up the announcements later by calling
        get_all_connections_for() or get_permuted_peers().
        """

    def get_all_connections():
        """Return a frozenset of (nodeid, service_name, rref) tuples, one for
        each active connection we've established to a remote service. This is
        mostly useful for unit tests that need to wait until a certain number
        of connections have been made."""

    def get_all_connectors():
        """Return a dict that maps from (nodeid, service_name) to a
        RemoteServiceConnector instance for all services that we are actively
        trying to connect to. Each RemoteServiceConnector has the following
        public attributes::

          service_name: the type of service provided, like 'storage'
          announcement_time: when we first heard about this service
          last_connect_time: when we last established a connection
          last_loss_time: when we last lost a connection

          version: the peer's version, from the most recent connection
          oldest_supported: the peer's oldest supported version, same

          rref: the RemoteReference, if connected, otherwise None
          remote_host: the IAddress, if connected, otherwise None

        This method is intended for monitoring interfaces, such as a web page
        which describes connecting and connected peers.
        """

    def get_all_peerids():
        """Return a frozenset of all peerids to whom we have a connection (to
        one or more services) established. Mostly useful for unit tests."""

    def get_all_connections_for(service_name):
        """Return a frozenset of (nodeid, service_name, rref) tuples, one
        for each active connection that provides the given SERVICE_NAME."""

    def get_permuted_peers(service_name, key):
        """Returns an ordered list of (peerid, rref) tuples, selecting from
        the connections that provide SERVICE_NAME, using a hash-based
        permutation keyed by KEY. This randomizes the service list in a
        repeatable way, to distribute load over many peers.
        """

    def connected_to_introducer():
        """Returns a boolean, True if we are currently connected to the
        introducer, False if not."""

class RIStubClient(RemoteInterface):
    """Each client publishes a service announcement for a dummy object called
    the StubClient. This object doesn't actually offer any services, but the
    announcement helps the Introducer keep track of which clients are
    subscribed (so the grid admin can keep track of things like the size of
    the grid and the client versions in use. This is the (empty)
    RemoteInterface for the StubClient."""

class RIBucketWriter(RemoteInterface):
    def write(offset=Offset, data=ShareData):
        return None

    def close():
        """
        If the data that has been written is incomplete or inconsistent then
        the server will throw the data away, else it will store it for future
        retrieval.
        """
        return None

    def abort():
        """Abandon all the data that has been written.
        """
        return None

class RIBucketReader(RemoteInterface):
    def read(offset=Offset, length=ReadSize):
        # ShareData is limited to 1MiB, so we don't need length= to be any
        # larger than that. Large files must be read in pieces.
        return ShareData

TestVector = ListOf(TupleOf(Offset, ReadSize, str, str))
# elements are (offset, length, operator, specimen)
# operator is one of "lt, le, eq, ne, ge, gt"
# nop always passes and is used to fetch data while writing.
# you should use length==len(specimen) for everything except nop
DataVector = ListOf(TupleOf(Offset, ShareData))
# (offset, data). This limits us to 30 writes of 1MiB each per call
TestAndWriteVectorsForShares = DictOf(int,
                                      TupleOf(TestVector,
                                              DataVector,
                                              ChoiceOf(None, Offset), # new_length
                                              ))
ReadVector = ListOf(TupleOf(Offset, ReadSize))
ReadData = ListOf(ShareData)
# returns data[offset:offset+length] for each element of TestVector

class RIStorageServer(RemoteInterface):
    __remote_name__ = "RIStorageServer.tahoe.allmydata.com"

    def get_versions():
        """Return a tuple of (my_version, oldest_supported) strings.
        Each string can be parsed by an allmydata.util.version.Version
        instance, and then compared. The first goal is to make sure that
        nodes are not confused by speaking to an incompatible peer. The
        second goal is to enable the development of backwards-compatibility
        code.

        This method is likely to change in incompatible ways until we get the
        whole compatibility scheme nailed down.
        """
        return TupleOf(str, str)

    def allocate_buckets(storage_index=StorageIndex,
                         renew_secret=LeaseRenewSecret,
                         cancel_secret=LeaseCancelSecret,
                         sharenums=SetOf(int, maxLength=MAX_BUCKETS),
                         allocated_size=Offset, canary=Referenceable):
        """
        @param storage_index: the index of the bucket to be created or
                              increfed.
        @param sharenums: these are the share numbers (probably between 0 and
                          99) that the sender is proposing to store on this
                          server.
        @param renew_secret: This is the secret used to protect bucket refresh
                             This secret is generated by the client and
                             stored for later comparison by the server. Each
                             server is given a different secret.
        @param cancel_secret: Like renew_secret, but protects bucket decref.
        @param canary: If the canary is lost before close(), the bucket is
                       deleted.
        @return: tuple of (alreadygot, allocated), where alreadygot is what we
                 already have and is what we hereby agree to accept. New
                 leases are added for shares in both lists.
        """
        return TupleOf(SetOf(int, maxLength=MAX_BUCKETS),
                       DictOf(int, RIBucketWriter, maxKeys=MAX_BUCKETS))

    def renew_lease(storage_index=StorageIndex, renew_secret=LeaseRenewSecret):
        """
        Renew the lease on a given bucket. Some networks will use this, some
        will not.
        """

    def cancel_lease(storage_index=StorageIndex,
                     cancel_secret=LeaseCancelSecret):
        """
        Cancel the lease on a given bucket. If this was the last lease on the
        bucket, the bucket will be deleted.
        """

    def get_buckets(storage_index=StorageIndex):
        return DictOf(int, RIBucketReader, maxKeys=MAX_BUCKETS)



    def slot_readv(storage_index=StorageIndex,
                   shares=ListOf(int), readv=ReadVector):
        """Read a vector from the numbered shares associated with the given
        storage index. An empty shares list means to return data from all
        known shares. Returns a dictionary with one key per share."""
        return DictOf(int, ReadData) # shnum -> results

    def slot_testv_and_readv_and_writev(storage_index=StorageIndex,
                                        secrets=TupleOf(Hash, Hash, Hash),
                                        tw_vectors=TestAndWriteVectorsForShares,
                                        r_vector=ReadVector,
                                        ):
        """General-purpose test-and-set operation for mutable slots. Perform
        a bunch of comparisons against the existing shares. If they all pass,
        then apply a bunch of write vectors to those shares. Then use the
        read vectors to extract data from all the shares and return the data.

        This method is, um, large. The goal is to allow clients to update all
        the shares associated with a mutable file in a single round trip.

        @param storage_index: the index of the bucket to be created or
                              increfed.
        @param write_enabler: a secret that is stored along with the slot.
                              Writes are accepted from any caller who can
                              present the matching secret. A different secret
                              should be used for each slot*server pair.
        @param renew_secret: This is the secret used to protect bucket refresh
                             This secret is generated by the client and
                             stored for later comparison by the server. Each
                             server is given a different secret.
        @param cancel_secret: Like renew_secret, but protects bucket decref.

        The 'secrets' argument is a tuple of (write_enabler, renew_secret,
        cancel_secret). The first is required to perform any write. The
        latter two are used when allocating new shares. To simply acquire a
        new lease on existing shares, use an empty testv and an empty writev.

        Each share can have a separate test vector (i.e. a list of
        comparisons to perform). If all vectors for all shares pass, then all
        writes for all shares are recorded. Each comparison is a 4-tuple of
        (offset, length, operator, specimen), which effectively does a bool(
        (read(offset, length)) OPERATOR specimen ) and only performs the
        write if all these evaluate to True. Basic test-and-set uses 'eq'.
        Write-if-newer uses a seqnum and (offset, length, 'lt', specimen).
        Write-if-same-or-newer uses 'le'.

        Reads from the end of the container are truncated, and missing shares
        behave like empty ones, so to assert that a share doesn't exist (for
        use when creating a new share), use (0, 1, 'eq', '').

        The write vector will be applied to the given share, expanding it if
        necessary. A write vector applied to a share number that did not
        exist previously will cause that share to be created.

        Each write vector is accompanied by a 'new_length' argument. If
        new_length is not None, use it to set the size of the container. This
        can be used to pre-allocate space for a series of upcoming writes, or
        truncate existing data. If the container is growing, new_length will
        be applied before datav. If the container is shrinking, it will be
        applied afterwards.

        The read vector is used to extract data from all known shares,
        *before* any writes have been applied. The same vector is used for
        all shares. This captures the state that was tested by the test
        vector.

        This method returns two values: a boolean and a dict. The boolean is
        True if the write vectors were applied, False if not. The dict is
        keyed by share number, and each value contains a list of strings, one
        for each element of the read vector.

        If the write_enabler is wrong, this will raise BadWriteEnablerError.
        To enable share migration, the exception will have the nodeid used
        for the old write enabler embedded in it, in the following string::

         The write enabler was recorded by nodeid '%s'.

        Note that the nodeid here is encoded using the same base32 encoding
        used by Foolscap and allmydata.util.idlib.nodeid_b2a().

        """
        return TupleOf(bool, DictOf(int, ReadData))

class IStorageBucketWriter(Interface):
    def put_block(segmentnum=int, data=ShareData):
        """@param data: For most segments, this data will be 'blocksize'
        bytes in length. The last segment might be shorter.
        @return: a Deferred that fires (with None) when the operation completes
        """

    def put_plaintext_hashes(hashes=ListOf(Hash, maxLength=2**20)):
        """
        @return: a Deferred that fires (with None) when the operation completes
        """

    def put_crypttext_hashes(hashes=ListOf(Hash, maxLength=2**20)):
        """
        @return: a Deferred that fires (with None) when the operation completes
        """

    def put_block_hashes(blockhashes=ListOf(Hash, maxLength=2**20)):
        """
        @return: a Deferred that fires (with None) when the operation completes
        """

    def put_share_hashes(sharehashes=ListOf(TupleOf(int, Hash),
                                            maxLength=2**20)):
        """
        @return: a Deferred that fires (with None) when the operation completes
        """

    def put_uri_extension(data=URIExtensionData):
        """This block of data contains integrity-checking information (hashes
        of plaintext, crypttext, and shares), as well as encoding parameters
        that are necessary to recover the data. This is a serialized dict
        mapping strings to other strings. The hash of this data is kept in
        the URI and verified before any of the data is used. All buckets for
        a given file contain identical copies of this data.

        The serialization format is specified with the following pseudocode:
        for k in sorted(dict.keys()):
            assert re.match(r'^[a-zA-Z_\-]+$', k)
            write(k + ':' + netstring(dict[k]))

        @return: a Deferred that fires (with None) when the operation completes
        """

    def close():
        """Finish writing and close the bucket. The share is not finalized
        until this method is called: if the uploading client disconnects
        before calling close(), the partially-written share will be
        discarded.

        @return: a Deferred that fires (with None) when the operation completes
        """

class IStorageBucketReader(Interface):

    def get_block(blocknum=int):
        """Most blocks will be the same size. The last block might be shorter
        than the others.

        @return: ShareData
        """

    def get_plaintext_hashes():
        """
        @return: ListOf(Hash, maxLength=2**20)
        """

    def get_crypttext_hashes():
        """
        @return: ListOf(Hash, maxLength=2**20)
        """

    def get_block_hashes():
        """
        @return: ListOf(Hash, maxLength=2**20)
        """

    def get_share_hashes():
        """
        @return: ListOf(TupleOf(int, Hash), maxLength=2**20)
        """

    def get_uri_extension():
        """
        @return: URIExtensionData
        """



# hm, we need a solution for forward references in schemas
from foolscap.schema import Any

FileNode_ = Any() # TODO: foolscap needs constraints on copyables
DirectoryNode_ = Any() # TODO: same
AnyNode_ = ChoiceOf(FileNode_, DirectoryNode_)
EncryptedThing = str

class IURI(Interface):
    def init_from_string(uri):
        """Accept a string (as created by my to_string() method) and populate
        this instance with its data. I am not normally called directly,
        please use the module-level uri.from_string() function to convert
        arbitrary URI strings into IURI-providing instances."""

    def is_readonly():
        """Return False if this URI be used to modify the data. Return True
        if this URI cannot be used to modify the data."""

    def is_mutable():
        """Return True if the data can be modified by *somebody* (perhaps
        someone who has a more powerful URI than this one)."""

    def get_readonly():
        """Return another IURI instance, which represents a read-only form of
        this one. If is_readonly() is True, this returns self."""

    def get_verifier():
        """Return an instance that provides IVerifierURI, which can be used
        to check on the availability of the file or directory, without
        providing enough capabilities to actually read or modify the
        contents. This may return None if the file does not need checking or
        verification (e.g. LIT URIs).
        """

    def to_string():
        """Return a string of printable ASCII characters, suitable for
        passing into init_from_string."""

class IVerifierURI(Interface):
    def init_from_string(uri):
        """Accept a string (as created by my to_string() method) and populate
        this instance with its data. I am not normally called directly,
        please use the module-level uri.from_string() function to convert
        arbitrary URI strings into IURI-providing instances."""

    def to_string():
        """Return a string of printable ASCII characters, suitable for
        passing into init_from_string."""

class IDirnodeURI(Interface):
    """I am a URI which represents a dirnode."""


class IFileURI(Interface):
    """I am a URI which represents a filenode."""
    def get_size():
        """Return the length (in bytes) of the file that I represent."""

class IMutableFileURI(Interface):
    """I am a URI which represents a mutable filenode."""
class INewDirectoryURI(Interface):
    pass
class IReadonlyNewDirectoryURI(Interface):
    pass


class IFilesystemNode(Interface):
    def get_uri():
        """
        Return the URI that can be used by others to get access to this
        node. If this node is read-only, the URI will only offer read-only
        access. If this node is read-write, the URI will offer read-write
        access.

        If you have read-write access to a node and wish to share merely
        read-only access with others, use get_readonly_uri().
        """

    def get_readonly_uri():
        """Return the directory URI that can be used by others to get
        read-only access to this directory node. The result is a read-only
        URI, regardless of whether this dirnode is read-only or read-write.

        If you have merely read-only access to this dirnode,
        get_readonly_uri() will return the same thing as get_uri().
        """

    def get_verifier():
        """Return an IVerifierURI instance that represents the
        'verifiy/refresh capability' for this node. The holder of this
        capability will be able to renew the lease for this node, protecting
        it from garbage-collection. They will also be able to ask a server if
        it holds a share for the file or directory.
        """

    def check():
        """Perform a file check. See IChecker.check for details."""

    def is_readonly():
        """Return True if this reference provides mutable access to the given
        file or directory (i.e. if you can modify it), or False if not. Note
        that even if this reference is read-only, someone else may hold a
        read-write reference to it."""

    def is_mutable():
        """Return True if this file or directory is mutable (by *somebody*,
        not necessarily you), False if it is is immutable. Note that a file
        might be mutable overall, but your reference to it might be
        read-only. On the other hand, all references to an immutable file
        will be read-only; there are no read-write references to an immutable
        file.
        """

class IMutableFilesystemNode(IFilesystemNode):
    pass

class IFileNode(IFilesystemNode):
    def download(target):
        """Download the file's contents to a given IDownloadTarget"""

    def download_to_data():
        """Download the file's contents. Return a Deferred that fires
        with those contents."""

    def get_size():
        """Return the length (in bytes) of the data this node represents."""

class IMutableFileNode(IFileNode, IMutableFilesystemNode):
    def download_to_data():
        """Download the file's contents. Return a Deferred that fires with
        those contents. If there are multiple retrievable versions in the
        grid (because you failed to avoid simultaneous writes, see
        docs/mutable.txt), this will return the first version that it can
        reconstruct, and will silently ignore the others. In the future, a
        more advanced API will signal and provide access to the multiple
        heads."""

    def update(newdata):
        """Attempt to replace the old contents with the new data.

        download_to_data() must have been called before calling update().

        Returns a Deferred. If the Deferred fires successfully, the update
        appeared to succeed. However, another writer (who read before your
        changes were published) might still clobber your changes: they will
        discover a problem but you will not. (see ticket #347 for details).

        If the mutable file has been changed (by some other writer) since the
        last call to download_to_data(), this will raise
        UncoordinatedWriteError and the file will be left in an inconsistent
        state (possibly the version you provided, possibly the old version,
        possibly somebody else's version, and possibly a mix of shares from
        all of these). The recommended response to UncoordinatedWriteError is
        to either return it to the caller (since they failed to coordinate
        their writes), or to do a new download_to_data() / modify-data /
        update() loop.

        update() is appropriate to use in a read-modify-write sequence, such
        as a directory modification.
        """

    def overwrite(newdata):
        """Attempt to replace the old contents with the new data.

        Unlike update(), overwrite() does not require a previous call to
        download_to_data(). It will unconditionally replace the old contents
        with new data.

        overwrite() is implemented by doing download_to_data() and update()
        in rapid succession, so there remains a (smaller) possibility of
        UncoordinatedWriteError. A future version will remove the full
        download_to_data step, making this faster than update().

        overwrite() is only appropriate to use when the new contents of the
        mutable file are completely unrelated to the old ones, and you do not
        care about other clients changes to the file.
        """

    def get_writekey():
        """Return this filenode's writekey, or None if the node does not have
        write-capability. This may be used to assist with data structures
        that need to make certain data available only to writers, such as the
        read-write child caps in dirnodes. The recommended process is to have
        reader-visible data be submitted to the filenode in the clear (where
        it will be encrypted by the filenode using the readkey), but encrypt
        writer-visible data using this writekey.
        """

class IDirectoryNode(IMutableFilesystemNode):
    """I represent a name-to-child mapping, holding the tahoe equivalent of a
    directory. All child names are unicode strings, and all children are some
    sort of IFilesystemNode (either files or subdirectories).
    """

    def get_uri():
        """
        The dirnode ('1') URI returned by this method can be used in
        set_uri() on a different directory ('2') to 'mount' a reference to
        this directory ('1') under the other ('2'). This URI is just a
        string, so it can be passed around through email or other out-of-band
        protocol.
        """

    def get_readonly_uri():
        """
        The dirnode ('1') URI returned by this method can be used in
        set_uri() on a different directory ('2') to 'mount' a reference to
        this directory ('1') under the other ('2'). This URI is just a
        string, so it can be passed around through email or other out-of-band
        protocol.
        """

    def list():
        """I return a Deferred that fires with a dictionary mapping child
        name (a unicode string) to (node, metadata_dict) tuples, in which
        'node' is either an IFileNode or IDirectoryNode, and 'metadata_dict'
        is a dictionary of metadata."""

    def has_child(name):
        """I return a Deferred that fires with a boolean, True if there
        exists a child of the given name, False if not. The child name must
        be a unicode string."""

    def get(name):
        """I return a Deferred that fires with a specific named child node,
        either an IFileNode or an IDirectoryNode. The child name must be a
        unicode string."""

    def get_metadata_for(name):
        """I return a Deferred that fires with the metadata dictionary for a
        specific named child node. This metadata is stored in the *edge*, not
        in the child, so it is attached to the parent dirnode rather than the
        child dir-or-file-node. The child name must be a unicode string."""

    def set_metadata_for(name, metadata):
        """I replace any existing metadata for the named child with the new
        metadata. The child name must be a unicode string. This metadata is
        stored in the *edge*, not in the child, so it is attached to the
        parent dirnode rather than the child dir-or-file-node. I return a
        Deferred (that fires with this dirnode) when the operation is
        complete."""

    def get_child_at_path(path):
        """Transform a child path into an IDirectoryNode or IFileNode.

        I perform a recursive series of 'get' operations to find the named
        descendant node. I return a Deferred that fires with the node, or
        errbacks with IndexError if the node could not be found.

        The path can be either a single string (slash-separated) or a list of
        path-name elements. All elements must be unicode strings.
        """

    def set_uri(name, child_uri, metadata=None):
        """I add a child (by URI) at the specific name. I return a Deferred
        that fires when the operation finishes. I will replace any existing
        child of the same name. The child name must be a unicode string.

        The child_uri could be for a file, or for a directory (either
        read-write or read-only, using a URI that came from get_uri() ).

        If metadata= is provided, I will use it as the metadata for the named
        edge. This will replace any existing metadata. If metadata= is left
        as the default value of None, I will set ['mtime'] to the current
        time, and I will set ['ctime'] to the current time if there was not
        already a child by this name present. This roughly matches the
        ctime/mtime semantics of traditional filesystems.

        If this directory node is read-only, the Deferred will errback with a
        NotMutableError."""

    def set_children(entries):
        """Add multiple (name, child_uri) pairs (or (name, child_uri,
        metadata) triples) to a directory node. Returns a Deferred that fires
        (with None) when the operation finishes. This is equivalent to
        calling set_uri() multiple times, but is much more efficient. All
        child names must be unicode strings.
        """

    def set_node(name, child, metadata=None):
        """I add a child at the specific name. I return a Deferred that fires
        when the operation finishes. This Deferred will fire with the child
        node that was just added. I will replace any existing child of the
        same name. The child name must be a unicode string.

        If metadata= is provided, I will use it as the metadata for the named
        edge. This will replace any existing metadata. If metadata= is left
        as the default value of None, I will set ['mtime'] to the current
        time, and I will set ['ctime'] to the current time if there was not
        already a child by this name present. This roughly matches the
        ctime/mtime semantics of traditional filesystems.

        If this directory node is read-only, the Deferred will errback with a
        NotMutableError."""

    def set_nodes(entries):
        """Add multiple (name, child_node) pairs (or (name, child_node,
        metadata) triples) to a directory node. Returns a Deferred that fires
        (with None) when the operation finishes. This is equivalent to
        calling set_node() multiple times, but is much more efficient. All
        child names must be unicode strings."""


    def add_file(name, uploadable, metadata=None):
        """I upload a file (using the given IUploadable), then attach the
        resulting FileNode to the directory at the given name. I set metadata
        the same way as set_uri and set_node. The child name must be a
        unicode string.

        I return a Deferred that fires (with the IFileNode of the uploaded
        file) when the operation completes."""

    def delete(name):
        """I remove the child at the specific name. I return a Deferred that
        fires when the operation finishes. The child name must be a unicode
        string."""

    def create_empty_directory(name):
        """I create and attach an empty directory at the given name. The
        child name must be a unicode string. I return a Deferred that fires
        when the operation finishes."""

    def move_child_to(current_child_name, new_parent, new_child_name=None):
        """I take one of my children and move them to a new parent. The child
        is referenced by name. On the new parent, the child will live under
        'new_child_name', which defaults to 'current_child_name'. TODO: what
        should we do about metadata? I return a Deferred that fires when the
        operation finishes. The child name must be a unicode string."""

    def build_manifest():
        """Return a frozenset of verifier-capability strings for all nodes
        (directories and files) reachable from this one."""

class ICodecEncoder(Interface):
    def set_params(data_size, required_shares, max_shares):
        """Set up the parameters of this encoder.

        This prepares the encoder to perform an operation that converts a
        single block of data into a number of shares, such that a future
        ICodecDecoder can use a subset of these shares to recover the
        original data. This operation is invoked by calling encode(). Once
        the encoding parameters are set up, the encode operation can be
        invoked multiple times.

        set_params() prepares the encoder to accept blocks of input data that
        are exactly 'data_size' bytes in length. The encoder will be prepared
        to produce 'max_shares' shares for each encode() operation (although
        see the 'desired_share_ids' to use less CPU). The encoding math will
        be chosen such that the decoder can get by with as few as
        'required_shares' of these shares and still reproduce the original
        data. For example, set_params(1000, 5, 5) offers no redundancy at
        all, whereas set_params(1000, 1, 10) provides 10x redundancy.

        Numerical Restrictions: 'data_size' is required to be an integral
        multiple of 'required_shares'. In general, the caller should choose
        required_shares and max_shares based upon their reliability
        requirements and the number of peers available (the total storage
        space used is roughly equal to max_shares*data_size/required_shares),
        then choose data_size to achieve the memory footprint desired (larger
        data_size means more efficient operation, smaller data_size means
        smaller memory footprint).

        In addition, 'max_shares' must be equal to or greater than
        'required_shares'. Of course, setting them to be equal causes
        encode() to degenerate into a particularly slow form of the 'split'
        utility.

        See encode() for more details about how these parameters are used.

        set_params() must be called before any other ICodecEncoder methods
        may be invoked.
        """

    def get_encoder_type():
        """Return a short string that describes the type of this encoder.

        There is required to be a global table of encoder classes. This method
        returns an index into this table; the value at this index is an
        encoder class, and this encoder is an instance of that class.
        """

    def get_serialized_params(): # TODO: maybe, maybe not
        """Return a string that describes the parameters of this encoder.

        This string can be passed to the decoder to prepare it for handling
        the encoded shares we create. It might contain more information than
        was presented to set_params(), if there is some flexibility of
        parameter choice.

        This string is intended to be embedded in the URI, so there are
        several restrictions on its contents. At the moment I'm thinking that
        this means it may contain hex digits and hyphens, and nothing else.
        The idea is that the URI contains something like '%s:%s:%s' %
        (encoder.get_encoder_name(), encoder.get_serialized_params(),
        b2a(crypttext_hash)), and this is enough information to construct a
        compatible decoder.
        """

    def get_block_size():
        """Return the length of the shares that encode() will produce.
        """

    def encode_proposal(data, desired_share_ids=None):
        """Encode some data.

        'data' must be a string (or other buffer object), and len(data) must
        be equal to the 'data_size' value passed earlier to set_params().

        This will return a Deferred that will fire with two lists. The first
        is a list of shares, each of which is a string (or other buffer
        object) such that len(share) is the same as what get_share_size()
        returned earlier. The second is a list of shareids, in which each is
        an integer. The lengths of the two lists will always be equal to each
        other. The user should take care to keep each share closely
        associated with its shareid, as one is useless without the other.

        The length of this output list will normally be the same as the value
        provided to the 'max_shares' parameter of set_params(). This may be
        different if 'desired_share_ids' is provided.

        'desired_share_ids', if provided, is required to be a sequence of
        ints, each of which is required to be >= 0 and < max_shares. If not
        provided, encode() will produce 'max_shares' shares, as if
        'desired_share_ids' were set to range(max_shares). You might use this
        if you initially thought you were going to use 10 peers, started
        encoding, and then two of the peers dropped out: you could use
        desired_share_ids= to skip the work (both memory and CPU) of
        producing shares for the peers which are no longer available.

        """

    def encode(inshares, desired_share_ids=None):
        """Encode some data. This may be called multiple times. Each call is
        independent.

        inshares is a sequence of length required_shares, containing buffers
        (i.e. strings), where each buffer contains the next contiguous
        non-overlapping segment of the input data. Each buffer is required to
        be the same length, and the sum of the lengths of the buffers is
        required to be exactly the data_size promised by set_params(). (This
        implies that the data has to be padded before being passed to
        encode(), unless of course it already happens to be an even multiple
        of required_shares in length.)

         ALSO: the requirement to break up your data into 'required_shares'
         chunks before calling encode() feels a bit surprising, at least from
         the point of view of a user who doesn't know how FEC works. It feels
         like an implementation detail that has leaked outside the
         abstraction barrier. Can you imagine a use case in which the data to
         be encoded might already be available in pre-segmented chunks, such
         that it is faster or less work to make encode() take a list rather
         than splitting a single string?

         ALSO ALSO: I think 'inshares' is a misleading term, since encode()
         is supposed to *produce* shares, so what it *accepts* should be
         something other than shares. Other places in this interface use the
         word 'data' for that-which-is-not-shares.. maybe we should use that
         term?

        'desired_share_ids', if provided, is required to be a sequence of
        ints, each of which is required to be >= 0 and < max_shares. If not
        provided, encode() will produce 'max_shares' shares, as if
        'desired_share_ids' were set to range(max_shares). You might use this
        if you initially thought you were going to use 10 peers, started
        encoding, and then two of the peers dropped out: you could use
        desired_share_ids= to skip the work (both memory and CPU) of
        producing shares for the peers which are no longer available.

        For each call, encode() will return a Deferred that fires with two
        lists, one containing shares and the other containing the shareids.
        The get_share_size() method can be used to determine the length of
        the share strings returned by encode(). Each shareid is a small
        integer, exactly as passed into 'desired_share_ids' (or
        range(max_shares), if desired_share_ids was not provided).

        The shares and their corresponding shareids are required to be kept
        together during storage and retrieval. Specifically, the share data is
        useless by itself: the decoder needs to be told which share is which
        by providing it with both the shareid and the actual share data.

        This function will allocate an amount of memory roughly equal to::

         (max_shares - required_shares) * get_share_size()

        When combined with the memory that the caller must allocate to
        provide the input data, this leads to a memory footprint roughly
        equal to the size of the resulting encoded shares (i.e. the expansion
        factor times the size of the input segment).
        """

        # rejected ideas:
        #
        #  returning a list of (shareidN,shareN) tuples instead of a pair of
        #  lists (shareids..,shares..). Brian thought the tuples would
        #  encourage users to keep the share and shareid together throughout
        #  later processing, Zooko pointed out that the code to iterate
        #  through two lists is not really more complicated than using a list
        #  of tuples and there's also a performance improvement
        #
        #  having 'data_size' not required to be an integral multiple of
        #  'required_shares'. Doing this would require encode() to perform
        #  padding internally, and we'd prefer to have any padding be done
        #  explicitly by the caller. Yes, it is an abstraction leak, but
        #  hopefully not an onerous one.


class ICodecDecoder(Interface):
    def set_serialized_params(params):
        """Set up the parameters of this encoder, from a string returned by
        encoder.get_serialized_params()."""

    def get_needed_shares():
        """Return the number of shares needed to reconstruct the data.
        set_serialized_params() is required to be called before this."""

    def decode(some_shares, their_shareids):
        """Decode a partial list of shares into data.

        'some_shares' is required to be a sequence of buffers of sharedata, a
        subset of the shares returned by ICodecEncode.encode(). Each share is
        required to be of the same length.  The i'th element of their_shareids
        is required to be the shareid of the i'th buffer in some_shares.

        This returns a Deferred which fires with a sequence of buffers. This
        sequence will contain all of the segments of the original data, in
        order. The sum of the lengths of all of the buffers will be the
        'data_size' value passed into the original ICodecEncode.set_params()
        call. To get back the single original input block of data, use
        ''.join(output_buffers), or you may wish to simply write them in
        order to an output file.

        Note that some of the elements in the result sequence may be
        references to the elements of the some_shares input sequence. In
        particular, this means that if those share objects are mutable (e.g.
        arrays) and if they are changed, then both the input (the
        'some_shares' parameter) and the output (the value given when the
        deferred is triggered) will change.

        The length of 'some_shares' is required to be exactly the value of
        'required_shares' passed into the original ICodecEncode.set_params()
        call.
        """

class IEncoder(Interface):
    """I take an object that provides IEncryptedUploadable, which provides
    encrypted data, and a list of shareholders. I then encode, hash, and
    deliver shares to those shareholders. I will compute all the necessary
    Merkle hash trees that are necessary to validate the crypttext that
    eventually comes back from the shareholders. I provide the URI Extension
    Block Hash, and the encoding parameters, both of which must be included
    in the URI.

    I do not choose shareholders, that is left to the IUploader. I must be
    given a dict of RemoteReferences to storage buckets that are ready and
    willing to receive data.
    """

    def set_size(size):
        """Specify the number of bytes that will be encoded. This must be
        peformed before get_serialized_params() can be called.
        """
    def set_params(params):
        """Override the default encoding parameters. 'params' is a tuple of
        (k,d,n), where 'k' is the number of required shares, 'd' is the
        shares_of_happiness, and 'n' is the total number of shares that will
        be created.

        Encoding parameters can be set in three ways. 1: The Encoder class
        provides defaults (3/7/10). 2: the Encoder can be constructed with
        an 'options' dictionary, in which the
        needed_and_happy_and_total_shares' key can be a (k,d,n) tuple. 3:
        set_params((k,d,n)) can be called.

        If you intend to use set_params(), you must call it before
        get_share_size or get_param are called.
        """

    def set_encrypted_uploadable(u):
        """Provide a source of encrypted upload data. 'u' must implement
        IEncryptedUploadable.

        When this is called, the IEncryptedUploadable will be queried for its
        length and the storage_index that should be used.

        This returns a Deferred that fires with this Encoder instance.

        This must be performed before start() can be called.
        """

    def get_param(name):
        """Return an encoding parameter, by name.

        'storage_index': return a string with the (16-byte truncated SHA-256
                         hash) storage index to which these shares should be
                         pushed.

        'share_counts': return a tuple describing how many shares are used:
                        (needed_shares, shares_of_happiness, total_shares)

        'num_segments': return an int with the number of segments that
                        will be encoded.

        'segment_size': return an int with the size of each segment.

        'block_size': return the size of the individual blocks that will
                      be delivered to a shareholder's put_block() method. By
                      knowing this, the shareholder will be able to keep all
                      blocks in a single file and still provide random access
                      when reading them. # TODO: can we avoid exposing this?

        'share_size': an int with the size of the data that will be stored
                      on each shareholder. This is aggregate amount of data
                      that will be sent to the shareholder, summed over all
                      the put_block() calls I will ever make. It is useful to
                      determine this size before asking potential
                      shareholders whether they will grant a lease or not,
                      since their answers will depend upon how much space we
                      need. TODO: this might also include some amount of
                      overhead, like the size of all the hashes. We need to
                      decide whether this is useful or not.

        'serialized_params': a string with a concise description of the
                             codec name and its parameters. This may be passed
                             into the IUploadable to let it make sure that
                             the same file encoded with different parameters
                             will result in different storage indexes.

        Once this is called, set_size() and set_params() may not be called.
        """

    def set_shareholders(shareholders):
        """Tell the encoder where to put the encoded shares. 'shareholders'
        must be a dictionary that maps share number (an integer ranging from
        0 to n-1) to an instance that provides IStorageBucketWriter. This
        must be performed before start() can be called."""

    def start():
        """Begin the encode/upload process. This involves reading encrypted
        data from the IEncryptedUploadable, encoding it, uploading the shares
        to the shareholders, then sending the hash trees.

        set_encrypted_uploadable() and set_shareholders() must be called
        before this can be invoked.

        This returns a Deferred that fires with a tuple of
        (uri_extension_hash, needed_shares, total_shares, size) when the
        upload process is complete. This information, plus the encryption
        key, is sufficient to construct the URI.
        """

class IDecoder(Interface):
    """I take a list of shareholders and some setup information, then
    download, validate, decode, and decrypt data from them, writing the
    results to an output file.

    I do not locate the shareholders, that is left to the IDownloader. I must
    be given a dict of RemoteReferences to storage buckets that are ready to
    send data.
    """

    def setup(outfile):
        """I take a file-like object (providing write and close) to which all
        the plaintext data will be written.

        TODO: producer/consumer . Maybe write() should return a Deferred that
        indicates when it will accept more data? But probably having the
        IDecoder be a producer is easier to glue to IConsumer pieces.
        """

    def set_shareholders(shareholders):
        """I take a dictionary that maps share identifiers (small integers)
        to RemoteReferences that provide RIBucketReader. This must be called
        before start()."""

    def start():
        """I start the download. This process involves retrieving data and
        hash chains from the shareholders, using the hashes to validate the
        data, decoding the shares into segments, decrypting the segments,
        then writing the resulting plaintext to the output file.

        I return a Deferred that will fire (with self) when the download is
        complete.
        """

class IDownloadTarget(Interface):
    def open(size):
        """Called before any calls to write() or close(). If an error
        occurs before any data is available, fail() may be called without
        a previous call to open().

        'size' is the length of the file being downloaded, in bytes."""

    def write(data):
        """Output some data to the target."""
    def close():
        """Inform the target that there is no more data to be written."""
    def fail(why):
        """fail() is called to indicate that the download has failed. 'why'
        is a Failure object indicating what went wrong. No further methods
        will be invoked on the IDownloadTarget after fail()."""
    def register_canceller(cb):
        """The FileDownloader uses this to register a no-argument function
        that the target can call to cancel the download. Once this canceller
        is invoked, no further calls to write() or close() will be made."""
    def finish():
        """When the FileDownloader is done, this finish() function will be
        called. Whatever it returns will be returned to the invoker of
        Downloader.download.
        """

class IDownloader(Interface):
    def download(uri, target):
        """Perform a CHK download, sending the data to the given target.
        'target' must provide IDownloadTarget.

        Returns a Deferred that fires (with the results of target.finish)
        when the download is finished, or errbacks if something went wrong."""

class IEncryptedUploadable(Interface):
    def set_upload_status(upload_status):
        """Provide an IUploadStatus object that should be filled with status
        information. The IEncryptedUploadable is responsible for setting
        key-determination progress ('chk'), size, storage_index, and
        ciphertext-fetch progress. It may delegate some of this
        responsibility to others, in particular to the IUploadable."""

    def get_size():
        """This behaves just like IUploadable.get_size()."""

    def get_all_encoding_parameters():
        """Return a Deferred that fires with a tuple of
        (k,happy,n,segment_size). The segment_size will be used as-is, and
        must match the following constraints: it must be a multiple of k, and
        it shouldn't be unreasonably larger than the file size (if
        segment_size is larger than filesize, the difference must be stored
        as padding).

        This usually passes through to the IUploadable method of the same
        name.

        The encoder strictly obeys the values returned by this method. To
        make an upload use non-default encoding parameters, you must arrange
        to control the values that this method returns.
        """

    def get_storage_index():
        """Return a Deferred that fires with a 16-byte storage index.
        """

    def read_encrypted(length, hash_only):
        """This behaves just like IUploadable.read(), but returns crypttext
        instead of plaintext. If hash_only is True, then this discards the
        data (and returns an empty list); this improves efficiency when
        resuming an interrupted upload (where we need to compute the
        plaintext hashes, but don't need the redundant encrypted data)."""

    def get_plaintext_hashtree_leaves(first, last, num_segments):
        """Get the leaf nodes of a merkle hash tree over the plaintext
        segments, i.e. get the tagged hashes of the given segments. The
        segment size is expected to be generated by the IEncryptedUploadable
        before any plaintext is read or ciphertext produced, so that the
        segment hashes can be generated with only a single pass.

        This returns a Deferred which fires with a sequence of hashes, using:

         tuple(segment_hashes[first:last])

        'num_segments' is used to assert that the number of segments that the
        IEncryptedUploadable handled matches the number of segments that the
        encoder was expecting.

        This method must not be called until the final byte has been read
        from read_encrypted(). Once this method is called, read_encrypted()
        can never be called again.
        """

    def get_plaintext_hash():
        """Get the hash of the whole plaintext.

        This returns a Deferred which fires with a tagged SHA-256 hash of the
        whole plaintext, obtained from hashutil.plaintext_hash(data).
        """

    def close():
        """Just like IUploadable.close()."""

class IUploadable(Interface):
    def set_upload_status(upload_status):
        """Provide an IUploadStatus object that should be filled with status
        information. The IUploadable is responsible for setting
        key-determination progress ('chk')."""

    def set_default_encoding_parameters(params):
        """Set the default encoding parameters, which must be a dict mapping
        strings to ints. The meaningful keys are 'k', 'happy', 'n', and
        'max_segment_size'. These might have an influence on the final
        encoding parameters returned by get_all_encoding_parameters(), if the
        Uploadable doesn't have more specific preferences.

        This call is optional: if it is not used, the Uploadable will use
        some built-in defaults. If used, this method must be called before
        any other IUploadable methods to have any effect.
        """

    def get_size():
        """Return a Deferred that will fire with the length of the data to be
        uploaded, in bytes. This will be called before the data is actually
        used, to compute encoding parameters.
        """

    def get_all_encoding_parameters():
        """Return a Deferred that fires with a tuple of
        (k,happy,n,segment_size). The segment_size will be used as-is, and
        must match the following constraints: it must be a multiple of k, and
        it shouldn't be unreasonably larger than the file size (if
        segment_size is larger than filesize, the difference must be stored
        as padding).

        The relative values of k and n allow some IUploadables to request
        better redundancy than others (in exchange for consuming more space
        in the grid).

        Larger values of segment_size reduce hash overhead, while smaller
        values reduce memory footprint and cause data to be delivered in
        smaller pieces (which may provide a smoother and more predictable
        download experience).

        The encoder strictly obeys the values returned by this method. To
        make an upload use non-default encoding parameters, you must arrange
        to control the values that this method returns. One way to influence
        them may be to call set_encoding_parameters() before calling
        get_all_encoding_parameters().
        """

    def get_encryption_key():
        """Return a Deferred that fires with a 16-byte AES key. This key will
        be used to encrypt the data. The key will also be hashed to derive
        the StorageIndex.

        Uploadables which want to achieve convergence should hash their file
        contents and the serialized_encoding_parameters to form the key
        (which of course requires a full pass over the data). Uploadables can
        use the upload.ConvergentUploadMixin class to achieve this
        automatically.

        Uploadables which do not care about convergence (or do not wish to
        make multiple passes over the data) can simply return a
        strongly-random 16 byte string.

        get_encryption_key() may be called multiple times: the IUploadable is
        required to return the same value each time.
        """

    def read(length):
        """Return a Deferred that fires with a list of strings (perhaps with
        only a single element) which, when concatenated together, contain the
        next 'length' bytes of data. If EOF is near, this may provide fewer
        than 'length' bytes. The total number of bytes provided by read()
        before it signals EOF must equal the size provided by get_size().

        If the data must be acquired through multiple internal read
        operations, returning a list instead of a single string may help to
        reduce string copies.

        'length' will typically be equal to (min(get_size(),1MB)/req_shares),
        so a 10kB file means length=3kB, 100kB file means length=30kB,
        and >=1MB file means length=300kB.

        This method provides for a single full pass through the data. Later
        use cases may desire multiple passes or access to only parts of the
        data (such as a mutable file making small edits-in-place). This API
        will be expanded once those use cases are better understood.
        """

    def close():
        """The upload is finished, and whatever filehandle was in use may be
        closed."""

class IUploadResults(Interface):
    """I am returned by upload() methods. I contain a number of public
    attributes which can be read to determine the results of the upload. Some
    of these are functional, some are timing information. All of these may be
    None.::

     .file_size : the size of the file, in bytes
     .uri : the CHK read-cap for the file
     .ciphertext_fetched : how many bytes were fetched by the helper
     .sharemap : dict mapping share number to placement string
     .servermap : dict mapping server peerid to a set of share numbers
     .timings : dict of timing information, mapping name to seconds (float)
       total : total upload time, start to finish
       storage_index : time to compute the storage index
       peer_selection : time to decide which peers will be used
       contacting_helper : initial helper query to upload/no-upload decision
       existence_check : helper pre-upload existence check
       helper_total : initial helper query to helper finished pushing
       cumulative_fetch : helper waiting for ciphertext requests
       total_fetch : helper start to last ciphertext response
       cumulative_encoding : just time spent in zfec
       cumulative_sending : just time spent waiting for storage servers
       hashes_and_close : last segment push to shareholder close
       total_encode_and_push : first encode to shareholder close

    """

class IDownloadResults(Interface):
    """I am created internally by download() methods. I contain a number of
    public attributes which contain details about the download process.::

     .file_size : the size of the file, in bytes
     .servers_used : set of server peerids that were used during download
     .server_problems : dict mapping server peerid to a problem string. Only
                        servers that had problems (bad hashes, disconnects) are
                        listed here.
     .servermap : dict mapping server peerid to a set of share numbers. Only
                  servers that had any shares are listed here.
     .timings : dict of timing information, mapping name to seconds (float)
       peer_selection : time to ask servers about shares
       servers_peer_selection : dict of peerid to DYHB-query time
       uri_extension : time to fetch a copy of the URI extension block
       hashtrees : time to fetch the hash trees
       segments : time to fetch, decode, and deliver segments
       cumulative_fetch : time spent waiting for storage servers
       cumulative_decode : just time spent in zfec
       cumulative_decrypt : just time spent in decryption
       total : total download time, start to finish
       fetch_per_server : dict of peerid to list of per-segment fetch times

    """

class IUploader(Interface):
    def upload(uploadable):
        """Upload the file. 'uploadable' must impement IUploadable. This
        returns a Deferred which fires with an UploadResults instance, from
        which the URI of the file can be obtained as results.uri ."""

    def upload_ssk(write_capability, new_version, uploadable):
        """TODO: how should this work?"""

class IChecker(Interface):
    def check(uri_to_check):
        """Accepts an IVerifierURI, and checks upon the health of its target.

        For now, uri_to_check must be an IVerifierURI. In the future we
        expect to relax that to be anything that can be adapted to
        IVerifierURI (like read-only or read-write dirnode/filenode URIs).

        This returns a Deferred. For dirnodes, this fires with either True or
        False (dirnodes are not distributed, so their health is a boolean).

        For filenodes, this fires with a tuple of (needed_shares,
        total_shares, found_shares, sharemap). The first three are ints. The
        basic health of the file is found_shares / needed_shares: if less
        than 1.0, the file is unrecoverable.

        The sharemap has a key for each sharenum. The value is a list of
        (binary) nodeids who hold that share. If two shares are kept on the
        same nodeid, they will fail as a pair, and overall reliability is
        decreased.

        The IChecker instance remembers the results of the check. By default,
        these results are stashed in RAM (and are forgotten at shutdown). If
        a file named 'checker_results.db' exists in the node's basedir, it is
        used as a sqlite database of results, making them persistent across
        runs. To start using this feature, just 'touch checker_results.db',
        and the node will initialize it properly the next time it is started.
        """

    def verify(uri_to_check):
        """Accepts an IVerifierURI, and verifies the crypttext of the target.

        This is a more-intensive form of checking. For verification, the
        file's crypttext contents are retrieved, and the associated hash
        checks are performed. If a storage server is holding a corrupted
        share, verification will detect the problem, but checking will not.
        This returns a Deferred that fires with True if the crypttext hashes
        look good, and will probably raise an exception if anything goes
        wrong.

        For dirnodes, 'verify' is the same as 'check', so the Deferred will
        fire with True or False.

        Verification currently only uses a minimal subset of peers, so a lot
        of share corruption will not be caught by it. We expect to improve
        this in the future.
        """

    def checker_results_for(uri_to_check):
        """Accepts an IVerifierURI, and returns a list of previously recorded
        checker results. This method performs no checking itself: it merely
        reports the results of checks that have taken place in the past.

        Each element of the list is a two-entry tuple: (when, results).
        The 'when' values are timestamps (float seconds since epoch), and the
        results are as defined in the check() method.

        Note: at the moment, this is specified to return synchronously. We
        might need to back away from this in the future.
        """

class IClient(Interface):
    def upload(uploadable):
        """Upload some data into a CHK, get back the UploadResults for it.
        @param uploadable: something that implements IUploadable
        @return: a Deferred that fires with the UploadResults instance.
                 To get the URI for this file, use results.uri .
        """

    def create_mutable_file(contents=""):
        """Create a new mutable file with contents, get back the URI string.
        @param contents: the initial contents to place in the file.
        @return: a Deferred that fires with tne (string) SSK URI for the new
                 file.
        """

    def create_empty_dirnode():
        """Create a new dirnode, empty and unattached.
        @return: a Deferred that fires with the new IDirectoryNode instance.
        """

    def create_node_from_uri(uri):
        """Create a new IFilesystemNode instance from the uri, synchronously.
        @param uri: a string or IURI-providing instance. This could be for a
                    LiteralFileNode, a CHK file node, a mutable file node, or
                    a directory node
        @return: an instance that provides IFilesystemNode (or more usefully one
                 of its subclasses). File-specifying URIs will result in
                 IFileNode or IMutableFileNode -providing instances, like
                 FileNode, LiteralFileNode, or MutableFileNode.
                 Directory-specifying URIs will result in
                 IDirectoryNode-providing instances, like NewDirectoryNode.
        """

class IClientStatus(Interface):
    def list_all_uploads():
        """Return a list of uploader objects, one for each upload which
        currently has an object available (tracked with weakrefs). This is
        intended for debugging purposes."""
    def list_active_uploads():
        """Return a list of active IUploadStatus objects."""
    def list_recent_uploads():
        """Return a list of IUploadStatus objects for the most recently
        started uploads."""

    def list_all_downloads():
        """Return a list of downloader objects, one for each download which
        currently has an object available (tracked with weakrefs). This is
        intended for debugging purposes."""
    def list_active_downloads():
        """Return a list of active IDownloadStatus objects."""
    def list_recent_downloads():
        """Return a list of IDownloadStatus objects for the most recently
        started downloads."""

class IUploadStatus(Interface):
    def get_started():
        """Return a timestamp (float with seconds since epoch) indicating
        when the operation was started."""
    def get_storage_index():
        """Return a string with the (binary) storage index in use on this
        upload. Returns None if the storage index has not yet been
        calculated."""
    def get_size():
        """Return an integer with the number of bytes that will eventually
        be uploaded for this file. Returns None if the size is not yet known.
        """
    def using_helper():
        """Return True if this upload is using a Helper, False if not."""
    def get_status():
        """Return a string describing the current state of the upload
        process."""
    def get_progress():
        """Returns a tuple of floats, (chk, ciphertext, encode_and_push),
        each from 0.0 to 1.0 . 'chk' describes how much progress has been
        made towards hashing the file to determine a CHK encryption key: if
        non-convergent encryption is in use, this will be trivial, otherwise
        the whole file must be hashed. 'ciphertext' describes how much of the
        ciphertext has been pushed to the helper, and is '1.0' for non-helper
        uploads. 'encode_and_push' describes how much of the encode-and-push
        process has finished: for helper uploads this is dependent upon the
        helper providing progress reports. It might be reasonable to add all
        three numbers and report the sum to the user."""
    def get_active():
        """Return True if the upload is currently active, False if not."""
    def get_results():
        """Return an instance of UploadResults (which contains timing and
        sharemap information). Might return None if the upload is not yet
        finished."""
    def get_counter():
        """Each upload status gets a unique number: this method returns that
        number. This provides a handle to this particular upload, so a web
        page can generate a suitable hyperlink."""

class IDownloadStatus(Interface):
    def get_started():
        """Return a timestamp (float with seconds since epoch) indicating
        when the operation was started."""
    def get_storage_index():
        """Return a string with the (binary) storage index in use on this
        download. This may be None if there is no storage index (i.e. LIT
        files)."""
    def get_size():
        """Return an integer with the number of bytes that will eventually be
        retrieved for this file. Returns None if the size is not yet known.
        """
    def using_helper():
        """Return True if this download is using a Helper, False if not."""
    def get_status():
        """Return a string describing the current state of the download
        process."""
    def get_progress():
        """Returns a float (from 0.0 to 1.0) describing the amount of the
        download that has completed. This value will remain at 0.0 until the
        first byte of plaintext is pushed to the download target."""
    def get_active():
        """Return True if the download is currently active, False if not."""
    def get_counter():
        """Each download status gets a unique number: this method returns
        that number. This provides a handle to this particular download, so a
        web page can generate a suitable hyperlink."""

class IServermapUpdaterStatus(Interface):
    pass
class IPublishStatus(Interface):
    pass
class IRetrieveStatus(Interface):
    pass

class NotCapableError(Exception):
    """You have tried to write to a read-only node."""

class BadWriteEnablerError(Exception):
    pass

class RIControlClient(RemoteInterface):

    def wait_for_client_connections(num_clients=int):
        """Do not return until we have connections to at least NUM_CLIENTS
        storage servers.
        """

    def upload_from_file_to_uri(filename=str, convergence=ChoiceOf(None, StringConstraint(2**20))):
        """Upload a file to the grid. This accepts a filename (which must be
        absolute) that points to a file on the node's local disk. The node will
        read the contents of this file, upload it to the grid, then return the
        URI at which it was uploaded.  If convergence is None then a random
        encryption key will be used, else the plaintext will be hashed, then
        that hash will be mixed together with the "convergence" string to form
        the encryption key.
        """
        return URI

    def download_from_uri_to_file(uri=URI, filename=str):
        """Download a file from the grid, placing it on the node's local disk
        at the given filename (which must be absolute[?]). Returns the
        absolute filename where the file was written."""
        return str

    # debug stuff

    def get_memory_usage():
        """Return a dict describes the amount of memory currently in use. The
        keys are 'VmPeak', 'VmSize', and 'VmData'. The values are integers,
        measuring memory consupmtion in bytes."""
        return DictOf(str, int)

    def speed_test(count=int, size=int, mutable=Any()):
        """Write 'count' tempfiles to disk, all of the given size. Measure
        how long (in seconds) it takes to upload them all to the servers.
        Then measure how long it takes to download all of them. If 'mutable'
        is 'create', time creation of mutable files. If 'mutable' is
        'upload', then time access to the same mutable file instead of
        creating one.

        Returns a tuple of (upload_time, download_time).
        """
        return (float, float)

    def measure_peer_response_time():
        """Send a short message to each connected peer, and measure the time
        it takes for them to respond to it. This is a rough measure of the
        application-level round trip time.

        @return: a dictionary mapping peerid to a float (RTT time in seconds)
        """

        return DictOf(Nodeid, float)

UploadResults = Any() #DictOf(str, str)

class RIEncryptedUploadable(RemoteInterface):
    __remote_name__ = "RIEncryptedUploadable.tahoe.allmydata.com"

    def get_size():
        return Offset

    def get_all_encoding_parameters():
        return (int, int, int, long)

    def read_encrypted(offset=Offset, length=ReadSize):
        return ListOf(str)

    def get_plaintext_hashtree_leaves(first=int, last=int, num_segments=int):
        return ListOf(Hash)

    def get_plaintext_hash():
        return Hash

    def close():
        return None


class RICHKUploadHelper(RemoteInterface):
    __remote_name__ = "RIUploadHelper.tahoe.allmydata.com"

    def upload(reader=RIEncryptedUploadable):
        return UploadResults


class RIHelper(RemoteInterface):
    __remote_name__ = "RIHelper.tahoe.allmydata.com"

    def upload_chk(si=StorageIndex):
        """See if a file with a given storage index needs uploading. The
        helper will ask the appropriate storage servers to see if the file
        has already been uploaded. If so, the helper will return a set of
        'upload results' that includes whatever hashes are needed to build
        the read-cap, and perhaps a truncated sharemap.

        If the file has not yet been uploaded (or if it was only partially
        uploaded), the helper will return an empty upload-results dictionary
        and also an RICHKUploadHelper object that will take care of the
        upload process. The client should call upload() on this object and
        pass it a reference to an RIEncryptedUploadable object that will
        provide ciphertext. When the upload is finished, the upload() method
        will finish and return the upload results.
        """
        return (UploadResults, ChoiceOf(RICHKUploadHelper, None))


class RIStatsProvider(RemoteInterface):
    __remote_name__ = "RIStatsProvider.tahoe.allmydata.com"
    """
    Provides access to statistics and monitoring information.
    """

    def get_stats():
        """
        returns a dictionary containing 'counters' and 'stats', each a dictionary
        with string counter/stat name keys, and numeric values.  counters are
        monotonically increasing measures of work done, and stats are instantaneous
        measures (potentially time averaged internally)
        """
        return DictOf(str, DictOf(str, ChoiceOf(float, int, long)))

class RIStatsGatherer(RemoteInterface):
    __remote_name__ = "RIStatsGatherer.tahoe.allmydata.com"
    """
    Provides a monitoring service for centralised collection of stats
    """

    def provide(provider=RIStatsProvider, nickname=str):
        """
        @param provider: a stats collector instance which should be polled
                         periodically by the gatherer to collect stats.
        @param nickname: a name useful to identify the provided client
        """
        return None


class IStatsProducer(Interface):
    def get_stats():
        """
        returns a dictionary, with str keys representing the names of stats
        to be monitored, and numeric values.
        """

class RIKeyGenerator(RemoteInterface):
    __remote_name__ = "RIKeyGenerator.tahoe.allmydata.com"
    """
    Provides a service offering to make RSA key pairs.
    """

    def get_rsa_key_pair(key_size=int):
        """
        @param key_size: the size of the signature key.
        @return: tuple(verifying_key, signing_key)
        """
        return TupleOf(str, str)
