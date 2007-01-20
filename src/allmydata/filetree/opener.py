
from zope.interface import implements
from twisted.internet import defer
from allmydata.filetree import interfaces, directory, redirect
#from allmydata.filetree.file import CHKFile, MutableSSKFile, ImmutableSSKFile
#from allmydata.filetree.specification import unserialize_subtree_specification

all_openable_subtree_types = [
    directory.CHKDirectorySubTree,
    directory.SSKDirectorySubTree,
    redirect.LocalFileRedirection,
    redirect.QueenRedirection,
    redirect.HTTPRedirection,
    redirect.QueenOrLocalFileRedirection,
    ]

# the Opener can turn an INode (which describes a subtree, like a directory
# or a redirection) into the fully-populated subtree.

class Opener(object):
    implements(interfaces.IOpener)
    def __init__(self, queen, downloader):
        self._queen = queen
        self._downloader = downloader
        self._cache = {}

    def _create(self, spec, parent_is_mutable):
        assert isinstance(spec, tuple)
        for subtree_class in all_openable_subtree_types:
            if spec[0] == subtree_class.stype:
                subtree = subtree_class()
                d = subtree.populate_from_specification(spec,
                                                        parent_is_mutable,
                                                        self._downloader)
                return d
        raise RuntimeError("unable to handle subtree specification '%s'"
                           % (spec,))

    def open(self, subtree_specification, parent_is_mutable):
        spec = interfaces.ISubTreeSpecification(subtree_specification)

        # is it in cache?
        if spec in self._cache:
            return defer.succeed(self._cache[spec])

        d = defer.maybeDeferred(self._create, spec, parent_is_mutable)
        d.addCallback(self._add_to_cache, spec)
        return d

    def _add_to_cache(self, subtree, spec):
        self._cache[spec] = subtree
        # TODO: remove things from the cache eventually
        return subtree

"""
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

"""
