
from zope.interface import implements
from allmydata.filetree.interfaces import INode

class BaseDataNode(object):
    implements(INode)
    prefix = None # must be set by subclass

    def new(self, data):
        self.set_base_data(data)
        return self

    def get_base_data(self):
        raise NotImplementedError # must be provided by subclass
    def set_base_data(self, data):
        raise NotImplementedError # must be provided by subclass
    def serialize_node(self):
        return "%s:%s" % (self.prefix, self.get_base_data())
    def populate_node(self, body, node_maker):
        self.set_base_data(body)

