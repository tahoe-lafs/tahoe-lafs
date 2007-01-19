
from zope.interface import implements
from allmydata.filetree.interfaces import (INode,
                                           IDirectoryNode,
                                           ISubTree,
                                           ICHKDirectoryNode, ISSKDirectoryNode,
                                           NoSuchChildError,
                                           )
from allmydata import download
from allmydata.util import bencode

# interesting feature ideas:
#  pubsub for MutableDirectoryNode: get rapid notification of changes
#  caused by someone else
#
#  bind a local physical directory to the MutableDirectoryNode contents:
#  each time the vdrive changes, update the local drive to match, and
#  vice versa.


def to_node(spec):
    # TODO
    pass
def to_spec(node):
    # TODO
    pass


class SubTreeNode:
    implements(INode, IDirectoryNode)

    def __init__(self, tree):
        self.enclosing_tree = tree
        # subdirectory_node_children maps child name to another SubTreeNode
        # instance. This is only for internal directory nodes. All other
        # nodes are listed in child_specifications instead.
        self.subdirectory_node_children = {}
        # child_specifications maps child name to a specification tuple which
        # describes how to obtain the actual child. For example, if "foo.jpg"
        # in this node represents a CHK-encoded FILE with a uri of "fooURI",
        # then self.child_specifications["foo.jpg"] = ("CHKFILE","fooURI")
        self.child_specifications = {}

    def is_directory(self):
        return True

    def list(self):
        return sorted(self.subdirectory_node_children.keys() +
                      self.child_specifications.keys())

    def get(self, childname):
        if childname in self.subdirectory_node_children:
            return self.subdirectory_node_children[childname]
        elif childname in self.child_specifications:
            return to_node(self.child_specifications[childname])
        else:
            raise NoSuchChildError("no child named '%s'" % (childname,))

    def get_subtree(self):
        return self.enclosing_tree

    def delete(self, childname):
        assert self.enclosing_tree.is_mutable()
        if childname in self.subdirectory_node_children:
            del self.subdirectory_node_children[childname]
        elif childname in self.child_specifications:
            del self.child_specifications[childname]
        else:
            raise NoSuchChildError("no child named '%s'" % (childname,))

    def add_subdir(self, childname):
        assert childname not in self.subdirectory_node_children
        assert childname not in self.child_specifications
        newnode = SubTreeNode(self.enclosing_tree)
        self.subdirectory_node_children[childname] = newnode
        return newnode

    def add(self, childname, node):
        assert childname not in self.subdirectory_node_children
        assert childname not in self.child_specifications
        spec = to_spec(node)
        self.child_specifications[childname] = spec
        return self

    def serialize_to_sexprs(self):
        # note: this is a one-pass recursive serialization that will result
        # in the whole file table being held in memory. This is only
        # appropriate for directories with fewer than, say, 10k nodes. If we
        # support larger directories, we should turn this into some kind of
        # generator instead, and write the serialized data directly to a
        # tempfile.
        #
        # ["DIRECTORY", name1, child1, name2, child2..]

        data = ["DIRECTORY"]
        for name in sorted(self.node_children.keys()):
            data.append(name)
            data.append(self.node_children[name].serialize())
        for name in sorted(self.child_specifications.keys()):
            data.append(name)
            data.append(self.child_specifications[name].serialize())
        return data

    def populate_from_sexprs(self, data):
        assert data[0] == "DIRECTORY"
        assert len(data) % 2 == 1
        for i in range(1, len(data), 2):
            name = data[i]
            child_data = data[i+1]
            assert isinstance(child_data, (list, tuple))
            child_type = child_data[0]
            if child_type == "DIRECTORY":
                child = SubTreeNode(self.enclosing_tree)
                child.populate_from_sexprs(child_data)
                self.node_children[name] = child
            else:
                self.child_specifications[name] = child_data



class _DirectorySubTree(object):
    """I represent a set of connected directories that all share the same
    access control: any given person can read or write anything in this tree
    as a group, and it is not possible to give access to some pieces of this
    tree and not to others. Read-only access to individual files can be
    granted independently, of course, but through an unnamed URI, not as a
    subdirectory.

    Each internal directory is represented by a separate Node.

    This is an abstract base class. Individual subclasses will implement
    various forms of serialization, persistence, and mutability.

    """
    implements(ISubTree)


    def new(self):
        self.root = SubTreeNode(self)
        self.mutable = True # sure, why not

    def populate_from_specification(self, spec, parent_is_mutable, downloader):
        return self.populate_from_node(to_node(spec),
                                       parent_is_mutable, downloader)

    def populate_from_data(self, data):
        self.root = SubTreeNode()
        self.root.populate_from_sexprs(bencode.bdecode(data))
        return self

    def serialize(self):
        """Return a series of nested lists which describe my structure
        in a form that can be bencoded."""
        return self.root.serialize_to_sexprs()

    def serialize_to_file(self, f):
        f.write(bencode.bencode(self.serialize()))

    def is_mutable(self):
        return self.mutable

    def get_node_for_path(self, path):
        # this is restricted to traversing our own subtree. Returns
        # (found_path, node, remaining_path)
        found_path = []
        remaining_path = path[:]
        node = self.root
        while remaining_path:
            name = remaining_path[0]
            if name in node.node_children:
                node = node.node_children[name]
                assert isinstance(node, SubTreeNode)
                found_path.append(name)
                remaining_path.pop(0)
                continue
            if name in node.child_specifications:
                # the path takes us out of this subtree and into another
                next_subtree_spec = node.child_specifications[name]
                node = to_node(next_subtree_spec)
                found_path.append(name)
                remaining_path.pop(0)
                break
            # The node *would* be in this subtree if it existed, but it
            # doesn't. Leave found_path and remaining_path alone, and node
            # points at the last parent node that was on the path.
            break
        return (found_path, node, remaining_path)

class CHKDirectorySubTree(_DirectorySubTree):
    # maybe mutable, maybe not

    def mutation_affects_parent(self):
        return True

    def set_uri(self, uri):
        self.old_uri = uri

    def populate_from_node(self, node, parent_is_mutable, downloader):
        node = ICHKDirectoryNode(node)
        self.mutable = parent_is_mutable
        d = downloader.download(node.get_uri(), download.Data())
        d.addCallback(self.populate_from_data)
        return d

    def update(self, prepath, work_queue):
        # this is the CHK form
        f, filename = work_queue.create_tempfile(".chkdir")
        self.serialize_to_file(f)
        f.close()
        boxname = work_queue.create_boxname()
        work_queue.add_upload_chk(filename, boxname)
        work_queue.add_delete_tempfile(filename)
        work_queue.add_retain_uri_from_box(boxname)
        work_queue.add_delete_box(boxname)
        work_queue.add_addpath(boxname, prepath)
        work_queue.add_unlink_uri(self.old_uri)
        # TODO: think about how self.old_uri will get updated. I *think* that
        # this whole instance will get replaced, so it ought to be ok. But
        # this needs investigation.
        return boxname

class SSKDirectorySubTree(_DirectorySubTree):

    def new(self):
        _DirectorySubTree.new(self)
        self.version = 0
        # TODO: populate

    def mutation_affects_parent(self):
        return False

    def populate_from_node(self, node, parent_is_mutable, downloader):
        node = ISSKDirectoryNode(node)
        self.read_capability = node.get_read_capability()
        self.write_capability = node.get_write_capability()
        self.mutable = bool(self.write_capability)
        d = downloader.download_ssk(self.read_capability, download.Data())
        d.addCallback(self.populate_from_data)
        return d

    def set_version(self, version):
        self.version = version

    def upload_my_serialized_form(self, work_queue):
        # this is the SSK form
        f, filename = work_queue.create_tempfile(".sskdir")
        self.serialize_to_file(f)
        f.close()
        work_queue.add_upload_ssk(filename, self.write_capability,
                                  self.version)
        self.version = self.version + 1
        work_queue.add_delete_tempfile(filename)
        work_queue.add_retain_ssk(self.read_capability)

