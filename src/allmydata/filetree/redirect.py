
from allmydata.util import bencode

class LocalFileRedirection(object):
    stype = "LocalFileRedirection"

    def populate_from_specification(self, spec, parent_is_mutable, downloader):
        # return a Deferred that fires (with self) when this node is ready
        # for use

        (stype, filename) = spec
        assert stype == self.stype
        #filename = spec.get_filename()
        # there is a local file which contains a bencoded serialized
        # subtree specification.

        # TODO: will this enable outsiders to cause us to read from
        # arbitrary files? Think about this.
        f = open(filename, "rb")
        data = f.read()
        f.close()
        # note: we don't cache the contents of the file. TODO: consider
        # doing this based upon mtime. It is important that we be able to
        # notice if the file has been changed.

        return self.populate_from_data(data)

    def populate_from_data(self, data):
        # data is a subtree specification for our one child
        self.child_spec = bencode.bdecode(data)
        return self

class QueenRedirection(object):
    stype = "QueenRedirection"

    def populate_from_specification(self, spec, parent_is_mutable, downloader):
        # this specifies a handle for which the Queen maintains a
        # serialized subtree specification.
        (stype, handle) = spec

        # TODO: queen?
        d = self._queen.callRemote("lookup_handle", handle)
        d.addCallback(self.populate_from_data)
        return d

    def populate_from_data(self, data):
        self.child_spec = bencode.bdecode(data)
        return self

class QueenOrLocalFileRedirection(object):
    stype = "QueenOrLocalFileRedirection"

    def populate_from_specification(self, spec, parent_is_mutable, downloader):
        # there is a local file which contains a bencoded serialized
        # subtree specification. The queen also has a copy. Whomever has
        # the higher version number wins.
        (stype, filename, handle) = spec

        f = open(filename, "rb")
        #local_version, local_data = bencode.bdecode(f.read())
        local_version_and_data = f.read()
        f.close()

        # TODO: queen?
        # TODO: pubsub so we can cache the queen's results
        d = self._queen.callRemote("lookup_handle", handle)
        d.addCallback(self._choose_winner, local_version_and_data)
        return d

    def _choose_winner(self, queen_version_and_data, local_version_and_data):
        queen_version, queen_data = bencode.bdecode(queen_version_and_data)
        local_version, local_data = bencode.bdecode(local_version_and_data)
        if queen_version > local_version:
            data = queen_data
        else:
            data = local_data
        return self.populate_from_data(data)

    def populate_from_data(self, data):
        # NOTE: two layers of bencoding here, TODO
        self.child_spec = bencode.bdecode(data)
        return self

class HTTPRedirection(object):
    stype = "HTTPRedirection"

    def populate_from_specification(self, spec, parent_is_mutable, downloader):
        # this specifies a URL at which there is a bencoded serialized
        # subtree specification.
        (stype, url) = spec
        from twisted.web import client
        d = client.getPage(url)
        d.addCallback(self.populate_from_data)
        return d

    def populate_from_data(self, data):
        self.child_spec = bencode.bdecode(data)
        return self
