
from zope.interface import implements
from twisted.internet import defer
from twisted.internet.interfaces import IPushProducer, IConsumer
from allmydata.interfaces import IFileNode, IFileURI, ICheckable
from allmydata.immutable.checker import SimpleCHKFileChecker, \
     SimpleCHKFileVerifier

class ImmutableFileNode(object):
    implements(IFileNode, ICheckable)
    checker_class = SimpleCHKFileChecker
    verifier_class = SimpleCHKFileVerifier

    def __init__(self, uri, client):
        self.u = IFileURI(uri)
        self._client = client

    def get_readonly_uri(self):
        return self.get_uri()

    def is_mutable(self):
        return False

    def is_readonly(self):
        return True

    def __hash__(self):
        return self.u.__hash__()
    def __eq__(self, other):
        if IFileNode.providedBy(other):
            return self.u.__eq__(other.u)
        else:
            return False
    def __ne__(self, other):
        if IFileNode.providedBy(other):
            return self.u.__eq__(other.u)
        else:
            return True

class FileNode(ImmutableFileNode):
    checker_class = SimpleCHKFileChecker

    def __init__(self, uri, client):
        ImmutableFileNode.__init__(self, uri, client)

    def get_uri(self):
        return self.u.to_string()

    def get_size(self):
        return self.u.get_size()

    def get_verifier(self):
        return self.u.get_verifier()

    def get_storage_index(self):
        return self.u.storage_index

    def check(self, verify=False):
        storage_index = self.u.storage_index
        k = self.u.needed_shares
        N = self.u.total_shares
        size = self.u.size
        ueb_hash = self.u.uri_extension_hash
        if verify:
            v = self.verifier_class(self._client,
                                    storage_index, k, N, size, ueb_hash)
        else:
            v = self.checker_class(self._client, storage_index, k, N)
        return v.start()

    def check_and_repair(self, verify=False):
        # this is a stub, to allow the deep-check tests to pass.
        #raise NotImplementedError("not implemented yet")
        from allmydata.checker_results import CheckAndRepairResults
        cr = CheckAndRepairResults(self.u.storage_index)
        d = self.check(verify)
        def _done(r):
            cr.pre_repair_results = cr.post_repair_results = r
            cr.repair_attempted = False
            return cr
        d.addCallback(_done)
        return d

    def download(self, target):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download(self.get_uri(), target)

    def download_to_data(self):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download_to_data(self.get_uri())

class LiteralProducer:
    implements(IPushProducer)
    def resumeProducing(self):
        pass
    def stopProducing(self):
        pass

class LiteralFileNode(ImmutableFileNode):

    def __init__(self, uri, client):
        ImmutableFileNode.__init__(self, uri, client)

    def get_uri(self):
        return self.u.to_string()

    def get_size(self):
        return len(self.u.data)

    def get_verifier(self):
        return None

    def get_storage_index(self):
        return None

    def check(self, verify=False):
        return defer.succeed(None)

    def check_and_repair(self, verify=False):
        return defer.succeed(None)

    def download(self, target):
        # note that this does not update the stats_provider
        data = self.u.data
        if IConsumer.providedBy(target):
            target.registerProducer(LiteralProducer(), True)
        target.open(len(data))
        target.write(data)
        if IConsumer.providedBy(target):
            target.unregisterProducer()
        target.close()
        return defer.maybeDeferred(target.finish)

    def download_to_data(self):
        data = self.u.data
        return defer.succeed(data)
