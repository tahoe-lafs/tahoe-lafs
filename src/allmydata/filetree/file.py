
from zope.interface import implements
from allmydata.filetree.interfaces import INode, IFileNode
from allmydata.filetree.basenode import BaseDataNode
from allmydata.util import bencode

class CHKFileNode(BaseDataNode):
    implements(IFileNode)
    prefix = "CHKFile"

    def get_base_data(self):
        return self.uri
    def set_base_data(self, data):
        self.uri = data

    def get_uri(self):
        return self.uri

class SSKFileNode(object):
    implements(INode, IFileNode)
    prefix = "SSKFile"

    def serialize_node(self):
        data = (self.read_cap, self.write_cap)
        return "%s:%s" % (self.prefix, bencode.bencode(data))
    def populate_node(self, data, node_maker):
        assert data.startswith(self.prefix + ":")
        capdata = data[len(self.prefix)+1:]
        self.read_cap, self.write_cap = bencode.bdecode(capdata)

    def get_read_capability(self):
        return self.read_cap
    def get_write_capability(self):
        return self.write_cap

