
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

class CHKDirectorySpecification(object):
    implements(ISubTreeSpecification)
    stype = "CHK-Directory"
    def set_uri(self, uri):
        self.uri = uri
    def serialize(self):
        return (self.stype, self.uri)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.uri = data[1]

class ImmutableSSKDirectorySpecification(object):
    implements(ISubTreeSpecification)
    stype = "SSK-Readonly-Directory"
    def set_read_capability(self, read_cap):
        self.read_cap = read_cap
    def get_read_capability(self):
        return self.read_cap
    def serialize(self):
        return (self.stype, self.read_cap)
    def unserialize(self, data):
        assert data[0] == self.stype
        self.read_cap = data[1]

class MutableSSKDirectorySpecification(ImmutableSSKDirectorySpecification):
    implements(ISubTreeSpecification)
    stype = "SSK-ReadWrite-Directory"
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



class LocalFileRedirection(object):
    implements(ISubTreeSpecification)
    stype = "LocalFile"
    def set_filename(self, filename):
        self.filename = filename
    def get_filename(self):
        return self.filename
    def serialize(self):
        return (self.stype, self.filename)

class QueenRedirection(object):
    implements(ISubTreeSpecification)
    stype = "QueenRedirection"
    def set_handle(self, handle):
        self.handle = handle
    def get_handle(self):
        return self.handle
    def serialize(self):
        return (self.stype, self.handle)

class HTTPRedirection(object):
    implements(ISubTreeSpecification)
    stype = "HTTPRedirection"
    def set_url(self, url):
        self.url = url
    def get_url(self):
        return self.url
    def serialize(self):
        return (self.stype, self.url)

class QueenOrLocalFileRedirection(object):
    implements(ISubTreeSpecification)
    stype = "QueenOrLocalFile"
    def set_filename(self, filename):
        self.filename = filename
    def get_filename(self):
        return self.filename
    def set_handle(self, handle):
        self.handle = handle
    def get_handle(self):
        return self.handle
    def serialize(self):
        return (self.stype, self.handle, self.filename)

