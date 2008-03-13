
import os
from zope.interface import implements
from twisted.internet import defer
from twisted.python import failure
from twisted.application import service
from allmydata import uri, dirnode
from allmydata.interfaces import IURI, IMutableFileNode, IFileNode
from allmydata.encode import NotEnoughPeersError
from allmydata.util import log

class FakeCHKFileNode:
    """I provide IFileNode, but all of my data is stored in a class-level
    dictionary."""
    implements(IFileNode)
    all_contents = {}

    def __init__(self, u, client):
        self.client = client
        self.my_uri = u.to_string()

    def get_uri(self):
        return self.my_uri
    def get_readonly_uri(self):
        return self.my_uri
    def get_verifier(self):
        return IURI(self.my_uri).get_verifier()
    def check(self):
        return defer.succeed(None)
    def is_mutable(self):
        return False
    def is_readonly(self):
        return True

    def download(self, target):
        if self.my_uri not in self.all_contents:
            f = failure.Failure(NotEnoughPeersError())
            target.fail(f)
            return defer.fail(f)
        data = self.all_contents[self.my_uri]
        target.open(len(data))
        target.write(data)
        target.close()
        return defer.maybeDeferred(target.finish)
    def download_to_data(self):
        if self.my_uri not in self.all_contents:
            return defer.fail(NotEnoughPeersError())
        data = self.all_contents[self.my_uri]
        return defer.succeed(data)
    def get_size(self):
        data = self.all_contents[self.my_uri]
        return len(data)

def make_chk_file_uri(size):
    return uri.CHKFileURI(key=os.urandom(16),
                          uri_extension_hash=os.urandom(32),
                          needed_shares=3,
                          total_shares=10,
                          size=size)

def create_chk_filenode(client, contents):
    u = make_chk_file_uri(len(contents))
    n = FakeCHKFileNode(u, client)
    FakeCHKFileNode.all_contents[u.to_string()] = contents
    return n


class FakeMutableFileNode:
    """I provide IMutableFileNode, but all of my data is stored in a
    class-level dictionary."""

    implements(IMutableFileNode)
    all_contents = {}
    def __init__(self, client):
        self.client = client
        self.my_uri = make_mutable_file_uri()
        self.storage_index = self.my_uri.storage_index
    def create(self, initial_contents):
        self.all_contents[self.storage_index] = initial_contents
        return defer.succeed(self)
    def init_from_uri(self, myuri):
        self.my_uri = IURI(myuri)
        self.storage_index = self.my_uri.storage_index
        return self
    def get_uri(self):
        return self.my_uri.to_string()
    def get_readonly_uri(self):
        return self.my_uri.get_readonly().to_string()
    def is_readonly(self):
        return self.my_uri.is_readonly()
    def is_mutable(self):
        return self.my_uri.is_mutable()
    def download_to_data(self):
        return defer.succeed(self.all_contents[self.storage_index])
    def get_writekey(self):
        return "\x00"*16
    def get_size(self):
        return "?" # TODO: see mutable.MutableFileNode.get_size

    def update(self, new_contents):
        assert not self.is_readonly()
        self.all_contents[self.storage_index] = new_contents
        return defer.succeed(None)

    def overwrite(self, new_contents):
        return self.update(new_contents)


def make_mutable_file_uri():
    return uri.WriteableSSKFileURI(writekey=os.urandom(16),
                                   fingerprint=os.urandom(32))
def make_verifier_uri():
    return uri.SSKVerifierURI(storage_index=os.urandom(16),
                              fingerprint=os.urandom(32))

class FakeDirectoryNode(dirnode.NewDirectoryNode):
    """This offers IDirectoryNode, but uses a FakeMutableFileNode for the
    backing store, so it doesn't go to the grid. The child data is still
    encrypted and serialized, so this isn't useful for tests that want to
    look inside the dirnodes and check their contents.
    """
    filenode_class = FakeMutableFileNode

class LoggingServiceParent(service.MultiService):
    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)
