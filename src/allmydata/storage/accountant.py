
"""
This file contains the cross-account management code. It creates per-client
Account objects for the FURLification dance, as well as the 'anonymous
account for use until the server admin decides to make accounting mandatory.
It also provides usage statistics and reports for the status UI. This will
also implement the backend of the control UI (once we figure out how to
express that: maybe a CLI command, or tahoe.cfg settings, or a web frontend),
for things like enabling/disabling accounts and setting quotas.

The name 'accountant.py' could be better, preferably something that doesn't
share a prefix with 'account.py' so my tab-autocomplete will work nicely.
"""


class Accountant(service.MultiService):
    def __init__(self, storage_server, dbfile, statefile):
        service.MultiService.__init__(self)
        self.storage_server = storage_server
        self._leasedb = LeaseDB(dbfile)
        self._active_accounts = weakref.WeakValueDictionary()
        self._accountant_window = None
        self._anonymous_account = Account(0, None,
                                          self.storage_server, self._leasedb)

        crawler = AccountingCrawler(storage_server, statefile, self._leasedb)
        self.accounting_crawler = crawler
        crawler.setServiceParent(self)

    def get_accountant_window(self, tub):
        if not self._accountant_window:
            self._accountant_window = AccountantWindow(self, tub)
        return self._accountant_window

    def get_leasedb(self):
        return self._leasedb

    def set_expiration_policy(self,
                              expiration_enabled=False,
                              expiration_mode="age",
                              expiration_override_lease_duration=None,
                              expiration_cutoff_date=None,
                              expiration_sharetypes=("mutable", "immutable")):
        pass # TODO

    # methods used by AccountantWindow

    def get_account(self, pubkey_vs):
        if pubkey_vs not in self._active_accounts:
            ownernum = self._leasedb.get_or_allocate_ownernum(pubkey_vs)
            a = Account(ownernum, pubkey_vs, self.storage_server, self._leasedb)
            self._active_accounts[pubkey_vs] = a
            # the client's RemoteReference will keep the Account alive. When
            # it disconnects, that reference will lapse, and it will be
            # removed from the _active_accounts WeakValueDictionary
        return self._active_accounts[pubkey_vs] # note: a is still alive

    def get_anonymous_account(self):
        return self._anonymous_account

    # methods used by admin interfaces
    def get_all_accounts(self):
        for ownerid, pubkey_vs in self._leasedb.get_all_accounts():
            if pubkey_vs in self._active_accounts:
                yield self._active_accounts[pubkey_vs]
            else:
                yield Account(ownerid, pubkey_vs,
                              self.storage_server, self._leasedb)


class AccountantWindow(Referenceable):
    def __init__(self, accountant, tub):
        self.accountant = accountant
        self.tub = tub

    def remote_get_account(self, msg, sig, pubkey_vs):
        print "GETTING ACCOUNT", msg
        vk = keyutil.parse_pubkey(pubkey_vs)
        vk.verify(sig, msg)
        account = self.accountant.get_account(pubkey_vs)
        msg_d = simplejson.loads(msg.decode("utf-8"))
        rxFURL = msg_d["please-give-Account-to-rxFURL"].encode("ascii")
        account.set_nickname(msg_d["nickname"])
        d = self.tub.getReference(rxFURL)
        def _got_rx(rx):
            account.connection_from(rx)
            d = rx.callRemote("account", account)
            d.addCallback(lambda ign: account._send_status())
            d.addCallback(lambda ign: account._send_account_message())
            return d
        d.addCallback(_got_rx)
        d.addErrback(log.err, umid="nFYfcA")
        return d


# XXX TODO new idea: move all leases into the DB. Do not store leases in
# shares at all. The crawler will exist solely to discover shares that
# have been manually added to disk (via 'scp' or some out-of-band means),
# and will add 30- or 60- day "migration leases" to them, to keep them
# alive until their original owner does a deep-add-lease and claims them
# properly. Better migration tools ('tahoe storage export'?) will create
# export files that include both the share data and the lease data, and
# then an import tool will both put the share in the right place and
# update the recipient node's lease DB.
#
# I guess the crawler will also be responsible for deleting expired
# shares, since it will be looking at both share files on disk and leases
# in the DB.
#
# So the DB needs a row per share-on-disk, and a separate table with
# leases on each bucket. When it sees a share-on-disk that isn't in the
# first table, it adds the migration-lease. When it sees a share-on-disk
# that is in the first table but has no leases in the second table (i.e.
# expired), it deletes both the share and the first-table row. When it
# sees a row in the first table but no share-on-disk (i.e. manually
# deleted share), it deletes the row (and any leases).
