
from zope.interface import Interface

class INode(Interface):
    """This is some sort of retrievable node."""

class IFileNode(Interface):
    """This is a file which can be retrieved."""

class IDirectoryNode(Interface):
    """This is a directory which can be listed."""
    def list():
        """Return a list of names which are children of this node."""


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

    def get(path, opener):
        """Return a Deferred that fires with the node at the given path, or
        None if there is no such node. This will traverse and create subtrees
        as necessary."""

    def add(path, child, opener, work_queue):
        """Add 'child' (which must implement INode) to the tree at 'path'
        (which must be a list of pathname components). This will schedule all
        the work necessary to cause the child to be added reliably."""

    def find_lowest_containing_subtree_for_path(path, opener):
        # not for external use. This is used internally by add().
        """Find the subtree which contains the target path, opening new
        subtrees if necessary. Return a Deferred that fires with (subtree,
        prepath, postpath), where prepath is the list of path components that
        got to the subtree, and postpath is the list of remaining path
        components (indicating a subpath within the resulting subtree). This
        will traverse and even create subtrees as necessary."""


    def is_mutable():
        """This returns True if we have the ability to modify this subtree.
        If this returns True, this reference may be adapted to
        IMutableSubTree to actually exercise these mutation rights.
        """

    def get_node_for_path(path):
        """Ask this subtree to follow the path through its internal nodes. If
        the path terminates within this subtree, return (True, node), where
        'node' implements INode (and also IMutableNode if this subtree
        is_mutable). If the path takes us beyond this subtree, return (False,
        next_subtree_spec, subpath), where 'next_subtree_spec' is a string
        that can be passed to an Opener to create a new subtree, and
        'subpath' is the subset of 'path' that can be passed to this new
        subtree. If the path cannot be found within the subtree (and it is
        not in the domain of some child subtree), return None.
        """

    def get_or_create_node_for_path(path):
        """Like get_node_for_path, but instead of returning None, the subtree
        will create internal nodes as necessary. Therefore it always returns
        either (True, node), or (False, next_subtree_spec, prepath, postpath).
        """

    def serialize():
        """Return a series of nested lists which describe my structure
        in a form that can be bencoded."""

    def unserialize(serialized_data):
        """Populate all nodes from serialized_data, previously created by
        calling my serialize() method. 'serialized_data' is a series of
        nested lists (s-expressions), probably recorded in bencoded form."""


class IMutableSubTree(Interface):
    def mutation_affects_parent():
        """This returns True for CHK nodes where you must inform the parent
        of the new URI each time you change the child subtree. It returns
        False for SSK nodes (or other nodes which have a pointer stored in
        some mutable form).
        """

    def add_subpath(subpath, child_spec, work_queue):
        """Ask this subtree to add the given child to an internal node at the
        given subpath. The subpath must not exit the subtree through another
        subtree (specifically get_subtree_for_path(subpath) must either
        return None or (True,node), and in the latter case, this subtree will
        create new internal nodes as necessary).

        The subtree will probably serialize itself to a file and add steps to
        the work queue to accomplish its goals.

        This returns a Deferred (the value of which is ignored) when
        everything has been added to the work queue.
        """

    def serialize_to_file(f):
        """Write a bencoded data structure to the given filehandle that can
        be used to reproduce the contents of this subtree."""

class ISubTreeSpecification(Interface):
    def serialize():
        """Return a tuple that describes this subtree. This tuple can be
        passed to IOpener.open() to reconstitute the subtree."""

class IOpener(Interface):
    def open(subtree_specification, parent_is_mutable):
        """I can take an ISubTreeSpecification-providing specification of a
        subtree and return a Deferred which fires with an instance that
        provides ISubTree (and maybe even IMutableSubTree). I probably do
        this by performing network IO: reading a file from the mesh, or from
        local disk, or asking some central-service node for the current
        value."""

