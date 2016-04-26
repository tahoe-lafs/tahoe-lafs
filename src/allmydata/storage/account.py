
"""
This file contains the client-facing interface for manipulating shares, named
"Account". It implements RIStorageServer. Each Account instance contains an
owner_num that is used for all operations that touch leases. In the current
version of the code, clients will receive a special 'anonymous' instance of
this class with owner_num=0. In a future version each client will get a
different instance, with a dedicated owner_num.
"""

import time

from foolscap.api import Referenceable

from zope.interface import implements
from allmydata.interfaces import RIStorageServer

from allmydata.storage.common import si_b2a


class Account(Referenceable):
    implements(RIStorageServer)

    def __init__(self, owner_num, pubkey_vs, server, leasedb):
        self.owner_num = owner_num
        self.server = server
        self._leasedb = leasedb
        # for static accounts ("starter", "anonymous"), pubkey_vs is None
        self.pubkey_vs = pubkey_vs
        self.debug = False

    def is_static(self):
        return self.owner_num in (0,1)

    # these methods are called by StorageServer

    def get_owner_num(self):
        return self.owner_num

    def get_renewal_and_expiration_times(self):
        renewal_time = time.time()
        return (renewal_time, renewal_time + 31*24*60*60)

    # immutable.BucketWriter.close() does:
    #  add_share(), add_or_renew_lease(), mark_share_as_stable()

    # mutable writev() does:
    #  deleted shares: mark_share_as_going(), remove_share_and_leases()
    #  new shares: add_share(), add_or_renew_lease(), mark_share_as_stable()
    #  changed shares: change_share_space(), add_or_renew_lease()

    def add_share(self, storage_index, shnum, used_space, sharetype):
        if self.debug: print "ADD_SHARE", si_b2a(storage_index), shnum, used_space, sharetype
        self._leasedb.add_new_share(storage_index, shnum, used_space, sharetype)

    def add_or_renew_default_lease(self, storage_index, shnum):
        renewal_time, expiration_time = self.get_renewal_and_expiration_times()
        return self.add_or_renew_lease(storage_index, shnum, renewal_time, expiration_time)

    def add_or_renew_lease(self, storage_index, shnum, renewal_time, expiration_time):
        if self.debug: print "ADD_OR_RENEW_LEASE", si_b2a(storage_index), shnum
        self._leasedb.add_or_renew_leases(storage_index, shnum, self.owner_num,
                                          renewal_time, expiration_time)

    def change_share_space(self, storage_index, shnum, used_space):
        if self.debug: print "CHANGE_SHARE_SPACE", si_b2a(storage_index), shnum, used_space
        self._leasedb.change_share_space(storage_index, shnum, used_space)

    def mark_share_as_stable(self, storage_index, shnum, used_space):
        if self.debug: print "MARK_SHARE_AS_STABLE", si_b2a(storage_index), shnum, used_space
        self._leasedb.mark_share_as_stable(storage_index, shnum, used_space)

    def mark_share_as_going(self, storage_index, shnum):
        if self.debug: print "MARK_SHARE_AS_GOING", si_b2a(storage_index), shnum
        self._leasedb.mark_share_as_going(storage_index, shnum)

    def remove_share_and_leases(self, storage_index, shnum):
        if self.debug: print "REMOVE_SHARE_AND_LEASES", si_b2a(storage_index), shnum
        self._leasedb.remove_deleted_share(storage_index, shnum)

    # remote_add_lease() and remote_renew_lease() do this
    def add_lease_for_bucket(self, storage_index):
        if self.debug: print "ADD_LEASE_FOR_BUCKET", si_b2a(storage_index)
        renewal_time, expiration_time = self.get_renewal_and_expiration_times()
        self._leasedb.add_or_renew_leases(storage_index, None,
                                          self.owner_num, renewal_time, expiration_time)

    # The following RIStorageServer methods are called by remote clients

    def remote_get_version(self):
        return self.server.client_get_version(self)

    # all other RIStorageServer methods should pass through to self.server
    # but add the account as a final argument.

    def remote_allocate_buckets(self, storage_index, renew_secret, cancel_secret,
                                sharenums, allocated_size, canary):
        if self.debug: print "REMOTE_ALLOCATE_BUCKETS", si_b2a(storage_index)
        return self.server.client_allocate_buckets(storage_index,
                                                   sharenums, allocated_size,
                                                   canary, self)

    def remote_add_lease(self, storage_index, renew_secret, cancel_secret):
        if self.debug: print "REMOTE_ADD_LEASE", si_b2a(storage_index)
        self.add_lease_for_bucket(storage_index)
        return None

    def remote_renew_lease(self, storage_index, renew_secret):
        self.add_lease_for_bucket(storage_index)
        return None

    def remote_get_buckets(self, storage_index):
        return self.server.client_get_buckets(storage_index, self)

    def remote_slot_testv_and_readv_and_writev(self, storage_index, secrets,
                                               test_and_write_vectors, read_vector):
        write_enabler = secrets[0]
        return self.server.client_slot_testv_and_readv_and_writev(
            storage_index, write_enabler, test_and_write_vectors, read_vector, self)

    def remote_slot_readv(self, storage_index, shares, readv):
        return self.server.client_slot_readv(storage_index, shares, readv, self)

    def remote_advise_corrupt_share(self, share_type, storage_index, shnum, reason):
        return self.server.client_advise_corrupt_share(share_type, storage_index, shnum,
                                                       reason, self)

    def get_account_creation_time(self):
        return self._leasedb.get_account_creation_time(self.owner_num)

    def get_id(self):
        return self.pubkey_vs

    def get_leases(self, storage_index):
        return self._leasedb.get_leases(storage_index, self.owner_num)

    def get_stats(self):
        return self.server.get_stats()

    def get_accounting_crawler(self):
        return self.server.get_accounting_crawler()

    def get_expiration_policy(self):
        return self.server.get_expiration_policy()

    def get_bucket_counter(self):
        return self.server.get_bucket_counter()

    def get_serverid(self):
        return self.server.get_serverid()
