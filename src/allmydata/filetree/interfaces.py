
from zope.interface import Interface

class INode(Interface):
    """This is some sort of retrievable node. All objects which implement
    other I*Node interfaces also implement this one."""

    # the INode-implementing class must have an attribute named .prefix which
    # contains a string.

    def serialize_node():
        """Return a data structure which contains enough information to build
        this node again in the future (by calling
        INodeMaker.make_node_from_serialized(). For IDirectoryNodes, this
        will be a list. For all other nodes this will be a string of the form
        'prefix:body', where 'prefix' must be the same as the class attribute
        .prefix ."""
    def populate_node(body, node_maker):
        """INodeMaker.make_node_from_serialized() will first use the prefix
        from the .prefix attribute to decide what kind of Node to create.
        They will then call this populate_node() method with the body to
        populate the new Node. 'node_maker' provides INodeMaker, which
        provides that same make_node_from_serialized function to create any
        internal child nodes that might be necessary."""

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

    # All ISubTree-providing instances must have a class-level attribute
    # named .node_class which references the matching INode-providing class.
    # This is used by the ISubTreeMaker to turn nodes into subtrees.

    def populate_from_node(node, parent_is_mutable, node_maker, downloader):
        """Subtrees are created by ISubTreeMaker.open() being called with an
        INode which describes both the kind of subtree to be created and a
        way to obtain its contents. open() uses the node to create a new
        instance of the appropriate subtree type, then calls this
        populate_from_node() method.

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

    def update_now(uploader):
        """Perform whatever work is necessary to record this subtree to
        persistent storage.

        This returns an Inode, or a Deferred that fires (with an INode) when
        the subtree has been persisted.

        For directory subtrees, this will cause the subtree to serialize
        itself to a file, then upload this file to the mesh, then create an
        INode-providing instance which describes where the file wound up. For
        redirections, this will cause the subtree to modify the redirection's
        persistent storage, then return the (unmodified) INode that describes
        the redirection.

        This form does not use the workqueue. If the node is shut down before
        the Deferred fires, a redirection or SSK subtree might be left in its
        previous state, or it might have been updated.
        """

    def update(workqueue):
        """Perform and schedule whatever work is necessary to record this
        subtree to persistent storage.

        Returns a boxname or None, synchronously. This function does not
        return a Deferred.

        If the parent subtree needs to be modified with the new identity of
        this subtree (i.e. for CHKDirectorySubTree instances), this will
        return a boxname in which the serialized INode will be placed once
        the added workqueue steps have completed. The caller should add
        'addpath' steps to the workqueue using this boxname (which will
        eventually cause recursion on other subtrees, until some subtree is
        updated which does not require notifying the parent). update() will
        add steps to delete the box at the end of the workqueue.

        If the parent subtree does not need to be modified (i.e. for
        SSKDirectorySubTree instances, or redirections), this will return
        None.

        This is like update_now(), but uses the workqueue to insure
        consistency in the face of node shutdowns. Once our intentions have
        been recorded in the workqueue, if the node is shut down before the
        upload steps have completed, the update will eventually complete the
        next time the node is started.
        """

    def create_node_now():
        """FOR TESTING ONLY. Immediately create and return an INode which
        describes the current state of this subtree. This does not perform
        any upload or persistence work, and thus depends upon any internal
        state having been previously set correctly. In general this will
        return the correct value for subtrees which have just been created
        (and not yet mutated). It will also return the correct value for
        subtrees which do not change their identity when they are mutated
        (SSKDirectorySubTrees and redirections).
        """

class INodeMaker(Interface):
    def make_node_from_serialized(serialized):
        """Turn a string into an INode, which contains information about the
        file or directory (like a URI), but does not contain the actual
        contents. An ISubTreeMaker can be used later to retrieve the contents
        (which means downloading the file if this is an IFileNode, or perhaps
        creating a new subtree from the contents)."""

class ISubTreeMaker(Interface):
    def make_subtree_from_node(node, parent_is_mutable):
        """Turn an INode into an ISubTree.

        I accept an INode-providing specification of a subtree, and return a
        Deferred that fires with an ISubTree-providing instance. I will
        perform network IO and download the serialized data that the INode
        references, if necessary, or ask the queen (or other provider) for a
        pointer, or read it from local disk.
        """


class IVirtualDrive(Interface):

    def __init__(workqueue, downloader, root_node):
        pass

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
