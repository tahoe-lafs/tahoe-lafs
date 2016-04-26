
import time

from foolscap.api import Referenceable
from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import RIBucketWriter, RIBucketReader

from allmydata.util import base32, log
from allmydata.util.assertutil import precondition
from allmydata.storage.leasedb import SHARETYPE_IMMUTABLE


class BucketWriter(Referenceable):
    implements(RIBucketWriter)

    def __init__(self, account, share, canary):
        self.ss = account.server
        self._account = account
        self._share = share
        self._canary = canary
        self._disconnect_marker = canary.notifyOnDisconnect(self._disconnected)
        self.closed = False
        self.throw_out_all_data = False

        self._account.add_share(share.get_storage_index(), share.get_shnum(),
                                share.get_allocated_data_length(), SHARETYPE_IMMUTABLE)

    def allocated_size(self):
        return self._share.get_allocated_data_length()

    def _add_latency(self, res, name, start):
        self.ss.add_latency(name, time.time() - start)
        self.ss.count(name)
        return res

    def remote_write(self, offset, data):
        start = time.time()
        precondition(not self.closed)
        if self.throw_out_all_data:
            return defer.succeed(None)
        d = self._share.write_share_data(offset, data)
        d.addBoth(self._add_latency, "write", start)
        return d

    def remote_close(self):
        precondition(not self.closed)
        start = time.time()

        d = defer.succeed(None)
        d.addCallback(lambda ign: self._share.close())
        d.addCallback(lambda ign: self._share.get_used_space())
        def _got_used_space(used_space):
            storage_index = self._share.get_storage_index()
            shnum = self._share.get_shnum()
            self._share = None
            self.closed = True
            self._canary.dontNotifyOnDisconnect(self._disconnect_marker)

            self.ss.bucket_writer_closed(self, used_space)
            self._account.add_or_renew_default_lease(storage_index, shnum)
            self._account.mark_share_as_stable(storage_index, shnum, used_space)
        d.addCallback(_got_used_space)
        d.addBoth(self._add_latency, "close", start)
        return d

    def _disconnected(self):
        if not self.closed:
            return self._abort()
        return defer.succeed(None)

    def remote_abort(self):
        log.msg("storage: aborting write to share %r" % self._share,
                facility="tahoe.storage", level=log.UNUSUAL)
        if not self.closed:
            self._canary.dontNotifyOnDisconnect(self._disconnect_marker)
        d = self._abort()
        def _count(ign):
            self.ss.count("abort")
        d.addBoth(_count)
        return d

    def _abort(self):
        d = defer.succeed(None)
        if self.closed:
            return d
        d.addCallback(lambda ign: self._share.unlink())
        def _unlinked(ign):
            self._share = None

            # We are now considered closed for further writing. We must tell
            # the storage server about this so that it stops expecting us to
            # use the space it allocated for us earlier.
            self.closed = True
            self.ss.bucket_writer_closed(self, 0)
        d.addCallback(_unlinked)
        return d


class BucketReader(Referenceable):
    implements(RIBucketReader)

    def __init__(self, account, share):
        self.ss = account.server
        self._account = account
        self._share = share
        self.storage_index = share.get_storage_index()
        self.shnum = share.get_shnum()

    def __repr__(self):
        return "<%s %s %s>" % (self.__class__.__name__,
                               base32.b2a_l(self.storage_index[:8], 60),
                               self.shnum)

    def _add_latency(self, res, name, start):
        self.ss.add_latency(name, time.time() - start)
        self.ss.count(name)
        return res

    def remote_read(self, offset, length):
        start = time.time()
        d = self._share.read_share_data(offset, length)
        d.addBoth(self._add_latency, "read", start)
        return d

    def remote_advise_corrupt_share(self, reason):
        return self._account.remote_advise_corrupt_share("immutable",
                                                         self.storage_index,
                                                         self.shnum,
                                                         reason)
