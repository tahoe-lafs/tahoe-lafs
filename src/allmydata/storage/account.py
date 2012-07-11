
"""
This file contains the client-facing interface for manipulating shares. It
implements RIStorageServer, and contains an embedded owner id which is used
for all operations that touch leases. Initially, clients will receive a
special 'anonymous' instance of this class with ownerid=0. Later, when the
FURLification dance is established, each client will get a different instance
(with a dedicated ownerid).
"""

class BaseAccount(Referenceable):
    def __init__(self, owner_num, server, leasedb):
        self.owner_num = owner_num
        self.server = server
        self._leasedb = leasedb

    def is_static(self):
        return self.owner_num in (0,1)

    # these methods are called by StorageServer

    def get_owner_num(self):
        return self.owner_num

    def get_expiration_time(self):
        return time.time() + 31*24*60*60

    # immutable.BucketWriter.close() does add_share() and add_lease()

    # mutable_writev() does:
    #  deleted shares: remove_share_and_leases()
    #  new shares: add_share(), add_lease()
    #  changed shares: update_share(), add_lease()

    def add_share(self, prefix, storage_index, shnum, filename, commit=True):
        size = size_of_disk_file(filename)
        self._leasedb.add_new_share(prefix, storage_index, shnum, size)
        if commit:
            self._leasedb.commit()

    def add_lease(self, storage_index, shnum, commit=True):
        expire_time = self.get_expiration_time()
        self._leasedb.add_or_renew_leases(storage_index, shnum,
                                          self.owner_num, expire_time)
        if commit:
            self._leasedb.commit()

    def update_share(self, storage_index, shnum, filename, commit=True):
        size = size_of_disk_file(filename)
        self._leasedb.change_share_size(storage_index, shnum, size)
        if commit:
            self._leasedb.commit()

    def remove_share_and_leases(self, storage_index, shnum):
        self._leasedb.remove_deleted_shares([storage_index, shnum])

    # remote_add_lease() and remote_renew_lease() do this
    def add_lease_for_bucket(self, storage_index, commit=True):
        expire_time = self.get_expiration_time()
        self._leasedb.add_or_renew_leases(storage_index, None,
                                          self.owner_num, expire_time)
        if commit:
            self._leasedb.commit()

    def commit(self):
        self._leasedb.commit()

    # The following RIStorageServer methods are called by remote clients

    def remote_get_version(self):
        return self.server.client_get_version(self)
    # all other RIStorageServer methods should pass through to self.server
    # but add owner_num=

    def remote_allocate_buckets(self, storage_index,
                                renew_secret, cancel_secret,
                                sharenums, allocated_size,
                                canary):
        return self.server.client_allocate_buckets(storage_index,
                                                   sharenums, allocated_size,
                                                   canary, self)
    def remote_add_lease(self, storage_index, renew_secret, cancel_secret):
        self.add_lease_for_bucket(storage_index)
        return None
    def remote_renew_lease(self, storage_index, renew_secret):
        self.add_lease_for_bucket(storage_index)
        return None
    #def remote_cancel_lease(self, storage_index, cancel_secret):
    #    raise NotImplementedError
    def remote_get_buckets(self, storage_index):
        return self.server.client_get_buckets(storage_index, self)
    def remote_slot_testv_and_readv_and_writev(self, storage_index,
                                               secrets,
                                               test_and_write_vectors,
                                               read_vector):
        (write_enabler, renew_secret, cancel_secret) = secrets
        meth = self.server.client_slot_testv_and_readv_and_writev
        return meth(storage_index, write_enabler,
                    test_and_write_vectors, read_vector, self)
    def remote_slot_readv(self, storage_index, shares, readv):
        return self.server.client_slot_readv(storage_index, shares, readv, self)
    def remote_advise_corrupt_share(self, share_type, storage_index, shnum,
                                    reason):
        return self.server.client_advise_corrupt_share(
            share_type, storage_index, shnum, reason, self)

class AnonymousAccount(BaseAccount):
    implements(RIStorageServer)

class Account(BaseAccount):
    def __init__(self, owner_num, pubkey_vs, server, leasedb):
        BaseAccount.__init__(self, owner_num, server, leasedb)
        self.pubkey_vs = pubkey_vs
        self.connected = False
        self.connected_since = None
        self.connection = None
        import random
        def maybe(): return bool(random.randint(0,1))
        self.status = {"write": maybe(),
                       "read": maybe(),
                       "save": maybe(),
                       }
        self.account_message = {
            "message": "free storage! %d" % random.randint(0,10),
            "fancy": "free pony if you knew how to ask",
            }

    def get_account_attribute(self, name):
        return self._leasedb.get_account_attribute(self.owner_num, name)
    def set_account_attribute(self, name, value):
        self._leasedb.set_account_attribute(self.owner_num, name, value)
    def get_account_creation_time(self):
        return self._leasedb.get_account_creation_time(self.owner_num)

    def remote_get_status(self):
        return self.status
    def remote_get_account_message(self):
        return self.account_message

    # these are the non-RIStorageServer methods, some remote, some local

    def set_nickname(self, nickname):
        if len(nickname) > 1000:
            raise ValueError("nickname too long")
        self.set_account_attribute("nickname", nickname)

    def get_nickname(self):
        n = self.get_account_attribute("nickname")
        if n:
            return n
        return u""

    def get_id(self):
        return self.pubkey_vs

    def remote_get_current_usage(self):
        return self.get_current_usage()

    def get_current_usage(self):
        return self._leasedb.get_account_usage(self.owner_num)

    def connection_from(self, rx):
        self.connected = True
        self.connected_since = time.time()
        self.connection = rx
        rhost = rx.getPeer()
        from twisted.internet import address
        if isinstance(rhost, address.IPv4Address):
            rhost_s = "%s:%d" % (rhost.host, rhost.port)
        elif "LoopbackAddress" in str(rhost):
            rhost_s = "loopback"
        else:
            rhost_s = str(rhost)
        self.set_account_attribute("last_connected_from", rhost_s)
        rx.notifyOnDisconnect(self._disconnected)

    def _disconnected(self):
        self.connected = False
        self.connected_since = None
        self.connection = None
        self.set_account_attribute("last_seen", int(time.time()))
        self.disconnected_since = None

    def _send_status(self):
        self.connection.callRemoteOnly("status", self.status)
    def _send_account_message(self):
        self.connection.callRemoteOnly("account_message", self.account_message)

    def set_status(self, write, read, save):
        self.status = { "write": write,
                        "read": read,
                        "save": save,
                        }
        self._send_status()
    def set_account_message(self, message):
        self.account_message = message
        self._send_account_message()

    def get_connection_status(self):
        # starts as: connected=False, connected_since=None,
        #            last_connected_from=None, last_seen=None
        # while connected: connected=True, connected_since=START,
        #                  last_connected_from=HOST, last_seen=IGNOREME
        # after disconnect: connected=False, connected_since=None,
        #                   last_connected_from=HOST, last_seen=STOP

        last_seen = int_or_none(self.get_account_attribute("last_seen"))
        last_connected_from = self.get_account_attribute("last_connected_from")
        created = int_or_none(self.get_account_creation_time())

        return {"connected": self.connected,
                "connected_since": self.connected_since,
                "last_connected_from": last_connected_from,
                "last_seen": last_seen,
                "created": created,
                }
