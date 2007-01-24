
from zope.interface import Interface
from foolscap.schema import StringConstraint, ListOf, TupleOf, Any
from foolscap import RemoteInterface

Nodeid = StringConstraint(20) # binary format 20-byte SHA1 hash
PBURL = StringConstraint(150)
Verifierid = StringConstraint(20)
URI = StringConstraint(100) # kind of arbitrary
ShareData = StringConstraint(100000)
# these four are here because Foolscap does not yet support the kind of
# restriction I really want to apply to these.
RIClient_ = Any()
Referenceable_ = Any()
RIBucketWriter_ = Any()
RIBucketReader_ = Any()
RIMutableDirectoryNode_ = Any()
RIMutableFileNode_ = Any()

class RIQueenRoster(RemoteInterface):
    def hello(nodeid=Nodeid, node=RIClient_, pburl=PBURL):
        return RIMutableDirectoryNode_ # the virtual drive root

class RIClient(RemoteInterface):
    def get_service(name=str):
        return Referenceable_
    def add_peers(new_peers=ListOf(TupleOf(Nodeid, PBURL), maxLength=100)):
        return None
    def lost_peers(lost_peers=ListOf(Nodeid)):
        return None

class RIStorageServer(RemoteInterface):
    def allocate_bucket(verifierid=Verifierid, bucket_num=int, size=int,
                        leaser=Nodeid, canary=Referenceable_):
        # if the canary is lost before close(), the bucket is deleted
        return RIBucketWriter_
    def get_buckets(verifierid=Verifierid):
        return ListOf(TupleOf(int, RIBucketReader_))

class RIBucketWriter(RemoteInterface):
    def write(data=ShareData):
        return None
    def set_metadata(metadata=str):
        return None
    def close():
        return None


class RIBucketReader(RemoteInterface):
    def read():
        return ShareData
    def get_metadata():
        return str


class RIMutableDirectoryNode(RemoteInterface):
    def list():
        return ListOf( TupleOf(str, # name, relative to directory
                               (RIMutableDirectoryNode_, Verifierid)),
                       maxLength=100,
                       )

    def get(name=str):
        return (RIMutableDirectoryNode_, Verifierid)

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

        See encode() for a description of how these parameters are used.
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

    def get_share_size():
        """Return the length of the shares that encode() will produce.
        """

    def encode(inshares, desired_share_ids=None):
        """Encode a chunk of data. This may be called multiple times. Each
        call is independent.

        The data is required to be a string with a length that exactly
        matches the data_size promised by set_params().

        'num_shares', if provided, is required to be equal or less than the
        'max_shares' set in set_params. If 'num_shares' is left at None,
        this method will produce 'max_shares' shares. This can be used to
        minimize the work that the encoder needs to do if we initially
        thought that we would need, say, 100 shares, but now that it is time
        to actually encode the data we only have 75 peers to send data to.

        For each call, encode() will return a Deferred that fires with two
        lists, one containing shares and the other containing the sharenums,
        which is an int from 0 to num_shares-1. The get_share_size() method
        can be used to determine the length of the 'sharedata' strings
        returned by encode().
        
        The sharedatas and their corresponding sharenums are required to be
        kept together during storage and retrieval. Specifically, the share
        data is useless by itself: the decoder needs to be told which share is
        which by providing it with both the share number and the actual
        share data.

        The memory usage of this function is expected to be on the order of
        total_shares * get_share_size().
        """
        # design note: we could embed the share number in the sharedata by
        # returning bencode((sharenum,sharedata)). The advantage would be
        # making it easier to keep these two pieces together, and probably
        # avoiding a round trip when reading the remote bucket (although this
        # could be achieved by changing RIBucketReader.read to
        # read_data_and_metadata). The disadvantage is that the share number
        # wants to be exposed to the storage/bucket layer (specifically to
        # handle the next stage of peer-selection algorithm in which we
        # propose to keep share#A on a given peer and they are allowed to
        # tell us that they already have share#B). Also doing this would make
        # the share size somewhat variable (one-digit sharenumbers will be a
        # byte shorter than two-digit sharenumbers), unless we zero-pad the
        # sharenumbers based upon the max_total_shares declared in
        # set_params.

class ICodecDecoder(Interface):
    def set_serialized_params(params):
        """Set up the parameters of this encoder, from a string returned by
        encoder.get_serialized_params()."""

    def get_required_shares():
        """Return the number of shares needed to reconstruct the data.
        set_serialized_params() is required to be called before this."""

    def decode(some_shares, their_shareids):
        """Decode a partial list of shares into data.

        'some_shares' is required to be a list of buffers of sharedata, a
        subset of the shares returned by ICodecEncode.encode(). Each share is
        required to be of the same length.  The i'th element of their_shareids
        is required to be the share id (or "share num") of the i'th buffer in
        some_shares.

        This returns a Deferred which fires with a sequence of buffers. This
        sequence will contain all of the segments of the original data, in
        order.  The sum of the lengths of all of the buffers will be the
        'data_size' value passed into the original ICodecEncode.set_params()
        call.

        The length of 'some_shares' is required to be exactly the value of
        'required_shares' passed into the original ICodecEncode.set_params()
        call.
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
        pass # TODO

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
        then modifies it such that a subtree-relative 'localpath' points to
        the new node. It then serializes the subtree in its new form, and
        optionally puts a node that describes the new subtree in
        'new_node_boxname'. If 'new_node_boxname' is None, this deletes the
        given path.

        The idea is that 'subtree_node' will refer a CHKDirectorySubTree, and
        'new_node_boxname' will contain the CHKFileNode that points to a
        newly-uploaded file. When the CHKDirectorySubTree is modified, it
        acquires a new URI, which will be stuffed (in the form of a
        CHKDirectorySubTreeNode) into 'new_subtree_boxname'. A following step
        would then read from 'new_subtree_boxname' and modify some other
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
