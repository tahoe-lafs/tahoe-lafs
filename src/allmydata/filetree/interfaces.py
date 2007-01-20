
from zope.interface import Interface

class INode(Interface):
    """This is some sort of retrievable node. All objects which implement
    other I*Node interfaces also implement this one."""

    # the INode-implementing class must have an attribute named .prefix which
    # contains a string.

    def serialize_node():
        """Return a data structure which contains enough information to build
        this node again in the future (by calling
        vdrive.make_node_from_serialized(). For IDirectoryNodes, this will be
        a list. For all other nodes this will be a string of the form
        'prefix:body', where 'prefix' must be the same as the class attribute
        .prefix ."""
    def populate_node(body, node_maker):
        """vdrive.make_node_from_serialized() will first use the prefix from
        the .prefix attribute to decide what kind of Node to create. It will
        then call this function with the body to populate the new Node."""

class IFileNode(Interface):
    """This is a file which can be retrieved."""
    # TODO: not sure which of these to provide.. should URIs contain "CHK" or
    # "SSK" in them? Or should that be a detail of IDownloader?
    def get_uri():
        """Return the URI of the target file. This URI can be passed
        to an IDownloader to retrieve the data."""
    def download(downloader, target):
        """Download the file to the given target (using the provided
        downloader). Return a deferred that fires (with 'target') when the
        download is complete."""

class IDirectoryNode(Interface):
    """This is a directory which can be listed."""
    # these calls do not modify the subtree
    def list():
        """Return a dictionary mapping each childname to a node. These nodes
        implement various I*Node interfaces depending upon what they can do."""
    def get(childname):
        """Return a child node. Raises NoSuchChildError if there is no
        child of that name."""
    def get_subtree():
        """Return the ISubTree which contains this node."""

    # the following calls modify the subtree. After calling them, you must
    # tell the enclosing subtree to serialize and upload itself. They can
    # only be called if this directory node is associated with a mutable
    # subtree.
    def delete(childname):
        """Delete any child referenced by this name."""
    def add_subdir(childname):
        """Create a new directory node, and return it."""
    def add(childname, node):
        """Add a new node to this path. Returns self."""

class ISubTree(Interface):
    """A subtree is a collection of Nodes: files, directories, other trees.

    A subtree represents a set of connected directories and files that all
    share the same access control: any given person can read or write
    anything in this tree as a group, and it is not possible to give access
    to some pieces of this tree and not to others. Read-only access to
    individual files can be granted independently, of course, but through an
    unnamed URI, not as a subdirectory.

    Each internal directory is represented by a separate Node. This might be
    a DirectoryNode, or it might be a FileNode.
    """

    def populate_from_node(node, parent_is_mutable, node_maker, downloader):
        """Subtrees are created by opener.open() being called with an INode
        which describes both the kind of subtree to be created and a way to
        obtain its contents. open() uses the node to create a new instance of
        the appropriate subtree type, then calls this populate_from_node()
        method.

        Each subtree's populate_from_node() method is expected to use the
        downloader to obtain a file with the subtree's serialized contents
        (probably by pulling data from some source, like the mesh, the queen,
        an HTTP server, or somewhere on the local filesystem), then
        unserialize them and populate the subtree's state.

        Return a Deferred that will fire (with self) when this subtree is
        ready for use (specifically when it is ready for get() and add()
        calls).
        """

    def is_mutable():
        """This returns True if we have the ability to modify this subtree.
        If this returns True, this reference may be adapted to
        IMutableSubTree to actually exercise these mutation rights.
        """

    def get_node_for_path(path):
        """Ask this subtree to follow the path through its internal nodes.

        Returns a tuple of (found_path, node, remaining_path). This method
        operations synchronously, and does not return a Deferred.

        (found_path=path, found_node, [])
        If the path terminates within this subtree, found_path=path and
        remaining_path=[], and the node will be an internal IDirectoryNode.

        (found_path, last_node, remaining_path)
        If the path does not terminate within this subtree but neither does
        it exit this subtree, the last internal IDirectoryNode that *was* on
        the path will be returned in 'node'. The path components that led to
        this node will be in found_path, and the remaining components will be
        in remaining_path. If you want to create the target node, loop over
        remaining_path as follows::

         while remaining_path:
           node = node.add_subdir(remaining_path.pop(0))

        (found_path, exit_node, remaining_path)
        If the path leaves this subtree, 'node' will be a different kind of
        INode (probably one that points at a child directory of some sort),
        found_path will be the components that led to this point, and
        remaining_path will be the remaining components. If you still wish to
        locate the target, use 'node' to open a new subtree, then provide
        'remaining_path' to the new subtree's get_node_for_path() method.

        """

    def serialize_subtree_to_file(f):
        """Create a string which describes my structure and write it to the
        given filehandle (using only .write()). This string should be
        suitable for uploading to the mesh or storing in a local file."""

    def update(prepath, workqueue):
        """Perform and schedule whatever work is necessary to record this
        subtree to persistent storage and update the parent at 'prepath'
        with a new child specification.

        For directory subtrees, this will cause the subtree to serialize
        itself to a file, then add instructions to the workqueue to first
        upload this file to the mesh, then add the file's URI to the parent's
        subtree. The second instruction will possibly cause recursion, until
        some subtree is updated which does not require notifying the parent.
        """


#class IMutableSubTree(Interface):
#    def mutation_affects_parent():
#        """This returns True for CHK nodes where you must inform the parent
#        of the new URI each time you change the child subtree. It returns
#        False for SSK nodes (or other nodes which have a pointer stored in
#        some mutable form).
#        """
#
#    def add_subpath(subpath, child_spec, work_queue):
#        """Ask this subtree to add the given child to an internal node at the
#        given subpath. The subpath must not exit the subtree through another
#        subtree (specifically get_subtree_for_path(subpath) must either
#        return None or (True,node), and in the latter case, this subtree will
#        create new internal nodes as necessary).
#
#        The subtree will probably serialize itself to a file and add steps to
#        the work queue to accomplish its goals.
#
#        This returns a Deferred (the value of which is ignored) when
#        everything has been added to the work queue.
#        """
#
#    def serialize_to_file(f):
#        """Write a bencoded data structure to the given filehandle that can
#        be used to reproduce the contents of this subtree."""
#
#class ISubTreeSpecification(Interface):
#    def serialize():
#        """Return a tuple that describes this subtree. This tuple can be
#        passed to IOpener.open() to reconstitute the subtree. It can also be
#        bencoded and stuffed in a series of persistent bytes somewhere on the
#        mesh or in a file."""

class IOpener(Interface):
    def open(subtree_specification, parent_is_mutable):
        """I can take an ISubTreeSpecification-providing specification of a
        subtree and return a Deferred which fires with an instance that
        provides ISubTree (and maybe even IMutableSubTree). I probably do
        this by performing network IO: reading a file from the mesh, or from
        local disk, or asking some central-service node for the current
        value."""


class IVirtualDrive(Interface):

    def __init__(workqueue, downloader, root_node):
        pass

    # internal methods

    def make_node_from_serialized(serialized):
        """Given a string produced by original_node.serialize_node(), produce
        an equivalent node.
        """

    # commands to manipulate files

    def list(path):
        """List the contents of the directory at the given path.

        'path' is a list of strings (empty to refer to the root directory)
        and must refer to a DIRECTORY node. This method returns a Deferred
        that fires with a dictionary that maps strings to filetypes. The
        strings are useful as path name components. The filetypes are
        Interfaces: either IDirectoryNode if path+[childname] can be used in
        a 'list' method, or IFileNode if path+[childname] can be used in a
        'download' method.
        """

    def download(path, target):
        """Download the file at the given path to 'target'.

        'path' must refer to a FILE. 'target' must implement IDownloadTarget.
        This returns a Deferred that fires (with 'target') when the download
        is complete.
        """

    def upload_now(path, uploadable):
        """Upload a file to the given path. The path must not already exist.

        path[:-1] must refer to a writable DIRECTORY node. 'uploadable' must
        implement IUploadable. This returns a Deferred that fires (with
        'uploadable') when the upload is complete.
        """

    def upload_later(path, filename):
        """Upload a file from disk to the given path.
        """

    def delete(path):
        """Delete the file or directory at the given path.

        Returns a Deferred that fires (with self) when the delete is
        complete.
        """

    # commands to manipulate subtrees

    # ... detach subtree, merge subtree, etc


# TODO

class ICHKDirectoryNode(Interface):
    def get_uri():
        pass
class ISSKDirectoryNode(Interface):
    def get_read_capability():
        pass
    def get_write_capability():
        pass



class NoSuchChildError(Exception):
    pass
class NoSuchDirectoryError(Exception):
    pass
class PathAlreadyExistsError(Exception):
    pass
class PathDoesNotExistError(Exception):
    pass
