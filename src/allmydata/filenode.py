
from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IFileNode, IFileURI, IURI
from allmydata import uri

class FileNode:
    implements(IFileNode)

    def __init__(self, uri, client):
        u = IFileURI(uri)
        self.uri = u.to_string()
        self._client = client

    def get_uri(self):
        return self.uri

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

    def check(self):
        verifier = self.get_verifier()
        return self._client.getServiceNamed("checker").check(verifier)

    def download(self, target):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download(self.uri, target)

    def download_to_data(self):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download_to_data(self.uri)



class LiteralFileNode:
    implements(IFileNode)

    def __init__(self, my_uri, client):
        u = IFileURI(my_uri)
        assert isinstance(u, uri.LiteralFileURI)
        self.uri = u.to_string()
        self._client = client

    def get_uri(self):
        return self.uri

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

    def check(self):
        return None

    def download(self, target):
        data = IURI(self.uri).data
        target.open(len(data))
        target.write(data)
        target.close()
        return defer.maybeDeferred(target.finish)

    def download_to_data(self):
        data = IURI(self.uri).data
        return defer.succeed(data)
