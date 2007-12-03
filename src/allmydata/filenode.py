
from zope.interface import implements
from allmydata.interfaces import IFileNode, IFileURI

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

