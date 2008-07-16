
from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IFileNode, IFileURI, IURI, ICheckable
from allmydata import uri
from allmydata.immutable.checker import Results, \
     SimpleCHKFileChecker, SimpleCHKFileVerifier

class FileNode:
    implements(IFileNode, ICheckable)

    def __init__(self, uri, client):
        u = IFileURI(uri)
        self.uri = u.to_string()
        self._client = client

    def get_uri(self):
        return self.uri

    def is_mutable(self):
        return False

    def is_readonly(self):
        return True

    def get_readonly_uri(self):
        return self.uri

    def get_size(self):
        return IFileURI(self.uri).get_size()

    def __hash__(self):
        return hash((self.__class__, self.uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.uri, them.uri)

    def get_verifier(self):
        return IFileURI(self.uri).get_verifier()

    def check(self, verify=False, repair=False):
        assert repair is False  # not implemented yet
        vcap = self.get_verifier()
        if verify:
            v = SimpleCHKFileVerifier(self._client, vcap)
            return v.start()
        else:
            peer_getter = self._client.get_permuted_peers
            v = SimpleCHKFileChecker(peer_getter, vcap)
            return v.check()

    def download(self, target):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download(self.uri, target)

    def download_to_data(self):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download_to_data(self.uri)



class LiteralFileNode:
    implements(IFileNode, ICheckable)

    def __init__(self, my_uri, client):
        u = IFileURI(my_uri)
        assert isinstance(u, uri.LiteralFileURI)
        self.uri = u.to_string()
        self._client = client

    def get_uri(self):
        return self.uri

    def is_mutable(self):
        return False

    def is_readonly(self):
        return True

    def get_readonly_uri(self):
        return self.uri

    def get_size(self):
        return len(IURI(self.uri).data)

    def __hash__(self):
        return hash((self.__class__, self.uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.uri, them.uri)

    def get_verifier(self):
        return None

    def check(self, verify=False, repair=False):
        # neither verify= nor repair= affect LIT files
        r = Results(None)
        r.healthy = True
        r.problems = []
        return defer.succeed(r)

    def download(self, target):
        # note that this does not update the stats_provider
        data = IURI(self.uri).data
        target.open(len(data))
        target.write(data)
        target.close()
        return defer.maybeDeferred(target.finish)

    def download_to_data(self):
        data = IURI(self.uri).data
        return defer.succeed(data)
