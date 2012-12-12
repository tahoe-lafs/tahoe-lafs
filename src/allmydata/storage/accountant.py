
"""
This file contains the cross-account management code. It creates per-client
Account objects, as well as the "anonymous account" for use until a future
version of Tahoe-LAFS implements the FURLification dance. It also provides
usage statistics and reports for the status UI. This will also implement the
backend of the control UI (once we figure out how to express that: maybe a
CLI command, or tahoe.cfg settings, or a web frontend), for things like
enabling/disabling accounts and setting quotas.
"""

import weakref

from twisted.application import service

from allmydata.storage.leasedb import LeaseDB
from allmydata.storage.accounting_crawler import AccountingCrawler
from allmydata.storage.account import Account


class Accountant(service.MultiService):
    def __init__(self, storage_server, dbfile, statefile, clock=None):
        service.MultiService.__init__(self)
        self._storage_server = storage_server
        self._leasedb = LeaseDB(dbfile)
        self._active_accounts = weakref.WeakValueDictionary()
        self._anonymous_account = Account(LeaseDB.ANONYMOUS_ACCOUNTID, None,
                                          self._storage_server, self._leasedb)
        self._starter_account = Account(LeaseDB.STARTER_LEASE_ACCOUNTID, None,
                                        self._storage_server, self._leasedb)

        crawler = AccountingCrawler(self._storage_server.backend, statefile, self._leasedb, clock=clock)
        self._accounting_crawler = crawler
        crawler.setServiceParent(self)

    def get_leasedb(self):
        return self._leasedb

    def set_expiration_policy(self, policy):
        self._accounting_crawler.set_expiration_policy(policy)

    def get_anonymous_account(self):
        return self._anonymous_account

    def get_starter_account(self):
        return self._starter_account

    def get_accounting_crawler(self):
        return self._accounting_crawler

    # methods used by admin interfaces
    def get_all_accounts(self):
        for ownerid, pubkey_vs in self._leasedb.get_all_accounts():
            if pubkey_vs in self._active_accounts:
                yield self._active_accounts[pubkey_vs]
            else:
                yield Account(ownerid, pubkey_vs,
                              self.storage_server, self._leasedb)
