
from zope.interface import implements
from allmydata.filetree.interfaces import INode, IFileNode

class CHKFile(object):
    implements(INode, IFileNode)
    def __init__(self, uri):
        self.uri = uri
    def get_uri(self):
        return self.uri

class MutableSSKFile(object):
    implements(INode, IFileNode)
    def __init__(self, read_cap, write_cap):
        self.read_cap = read_cap
        self.write_cap = write_cap
    def get_read_capability(self):
        return self.read_cap
    def get_write_capability(self):
        return self.write_cap

class ImmutableSSKFile(object):
    implements(INode, IFileNode)
    def __init__(self, read_cap):
        self.read_cap = read_cap
    def get_read_capability(self):
        return self.read_cap

