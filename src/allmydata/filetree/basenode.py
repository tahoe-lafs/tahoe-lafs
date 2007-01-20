
from zope.interface import implements
from allmydata.filetree.interfaces import INode

class BaseURINode(object):
    implements(INode)
    prefix = None # must be set by subclass

    def is_directory(self):
        return False
    def serialize_node(self):
        return "%s:%s" % (self.prefix, self.uri)
    def populate_node(self, data, node_maker):
        assert data.startswith(self.prefix + ":")
        self.uri = data[len(self.prefix)+1:]

