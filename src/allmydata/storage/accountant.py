
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

import weakref

from twisted.application import service
from twisted.internet import defer

from allmydata.storage.leasedb import create_lease_db, LeaseDB
from allmydata.storage.accounting_crawler import AccountingCrawler
from allmydata.storage.account import create_anonymous_account, create_starter_account
from allmydata.storage.expiration import ExpirationPolicy


@defer.inlineCallbacks
def create_accountant(storage_server, dbfile, statefile, expiration_policy=None):
    leasedb = yield create_lease_db(dbfile)
    anonymous_account = yield create_anonymous_account(leasedb, storage_server)
    starter_account = yield create_starter_account(leasedb, storage_server)
    crawler = AccountingCrawler(storage_server, statefile, leasedb)
    expiration_policy = expiration_policy or ExpirationPolicy(enabled=False)

    accountant = Accountant(
        leasedb, anonymous_account, starter_account, crawler,
        expiration_policy
    )
    accountant.setServiceParent(storage_server)
    defer.returnValue(accountant)


class Accountant(service.MultiService):
    """
    Manages accounts and owns the LeaseDB instance.

    Acts as an Account factory.

    TODO:

     - (medium-term) should access the leasedb via "a backend".

    """

    def __init__(self, leasedb, anonymous_account, starter_account, crawler,
                 expiration_policy):
        service.MultiService.__init__(self)
        self._active_accounts = weakref.WeakValueDictionary()
        self._leasedb = leasedb
        self._anonymous_account = anonymous_account
        self._starter_account = starter_account
        self._crawler = crawler
        self._crawler.setServiceParent(self)
        self._crawler.set_expiration_policy(expiration_policy)
        self._expiration_policy = expiration_policy

    def stopService(self):
        d = super(Accountant, self).stopService()
        if self._leasedb is not None:
            self._leasedb.close()  # should probably use twisted.enterprise.adbapi
        # anything else?
        return d

    def get_expiration_policy(self):
        return self._expiration_policy

    def set_expiration_policy(self, policy):
        self._expiration_policy = policy
        self._crawler.set_expiration_policy(policy)

    # XXX what about:
    # @property
    # def anonymous_account(self):
    def get_anonymous_account(self):
        return self._anonymous_account

    def get_starter_account(self):
        return self._starter_account

    def get_accounting_crawler(self):
        return self._crawler

    # methods used by admin interfaces
    # XXX think, fixme: should be async, maybe pass in storage_server?
    def get_all_accounts(self):
        for ownerid, pubkey_vs in self._leasedb.get_all_accounts():
            if pubkey_vs in self._active_accounts:
                yield self._active_accounts[pubkey_vs]
            else:
                yield Account(ownerid, pubkey_vs,
                              self.storage_server, self._leasedb)
