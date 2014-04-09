
from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import IStorageBackend, IShareSet, IShareBase, \
    IShareForReading, IShareForWriting, IMutableShare

from allmydata.util.assertutil import precondition
from allmydata.storage.backends.base import Backend, ShareSet, empty_check_testv
from allmydata.storage.bucket import BucketWriter
from allmydata.storage.common import si_b2a


def configure_null_backend(storedir, config):
    return NullBackend()


class NullBackend(Backend):
    implements(IStorageBackend)
    """
    I am a test backend that records (in memory) which shares exist, but not their contents, leases,
    or write-enablers.
    """

    def __init__(self):
        Backend.__init__(self)
        # mapping from storage_index to NullShareSet
        self._sharesets = {}

    def get_available_space(self):
        return None

    def get_sharesets_for_prefix(self, prefix):
        sharesets = []
        for (si, shareset) in self._sharesets.iteritems():
            if si_b2a(si).startswith(prefix):
                sharesets.append(shareset)

        def _by_base32si(b):
            return b.get_storage_index_string()
        sharesets.sort(key=_by_base32si)
        return defer.succeed(sharesets)

    def get_shareset(self, storage_index):
        shareset = self._sharesets.get(storage_index, None)
        if shareset is None:
            shareset = NullShareSet(storage_index)
            self._sharesets[storage_index] = shareset
        return shareset

    def fill_in_space_stats(self, stats):
        pass


class NullShareSet(ShareSet):
    implements(IShareSet)

    def __init__(self, storage_index):
        self.storage_index = storage_index
        self._incoming_shnums = set()
        self._immutable_shnums = set()
        self._mutable_shnums = set()

    def close_shnum(self, shnum):
        self._incoming_shnums.remove(shnum)
        self._immutable_shnums.add(shnum)
        return defer.succeed(None)

    def get_overhead(self):
        return 0

    def get_shares(self):
        shares = {}
        for shnum in self._immutable_shnums:
            shares[shnum] = ImmutableNullShare(self, shnum)
        for shnum in self._mutable_shnums:
            shares[shnum] = MutableNullShare(self, shnum)
        # This backend never has any corrupt shares.
        return defer.succeed( ([shares[shnum] for shnum in sorted(shares.keys())], set()) )

    def get_share(self, shnum):
        if shnum in self._immutable_shnums:
            return defer.succeed(ImmutableNullShare(self, shnum))
        elif shnum in self._mutable_shnums:
            return defer.succeed(MutableNullShare(self, shnum))
        else:
            def _not_found(): raise IndexError("no such share %d" % (shnum,))
            return defer.execute(_not_found)

    def delete_share(self, shnum, include_incoming=False):
        if include_incoming and (shnum in self._incoming_shnums):
            self._incoming_shnums.remove(shnum)
        if shnum in self._immutable_shnums:
            self._immutable_shnums.remove(shnum)
        if shnum in self._mutable_shnums:
            self._mutable_shnums.remove(shnum)
        return defer.succeed(None)

    def has_incoming(self, shnum):
        return shnum in self._incoming_shnums

    def get_storage_index(self):
        return self.storage_index

    def get_storage_index_string(self):
        return si_b2a(self.storage_index)

    def make_bucket_writer(self, account, shnum, allocated_data_length, canary):
        self._incoming_shnums.add(shnum)
        immutableshare = ImmutableNullShare(self, shnum)
        bw = BucketWriter(account, immutableshare, canary)
        bw.throw_out_all_data = True
        return bw


class NullShareBase(object):
    implements(IShareBase)

    def __init__(self, shareset, shnum):
        self.shareset = shareset
        self.shnum = shnum

    def get_storage_index(self):
        return self.shareset.get_storage_index()

    def get_storage_index_string(self):
        return self.shareset.get_storage_index_string()

    def get_shnum(self):
        return self.shnum

    def get_data_length(self):
        return 0

    def get_size(self):
        return 0

    def get_used_space(self):
        return 0

    def unlink(self):
        return self.shareset.delete_share(self.shnum, include_incoming=True)

    def readv(self, readv):
        datav = []
        for (offset, length) in readv:
            datav.append("")
        return defer.succeed(datav)

    def get_leases(self):
        pass

    def add_lease(self, lease):
        pass

    def renew_lease(self, renew_secret, new_expire_time):
        raise IndexError("unable to renew non-existent lease")

    def add_or_renew_lease(self, lease_info):
        pass


class ImmutableNullShare(NullShareBase):
    implements(IShareForReading, IShareForWriting)
    sharetype = "immutable"

    def read_share_data(self, offset, length):
        precondition(offset >= 0)
        return defer.succeed("")

    def get_allocated_data_length(self):
        return 0

    def write_share_data(self, offset, data):
        return defer.succeed(None)

    def close(self):
        return self.shareset.close_shnum(self.shnum)


class MutableNullShare(NullShareBase):
    implements(IMutableShare)
    sharetype = "mutable"

    def check_write_enabler(self, write_enabler):
        # Null backend doesn't check write enablers.
        return defer.succeed(None)

    def check_testv(self, testv):
        return defer.succeed(empty_check_testv(testv))

    def writev(self, datav, new_length):
        return defer.succeed(None)
