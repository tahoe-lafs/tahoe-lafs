
import os, time

from foolscap.api import Referenceable

from zope.interface import implements
from allmydata.interfaces import RIBucketWriter, RIBucketReader

from allmydata.util import base32, fileutil, log
from allmydata.util.fileutil import get_used_space
from allmydata.util.assertutil import precondition
from allmydata.storage.backends.disk.immutable import ShareFile
from allmydata.storage.leasedb import SHARETYPE_IMMUTABLE



class BucketWriter(Referenceable):
    implements(RIBucketWriter)

    def __init__(self, ss, account, storage_index, shnum,
                 incominghome, finalhome, max_size, canary):
        self.ss = ss
        self.incominghome = incominghome
        self.finalhome = finalhome
        self._max_size = max_size # don't allow the client to write more than this
        self._account = account
        self._storage_index = storage_index
        self._shnum = shnum
        self._canary = canary
        self._disconnect_marker = canary.notifyOnDisconnect(self._disconnected)
        self.closed = False
        self.throw_out_all_data = False
        self._sharefile = ShareFile(incominghome, create=True, max_size=max_size)
        self._account.add_share(self._storage_index, self._shnum, max_size, SHARETYPE_IMMUTABLE)

    def allocated_size(self):
        return self._max_size

    def remote_write(self, offset, data):
        start = time.time()
        precondition(not self.closed)
        if self.throw_out_all_data:
            return
        self._sharefile.write_share_data(offset, data)
        self.ss.add_latency("write", time.time() - start)
        self.ss.count("write")

    def remote_close(self):
        precondition(not self.closed)
        start = time.time()

        fileutil.make_dirs(os.path.dirname(self.finalhome))
        fileutil.rename(self.incominghome, self.finalhome)
        try:
            # self.incominghome is like storage/shares/incoming/ab/abcde/4 .
            # We try to delete the parent (.../ab/abcde) to avoid leaving
            # these directories lying around forever, but the delete might
            # fail if we're working on another share for the same storage
            # index (like ab/abcde/5). The alternative approach would be to
            # use a hierarchy of objects (PrefixHolder, BucketHolder,
            # ShareWriter), each of which is responsible for a single
            # directory on disk, and have them use reference counting of
            # their children to know when they should do the rmdir. This
            # approach is simpler, but relies on os.rmdir refusing to delete
            # a non-empty directory. Do *not* use fileutil.rm_dir() here!
            os.rmdir(os.path.dirname(self.incominghome))
            # we also delete the grandparent (prefix) directory, .../ab ,
            # again to avoid leaving directories lying around. This might
            # fail if there is another bucket open that shares a prefix (like
            # ab/abfff).
            os.rmdir(os.path.dirname(os.path.dirname(self.incominghome)))
            # we leave the great-grandparent (incoming/) directory in place.
        except EnvironmentError:
            # ignore the "can't rmdir because the directory is not empty"
            # exceptions, those are normal consequences of the
            # above-mentioned conditions.
            pass
        self._sharefile = None
        self.closed = True
        self._canary.dontNotifyOnDisconnect(self._disconnect_marker)

        filelen = get_used_space(self.finalhome)
        self.ss.bucket_writer_closed(self, filelen)
        self._account.add_or_renew_default_lease(self._storage_index, self._shnum)
        self._account.mark_share_as_stable(self._storage_index, self._shnum, filelen)
        self.ss.add_latency("close", time.time() - start)
        self.ss.count("close")

    def _disconnected(self):
        if not self.closed:
            self._abort()

    def remote_abort(self):
        log.msg("storage: aborting sharefile %s" % self.incominghome,
                facility="tahoe.storage", level=log.UNUSUAL)
        if not self.closed:
            self._canary.dontNotifyOnDisconnect(self._disconnect_marker)
        self._abort()
        self.ss.count("abort")

    def _abort(self):
        if self.closed:
            return

        os.remove(self.incominghome)
        # if we were the last share to be moved, remove the incoming/
        # directory that was our parent
        parentdir = os.path.split(self.incominghome)[0]
        if not os.listdir(parentdir):
            os.rmdir(parentdir)
        self._sharefile = None
        self._account.remove_share_and_leases(self._storage_index, self._shnum)

        # We are now considered closed for further writing. We must tell
        # the storage server about this so that it stops expecting us to
        # use the space it allocated for us earlier.
        self.closed = True
        self.ss.bucket_writer_closed(self, 0)


class BucketReader(Referenceable):
    implements(RIBucketReader)

    def __init__(self, ss, sharefname, storage_index=None, shnum=None):
        self.ss = ss
        self._share_file = ShareFile(sharefname)
        self.storage_index = storage_index
        self.shnum = shnum

    def __repr__(self):
        return "<%s %s %s>" % (self.__class__.__name__,
                               base32.b2a_l(self.storage_index[:8], 60),
                               self.shnum)

    def remote_read(self, offset, length):
        start = time.time()
        data = self._share_file.read_share_data(offset, length)
        self.ss.add_latency("read", time.time() - start)
        self.ss.count("read")
        return data

    def remote_advise_corrupt_share(self, reason):
        return self.ss.client_advise_corrupt_share("immutable",
                                                   self.storage_index,
                                                   self.shnum,
                                                   reason)
