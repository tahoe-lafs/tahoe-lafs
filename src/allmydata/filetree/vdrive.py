
import os.path
from zope.interface import implements
from twisted.internet import defer
from allmydata.filetree import directory, redirect
from allmydata.filetree.interfaces import (
    IVirtualDrive, ISubTreeMaker,
    INodeMaker, INode, ISubTree, IFileNode, IDirectoryNode,
    NoSuchDirectoryError, NoSuchChildError, PathAlreadyExistsError,
    PathDoesNotExistError,
    )
from allmydata.interfaces import IDownloader, IUploader, IWorkQueue

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
        assert IDownloader(downloader)
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
    debug = False

    def __init__(self, workqueue, downloader, uploader, root_node):
        assert IWorkQueue(workqueue)
        assert IDownloader(downloader)
        assert IUploader(uploader)
        assert INode(root_node)
        self.workqueue = workqueue
        workqueue.set_vdrive(self)
        workqueue.set_uploader(uploader)
        self._downloader = downloader
        self._uploader = uploader
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
        if IDirectoryNode.providedBy(node) or node.is_leaf_subtree():
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

    def get_subtrees_for_path(self, path):
        # compute a list of [(subtree1, subpath1), ...], which represents
        # which parts of 'path' traverse which subtrees. This can be used to
        # present the virtual drive to the user in a form that includes
        # redirection nodes (which do not consume path segments), or to
        # figure out which subtrees need to be updated when the identity of a
        # lower subtree (i.e. CHK) is changed.

        # TODO: it might be useful to add some items to the return value.
        # Like if there is a node already present at that path, to return it.
        d = self._get_root()
        results = []
        d.addCallback(self._get_subtrees_for_path_1, results, path)
        return d

    def _get_subtrees_for_path_1(self, subtree, results, path):
        (found_path, node, remaining_path) = subtree.get_node_for_path(path)
        if IDirectoryNode.providedBy(node):
            # traversal done. We are looking at the final subtree, and the
            # entire path (found_path + remaining_path) will live in here.
            r = (subtree, (found_path + remaining_path))
            results.append(r)
            return results
        if node.is_leaf_subtree():
            # for this assert to fail, we found a File or something where we
            # were expecting to find another subdirectory.
            assert len(remaining_path) == 0
            results.append((subtree, found_path))
            return results
        # otherwise we must open and recurse into a new subtree
        results.append((subtree, found_path))
        parent_is_mutable = subtree.is_mutable()
        d = self.subtree_maker.make_subtree_from_node(node, parent_is_mutable)
        def _opened(next_subtree):
            next_subtree = ISubTree(next_subtree)
            return self._get_subtrees_for_path_1(next_subtree, results,
                                                 remaining_path)
        d.addCallback(_opened)
        return d


    # these are called by the workqueue

    def addpath_with_node(self, path, new_node):
        new_node_boxname = self.workqueue.create_boxname(new_node)
        self.workqueue.add_delete_box(new_node_boxname)
        return self.addpath(path, new_node_boxname)

    def addpath(self, path, new_node_boxname):
        # this adds a block of steps to the workqueue which, when complete,
        # will result in the new_node existing in the virtual drive at
        # 'path'.

        # First we figure out which subtrees are involved
        d = self.get_subtrees_for_path(path)

        # then we walk through them from the bottom, arranging to modify them
        # as necessary
        def _got_subtrees(subtrees, new_node_boxname):
            for (subtree, subpath) in reversed(subtrees):
                if self.debug:
                    print "SUBTREE", subtree, subpath
                assert subtree.is_mutable()
                must_update = subtree.mutation_modifies_parent()
                subtree_node = subtree.create_node_now()
                new_subtree_boxname = None
                if must_update:
                    new_subtree_boxname = self.workqueue.create_boxname()
                    self.workqueue.add_delete_box(new_subtree_boxname)
                    self.workqueue.add_modify_subtree(subtree_node, subpath,
                                                      new_node_boxname,
                                                      new_subtree_boxname)
                    # the box filled by the modify_subtree will be propagated
                    # upwards
                    new_node_boxname = new_subtree_boxname
                else:
                    # the buck stops here
                    self.workqueue.add_modify_subtree(subtree_node, subpath,
                                                      new_node_boxname)
                    return
        d.addCallback(_got_subtrees, new_node_boxname)
        return d

    def deletepath(self, path):
        if self.debug:
            print "DELETEPATH(%s)" % (path,)
        return self.addpath(path, None)

    def modify_subtree(self, subtree_node, localpath, new_node,
                       new_subtree_boxname=None):
        # TODO: I'm lying here, we don't know who the parent is, so we can't
        # really say whether they're mutable or not. But we're pretty sure
        # that the new subtree is supposed to be mutable, because we asserted
        # that earlier (although I suppose perhaps someone could change a
        # QueenRedirection or an SSK file while we're offline in the middle
        # of our workqueue..). Tell the new subtree that their parent is
        # mutable so we can be sure it will believe that it itself is
        # mutable.
        parent_is_mutable = True
        d = self.subtree_maker.make_subtree_from_node(subtree_node,
                                                      parent_is_mutable)
        def _got_subtree(subtree):
            assert subtree.is_mutable()
            if new_node:
                subtree.put_node_at_path(localpath, new_node)
            else:
                subtree.delete_node_at_path(localpath)
            return subtree.update_now(self._uploader)
        d.addCallback(_got_subtree)
        if new_subtree_boxname:
            d.addCallback(lambda new_subtree_node:
                          self.workqueue.write_to_box(new_subtree_boxname,
                                                      new_subtree_node))
        return d


    # these are user-visible

    def list(self, path):
        assert isinstance(path, list)
        d = self._get_directory(path)
        d.addCallback(lambda node: node.list())
        return d

    def download(self, path, target):
        # TODO: does this mean download it right now? or schedule it in the
        # workqueue for eventual download? should we add download steps to
        # the workqueue?
        assert isinstance(path, list)
        d = self._get_file_uri(path)
        d.addCallback(lambda uri: self._downloader.download(uri, target))
        return d

    def download_as_data(self, path):
        # TODO: this is kind of goofy.. think of a better download API that
        # is appropriate for this class
        from allmydata import download
        target = download.Data()
        return self.download(path, target)

    def upload_data(self, path, data):
        assert isinstance(path, list)
        f, tempfilename = self.workqueue.create_tempfile()
        f.write(data)
        f.close()
        boxname = self.workqueue.create_boxname()
        self.workqueue.add_upload_chk(tempfilename, boxname)
        self.workqueue.add_addpath(boxname, path)
        self.workqueue.add_delete_box(boxname)
        self.workqueue.add_delete_tempfile(tempfilename)

    def upload(self, path, filename):
        assert isinstance(path, list)
        filename = os.path.abspath(filename)
        boxname = self.workqueue.create_boxname()
        self.workqueue.add_upload_chk(filename, boxname)
        self.workqueue.add_addpath(boxname, path)
        self.workqueue.add_delete_box(boxname)

    def delete(self, path):
        assert isinstance(path, list)
        self.workqueue.add_deletepath(path)

    def add_node(self, path, node):
        assert isinstance(path, list)
        assert INode(node)
        assert not IDirectoryNode.providedBy(node)
        boxname = self.workqueue.create_boxname(node)
        self.workqueue.add_addpath(boxname, path)
        self.workqueue.add_delete_box(boxname)

