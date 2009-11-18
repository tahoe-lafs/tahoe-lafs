from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IFilesystemNode

class UnknownNode:
    implements(IFilesystemNode)
    def __init__(self, writecap, readcap):
        assert writecap is None or isinstance(writecap, str)
        self.writecap = writecap
        assert readcap is None or isinstance(readcap, str)
        self.readcap = readcap
    def get_uri(self):
        return self.writecap
    def get_readonly_uri(self):
        return self.readcap
    def get_storage_index(self):
        return None
    def get_verify_cap(self):
        return None
    def get_repair_cap(self):
        return None
    def get_size(self):
        return None
    def get_current_size(self):
        return defer.succeed(None)
    def check(self, monitor, verify, add_lease):
        return defer.succeed(None)
    def check_and_repair(self, monitor, verify, add_lease):
        return defer.succeed(None)
