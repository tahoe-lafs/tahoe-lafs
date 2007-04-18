
from zope.interface import Interface
from foolscap.schema import StringConstraint, ListOf, TupleOf, SetOf, DictOf, \
     ChoiceOf
from foolscap import RemoteInterface, Referenceable

HASH_SIZE=32

Hash = StringConstraint(maxLength=HASH_SIZE,
                        minLength=HASH_SIZE)# binary format 32-byte SHA256 hash
Nodeid = StringConstraint(maxLength=20,
                          minLength=20) # binary format 20-byte SHA1 hash
PBURL = StringConstraint(150)
Verifierid = StringConstraint(20)
URI = StringConstraint(200) # kind of arbitrary
MAX_BUCKETS = 200  # per peer
ShareData = StringConstraint(100000)

class RIIntroducerClient(RemoteInterface):
    def new_peers(pburls=SetOf(PBURL)):
        return None

class RIIntroducer(RemoteInterface):
    def hello(node=RIIntroducerClient, pburl=PBURL):
        return None

class RIClient(RemoteInterface):
    def get_service(name=str):
        return Referenceable
    def get_nodeid():
        return Nodeid

class RIBucketWriter(RemoteInterface):
    def put_block(segmentnum=int, data=ShareData):
        """@param data: For most segments, this data will be 'blocksize'
        bytes in length. The last segment might be shorter.
        """
        return None
    
    def put_block_hashes(blockhashes=ListOf(Hash)):
        return None
        
    def put_share_hashes(sharehashes=ListOf(TupleOf(int, Hash))):
        return None

    def close():
        """
        If the data that has been written is incomplete or inconsistent then
        the server will throw the data away, else it will store it for future
        retrieval.
        """
        return None

class RIBucketReader(RemoteInterface):
    def get_block(blocknum=int):
        """Most blocks will be the same size. The last block might be shorter
        than the others.
        """
        return ShareData
    def get_block_hashes():
        return ListOf(Hash)
    def get_share_hashes():
        return ListOf(TupleOf(int, Hash))

class RIStorageServer(RemoteInterface):
    def allocate_buckets(verifierid=Verifierid,
                         sharenums=SetOf(int, maxLength=MAX_BUCKETS),
                         sharesize=int, blocksize=int, canary=Referenceable):
        """
        @param canary: If the canary is lost before close(), the bucket is deleted.
        @return: tuple of (alreadygot, allocated), where alreadygot is what we
            already have and is what we hereby agree to accept
        """
        return TupleOf(SetOf(int, maxLength=MAX_BUCKETS),
                       DictOf(int, RIBucketWriter, maxKeys=MAX_BUCKETS))
    def get_buckets(verifierid=Verifierid):
        return DictOf(int, RIBucketReader, maxKeys=MAX_BUCKETS)

# hm, we need a solution for forward references in schemas
from foolscap.schema import Any
RIMutableDirectoryNode_ = Any() # TODO: how can we avoid this?
class RIMutableDirectoryNode(RemoteInterface):
    def list():
        return ListOf( TupleOf(str, # name, relative to directory
                               ChoiceOf(RIMutableDirectoryNode_, Verifierid)),
                       maxLength=100,
                       )

    def get(name=str):
        return ChoiceOf(RIMutableDirectoryNode_, Verifierid)

    def add_directory(name=str):
        return RIMutableDirectoryNode_

    def add_file(name=str, uri=URI):
        return None

    def remove(name=str):
        return None

    # need more to move directories


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
        b2a(verifierid)), and this is enough information to construct a
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
    """I take a file-like object that provides a sequence of bytes and a list
    of shareholders, then encrypt, encode, hash, and deliver shares to those
    shareholders. I will compute all the necessary Merkle hash trees that are
    necessary to validate the data that eventually comes back from the
    shareholders. I provide the root hash of the hash tree, and the encoding
    parameters, both of which must be included in the URI.

    I do not choose shareholders, that is left to the IUploader. I must be
    given a dict of RemoteReferences to storage buckets that are ready and
    willing to receive data.
    """

    def setup(infile):
        """I take a file-like object (providing seek, tell, and read) from
        which all the plaintext data that is to be uploaded can be read. I
        will seek to the beginning of the file before reading any data.
        setup() must be called before making any other calls, in particular
        before calling get_reservation_size().
        """

    def get_share_size():
        """I return the size of the data that will be stored on each
        shareholder. This is aggregate amount of data that will be sent to
        the shareholder, summed over all the put_block() calls I will ever
        make.

        TODO: this might also include some amount of overhead, like the size
        of all the hashes. We need to decide whether this is useful or not.

        It is useful to determine this size before asking potential
        shareholders whether they will grant a lease or not, since their
        answers will depend upon how much space we need.
        """

    def get_block_size(): # TODO: can we avoid exposing this?
        """I return the size of the individual blocks that will be delivered
        to a shareholder's put_block() method. By knowing this, the
        shareholder will be able to keep all blocks in a single file and
        still provide random access when reading them.
        """

    def set_shareholders(shareholders):
        """I take a dictionary that maps share identifiers (small integers,
        starting at 0) to RemoteReferences that provide RIBucketWriter. This
        must be called before start().
        """

    def start():
        """I start the upload. This process involves reading data from the
        input file, encrypting it, encoding the pieces, uploading the shares
        to the shareholders, then sending the hash trees.

        I return a Deferred that fires with the root hash.
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
    def open():
        """Called before any calls to write() or close()."""
    def write(data):
        """Output some data to the target."""
    def close():
        """Inform the target that there is no more data to be written."""
    def fail():
        """fail() is called to indicate that the download has failed. No
        further methods will be invoked on the IDownloadTarget after fail()."""
    def register_canceller(cb):
        """The FileDownloader uses this to register a no-argument function
        that the target can call to cancel the download. Once this canceller
        is invoked, no further calls to write() or close() will be made."""
    def finish(self):
        """When the FileDownloader is done, this finish() function will be
        called. Whatever it returns will be returned to the invoker of
        Downloader.download.
        """

class IDownloader(Interface):
    def download(uri, target):
        """Perform a CHK download, sending the data to the given target.
        'target' must provide IDownloadTarget."""

class IUploadable(Interface):
    def get_filehandle():
        """Return a filehandle from which the data to be uploaded can be
        read. It must implement .read, .seek, and .tell (since the latter two
        are used to determine the length of the data)."""
    def close_filehandle(f):
        """The upload is finished. This provides the same filehandle as was
        returned by get_filehandle. This is an appropriate place to close the
        filehandle."""

class IUploader(Interface):
    def upload(uploadable):
        """Upload the file. 'uploadable' must impement IUploadable. This
        returns a Deferred which fires with the URI of the file."""

    def upload_ssk(write_capability, new_version, uploadable):
        """TODO: how should this work?"""
    def upload_data(data):
        """Like upload(), but accepts a string."""

    def upload_filename(filename):
        """Like upload(), but accepts an absolute pathname."""

    def upload_filehandle(filehane):
        """Like upload(), but accepts an open filehandle."""


class IWorkQueue(Interface):
    """Each filetable root is associated a work queue, which is persisted on
    disk and contains idempotent actions that need to be performed. After
    each action is completed, it is removed from the queue.

    The queue is broken up into several sections. First are the 'upload'
    steps. After this are the 'add_subpath' commands. The last section has
    the 'unlink' steps. Somewhere in here are the 'retain' steps.. maybe
    interspersed with 'upload', maybe after 'add_subpath' and before
    'unlink'.

    The general idea is that the processing of the work queue could be
    interrupted at any time, in the middle of a step, and the next time the
    application is started, the step can be re-started without problems. The
    placement of the 'retain' commands depends upon how long we might expect
    the app to be offline.

    tempfiles: the workqueue has a special directory where temporary files
    are stored. create_tempfile() generates these files, while steps like
    add_upload_chk() use them. The add_delete_tempfile() will delete the
    tempfile. All tempfiles are deleted when the workqueue becomes empty,
    since at that point none of them can still be referenced.

    boxes: there is another special directory where named slots (called
    'boxes') hold serialized INode specifications (the strings which are
    returned by INode.serialize_node()). Boxes are created by calling
    create_boxname(). Boxes are filled either at the time of creation or by
    steps like add_upload_chk(). Boxes are used by steps like add_addpath()
    and add_retain_uri_from_box. Boxes are deleted by add_delete_box(), as
    well as when the workqueue becomes empty.
    """

    def create_tempfile(suffix=""):
        """Return (f, filename), where 'f' is an open filehandle, and
        'filename' is a string that can be passed to other workqueue steps to
        refer to that same file later. NOTE: 'filename' is not an absolute
        path, rather it will be interpreted relative to some directory known
        only by the workqueue."""
    def create_boxname(contents=None):
        """Return a unique box name (as a string). If 'contents' are
        provided, it must be an instance that provides INode, and the
        serialized form of the node will be written into the box. Otherwise
        the boxname can be used by steps like add_upload_chk to hold the
        generated uri."""

    def add_upload_chk(source_filename, stash_uri_in_boxname):
        """This step uploads a file to the mesh and obtains a content-based
        URI which can be used to later retrieve the same contents ('CHK'
        mode). This URI includes unlink rights. It does not mark the file for
        retention.

        Non-absolute filenames are interpreted relative to the workqueue's
        special just-for-tempfiles directory.

        When the upload is complete, the resulting URI is stashed in a 'box'
        with the specified name. This is basically a local variable. A later
        'add_subpath' step will reference this boxname and retrieve the URI.
        """

    def add_upload_ssk(write_capability, previous_version, source_filename):
        """This step uploads a file to the mesh in a way that replaces the
        previous version and does not require a change to the ID referenced
        by the parent.
        """

    def add_queen_update_handle(handle, source_filename):
        """Arrange for a central queen to be notified that the given handle
        has been updated with the contents of the given tempfile. This will
        send a set_handle() message to the queen."""

    def add_retain_ssk(read_capability):
        """Arrange for the given SSK to be kept alive."""

    def add_unlink_ssk(write_capability):
        """Stop keeping the given SSK alive."""

    def add_retain_uri_from_box(boxname):
        """When executed, this step retrieves the URI from the given box and
        marks it for retention: this adds it to a list of all URIs that this
        system cares about, which will initiate filechecking/repair for the
        file."""

    def add_addpath(boxname, path):
        """When executed, this step pulls a node specification from 'boxname'
        and figures out which subtrees must be modified to allow that node to
        live at the 'path' (which is an absolute path). This will probably
        cause one or more 'add_modify_subtree' or 'add_modify_redirection'
        steps to be added to the workqueue.
        """

    def add_deletepath(path):
        """When executed, finds the subtree that contains the node at 'path'
        and modifies it (and any necessary parent subtrees) to delete that
        path. This will probably cause one or more 'add_modify_subtree' or
        'add_modify_redirection' steps to be added to the workqueue.
        """

    def add_modify_subtree(subtree_node, localpath, new_node_boxname,
                           new_subtree_boxname=None):
        """When executed, this step retrieves the subtree specified by
        'subtree_node', pulls a node specification out of 'new_node_boxname',
        then modifies the subtree such that a subtree-relative 'localpath'
        points to the new node. If 'new_node_boxname' is None, this deletes
        the given path. It then serializes the subtree in its new form, and
        optionally puts a node that describes the new subtree in
        'new_subtree_boxname' for use by another add_modify_subtree step.

        The idea is that 'subtree_node' will refer a CHKDirectorySubTree, and
        'new_node_boxname' will contain the CHKFileNode that points to a
        newly-uploaded file. When the CHKDirectorySubTree is modified, it
        acquires a new URI, which will be stuffed (in the form of a
        CHKDirectorySubTreeNode) into 'new_subtree_boxname'. A subsequent
        step would then read from 'new_subtree_boxname' and modify some other
        subtree with the contents.

        If 'subtree_node' refers to a redirection subtree like
        LocalFileRedirection or QueenRedirection, then 'localpath' is
        ignored, because redirection subtrees don't consume path components
        and have no internal directory structure (they just have the one
        redirection target). Redirection subtrees generally retain a constant
        identity, so it is unlikely that 'new_subtree_boxname' will be used.
        """

    def add_unlink_uri(uri):
        """When executed, this step will unlink the data referenced by the
        given URI: the unlink rights are used to tell any shareholders to
        unlink the file (possibly deleting it), and the URI is removed from
        the list that this system cares about, cancelling filechecking/repair
        for the file.

        All 'unlink' steps are pushed to the end of the queue.
        """

    def add_delete_tempfile(filename):
        """This step will delete a tempfile created by create_tempfile."""

    def add_delete_box(boxname):
        """When executed, this step deletes the given box."""


    # methods for use in unit tests

    def flush():
        """Execute all steps in the WorkQueue right away. Return a Deferred
        that fires (with self) when the queue is empty.
        """

class NotCapableError(Exception):
    """You have tried to write to a read-only node."""

class RIControlClient(RemoteInterface):
    def upload_from_file_to_uri(filename=str):
        """Upload a file to the mesh. This accepts a filename (which must be
        absolute) that points to a file on the node's local disk. The node
        will read the contents of this file, upload it to the mesh, then
        return the URI at which it was uploaded.
        """
        return URI

    def download_from_uri_to_file(uri=URI, filename=str):
        """Download a file from the mesh, placing it on the node's local disk
        at the given filename (which must be absolute[?]). Returns the
        absolute filename where the file was written."""
        return str

    # debug stuff

    def get_memory_usage():
        """Return a dict describes the amount of memory currently in use. The
        keys are 'VmPeak', 'VmSize', and 'VmData'. The values are integers,
        measuring memory consupmtion in bytes."""
        return DictOf(str, int)
