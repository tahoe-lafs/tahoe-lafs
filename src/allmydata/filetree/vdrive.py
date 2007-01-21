
from zope.interface import implements
from twisted.internet import defer
from allmydata.filetree import directory, file, redirect
from allmydata.filetree.interfaces import (
    IVirtualDrive, ISubTreeMaker,
    INodeMaker, INode, ISubTree, IFileNode, IDirectoryNode,
    NoSuchDirectoryError, NoSuchChildError, PathAlreadyExistsError,
    PathDoesNotExistError,
    )
from allmydata.upload import IUploadable

from allmydata.filetree.nodemaker import NodeMaker

all_openable_subtree_types = [
    directory.LocalFileSubTree,
    directory.CHKDirectorySubTree,
    directory.SSKDirectorySubTree,
    redirect.LocalFileRedirection,
    redirect.QueenRedirection,
    redirect.QueenOrLocalFileRedirection,
    redirect.HTTPRedirection,
    ]

class SubTreeMaker(object):
    implements(ISubTreeMaker)

    def __init__(self, queen, downloader):
        # this is created with everything it might need to download and
        # create subtrees. That means a Downloader and a reference to the
        # queen.
        self._queen = queen
        self._downloader = downloader
        self._node_maker = NodeMaker()
        self._cache = {}

    def _create(self, node, parent_is_mutable):
        assert INode(node)
        assert INodeMaker(self._node_maker)
        for subtree_class in all_openable_subtree_types:
            if isinstance(node, subtree_class.node_class):
                subtree = subtree_class()
                d = subtree.populate_from_node(node,
                                               parent_is_mutable,
                                               self._node_maker,
                                               self._downloader)
                return d
        raise RuntimeError("unable to handle subtree specification '%s'"
                           % (node,))

    def make_subtree_from_node(self, node, parent_is_mutable):
        assert INode(node)
        assert not isinstance(node, IDirectoryNode)

        # is it in cache? To check this we need to use the node's serialized
        # form, since nodes are instances and don't compare by value
        node_s = node.serialize_node()
        if node_s in self._cache:
            return defer.succeed(self._cache[node_s])

        d = defer.maybeDeferred(self._create, node, parent_is_mutable)
        d.addCallback(self._add_to_cache, node_s)
        return d

    def _add_to_cache(self, subtree, node_s):
        self._cache[node_s] = subtree
        # TODO: remove things from the cache eventually
        return subtree



class VirtualDrive(object):
    implements(IVirtualDrive)

    def __init__(self, workqueue, downloader, root_node):
        assert INode(root_node)
        self.workqueue = workqueue
        workqueue.set_vdrive(self)
        # TODO: queen?
        self.queen = None
        self.root_node = root_node
        self.subtree_maker = SubTreeMaker(self.queen, downloader)

    # these methods are used to walk through our subtrees

    def _get_root(self):
        return self.subtree_maker.make_subtree_from_node(self.root_node, False)

    def _get_node(self, path):
        d = self._get_closest_node(path)
        def _got_node((node, remaining_path)):
            if remaining_path:
                return None
            return node
        d.addCallback(_got_node)
        return d

    def _get_closest_node(self, path):
        """Find the closest directory node parent for the desired path.
        Return a Deferred that fires with (node, remaining_path).
        """
        d = self._get_root()
        d.addCallback(self._get_closest_node_1, path)
        return d

    def _get_closest_node_1(self, subtree, path):
        (found_path, node, remaining_path) = subtree.get_node_for_path(path)
        parent_is_mutable = subtree.is_mutable()
        if IDirectoryNode.providedBy(node):
            # traversal done
            return (node, remaining_path)
        # otherwise, we must open and recurse into a new subtree
        d = self.subtree_maker.make_subtree_from_node(node, parent_is_mutable)
        def _opened(next_subtree):
            next_subtree = ISubTree(next_subtree)
            return self._get_closest_node_1(next_subtree, remaining_path)
        d.addCallback(_opened)
        return d

    def _get_directory(self, path):
        """Return a Deferred that fires with the IDirectoryNode at the given
        path, or raise NoSuchDirectoryError if there is no such node. This
        will traverse subtrees as necessary."""
        d = self._get_node(path)
        def _got_directory(node):
            if not node:
                raise NoSuchDirectoryError
            assert IDirectoryNode(node)
            return node
        d.addCallback(_got_directory)
        return d

    def _get_file(self, path):
        """Return a Deferred that files with an IFileNode at the given path,
        or raises a NoSuchDirectoryError or NoSuchChildError, or some other
        error if the path refers to something other than a file."""
        d = self._get_node(path)
        def _got_node(node):
            if not node:
                raise NoSuchChildError
            return IFileNode(node)
        d.addCallback(_got_node)
        return d

    def _get_file_uri(self, path):
        d = self._get_file(path)
        d.addCallback(lambda filenode: filenode.get_uri())
        return d

    def _child_should_not_exist(self, path):
        d = self._get_node(path)
        def _got_node(node):
            if node is not None:
                raise PathAlreadyExistsError
        d.addCallback(_got_node)
        return d

    def _child_should_exist(self, path):
        d = self._get_node(path)
        def _got_node(node):
            if node is None:
                raise PathDoesNotExistError
        d.addCallback(_got_node)
        return d

    def _get_closest_node_and_prepath(self, path):
        d = self._get_closest_node(path)
        def _got_closest((node, remaining_path)):
            prepath_len = len(path) - len(remaining_path)
            prepath = path[:prepath_len]
            assert path[prepath_len:] == remaining_path, "um, path=%s, prepath=%s, prepath_len=%d, remaining_path=%s" % (path, prepath, prepath_len, remaining_path)
            return (prepath, node, remaining_path)
        d.addCallback(_got_closest)
        return d

    def _get_subtree_path(self, path):
        # compute a list of [(subtree1, subpath1), ...], which represents
        # which parts of 'path' traverse which subtrees. This can be used to
        # present the virtual drive to the user in a form that includes
        # redirection nodes (which do not consume path segments), or to
        # figure out which subtrees need to be updated when the identity of a
        # lower subtree (i.e. CHK) is changed.
        pass # TODO

    # these are called by the workqueue

    def add(self, path, new_node):
        parent_path = path[:-1]
        new_node_path = path[-1]
        d = self._get_closest_node_and_prepath(parent_path)
        def _got_closest((prepath, node, remaining_path)):
            # now tell it to create any necessary parent directories
            remaining_path = remaining_path[:]
            while remaining_path:
                node = node.add_subdir(remaining_path.pop(0))
            # 'node' is now the directory where the child wants to go
            return node, prepath
        d.addCallback(_got_closest)
        def _add_new_node((node, prepath)):
            node.add(new_node_path, new_node)
            subtree = node.get_subtree()
            # now, tell the subtree to serialize and upload itself, using the
            # workqueue.
            boxname = subtree.update(self.workqueue)
            if boxname:
                # the parent needs to be notified, so queue a step to notify
                # them (using 'prepath')
                self.workqueue.add_addpath(boxname, prepath)
            return self # TODO: what wold be the most useful?
        d.addCallback(_add_new_node)
        return d

    # these are user-visible

    def list(self, path):
        assert isinstance(path, list)
        d = self._get_directory(path)
        d.addCallback(lambda node: node.list())
        return d

    def download(self, path, target):
        assert isinstance(path, list)
        d = self._get_file_uri(path)
        d.addCallback(lambda uri: self.downloader.download(uri, target))
        return d

    def upload_now(self, path, uploadable):
        assert isinstance(path, list)
        # note: the first few steps of this do not use the workqueue, but I
        # think things should remain consistent anyways. If the node is shut
        # down before the file has finished uploading, then we forget all
        # abou the file.
        uploadable = IUploadable(uploadable)
        d = self._child_should_not_exist(path)
        # then we upload the file
        d.addCallback(lambda ignored: self.uploader.upload(uploadable))
        def _uploaded(uri):
            assert isinstance(uri, str)
            new_node = file.CHKFileNode().new(uri)
            boxname = self.workqueue.create_boxname(new_node)
            self.workqueue.add_addpath(boxname, path)
            self.workqueue.add_delete_box(boxname)
        d.addCallback(_uploaded)
        return d

    def upload_later(self, path, filename):
        assert isinstance(path, list)
        boxname = self.workqueue.create_boxname()
        self.workqueue.add_upload_chk(filename, boxname)
        self.workqueue.add_addpath(boxname, path)
        self.workqueue.add_delete_box(boxname)

    def delete(self, path):
        assert isinstance(path, list)
        parent_path = path[:-1]
        orphan_path = path[-1]
        d = self._get_closest_node_and_prepath(parent_path)
        def _got_parent((prepath, node, remaining_path)):
            assert not remaining_path
            node.delete(orphan_path)
            # now serialize and upload
            subtree = node.get_subtree()
            boxname = subtree.update(self.workqueue)
            if boxname:
                self.workqueue.add_addpath(boxname, prepath)
                self.workqueue.add_delete_box(boxname)
            return self
        d.addCallback(_got_parent)
        return d

    def add_node(self, path, node):
        assert isinstance(path, list)
        assert INode(node)
        assert not IDirectoryNode.providedBy(node)
        boxname = self.workqueue.create_boxname(node)
        self.workqueue.add_addpath(boxname, path)
        self.workqueue.add_delete_box(boxname)

