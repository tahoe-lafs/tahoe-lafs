
"""
from zope.interface import implements
from allmydata.filetree.interfaces import ISubTreeSpecification

class CHKFileSpecification(object):
    implements(ISubTreeSpecification)
    stype = "CHK-File"
    def set_uri(self, uri):
        self.uri = uri
    def serialize(self):
        return (self.stype, self.uri)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.uri = data[1]

class ImmutableSSKFileSpecification(object):
    implements(ISubTreeSpecification)
    stype = "SSK-Readonly-File"
    def set_read_capability(self, read_cap):
        self.read_cap = read_cap
    def get_read_capability(self):
        return self.read_cap
    def serialize(self):
        return (self.stype, self.read_cap)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.read_cap = data[1]

class MutableSSKFileSpecification(ImmutableSSKFileSpecification):
    implements(ISubTreeSpecification)
    stype = "SSK-ReadWrite-File"
    def set_write_capability(self, write_cap):
        self.write_cap = write_cap
    def get_write_capability(self):
        return self.write_cap
    def serialize(self):
        return (self.stype, self.read_cap, self.write_cap)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.read_cap = data[1]
        self.write_cap = data[2]




def unserialize_subtree_specification(serialized_spec):
    assert isinstance(serialized_spec, tuple)
    for stype in [CHKDirectorySpecification,
                  ImmutableSSKDirectorySpecification,
                  MutableSSKDirectorySpecification,

                  LocalFileRedirection,
                  QueenRedirection,
                  HTTPRedirection,
                  QueenOrLocalFileRedirection,
                  ]:
        if tuple[0] == stype:
            spec = stype()
            spec.unserialize(serialized_spec)
            return spec
    raise RuntimeError("unable to unserialize subtree specification '%s'" %
                       (serialized_spec,))
"""
