
from zope.interface import implements
from allmydata.filetree.interfaces import (
    INode, IDirectoryNode, ISubTree,
    ICHKDirectoryNode, ISSKDirectoryNode,
    NoSuchChildError,
    )
from allmydata.filetree.basenode import BaseURINode
from allmydata import download
from allmydata.util import bencode

# interesting feature ideas:
#  pubsub for MutableDirectoryNode: get rapid notification of changes
#  caused by someone else
#
#  bind a local physical directory to the MutableDirectoryNode contents:
#  each time the vdrive changes, update the local drive to match, and
#  vice versa.

# from the itertools 'recipes' page
from itertools import izip, tee
def pairwise(iterable):
    "s -> (s0,s1), (s1,s2), (s2, s3), ..."
    a, b = tee(iterable)
    try:
        b.next()
    except StopIteration:
        pass
    return izip(a, b)

class SubTreeNode:
    implements(INode, IDirectoryNode)

    def __init__(self, tree):
        self.enclosing_tree = tree
        self.children = {}
#        # subdirectory_node_children maps child name to another SubTreeNode
#        # instance. This is only for internal directory nodes. All other
#        # nodes are listed in child_specifications instead.
#        self.subdirectory_node_children = {}
#        # child_specifications maps child name to a specification tuple which
#        # describes how to obtain the actual child. For example, if "foo.jpg"
#        # in this node represents a CHK-encoded FILE with a uri of "fooURI",
#        # then self.child_specifications["foo.jpg"] = ("CHKFILE","fooURI")
#        self.child_specifications = {}

    def is_directory(self):
        return True

    def list(self):
        return sorted(self.children.keys())

    def get(self, childname):
        if childname in self.children:
            return self.children[childname]
        else:
            raise NoSuchChildError("no child named '%s'" % (childname,))

    def get_subtree(self):
        return self.enclosing_tree

    def delete(self, childname):
        assert self.enclosing_tree.is_mutable()
        if childname in self.children:
            del self.children[childname]
        else:
            raise NoSuchChildError("no child named '%s'" % (childname,))

    def add_subdir(self, childname):
        assert childname not in self.children
        newnode = SubTreeNode(self.enclosing_tree)
        self.children[childname] = newnode
        return newnode

    def add(self, childname, node):
        assert childname not in self.children
        assert INode(node)
        self.children[childname] = node
        return self

    def serialize_node(self):
        # note: this is a one-pass recursive serialization that will result
        # in the whole file table being held in memory. This is only
        # appropriate for directories with fewer than, say, 10k nodes. If we
        # support larger directories, we should turn this into some kind of
        # generator instead, and write the serialized data directly to a
        # tempfile.
        #
        # [name1, child1, name2, child2..]
        #
        #  child1 is either a list for subdirs, or a string for non-subdirs

        data = []
        for name in sorted(self.children.keys()):
            data.append(name)
            data.append(self.children[name].serialize_node())
        return data

    def populate_node(self, data, node_maker):
        assert len(data) % 2 == 0
        for (name, child_data) in pairwise(data):
            if isinstance(child_data, (list, tuple)):
                child = SubTreeNode(self.enclosing_tree)
                child.populate_node(child_data)
            else:
                assert isinstance(child_data, str)
                child = node_maker(child_data)
            self.children[name] = child



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

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # self.populate_from_node must be defined by the subclass (CHK or
        # SSK), since it controls how the spec is interpreted. It will
        # probably use the contents of the node to figure out what to
        # download from the mesh, then pass this downloaded serialized data
        # to populate_from_data()
        raise NotImplementedError

    def populate_from_data(self, data, node_maker):
        self.root = SubTreeNode(self)
        self.root.populate_node(bencode.bdecode(data), node_maker)
        return self

    def serialize_subtree_to_file(self, f):
        sexprs = self.root.serialize_node()
        bencode.bwrite(sexprs, f)

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
            try:
                childnode = node.get(name)
            except NoSuchChildError:
                # The node *would* be in this subtree if it existed, but it
                # doesn't. Leave found_path and remaining_path alone, and
                # node points at the last parent node that was on the path.
                break
            if IDirectoryNode.providedBy(childnode):
                # recurse
                node = childnode
                found_path.append(name)
                remaining_path.pop(0)
                continue
            else:
                # the path takes us out of this subtree and into another
                node = childnode # next subtree node
                found_path.append(name)
                remaining_path.pop(0)
                break
        return (found_path, node, remaining_path)

class CHKDirectorySubTreeNode(BaseURINode):
    implements(ICHKDirectoryNode)
    prefix = "CHKDirectory"

    def get_uri(self):
        return self.uri


class CHKDirectorySubTree(_DirectorySubTree):
    # maybe mutable, maybe not

    def mutation_affects_parent(self):
        return True

    def set_uri(self, uri):
        self.old_uri = uri

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        assert ICHKDirectoryNode(node)
        self.mutable = parent_is_mutable
        d = downloader.download(node.get_uri(), download.Data())
        d.addCallback(self.populate_from_data, node_maker)
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


class SSKDirectorySubTreeNode(object):
    implements(INode, ISSKDirectoryNode)
    prefix = "SSKDirectory"

    def is_directory(self):
        return False
    def serialize_node(self):
        data = (self.read_cap, self.write_cap)
        return "%s:%s" % (self.prefix, bencode.bencode(data))
    def populate_node(self, data, node_maker):
        assert data.startswith(self.prefix + ":")
        capdata = data[len(self.prefix)+1:]
        self.read_cap, self.write_cap = bencode.bdecode(capdata)

    def get_read_capability(self):
        return self.read_cap
    def get_write_capability(self):
        return self.write_cap


class SSKDirectorySubTree(_DirectorySubTree):

    def new(self):
        _DirectorySubTree.new(self)
        self.version = 0
        # TODO: populate

    def mutation_affects_parent(self):
        return False

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        node = ISSKDirectoryNode(node)
        self.read_capability = node.get_read_capability()
        self.write_capability = node.get_write_capability()
        self.mutable = bool(self.write_capability)
        d = downloader.download_ssk(self.read_capability, download.Data())
        d.addCallback(self.populate_from_data, node_maker)
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

