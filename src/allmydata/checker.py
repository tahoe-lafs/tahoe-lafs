
"""
Given a StorageIndex, count how many shares we can find.

This does no verification of the shares whatsoever. If the peer claims to
have the share, we believe them.
"""

from twisted.internet import defer
from twisted.application import service
from twisted.python import log
from allmydata.interfaces import IVerifierURI
from allmydata import uri

class SimpleCHKFileChecker:

    def __init__(self, peer_getter):
        self.peer_getter = peer_getter
        self.found_shares = set()

    '''
    def check_synchronously(self, si):
        # this is how we would write this class if we were using synchronous
        # messages (or if we used promises).
        found = set()
        for (pmpeerid, peerid, connection) in self.peer_getter(storage_index):
            buckets = connection.get_service("storageserver").get_buckets(si)
            found.update(buckets.keys())
        return len(found)
    '''

    def check(self, uri_to_check):
        d = self._get_all_shareholders(uri_to_check.storage_index)
        d.addCallback(self._done)
        return d

    def _get_all_shareholders(self, storage_index):
        dl = []
        for (pmpeerid, peerid, connection) in self.peer_getter(storage_index):
            d = connection.callRemote("get_service", "storageserver")
            d.addCallback(lambda ss: ss.callRemote("get_buckets",
                                                   storage_index))
            d.addCallbacks(self._got_response, self._got_error)
            dl.append(d)
        return defer.DeferredList(dl)

    def _got_response(self, buckets):
        # buckets is a dict: maps shum to an rref of the server who holds it
        self.found_shares.update(buckets.keys())

    def _got_error(self, f):
        if f.check(KeyError):
            pass
        log.err(f)
        pass

    def _done(self, res):
        return len(self.found_shares)

class SimpleDirnodeChecker:

    def __init__(self, tub):
        self.tub = tub

    def check(self, node):
        si = node.storage_index
        d = self.tub.getReference(node.furl)
        d.addCallback(self._get_dirnode, node.storage_index)
        d.addCallbacks(self._success, self._failed)
        return d

    def _get_dirnode(self, rref, storage_index):
        d = rref.callRemote("list", storage_index)
        return d

    def _success(self, res):
        return True
    def _failed(self, f):
        if f.check(IndexError):
            return False
        log.err(f)
        return False

class Checker(service.MultiService):
    """I am a service that helps perform file checks.
    """
    name = "checker"

    def check(self, uri_to_check):
        uri_to_check = IVerifierURI(uri_to_check)
        if uri_to_check is None:
            return defer.succeed(True)
        elif isinstance(uri_to_check, uri.CHKFileVerifierURI):
            peer_getter = self.parent.get_permuted_peers
            c = SimpleCHKFileChecker(peer_getter)
            return c.check(uri_to_check)
        elif isinstance(uri_to_check, uri.DirnodeVerifierURI):
            tub = self.parent.tub
            c = SimpleDirnodeChecker(tub)
            return c.check(uri_to_check)
        else:
            raise ValueError("I don't know how to check '%s'" % (uri_to_check,))

