
from zope.interface import implements
from twisted.internet import defer
from allmydata.util import bencode
from allmydata.filetree import interfaces, directory
from allmydata.filetree import specification as fspec
from allmydata.filetree.file import CHKFile, MutableSSKFile, ImmutableSSKFile

def unserialize_subtree_specification(serialized_spec):
    assert isinstance(serialized_spec, tuple)
    for stype in [fspec.CHKDirectorySpecification,
                  fspec.ImmutableSSKDirectorySpecification,
                  fspec.MutableSSKDirectorySpecification,
                  fspec.LocalFileRedirection,
                  fspec.QueenRedirection,
                  fspec.HTTPRedirection,
                  fspec.QueenOrLocalFileRedirection,
                  ]:
        if tuple[0] == stype:
            spec = stype()
            spec.unserialize(serialized_spec)
            return spec
    raise RuntimeError("unable to unserialize subtree specification '%s'" %
                       (serialized_spec,))


class Opener(object):
    implements(interfaces.IOpener)
    def __init__(self, queen):
        self._queen = queen
        self._cache = {}

    def open(self, subtree_specification, parent_is_mutable):
        spec = interfaces.ISubTreeSpecification(subtree_specification)

        # is it in cache?
        if spec in self._cache:
            return defer.succeed(self._cache[spec])

        # is it a file?
        if isinstance(spec, fspec.CHKFileSpecification):
            return self._get_chk_file(spec)
        if isinstance(spec, (fspec.MutableSSKFileSpecification,
                             fspec.ImmutableSSKFileSpecification)):
            return self._get_ssk_file(spec)

        # is it a directory?
        if isinstance(spec, fspec.CHKDirectorySpecification):
            return self._get_chk_dir(spec, parent_is_mutable)
        if isinstance(spec, (fspec.ImmutableSSKDirectorySpecification,
                             fspec.MutableSSKDirectorySpecification)):
            return self._get_ssk_dir(spec)

        # is it a redirection to a file or directory?
        if isinstance(spec, fspec.LocalFileRedirection):
            return self._get_local_redir(spec)
        if isinstance(spec, fspec.QueenRedirection):
            return self._get_queen_redir(spec)
        if isinstance(spec, fspec.HTTPRedirection):
            return self._get_http_redir(spec)
        if isinstance(spec, fspec.QueenOrLocalFileRedirection):
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
        if isinstance(spec, fspec.MutableSSKFileSpecification):
            subtree = MutableSSKFile(spec.get_read_capability(),
                                     spec.get_write_capability())
        else:
            assert isinstance(spec, fspec.ImmutableSSKFileSpecification)
            subtree = ImmutableSSKFile(spec.get_read_cap())
        return defer.succeed(subtree)

    def _get_chk_dir(self, spec, parent_is_mutable):
        uri = spec.get_uri()
        if parent_is_mutable:
            subtree = directory.MutableCHKDirectorySubTree()
            subtree.set_uri(uri)
        else:
            subtree = directory.ImmutableDirectorySubTree()
        d = self.downloader.get_chk(uri)
        d.addCallback(subtree.unserialize)
        d.addCallback(self._add_to_cache, spec)
        return d

    def _get_ssk_dir(self, spec):
        mutable = isinstance(spec, fspec.ImmutableSSKDirectorySpecification)
        if mutable:
            subtree = directory.ImmutableDirectorySubTree()
        else:
            assert isinstance(spec, fspec.MutableSSKDirectorySpecification)
            subtree = directory.MutableSSKDirectorySubTree()
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


