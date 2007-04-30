
from cStringIO import StringIO
from zope.interface import implements
from twisted.internet import defer

from allmydata.filetree.interfaces import ISubTree, INodeMaker
from allmydata.filetree.basenode import BaseDataNode
from allmydata.util import bencode

class LocalFileRedirectionNode(BaseDataNode):
    prefix = "LocalFileRedirection"

    def new(self, handle):
        self.handle = handle
        return self

    def get_base_data(self):
        return self.handle
    def set_base_data(self, data):
        self.handle = data

    def is_leaf_subtree(self):
        return False

class _BaseRedirection(object):
    implements(ISubTree)

    def new(self, child_node):
        self.child_node = child_node
        return self

    def mutation_modifies_parent(self):
        return False

    def get_node_for_path(self, path):
        return ([], self.child_node, path)

    def put_node_at_path(self, path, node):
        assert path == []
        self.child_node = node

    def serialize_subtree_to_file(self, f):
        f.write(self.child_node.serialize_node())

    def _populate_from_data(self, data, node_maker):
        assert INodeMaker(node_maker)
        self.child_node = node_maker.make_node_from_serialized(data)
        return self


class LocalFileRedirection(_BaseRedirection):
    node_class = LocalFileRedirectionNode

    def new(self, handle, child_node):
        self.filename = handle
        return _BaseRedirection.new(self, child_node)

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # return a Deferred that fires (with self) when this node is ready
        # for use

        assert isinstance(node, LocalFileRedirectionNode)
        self.filename = node.handle
        # there is a local file which contains a bencoded serialized subtree
        # specification.

        # TODO: will this enable outsiders to cause us to read from arbitrary
        # files? Think about this. It is probably a good idea to restrict the
        # filename to be a single component, and to always put them in a
        # well-known directory that contains nothing else, and maybe make
        # them unguessable.
        f = open(self.filename, "rb")
        data = f.read()
        f.close()
        # note: we don't cache the contents of the file. TODO: consider
        # doing this based upon mtime. It is important that we be able to
        # notice if the file has been changed.
        d = defer.succeed(data)
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def is_mutable(self):
        return True

    def create_node_now(self):
        return LocalFileRedirectionNode().new(self.filename)

    def _update(self):
        f = open(self.filename, "wb")
        self.serialize_subtree_to_file(f)
        f.close()

    def update_now(self, uploader):
        self._update()
        return self.create_node_now()

    def update(self, workqueue):
        # TODO: this happens too early, before earlier items in the workqueue
        # have been executed. This might not be a problem, if our update()
        # method isn't actually called until everything earlier has been
        # executed anyways. Need to ponder this.
        self._update()
        return None

class VdriveRedirectionNode(LocalFileRedirectionNode):
    prefix = "VdriveRedirection"

class VdriveRedirection(_BaseRedirection):
    node_class = VdriveRedirectionNode

    def new(self, handle):
        self.handle = handle
        return self

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # this specifies a handle for which the Vdrive maintains a serialized
        # subtree specification.
        assert isinstance(node, VdriveRedirectionNode)
        self.handle = node.handle

        # TODO: vdrive?
        d = self._vdrive.callRemote("lookup_handle", self.handle)
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def is_mutable(self):
        return True # TODO: maybe, maybe not

    def create_node_now(self):
        return VdriveRedirectionNode().new(self.handle)

    def update_now(self, uploader):
        f = StringIO()
        self.serialize_subtree_to_file(f)
        d = self._vdrive.callRemote("set_handle", self.handle, f.getvalue())
        def _done(res):
            return self.create_node_now()
        d.addCallback(_done)
        return d

    def update(self, workqueue):
        f, filename = workqueue.create_tempfile(".tovdrive")
        self.serialize_subtree_to_file(f)
        f.close()
        workqueue.add_vdrive_update_handle(self.handle, filename)
        workqueue.add_delete_tempfile(filename)
        return None

class VdriveOrLocalFileRedirectionNode(LocalFileRedirectionNode):
    prefix = "VdriveOrLocalFileRedirection"

class VdriveOrLocalFileRedirection(_BaseRedirection):
    node_class = VdriveOrLocalFileRedirectionNode

    def new(self, handle, child_node):
        self.handle = handle
        self.version = 0
        self.child_node = child_node
        # TODO
        return self

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # there is a local file which contains a bencoded serialized
        # subtree specification. The vdrive also has a copy. Whomever has
        # the higher version number wins.
        assert isinstance(node, VdriveOrLocalFileRedirectionNode)
        self.filename = self.handle = node.handle

        f = open(self.filename, "rb")
        #local_version, local_data = bencode.bdecode(f.read())
        local_version_and_data = f.read()
        f.close()

        # TODO: vdrive?
        # TODO: pubsub so we can cache the vdrive's results
        d = self._vdrive.callRemote("lookup_handle", self.handle)
        d.addCallback(self._choose_winner, local_version_and_data)
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def _choose_winner(self, vdrive_version_and_data, local_version_and_data):
        vdrive_version, vdrive_data = bencode.bdecode(vdrive_version_and_data)
        local_version, local_data = bencode.bdecode(local_version_and_data)
        if vdrive_version > local_version:
            data = vdrive_data
            self.version = vdrive_version
        else:
            data = local_data
            self.version = local_version
        # NOTE: two layers of bencoding here, TODO
        return data

    def is_mutable(self):
        return True

    def create_node_now(self):
        return VdriveOrLocalFileRedirectionNode().new(self.handle)

    def _update(self):
        self.version += 1
        f = StringIO()
        self.serialize_subtree_to_file(f)
        version_and_data = bencode.bencode((self.version, f.getvalue()))
        return version_and_data

    def update_now(self, uploader):
        version_and_data = self._update()
        f = open(self.filename, "wb")
        f.write(version_and_data)
        f.close()

        d = self._vdrive.callRemote("set_handle", self.handle, version_and_data)
        def _done(res):
            return self.create_node_now()
        d.addCallback(_done)
        return d

    def update(self, workqueue):
        version_and_data = self._update()
        # TODO: this may have the same problem as LocalFileRedirection.update
        f = open(self.filename, "wb")
        f.write(version_and_data)
        f.close()

        f, filename = workqueue.create_tempfile(".tovdrive")
        self.serialize_subtree_to_file(f)
        f.close()
        workqueue.add_vdrive_update_handle(self.handle, filename)
        workqueue.add_delete_tempfile(filename)
        return None

class HTTPRedirectionNode(BaseDataNode):
    prefix = "HTTPRedirection"

    def new(self, url):
        self.url = url
        return self

    def get_base_data(self):
        return self.url
    def set_base_data(self, data):
        self.url = data

    def is_leaf_subtree(self):
        return False

class HTTPRedirection(_BaseRedirection):
    node_class = HTTPRedirectionNode

    def new(self, url):
        self.url = url

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # this specifies a URL at which there is a bencoded serialized
        # subtree specification.
        self.url = node.url
        assert isinstance(node, HTTPRedirectionNode)
        from twisted.web import client
        d = client.getPage(self.url)
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def is_mutable(self):
        return False

    def create_node_now(self):
        return HTTPRedirectionNode().new(self.url)

    def update_now(self, uploader):
        raise RuntimeError("HTTPRedirection is not mutable")

    def update(self, workqueue):
        raise RuntimeError("HTTPRedirection is not mutable")
