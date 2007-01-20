
from zope.interface import implements
from twisted.internet import defer
from allmydata.filetree import interfaces, directory, redirect
#from allmydata.filetree.file import CHKFile, MutableSSKFile, ImmutableSSKFile
from allmydata.filetree.interfaces import INode, IDirectoryNode

all_openable_subtree_types = [
    directory.LocalFileSubTree,
    directory.CHKDirectorySubTree,
    directory.SSKDirectorySubTree,
    redirect.LocalFileRedirection,
    redirect.QueenRedirection,
    redirect.QueenOrLocalFileRedirection,
    redirect.HTTPRedirection,
    ]

# the Opener can turn an INode (which describes a subtree, like a directory
# or a redirection) into the fully-populated subtree.

class Opener(object):
    implements(interfaces.IOpener)
    def __init__(self, queen, downloader):
        self._queen = queen
        self._downloader = downloader
        self._cache = {}

    def _create(self, node, parent_is_mutable, node_maker):
        assert INode(node)
        for subtree_class in all_openable_subtree_types:
            if isinstance(node, subtree_class.node_class):
                subtree = subtree_class()
                d = subtree.populate_from_node(node,
                                               parent_is_mutable,
                                               node_maker,
                                               self._downloader)
                return d
        raise RuntimeError("unable to handle subtree specification '%s'"
                           % (node,))

    def open(self, node, parent_is_mutable, node_maker):
        assert INode(node)
        assert not isinstance(node, IDirectoryNode)

        # is it in cache? To check this we need to use the node's serialized
        # form, since nodes are instances and don't compare by value
        node_s = node.serialize_node()
        if node_s in self._cache:
            return defer.succeed(self._cache[node_s])

        d = defer.maybeDeferred(self._create,
                                node, parent_is_mutable, node_maker)
        d.addCallback(self._add_to_cache, node_s)
        return d

    def _add_to_cache(self, subtree, node_s):
        self._cache[node_s] = subtree
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
