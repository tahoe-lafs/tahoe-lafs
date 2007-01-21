
from zope.interface import implements
from twisted.internet import defer
from cStringIO import StringIO
from allmydata.filetree.interfaces import (
    INode, INodeMaker, IDirectoryNode, ISubTree,
    ICHKDirectoryNode, ISSKDirectoryNode,
    NoSuchChildError,
    )
from allmydata.filetree.basenode import BaseDataNode
from allmydata import download
from allmydata.util import bencode

# interesting feature ideas:
#  pubsub for MutableDirectoryNode: get rapid notification of changes
#  caused by someone else
#
#  bind a local physical directory to the MutableDirectoryNode contents:
#  each time the vdrive changes, update the local drive to match, and
#  vice versa.

from itertools import islice, izip
def in_pairs(iterable):
    "s -> (s0,s1), (s2,s3), (s4,s5), ..."
    a = islice(iterable, 0, None, 2)
    b = islice(iterable, 1, None, 2)
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

    def list(self):
        return self.children

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

    def populate_dirnode(self, data, node_maker):
        assert INodeMaker(node_maker)
        assert len(data) % 2 == 0
        for (name, child_data) in in_pairs(data):
            if isinstance(child_data, (list, tuple)):
                child = SubTreeNode(self.enclosing_tree)
                child.populate_dirnode(child_data, node_maker)
            else:
                assert isinstance(child_data, str)
                child = node_maker.make_node_from_serialized(child_data)
            self.children[name] = child

    def is_leaf_subtree(self):
        return False


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
        # create a new, empty directory
        self.root = SubTreeNode(self)
        self.mutable = True # sure, why not
        return self

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # self.populate_from_node must be defined by the subclass (CHK or
        # SSK), since it controls how the spec is interpreted. It will
        # probably use the contents of the node to figure out what to
        # download from the mesh, then pass this downloaded serialized data
        # to populate_from_data()
        raise NotImplementedError

    def _populate_from_data(self, data, node_maker):
        self.root = SubTreeNode(self)
        self.root.populate_dirnode(bencode.bdecode(data), node_maker)
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

class LocalFileSubTreeNode(BaseDataNode):
    prefix = "LocalFileDirectory"

    def new(self, filename):
        self.filename = filename
        return self

    def get_base_data(self):
        return self.filename
    def set_base_data(self, data):
        self.filename = data

    def is_leaf_subtree(self):
        return False

class LocalFileSubTree(_DirectorySubTree):
    node_class = LocalFileSubTreeNode

    def new(self, filename):
        self.filename = filename
        return _DirectorySubTree.new(self)

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        self.mutable = True # probably
        self.filename = node.filename
        f = open(self.filename, "rb")
        data = f.read()
        f.close()
        d = defer.succeed(data)
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def create_node_now(self):
        return LocalFileSubTreeNode().new(self.filename)

    def _update(self):
        f = open(self.filename, "wb")
        self.serialize_subtree_to_file(f)
        f.close()

    def update_now(self, uploader):
        self._update()
        return self.create_node_now()

    def update(self, work_queue):
        # TODO: this may suffer from the same execute-too-early problem as
        # redirect.LocalFileRedirection
        self._update()
        return None


class CHKDirectorySubTreeNode(BaseDataNode):
    implements(ICHKDirectoryNode)
    prefix = "CHKDirectory"

    def get_base_data(self):
        return self.uri
    def set_base_data(self, data):
        self.uri = data

    def get_uri(self):
        return self.uri

    def is_leaf_subtree(self):
        return False


class CHKDirectorySubTree(_DirectorySubTree):
    # maybe mutable, maybe not
    node_class = CHKDirectorySubTreeNode

    def set_uri(self, uri):
        self.uri = uri

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        assert ICHKDirectoryNode(node)
        self.mutable = parent_is_mutable
        d = downloader.download(node.get_uri(), download.Data())
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def create_node_now(self):
        return CHKDirectorySubTreeNode().new(self.uri)

    def update_now(self, uploader):
        f = StringIO()
        self.serialize_subtree_to_file(f)
        data = f.getvalue()
        d = uploader.upload_data(data)
        def _uploaded(uri):
            self.uri = uri
            return self.create_node_now()
        d.addCallback(_uploaded)
        return d

    def update(self, workqueue):
        # this is the CHK form
        old_uri = self.uri
        f, filename = workqueue.create_tempfile(".chkdir")
        self.serialize_subtree_to_file(f)
        f.close()
        boxname = workqueue.create_boxname()
        workqueue.add_upload_chk(filename, boxname)
        workqueue.add_delete_tempfile(filename)
        workqueue.add_retain_uri_from_box(boxname)
        workqueue.add_delete_box(boxname)
        workqueue.add_unlink_uri(old_uri)
        # TODO: think about how self.old_uri will get updated. I *think* that
        # this whole instance will get replaced, so it ought to be ok. But
        # this needs investigation.

        # mutation affects our parent, so we return a boxname for them
        return boxname


class SSKDirectorySubTreeNode(object):
    implements(INode, ISSKDirectoryNode)
    prefix = "SSKDirectory"

    def serialize_node(self):
        data = (self.read_cap, self.write_cap)
        return "%s:%s" % (self.prefix, bencode.bencode(data))
    def populate_node(self, body, node_maker):
        self.read_cap, self.write_cap = bencode.bdecode(body)

    def get_read_capability(self):
        return self.read_cap
    def get_write_capability(self):
        return self.write_cap
    def set_read_capability(self, read_cap):
        self.read_cap = read_cap
    def set_write_capability(self, write_cap):
        self.write_cap = write_cap

    def is_leaf_subtree(self):
        return False


class SSKDirectorySubTree(_DirectorySubTree):
    node_class = SSKDirectorySubTreeNode

    def new(self):
        _DirectorySubTree.new(self)
        self.version = 0
        # TODO: populate
        return self

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        node = ISSKDirectoryNode(node)
        self.read_capability = node.get_read_capability()
        self.write_capability = node.get_write_capability()
        self.mutable = bool(self.write_capability)
        d = downloader.download_ssk(self.read_capability, download.Data())
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def set_version(self, version):
        self.version = version

    def create_node_now(self):
        node = SSKDirectorySubTreeNode()
        node.set_read_capability(self.read_capability)
        node.set_write_capability(self.write_capability)
        return node

    def update_now(self, uploader):
        if not self.write_capability:
            raise RuntimeError("This SSKDirectorySubTree is not mutable")

        f = StringIO()
        self.serialize_subtree_to_file(f)
        data = f.getvalue()

        self.version += 1
        d = uploader.upload_ssk_data(self.write_capability, self.version, data)
        d.addCallback(lambda ignored: self.create_node_now())
        return d

    def update(self, workqueue):
        # this is the SSK form
        f, filename = workqueue.create_tempfile(".sskdir")
        self.serialize_subtree_to_file(f)
        f.close()

        oldversion = self.version
        self.version = self.version + 1

        workqueue.add_upload_ssk(self.write_capability, oldversion, filename)
        workqueue.add_delete_tempfile(filename)
        workqueue.add_retain_ssk(self.read_capability)
        # mutation does not affect our parent
        return None
