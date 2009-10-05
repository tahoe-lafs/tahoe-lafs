from foolscap.api import DeadReferenceError, RemoteException
from twisted.internet import defer
from allmydata.hashtree import IncompleteHashTree
from allmydata.check_results import CheckResults
from allmydata.immutable import download
from allmydata.uri import CHKFileVerifierURI
from allmydata.util.assertutil import precondition
from allmydata.util import base32, deferredutil, dictutil, log
from allmydata.util.hashutil import file_renewal_secret_hash, \
     file_cancel_secret_hash, bucket_renewal_secret_hash, \
     bucket_cancel_secret_hash

from allmydata.immutable import layout

class Checker(log.PrefixingLogMixin):
    """I query all servers to see if M uniquely-numbered shares are
    available.

    If the verify flag was passed to my constructor, then for each share I
    download every data block and all metadata from each server and perform a
    cryptographic integrity check on all of it. If not, I just ask each
    server 'Which shares do you have?' and believe its answer.

    In either case, I wait until I have gotten responses from all servers.
    This fact -- that I wait -- means that an ill-behaved server which fails
    to answer my questions will make me wait indefinitely. If it is
    ill-behaved in a way that triggers the underlying foolscap timeouts, then
    I will wait only as long as those foolscap timeouts, but if it is
    ill-behaved in a way which placates the foolscap timeouts but still
    doesn't answer my question then I will wait indefinitely.

    Before I send any new request to a server, I always ask the 'monitor'
    object that was passed into my constructor whether this task has been
    cancelled (by invoking its raise_if_cancelled() method).
    """
    def __init__(self, verifycap, servers, verify, add_lease, secret_holder,
                 monitor):
        assert precondition(isinstance(verifycap, CHKFileVerifierURI), verifycap, type(verifycap))
        assert precondition(isinstance(servers, (set, frozenset)), servers)
        for (serverid, serverrref) in servers:
            assert precondition(isinstance(serverid, str))

        prefix = "%s" % base32.b2a_l(verifycap.storage_index[:8], 60)
        log.PrefixingLogMixin.__init__(self, facility="tahoe.immutable.checker", prefix=prefix)

        self._verifycap = verifycap

        self._monitor = monitor
        self._servers = servers
        self._verify = verify # bool: verify what the servers claim, or not?
        self._add_lease = add_lease

        self._share_hash_tree = None

        frs = file_renewal_secret_hash(secret_holder.get_renewal_secret(),
                                       self._verifycap.storage_index)
        self.file_renewal_secret = frs
        fcs = file_cancel_secret_hash(secret_holder.get_cancel_secret(),
                                      self._verifycap.storage_index)
        self.file_cancel_secret = fcs

    def _get_renewal_secret(self, peerid):
        return bucket_renewal_secret_hash(self.file_renewal_secret, peerid)
    def _get_cancel_secret(self, peerid):
        return bucket_cancel_secret_hash(self.file_cancel_secret, peerid)

    def _get_buckets(self, server, storageindex, serverid):
        """Return a deferred that eventually fires with ({sharenum: bucket},
        serverid, success). In case the server is disconnected or returns a
        Failure then it fires with ({}, serverid, False) (A server
        disconnecting or returning a Failure when we ask it for buckets is
        the same, for our purposes, as a server that says it has none, except
        that we want to track and report whether or not each server
        responded.)"""

        d = server.callRemote("get_buckets", storageindex)
        if self._add_lease:
            renew_secret = self._get_renewal_secret(serverid)
            cancel_secret = self._get_cancel_secret(serverid)
            d2 = server.callRemote("add_lease", storageindex,
                                   renew_secret, cancel_secret)
            dl = defer.DeferredList([d, d2], consumeErrors=True)
            def _done(res):
                [(get_success, get_result),
                 (addlease_success, addlease_result)] = res
                # ignore remote IndexError on the add_lease call. Propagate
                # local errors and remote non-IndexErrors
                if addlease_success:
                    return get_result
                if not addlease_result.check(RemoteException):
                    # Propagate local errors
                    return addlease_result
                if addlease_result.value.failure.check(IndexError):
                    # tahoe=1.3.0 raised IndexError on non-existant
                    # buckets, which we ignore
                    return get_result
                # propagate remote errors that aren't IndexError, including
                # the unfortunate internal KeyError bug that <1.3.0 had.
                return addlease_result
            dl.addCallback(_done)
            d = dl

        def _wrap_results(res):
            return (res, serverid, True)

        def _trap_errs(f):
            level = log.WEIRD
            if f.check(DeadReferenceError):
                level = log.UNUSUAL
            self.log("failure from server on 'get_buckets' the REMOTE failure was:", facility="tahoe.immutable.checker", failure=f, level=level, umid="3uuBUQ")
            return ({}, serverid, False)

        d.addCallbacks(_wrap_results, _trap_errs)
        return d

    def _download_and_verify(self, serverid, sharenum, bucket):
        """Start an attempt to download and verify every block in this bucket
        and return a deferred that will eventually fire once the attempt
        completes.

        If you download and verify every block then fire with (True,
        sharenum, None), else if the share data couldn't be parsed because it
        was of an unknown version number fire with (False, sharenum,
        'incompatible'), else if any of the blocks were invalid, fire with
        (False, sharenum, 'corrupt'), else if the server disconnected (False,
        sharenum, 'disconnect'), else if the server returned a Failure during
        the process fire with (False, sharenum, 'failure').

        If there is an internal error such as an uncaught exception in this
        code, then the deferred will errback, but if there is a remote error
        such as the server failing or the returned data being incorrect then
        it will not errback -- it will fire normally with the indicated
        results."""

        vcap = self._verifycap
        b = layout.ReadBucketProxy(bucket, serverid, vcap.storage_index)
        veup = download.ValidatedExtendedURIProxy(b, vcap)
        d = veup.start()

        def _got_ueb(vup):
            self._share_hash_tree = IncompleteHashTree(vcap.total_shares)
            self._share_hash_tree.set_hashes({0: vup.share_root_hash})

            vrbp = download.ValidatedReadBucketProxy(sharenum, b,
                                                     self._share_hash_tree,
                                                     vup.num_segments,
                                                     vup.block_size,
                                                     vup.share_size)

            ds = []
            for blocknum in range(vup.num_segments):
                def _discard_result(r):
                    assert isinstance(r, str), r
                    # to free up the RAM
                    return None
                d2 = vrbp.get_block(blocknum)
                d2.addCallback(_discard_result)
                ds.append(d2)

            dl = deferredutil.gatherResults(ds)
            # dl will fire once every block of this share has been downloaded
            # and verified, or else it will errback.

            def _cb(result):
                return (True, sharenum, None)

            dl.addCallback(_cb)
            return dl
        d.addCallback(_got_ueb)

        def _errb(f):
            # We didn't succeed at fetching and verifying all the blocks of
            # this share. Handle each reason for failure differently.

            if f.check(DeadReferenceError):
                return (False, sharenum, 'disconnect')
            elif f.check(RemoteException):
                return (False, sharenum, 'failure')
            elif f.check(layout.ShareVersionIncompatible):
                return (False, sharenum, 'incompatible')
            elif f.check(layout.LayoutInvalid,
                         layout.RidiculouslyLargeURIExtensionBlock,
                         download.BadOrMissingHash,
                         download.BadURIExtensionHashValue):
                return (False, sharenum, 'corrupt')

            # if it wasn't one of those reasons, re-raise the error
            return f
        d.addErrback(_errb)

        return d

    def _verify_server_shares(self, serverid, ss):
        """ Return a deferred which eventually fires with a tuple of
        (set(sharenum), serverid, set(corruptsharenum),
        set(incompatiblesharenum), success) showing all the shares verified
        to be served by this server, and all the corrupt shares served by the
        server, and all the incompatible shares served by the server. In case
        the server is disconnected or returns a Failure then it fires with
        the last element False.

        A server disconnecting or returning a failure when we ask it for
        shares is the same, for our purposes, as a server that says it has
        none or offers invalid ones, except that we want to track and report
        the server's behavior. Similarly, the presence of corrupt shares is
        mainly of use for diagnostics -- you can typically treat it as just
        like being no share at all by just observing its absence from the
        verified shares dict and ignoring its presence in the corrupt shares
        dict.

        The 'success' argument means whether the server responded to *any*
        queries during this process, so if it responded to some queries and
        then disconnected and ceased responding, or returned a failure, it is
        still marked with the True flag for 'success'.
        """
        d = self._get_buckets(ss, self._verifycap.storage_index, serverid)

        def _got_buckets(result):
            bucketdict, serverid, success = result

            shareverds = []
            for (sharenum, bucket) in bucketdict.items():
                d = self._download_and_verify(serverid, sharenum, bucket)
                shareverds.append(d)

            dl = deferredutil.gatherResults(shareverds)

            def collect(results):
                verified = set()
                corrupt = set()
                incompatible = set()
                for succ, sharenum, whynot in results:
                    if succ:
                        verified.add(sharenum)
                    else:
                        if whynot == 'corrupt':
                            corrupt.add(sharenum)
                        elif whynot == 'incompatible':
                            incompatible.add(sharenum)
                return (verified, serverid, corrupt, incompatible, success)

            dl.addCallback(collect)
            return dl

        def _err(f):
            f.trap(RemoteException, DeadReferenceError)
            return (set(), serverid, set(), set(), False)

        d.addCallbacks(_got_buckets, _err)
        return d

    def _check_server_shares(self, serverid, ss):
        """Return a deferred which eventually fires with a tuple of
        (set(sharenum), serverid, set(), set(), responded) showing all the
        shares claimed to be served by this server. In case the server is
        disconnected then it fires with (set() serverid, set(), set(), False)
        (a server disconnecting when we ask it for buckets is the same, for
        our purposes, as a server that says it has none, except that we want
        to track and report whether or not each server responded.)"""
        def _curry_empty_corrupted(res):
            buckets, serverid, responded = res
            return (set(buckets), serverid, set(), set(), responded)
        d = self._get_buckets(ss, self._verifycap.storage_index, serverid)
        d.addCallback(_curry_empty_corrupted)
        return d

    def _format_results(self, results):
        cr = CheckResults(self._verifycap, self._verifycap.storage_index)
        d = {}
        d['count-shares-needed'] = self._verifycap.needed_shares
        d['count-shares-expected'] = self._verifycap.total_shares

        verifiedshares = dictutil.DictOfSets() # {sharenum: set(serverid)}
        servers = {} # {serverid: set(sharenums)}
        corruptsharelocators = [] # (serverid, storageindex, sharenum)
        incompatiblesharelocators = [] # (serverid, storageindex, sharenum)

        for theseverifiedshares, thisserverid, thesecorruptshares, theseincompatibleshares, thisresponded in results:
            servers.setdefault(thisserverid, set()).update(theseverifiedshares)
            for sharenum in theseverifiedshares:
                verifiedshares.setdefault(sharenum, set()).add(thisserverid)
            for sharenum in thesecorruptshares:
                corruptsharelocators.append((thisserverid, self._verifycap.storage_index, sharenum))
            for sharenum in theseincompatibleshares:
                incompatiblesharelocators.append((thisserverid, self._verifycap.storage_index, sharenum))

        d['count-shares-good'] = len(verifiedshares)
        d['count-good-share-hosts'] = len([s for s in servers.keys() if servers[s]])

        assert len(verifiedshares) <= self._verifycap.total_shares, (verifiedshares.keys(), self._verifycap.total_shares)
        if len(verifiedshares) == self._verifycap.total_shares:
            cr.set_healthy(True)
            cr.set_summary("Healthy")
        else:
            cr.set_healthy(False)
            cr.set_summary("Not Healthy: %d shares (enc %d-of-%d)" %
                           (len(verifiedshares),
                            self._verifycap.needed_shares,
                            self._verifycap.total_shares))
        if len(verifiedshares) >= self._verifycap.needed_shares:
            cr.set_recoverable(True)
            d['count-recoverable-versions'] = 1
            d['count-unrecoverable-versions'] = 0
        else:
            cr.set_recoverable(False)
            d['count-recoverable-versions'] = 0
            d['count-unrecoverable-versions'] = 1

        d['servers-responding'] = list(servers)
        d['sharemap'] = verifiedshares
        # no such thing as wrong shares of an immutable file
        d['count-wrong-shares'] = 0
        d['list-corrupt-shares'] = corruptsharelocators
        d['count-corrupt-shares'] = len(corruptsharelocators)
        d['list-incompatible-shares'] = incompatiblesharelocators
        d['count-incompatible-shares'] = len(incompatiblesharelocators)


        # The file needs rebalancing if the set of servers that have at least
        # one share is less than the number of uniquely-numbered shares
        # available.
        cr.set_needs_rebalancing(d['count-good-share-hosts'] < d['count-shares-good'])

        cr.set_data(d)

        return cr

    def start(self):
        ds = []
        if self._verify:
            for (serverid, ss) in self._servers:
                ds.append(self._verify_server_shares(serverid, ss))
        else:
            for (serverid, ss) in self._servers:
                ds.append(self._check_server_shares(serverid, ss))

        return deferredutil.gatherResults(ds).addCallback(self._format_results)
