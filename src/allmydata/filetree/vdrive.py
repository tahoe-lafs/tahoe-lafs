
from allmydata.filetree import interfaces, opener

class VirtualDrive(object):
    implements(interfaces.IVirtualDrive)

    def __init__(self, workqueue, downloader, root_specification):
        self.workqueue = workqueue
        workqueue.set_vdrive(self)
        # TODO: queen?
        self.opener = Opener(queen, downloader)
        self.root_specification = root_specification

    # these methods are used to walk through our subtrees

    def _get_root(self):
        return self.opener.open(self.root_specification, False)

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
        d = subtree.get_node_for_path(path)
        d.addCallback(self._get_closest_node_2, subtree.is_mutable())
        return d

    def _get_closest_node_2(self, res, parent_is_mutable):
        (found_path, node, remaining_path) = res
        if node.is_directory():
            # traversal done
            return (node, remaining_path)
        # otherwise, we must open and recurse into a new subtree
        d = self.opener.open(node, parent_is_mutable)
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
            assert interfaces.IDirectoryNode(node)
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
            assert path[prepath_len:] == remaining_path
            return (prepath, node, remaining_path)
        d.addCallback(_got_closest)
        return d

    # these are called by the workqueue

    def add(self, path, new_node):
        parent_path = path[:-1]
        new_node_path = path[-1]
        d = self._get_closest_node_and_prepath(parent_path)
        def _got_closest((prepath, node, remaining_path)):
            # now tell it to create any necessary parent directories
            while remaining_path:
                node = node.add_subdir(remaining_path.pop(0))
            # 'node' is now the directory where the child wants to go
            return node, prepath
        d.addCallback(_got_closest)
        def _add_new_node((node, prepath)):
            node.add(new_node_path, new_node)
            subtree = node.get_subtree()
            # now, tell the subtree to serialize and upload itself, using the
            # workqueue. The subtree will also queue a step to notify its
            # parent (using 'prepath'), if necessary.
            return subtree.update(prepath, self.workqueue)
        d.addCallback(_add_new_node)
        return d

    # these are user-visible

    def list(self, path):
        d = self._get_directory(path)
        d.addCallback(lambda node: node.list())
        return d

    def download(self, path, target):
        d = self._get_file_uri(path)
        d.addCallback(lambda uri: self.downloader.download(uri, target))
        return d

    def upload_now(self, path, uploadable):
        # note: the first few steps of this do not use the workqueue, but I
        # think things should remain consistent anyways. If the node is shut
        # down before the file has finished uploading, then we forget all
        # abou the file.
        uploadable = IUploadable(uploadable)
        d = self._child_should_not_exist(path)
        # then we upload the file
        d.addCallback(lambda ignored: self.uploader.upload(uploadable))
        d.addCallback(lambda uri: self.workqueue.create_boxname(uri))
        d.addCallback(lambda boxname:
                      self.workqueue.add_addpath(boxname, path))
        return d

    def upload_later(self, path, filename):
        boxname = self.workqueue.create_boxname()
        self.workqueue.add_upload_chk(filename, boxname)
        self.workqueue.add_addpath(boxname, path)

    def delete(self, path):
        parent_path = path[:-1]
        orphan_path = path[-1]
        d = self._get_closest_node_and_prepath(parent_path)
        def _got_parent((prepath, node, remaining_path)):
            assert not remaining_path
            node.delete(orphan_path)
            # now serialize and upload
            subtree = node.get_subtree()
            return subtree.update(prepath, self.workqueue)
        d.addCallback(_got_parent)
        return d

