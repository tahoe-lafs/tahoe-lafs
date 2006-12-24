#! /usr/bin/python

from zope.interface import Interface, implements
from twisted.internet import defer
from allmydata.util import bencode

# interesting feature ideas:
#  pubsub for MutableDirectoryNode: get rapid notification of changes
#  caused by someone else
#
#  bind a local physical directory to the MutableDirectoryNode contents:
#  each time the vdrive changes, update the local drive to match, and
#  vice versa.

class INode(Interface):
    """This is some sort of retrievable node."""
    pass

class IFileNode(Interface):
    """This is a file which can be retrieved."""
    pass

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

    def serialize_to_file():
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


class CHKFile(object):
    implements(INode, IFileNode)
    def __init__(self, uri):
        self.uri = uri
    def get_uri(self):
        return self.uri

class MutableSSKFile(object):
    implements(INode, IFileNode)
    def __init__(self, read_cap, write_cap):
        self.read_cap = read_cap
        self.write_cap = write_cap
    def get_read_capability(self):
        return self.read_cap
    def get_write_capability(self):
        return self.write_cap

class ImmutableSSKFile(object):
    implements(INode, IFileNode)
    def __init__(self, read_cap):
        self.read_cap = read_cap
    def get_read_capability(self):
        return self.read_cap


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
            assert isinstance(child_data, list)
            child_type = child_data[0]
            if child_type == "DIRECTORY":
                child = SubTreeNode(self.enclosing_tree)
                child.unserialize(child_data)
                self.node_children[name] = child
            elif child_type == "LINK":
                self.child_specifications[name] = child_data[1]
            else:
                raise RuntimeError("unknown serialized-node type '%s'" %
                                   child_type)

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
        f, filename = work_queue.create_tempfile()
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
        f, filename = work_queue.create_tempfile()
        self.serialize_to_file(f)
        f.close()
        work_queue.add_upload_ssk(filename, self.get_write_capability(),
                                  self.version)
        self.version = self.version + 1
        work_queue.add_delete_tempfile(filename)
        work_queue.add_retain_ssk(self.get_read_capability())



class CHKFileSpecification(object):
    implements(ISubTreeSpecification)
    stype = "CHK-File"
    def set_uri(self, uri):
        self.uri = uri
    def serialize(self):
        return (self.stype, self.uri)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.uri = data[1]

class ImmutableSSKFileSpecification(object):
    implements(ISubTreeSpecification)
    stype = "SSK-Readonly-File"
    def set_read_capability(self, read_cap):
        self.read_cap = read_cap
    def get_read_capability(self):
        return self.read_cap
    def serialize(self):
        return (self.stype, self.read_cap)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.read_cap = data[1]

class MutableSSKFileSpecification(ImmutableSSKFileSpecification):
    implements(ISubTreeSpecification)
    stype = "SSK-ReadWrite-File"
    def set_write_capability(self, write_cap):
        self.write_cap = write_cap
    def get_write_capability(self):
        return self.write_cap
    def serialize(self):
        return (self.stype, self.read_cap, self.write_cap)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.read_cap = data[1]
        self.write_cap = data[2]

class CHKDirectorySpecification(object):
    implements(ISubTreeSpecification)
    stype = "CHK-Directory"
    def set_uri(self, uri):
        self.uri = uri
    def serialize(self):
        return (self.stype, self.uri)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.uri = data[1]

class ImmutableSSKDirectorySpecification(object):
    implements(ISubTreeSpecification)
    stype = "SSK-Readonly-Directory"
    def set_read_capability(self, read_cap):
        self.read_cap = read_cap
    def get_read_capability(self):
        return self.read_cap
    def serialize(self):
        return (self.stype, self.read_cap)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.read_cap = data[1]

class MutableSSKDirectorySpecification(ImmutableSSKDirectorySpecification):
    implements(ISubTreeSpecification)
    stype = "SSK-ReadWrite-Directory"
    def set_write_capability(self, write_cap):
        self.write_cap = write_cap
    def get_write_capability(self):
        return self.write_cap
    def serialize(self):
        return (self.stype, self.read_cap, self.write_cap)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.read_cap = data[1]
        self.write_cap = data[2]

class LocalFileRedirection(object):
    implements(ISubTreeSpecification)
    stype = "LocalFile"
    def set_filename(self, filename):
        self.filename = filename
    def get_filename(self):
        return self.filename
    def serialize(self):
        return (self.stype, self.filename)

class QueenRedirection(object):
    implements(ISubTreeSpecification)
    stype = "QueenRedirection"
    def set_handle(self, handle):
        self.handle = handle
    def get_handle(self):
        return self.handle
    def serialize(self):
        return (self.stype, self.handle)

class HTTPRedirection(object):
    implements(ISubTreeSpecification)
    stype = "HTTPRedirection"
    def set_url(self, url):
        self.url = url
    def get_url(self):
        return self.url
    def serialize(self):
        return (self.stype, self.url)

class QueenOrLocalFileRedirection(object):
    implements(ISubTreeSpecification)
    stype = "QueenOrLocalFile"
    def set_filename(self, filename):
        self.filename = filename
    def get_filename(self):
        return self.filename
    def set_handle(self, handle):
        self.handle = handle
    def get_handle(self):
        return self.handle
    def serialize(self):
        return (self.stype, self.handle, self.filename)

def unserialize_subtree_specification(serialized_spec):
    assert isinstance(serialized_spec, tuple)
    for stype in [CHKDirectorySpecification,
                  ImmutableSSKDirectorySpecification,
                  MutableSSKDirectorySpecification,
                  LocalFileRedirection,
                  QueenRedirection,
                  HTTPRedirection,
                  QueenOrLocalFileRedirection,
                  ]:
        if tuple[0] == stype:
            spec = stype()
            spec.unserialize(serialized_spec)
            return spec
    raise RuntimeError("unable to unserialize subtree specification '%s'" %
                       (serialized_spec,))




class Opener(object):
    implements(IOpener)
    def __init__(self, queen):
        self._queen = queen
        self._cache = {}

    def open(self, subtree_specification, parent_is_mutable):
        spec = ISubTreeSpecification(subtree_specification)

        # is it in cache?
        if spec in self._cache:
            return defer.succeed(self._cache[spec])

        # is it a file?
        if isinstance(spec, CHKFileSpecification):
            return self._get_chk_file(spec)
        if isinstance(spec, (MutableSSKFileSpecification,
                             ImmutableSSKFileSpecification)):
            return self._get_ssk_file(spec)

        # is it a directory?
        if isinstance(spec, CHKDirectorySpecification):
            return self._get_chk_dir(spec, parent_is_mutable)
        if isinstance(spec, (ImmutableSSKDirectorySpecification,
                             MutableSSKDirectorySpecification)):
            return self._get_ssk_dir(spec)

        # is it a redirection to a file or directory?
        if isinstance(spec, LocalFileRedirection):
            return self._get_local_redir(spec)
        if isinstance(spec, QueenRedirection):
            return self._get_queen_redir(spec)
        if isinstance(spec, HTTPRedirection):
            return self._get_http_redir(spec)
        if isinstance(spec, QueenOrLocalFileRedirection):
            return self._get_queen_or_local_redir(spec)

        # none of the above
        raise RuntimeError("I do not know how to open '%s'" % (spec,))

    def _add_to_cache(self, subtree, spec):
        self._cache[spec] = subtree
        # TODO: remove things from the cache eventually
        return subtree

    def _get_chk_file(self, spec):
        subtree = CHKFile(spec.get_uri())
        return defer.succeed(subtree)

    def _get_ssk_file(self, spec):
        if isinstance(spec, MutableSSKFileSpecification):
            subtree = MutableSSKFile(spec.get_read_capability(),
                                     spec.get_write_capability())
        else:
            assert isinstance(spec, ImmutableSSKFileSpecification)
            subtree = ImmutableSSKFile(spec.get_read_cap())
        return defer.succeed(subtree)

    def _get_chk_dir(self, spec, parent_is_mutable):
        uri = spec.get_uri()
        if parent_is_mutable:
            subtree = MutableCHKDirectorySubTree()
            subtree.set_uri(uri)
        else:
            subtree = ImmutableDirectorySubTree()
        d = self.downloader.get_chk(uri)
        d.addCallback(subtree.unserialize)
        d.addCallback(self._add_to_cache, spec)
        return d

    def _get_ssk_dir(self, spec):
        mutable = isinstance(spec, ImmutableSSKDirectorySpecification)
        if mutable:
            subtree = ImmutableDirectorySubTree()
        else:
            assert isinstance(spec, MutableSSKDirectorySpecification)
            subtree = MutableSSKDirectorySubTree()
            subtree.set_write_capability(spec.get_write_capability())
        read_cap = spec.get_read_capability()
        subtree.set_read_capability(read_cap)
        d = self.downloader.get_ssk_latest(read_cap)
        def _set_version(res):
            version, data = res
            if mutable:
                subtree.set_version(version)
            return data
        d.addCallback(_set_version)
        d.addCallback(subtree.unserialize)
        d.addCallback(self._add_to_cache, spec)
        return d

    def _get_local_redir(self, spec):
        # there is a local file which contains a bencoded serialized
        # subtree specification.
        filename = spec.get_filename()
        # TODO: will this enable outsiders to cause us to read from
        # arbitrary files? Think about this.
        f = open(filename, "rb")
        data = bencode.bdecode(f.read())
        f.close()
        # note: we don't cache the contents of the file. TODO: consider
        # doing this based upon mtime. It is important that we be able to
        # notice if the file has been changed.
        new_spec = unserialize_subtree_specification(data)
        return self.open(new_spec, True)

    def _get_queen_redir(self, spec):
        # this specifies a handle for which the Queen maintains a
        # serialized subtree specification.
        handle = spec.get_handle()
        d = self._queen.callRemote("lookup_handle", handle)
        d.addCallback(unserialize_subtree_specification)
        d.addCallback(self.open, True)
        return d

    def _get_http_redir(self, spec):
        # this specifies a URL at which there is a bencoded serialized
        # subtree specification.
        url = spec.get_url()
        from twisted.web import client
        d = client.getPage(url)
        d.addCallback(bencode.bdecode)
        d.addCallback(unserialize_subtree_specification)
        d.addCallback(self.open, False)
        return d

    def _get_queen_or_local_redir(self, spec):
        # there is a local file which contains a bencoded serialized
        # subtree specification. The queen also has a copy. Whomever has
        # the higher version number wins.
        filename = spec.get_filename()
        f = open(filename, "rb")
        local_version, local_data = bencode.bdecode(f.read())
        f.close()
        handle = spec.get_handle()
        # TODO: pubsub so we can cache the queen's results
        d = self._queen.callRemote("lookup_handle", handle)
        def _got_queen(response):
            queen_version, queen_data = response
            if queen_version > local_version:
                return queen_data
            return local_data
        d.addCallback(_got_queen)
        d.addCallback(unserialize_subtree_specification)
        d.addCallback(self.open, True)
        return d

