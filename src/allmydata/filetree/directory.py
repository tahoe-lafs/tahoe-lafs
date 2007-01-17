
from zope.interface import implements
from twisted.internet import defer
from allmydata.filetree.interfaces import (INode,
                                           IDirectoryNode,
                                           ISubTree,
                                           IMutableSubTree)
from allmydata.util import bencode

# interesting feature ideas:
#  pubsub for MutableDirectoryNode: get rapid notification of changes
#  caused by someone else
#
#  bind a local physical directory to the MutableDirectoryNode contents:
#  each time the vdrive changes, update the local drive to match, and
#  vice versa.


class SubTreeNode:
    implements(INode, IDirectoryNode)

    def __init__(self, tree):
        self.enclosing_tree = tree
        # node_children maps child name to another SubTreeNode instance. This
        # is only for internal directory nodes. All Files and external links
        # are listed in child_specifications instead.
        self.node_children = {}
        # child_specifications maps child name to a string which describes
        # how to obtain the actual child. For example, if "foo.jpg" in this
        # node represents a FILE with a uri of "fooURI", then
        # self.child_specifications["foo.jpg"] = "(FILE,fooURI")
        self.child_specifications = {}

    def list(self):
        return sorted(self.node_children.keys() +
                      self.child_specifications.keys())

    def serialize(self):
        # note: this is a one-pass recursive serialization that will result
        # in the whole file table being held in memory. This is only
        # appropriate for directories with fewer than, say, 10k nodes. If we
        # support larger directories, we should turn this into some kind of
        # generator instead, and write the serialized data directly to a
        # tempfile.
        data = ["DIRECTORY"]
        for name in sorted(self.node_children.keys()):
            data.append(name)
            data.append(self.node_children[name].serialize())
        for name in sorted(self.child_specifications.keys()):
            data.append(name)
            data.append(self.child_specifications[name].serialize())
        return data

    def unserialize(self, data):
        assert data[0] == "DIRECTORY"
        assert len(data) % 2 == 1
        for i in range(1, len(data), 2):
            name = data[i]
            child_data = data[i+1]
            assert isinstance(child_data, (list, tuple))
            child_type = child_data[0]
            if child_type == "DIRECTORY":
                child = SubTreeNode(self.enclosing_tree)
                child.unserialize(child_data)
                self.node_children[name] = child
            else:
                self.child_specifications[name] = child_data

class _SubTreeMixin(object):

    def get(self, path, opener):
        """Return a Deferred that fires with the node at the given path, or
        None if there is no such node. This will traverse and even create
        subtrees as necessary."""
        d = self.get_node_for_path(path)
        def _done(res):
            if res == None:
                # traversal done, unable to find the node
                return None
            if res[0] == True:
                # found the node
                node = res[1]
                assert INode.providedBy(node)
                return node
            # otherwise, we must open and recurse into a new subtree
            next_subtree_spec = res[1]
            subpath = res[2]
            d1 = opener.open(next_subtree_spec, self.is_mutable())
            def _opened(next_subtree):
                assert ISubTree.providedBy(next_subtree)
                return next_subtree.get(subpath, opener)
            d1.addCallback(_opened)
            return d1
        d.addCallback(_done)
        return d

    def find_lowest_containing_subtree_for_path(self, path, opener):
        """Find the subtree which contains the target path, opening new
        subtrees if necessary. Return a Deferred that fires with (subtree,
        prepath, postpath), where prepath is the list of path components that
        got to the subtree, and postpath is the list of remaining path
        components (indicating a subpath within the resulting subtree). This
        will traverse and even create subtrees as necessary."""
        d = self.get_or_create_node_for_path(path)
        def _done(res):
            if res[0] == True:
                node = res[1]
                # found the node in our own tree. The whole path we were
                # given was used internally, and is therefore the postpath
                return (self, [], path)
            # otherwise, we must open and recurse into a new subtree
            ignored, next_subtree_spec, prepath, postpath = res
            d1 = opener.open(next_subtree_spec, self.is_mutable())
            def _opened(next_subtree):
                assert ISubTree.providedBy(next_subtree)
                f = next_subtree.find_lowest_containing_subtree_for_path
                return f(postpath, opener)
            d1.addCallback(_opened)
            def _found(res2):
                subtree, prepath2, postpath2 = res2
                return (subtree, prepath + prepath2, postpath2)
            d1.addCallback(_found)
            return d1
        d.addCallback(_done)
        return d


class _MutableSubTreeMixin(object):

    def add(self, path, child, opener, work_queue):
        assert len(path) > 0
        d = self.find_lowest_containing_subtree_for_path(path[:-1], opener)
        def _found(res):
            subtree, prepath, postpath = res
            assert IMutableSubTree.providedBy(subtree)
            # postpath is from the top of the subtree to the directory where
            # this child should be added. add_subpath wants the path from the
            # top of the subtree to the child itself, so we need to append
            # the child's name here.
            addpath = postpath + [path[-1]]
            # this add_path will cause some steps to be added, as well as the
            # internal node to be modified
            d1 = subtree.add_subpath(addpath, child, work_queue)
            if subtree.mutation_affects_parent():
                def _added(boxname):
                    work_queue.add_addpath(boxname, prepath)
                d1.addCallback(_added)
            return d1
        d.addCallback(_found)
        return d



class _DirectorySubTree(_SubTreeMixin):
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

    def unserialize(self, serialized_data):
        """Populate all nodes from serialized_data, previously created by
        calling my serialize() method. 'serialized_data' is a series of
        nested lists (s-expressions), probably recorded in bencoded form."""
        self.root = SubTreeNode(self)
        self.root.unserialize(serialized_data)
        return self

    def serialize(self):
        """Return a series of nested lists which describe my structure
        in a form that can be bencoded."""
        return self.root.serialize()

    def is_mutable(self):
        return IMutableSubTree.providedBy(self)

    def get_node_for_path(self, path):
        # this is restricted to traversing our own subtree.
        subpath = path
        node = self.root
        while subpath:
            name = subpath.pop(0)
            if name in node.node_children:
                node = node.node_children[name]
                assert isinstance(node, SubTreeNode)
                continue
            if name in node.child_specifications:
                # the path takes us out of this SubTree and into another
                next_subtree_spec = node.child_specifications[name]
                result = (False, next_subtree_spec, subpath)
                return defer.succeed(result)
            return defer.succeed(None)
        # we've run out of path components, so we must be at the terminus
        result = (True, node)
        return defer.succeed(result)

    def get_or_create_node_for_path(self, path):
        # this is restricted to traversing our own subtree, but will create
        # internal directory nodes as necessary
        prepath = []
        postpath = path[:]
        node = self.root
        while postpath:
            name = postpath.pop(0)
            prepath.append(name)
            if name in node.node_children:
                node = node.node_children[name]
                assert isinstance(node, SubTreeNode)
                continue
            if name in node.child_specifications:
                # the path takes us out of this SubTree and into another
                next_subtree_spec = node.child_specifications[name]
                result = (False, next_subtree_spec, prepath, postpath)
                return defer.succeed(result)
            # need to create a new node
            new_node = SubTreeNode(self)
            node.node_children[name] = new_node
            node = new_node
            continue
        # we've run out of path components, so we must be at the terminus
        result = (True, node)
        return defer.succeed(result)

class ImmutableDirectorySubTree(_DirectorySubTree):
    pass

class _MutableDirectorySubTree(_DirectorySubTree, _MutableSubTreeMixin):
    implements(IMutableSubTree)

    def add_subpath(self, subpath, child, work_queue):
        prepath = subpath[:-1]
        name = subpath[-1]
        d = self.get_node_for_path(prepath)
        def _found(results):
            assert results is not None
            assert results[0] == True
            node = results[1]
            # modify the in-RAM copy
            node.child_specifications[name] = child
            # now serialize and upload ourselves
            boxname = self.upload_my_serialized_form(work_queue)
            # our caller will perform the addpath, if necessary
            return boxname
        d.addCallback(_found)
        return d

    def serialize_to_file(self, f):
        f.write(bencode.bencode(self.serialize()))

class MutableCHKDirectorySubTree(_MutableDirectorySubTree):

    def mutation_affects_parent(self):
        return True

    def set_uri(self, uri):
        self.old_uri = uri

    def upload_my_serialized_form(self, work_queue):
        # this is the CHK form
        f, filename = work_queue.create_tempfile(".chkdir")
        self.serialize_to_file(f)
        f.close()
        boxname = work_queue.create_boxname()
        work_queue.add_upload_chk(filename, boxname)
        work_queue.add_delete_tempfile(filename)
        work_queue.add_retain_uri_from_box(boxname)
        work_queue.add_delete_box(boxname)
        work_queue.add_unlink_uri(self.old_uri)
        # TODO: think about how self.old_uri will get updated. I *think* that
        # this whole instance will get replaced, so it ought to be ok. But
        # this needs investigation.
        return boxname

class MutableSSKDirectorySubTree(_MutableDirectorySubTree):

    def new(self):
        _MutableDirectorySubTree.new(self)
        self.version = 0

    def mutation_affects_parent(self):
        return False

    def set_version(self, version):
        self.version = version

    def upload_my_serialized_form(self, work_queue):
        # this is the SSK form
        f, filename = work_queue.create_tempfile(".sskdir")
        self.serialize_to_file(f)
        f.close()
        work_queue.add_upload_ssk(filename, self.get_write_capability(),
                                  self.version)
        self.version = self.version + 1
        work_queue.add_delete_tempfile(filename)
        work_queue.add_retain_ssk(self.get_read_capability())

