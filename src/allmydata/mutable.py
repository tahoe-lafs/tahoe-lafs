
from zope.interface import implements
from twisted.internet import defer
from allmydata.interfaces import IMutableFileNode, IMutableFileURI
from allmydata.util import hashutil
from allmydata.uri import WriteableSSKFileURI

class MutableFileNode:
    implements(IMutableFileNode)

    def __init__(self, client):
        self._client = client
        self._pubkey = None # filled in upon first read
        self._privkey = None # filled in if we're mutable
        self._sharemap = {} # known shares, shnum-to-nodeid

    def init_from_uri(self, myuri):
        self._uri = IMutableFileURI(myuri)
        return self

    def create(self, initial_contents):
        """Call this when the filenode is first created. This will generate
        the keys, generate the initial shares, allocate shares, and upload
        the initial contents. Returns a Deferred that fires (with the
        MutableFileNode instance you should use) when it completes.
        """
        self._privkey = "very private"
        self._pubkey = "public"
        self._writekey = hashutil.ssk_writekey_hash(self._privkey)
        self._fingerprint = hashutil.ssk_pubkey_fingerprint_hash(self._pubkey)
        self._uri = WriteableSSKFileURI(self._writekey, self._fingerprint)
        d = defer.succeed(None)
        return d


    def get_uri(self):
        return self._uri.to_string()

    def is_mutable(self):
        return self._uri.is_mutable()

    def __hash__(self):
        return hash((self.__class__, self.uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.uri, them.uri)

    def get_verifier(self):
        return IMutableFileURI(self._uri).get_verifier()

    def check(self):
        verifier = self.get_verifier()
        return self._client.getServiceNamed("checker").check(verifier)

    def download(self, target):
        #downloader = self._client.getServiceNamed("downloader")
        #return downloader.download(self.uri, target)
        raise NotImplementedError

    def download_to_data(self):
        #downloader = self._client.getServiceNamed("downloader")
        #return downloader.download_to_data(self.uri)
        return defer.succeed("this isn't going to fool you, is it")

    def replace(self, newdata):
        return defer.succeed(None)

# use client.create_mutable_file() to make one of these
