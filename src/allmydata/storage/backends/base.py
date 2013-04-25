
from weakref import WeakValueDictionary

from twisted.application import service
from twisted.internet import defer

from allmydata.util.deferredutil import async_iterate, gatherResults
from allmydata.storage.common import si_b2a
from allmydata.storage.bucket import BucketReader
from allmydata.storage.leasedb import SHARETYPE_MUTABLE


class Backend(service.MultiService):
    def __init__(self):
        service.MultiService.__init__(self)
        self._lock_table = WeakValueDictionary()

    def _get_lock(self, storage_index):
        # Getting a shareset ensures that a lock exists for that storage_index.
        # The _lock_table won't let go of an entry while the ShareSet (or any
        # other objects that reference the lock) are live, or while it is locked.

        lock = self._lock_table.get(storage_index, None)
        if lock is None:
            lock = defer.DeferredLock()
            self._lock_table[storage_index] = lock
        return lock

    def must_use_tubid_as_permutation_seed(self):
        # New backends cannot have been around before #466, and so have no backward
        # compatibility requirements for permutation seeds. The disk backend overrides this.
        return False


class ShareSet(object):
    """
    This class implements shareset logic that could work for all backends, but
    might be useful to override for efficiency.
    """

    def __init__(self, storage_index, lock):
        self.storage_index = storage_index
        self.lock = lock

    def get_storage_index(self):
        return self.storage_index

    def get_storage_index_string(self):
        return si_b2a(self.storage_index)

    def make_bucket_reader(self, account, share):
        return BucketReader(account, share)

    def get_shares(self):
        return self.lock.run(self._locked_get_shares)

    def get_share(self, shnum):
        return self.lock.run(self._locked_get_share, shnum)

    def delete_share(self, shnum):
        return self.lock.run(self._locked_delete_share, shnum)

    def testv_and_readv_and_writev(self, write_enabler,
                                   test_and_write_vectors, read_vector,
                                   expiration_time, account):
        return self.lock.run(self._locked_testv_and_readv_and_writev, write_enabler,
                             test_and_write_vectors, read_vector,
                             expiration_time, account)

    def _locked_testv_and_readv_and_writev(self, write_enabler,
                                           test_and_write_vectors, read_vector,
                                           expiration_time, account):
        # The implementation here depends on the following helper methods,
        # which must be provided by subclasses:
        #
        # def _clean_up_after_unlink(self):
        #     """clean up resources associated with the shareset after some
        #     shares might have been deleted"""
        #
        # def _create_mutable_share(self, account, shnum, write_enabler):
        #     """create a mutable share with the given shnum and write_enabler"""

        sharemap = {}
        d = self._locked_get_shares()
        def _got_shares( (shares, corrupted) ):
            d2 = defer.succeed(None)
            for share in shares:
                assert not isinstance(share, defer.Deferred), share
                # XXX is it correct to ignore immutable shares? Maybe get_shares should
                # have a parameter saying what type it's expecting.
                if share.sharetype == "mutable":
                    d2.addCallback(lambda ign, share=share: share.check_write_enabler(write_enabler))
                    sharemap[share.get_shnum()] = share

            shnums = sorted(sharemap.keys())

            # if d2 does not fail, write_enabler is good for all existing shares

            # now evaluate test vectors
            def _check_testv(shnum):
                (testv, datav, new_length) = test_and_write_vectors[shnum]
                if shnum in sharemap:
                    d3 = sharemap[shnum].check_testv(testv)
                elif shnum in corrupted:
                    # a corrupted share does not match any test vector
                    d3 = defer.succeed(False)
                else:
                    # compare the vectors against an empty share, in which all
                    # reads return empty strings
                    d3 = defer.succeed(empty_check_testv(testv))

                def _check_result(res):
                    if not res:
                        account.server.log("testv failed: [%d] %r" % (shnum, testv))
                    return res
                d3.addCallback(_check_result)
                return d3

            d2.addCallback(lambda ign: async_iterate(_check_testv, test_and_write_vectors))

            def _gather(testv_is_good):
                # Gather the read vectors, before we do any writes. This ignores any
                # corrupted shares.
                d3 = gatherResults([sharemap[shnum].readv(read_vector) for shnum in shnums])

                def _do_writes(reads):
                    read_data = {}
                    for i in range(len(shnums)):
                        read_data[shnums[i]] = reads[i]

                    d4 = defer.succeed(None)
                    if testv_is_good:
                        if len(set(test_and_write_vectors.keys()) & corrupted) > 0:
                            # XXX think of a better exception to raise
                            raise AssertionError("You asked to write share numbers %r of storage index %r, "
                                                 "but one or more of those is corrupt (numbers %r)"
                                                 % (list(sorted(test_and_write_vectors.keys())),
                                                    self.get_storage_index_string(),
                                                    list(sorted(corrupted))) )

                        # now apply the write vectors
                        for shnum in test_and_write_vectors:
                            (testv, datav, new_length) = test_and_write_vectors[shnum]
                            if new_length == 0:
                                if shnum in sharemap:
                                    d4.addCallback(lambda ign, shnum=shnum:
                                                   sharemap[shnum].unlink())
                                    d4.addCallback(lambda ign, shnum=shnum:
                                                   account.remove_share_and_leases(self.storage_index, shnum))
                            else:
                                if shnum not in sharemap:
                                    # allocate a new share
                                    d4.addCallback(lambda ign, shnum=shnum:
                                                   self._create_mutable_share(account, shnum,
                                                                              write_enabler))
                                    def _record_share(share, shnum=shnum):
                                        sharemap[shnum] = share
                                        account.add_share(self.storage_index, shnum, share.get_used_space(),
                                                          SHARETYPE_MUTABLE)
                                    d4.addCallback(_record_share)
                                d4.addCallback(lambda ign, shnum=shnum, datav=datav, new_length=new_length:
                                               sharemap[shnum].writev(datav, new_length))
                                def _update_lease(ign, shnum=shnum):
                                    account.add_or_renew_default_lease(self.storage_index, shnum)
                                    account.mark_share_as_stable(self.storage_index, shnum,
                                                                 sharemap[shnum].get_used_space())
                                d4.addCallback(_update_lease)

                        if new_length == 0:
                            d4.addCallback(lambda ign: self._clean_up_after_unlink())

                    d4.addCallback(lambda ign: (testv_is_good, read_data))
                    return d4
                d3.addCallback(_do_writes)
                return d3
            d2.addCallback(_gather)
            return d2
        d.addCallback(_got_shares)
        return d

    def readv(self, wanted_shnums, read_vector):
        return self.lock.run(self._locked_readv, wanted_shnums, read_vector)

    def _locked_readv(self, wanted_shnums, read_vector):
        """
        Read a vector from the numbered shares in this shareset. An empty
        shares list means to return data from all known shares.

        @param wanted_shnums=ListOf(int)
        @param read_vector=ReadVector
        @return DictOf(int, ReadData): shnum -> results, with one key per share
        """
        shnums = []
        dreads = []
        d = self._locked_get_shares()
        def _got_shares( (shares, corrupted) ):
            # We ignore corrupted shares.
            for share in shares:
                assert not isinstance(share, defer.Deferred), share
                shnum = share.get_shnum()
                if not wanted_shnums or shnum in wanted_shnums:
                    shnums.append(share.get_shnum())
                    dreads.append(share.readv(read_vector))
            return gatherResults(dreads)
        d.addCallback(_got_shares)

        def _got_reads(reads):
            datavs = {}
            for i in range(len(shnums)):
                datavs[shnums[i]] = reads[i]
            return datavs
        d.addCallback(_got_reads)
        return d


def testv_compare(a, op, b):
    assert op in ("lt", "le", "eq", "ne", "ge", "gt")
    if op == "lt":
        return a < b
    if op == "le":
        return a <= b
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if op == "ge":
        return a >= b
    if op == "gt":
        return a > b
    # never reached


def empty_check_testv(testv):
    test_good = True
    for (offset, length, operator, specimen) in testv:
        data = ""
        if not testv_compare(data, operator, specimen):
            test_good = False
            break
    return test_good
