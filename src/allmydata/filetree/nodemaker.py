
from zope.interface import implements
from allmydata.filetree import directory, file, redirect
from allmydata.filetree.interfaces import INodeMaker

# this list is used by NodeMaker to convert node specification strings (found
# inside the serialized form of subtrees) into Nodes (which live in the
# in-RAM form of subtrees).
all_node_types = [
    directory.LocalFileSubTreeNode,
    directory.CHKDirectorySubTreeNode,
    directory.SSKDirectorySubTreeNode,
    file.CHKFileNode,
    file.SSKFileNode,
    redirect.LocalFileRedirectionNode,
    redirect.QueenRedirectionNode,
    redirect.HTTPRedirectionNode,
    redirect.QueenOrLocalFileRedirectionNode,
]

class NodeMaker(object):
    implements(INodeMaker)

    def make_node_from_serialized(self, serialized):
        # this turns a string into an INode, which contains information about
        # the file or directory (like a URI), but does not contain the actual
        # contents. An ISubTreeMaker can be used later to retrieve the
        # contents (which means downloading the file if this is an IFileNode,
        # or perhaps creating a new subtree from the contents)

        # maybe include parent_is_mutable?
        assert isinstance(serialized, str)
        prefix, body = serialized.split(":", 2)

        for node_class in all_node_types:
            if prefix == node_class.prefix:
                node = node_class()
                node.populate_node(body, self)
                return node
        raise RuntimeError("unable to handle node type '%s'" % prefix)

