
from cStringIO import StringIO
from zope.interface import implements
from twisted.internet import defer

from allmydata.filetree.interfaces import ISubTree
from allmydata.filetree.basenode import BaseDataNode
from allmydata.util import bencode

class LocalFileRedirectionNode(BaseDataNode):
    prefix = "LocalFileRedirection"

    def new(self, handle):
        self.handle = handle

    def get_base_data(self):
        return self.handle
    def set_base_data(self, data):
        self.handle = data

class _BaseRedirection(object):
    implements(ISubTree)

    def new(self, child_node):
        self.child_node = child_node

    def get_node_for_path(self, path):
        return ([], self.child_node, path)

    def serialize_subtree_to_file(self, f):
        return self.child_node.serialize_node()

    def _populate_from_data(self, data, node_maker):
        self.child_node = node_maker(data)
        return self

class LocalFileRedirection(_BaseRedirection):
    stype = "LocalFileRedirection"

    def new(self, handle, child_node):
        self.filename = handle
        _BaseRedirection.new(self, child_node)

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
        return defer.succeed(self._populate_from_data(data, node_maker))

    def is_mutable(self):
        return True

    def update(self, prepath, workqueue):
        f = open(self.filename, "wb")
        self.serialize_subtree_to_file(f)
        f.close()


class QueenRedirectionNode(LocalFileRedirectionNode):
    prefix = "QueenRedirection"

class QueenRedirection(_BaseRedirection):
    style = "QueenRedirection"

    def new(self, handle):
        self.handle = handle

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # this specifies a handle for which the Queen maintains a serialized
        # subtree specification.
        assert isinstance(node, QueenRedirectionNode)
        self.handle = node.handle

        # TODO: queen?
        d = self._queen.callRemote("lookup_handle", self.handle)
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def is_mutable(self):
        return True # TODO: maybe, maybe not

    def update(self, prepath, workqueue):
        f = StringIO()
        self.serialize_subtree_to_file(f)
        d = self._queen.callRemote("set_handle", self.handle, f.getvalue())
        return d

class QueenOrLocalFileRedirectionNode(LocalFileRedirectionNode):
    prefix = "QueenOrLocalFileRedirection"

class QueenOrLocalFileRedirection(_BaseRedirection):
    stype = "QueenOrLocalFileRedirection"

    def new(self, handle, child_node):
        self.handle = handle
        self.version = 0
        self.child_node = child_node
        # TODO

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # there is a local file which contains a bencoded serialized
        # subtree specification. The queen also has a copy. Whomever has
        # the higher version number wins.
        assert isinstance(node, QueenOrLocalFileRedirectionNode)
        self.filename = self.handle = node.handle

        f = open(self.filename, "rb")
        #local_version, local_data = bencode.bdecode(f.read())
        local_version_and_data = f.read()
        f.close()

        # TODO: queen?
        # TODO: pubsub so we can cache the queen's results
        d = self._queen.callRemote("lookup_handle", self.handle)
        d.addCallback(self._choose_winner, local_version_and_data, node_maker)
        return d

    def _choose_winner(self, queen_version_and_data, local_version_and_data, node_maker):
        queen_version, queen_data = bencode.bdecode(queen_version_and_data)
        local_version, local_data = bencode.bdecode(local_version_and_data)
        if queen_version > local_version:
            data = queen_data
            self.version = queen_version
        else:
            data = local_data
            self.version = local_version
        # NOTE: two layers of bencoding here, TODO
        return self._populate_from_data(data, node_maker)

    def is_mutable(self):
        return True

    def update(self, prepath, workqueue):
        self.version += 1
        f = StringIO()
        self.serialize_subtree_to_file(f)
        version_and_data = bencode.bencode((self.version, f.getvalue()))
        f = open(self.filename, "wb")
        f.write(version_and_data)
        f.close()
        d = self._queen.callRemote("set_handle", self.handle, version_and_data)
        return d

class HTTPRedirectionNode(BaseDataNode):
    prefix = "HTTPRedirection"

    def new(self, url):
        self.url = url

    def get_base_data(self):
        return self.url
    def set_base_data(self, data):
        self.url = data

class HTTPRedirection(_BaseRedirection):
    stype = "HTTPRedirection"

    def populate_from_node(self, node, parent_is_mutable, node_maker, downloader):
        # this specifies a URL at which there is a bencoded serialized
        # subtree specification.
        assert isinstance(node, HTTPRedirectionNode)
        from twisted.web import client
        d = client.getPage(node.url)
        d.addCallback(self._populate_from_data, node_maker)
        return d

    def is_mutable(self):
        return False

    def update(self, prepath, workqueue):
        raise RuntimeError("HTTPRedirection is not mutable")
