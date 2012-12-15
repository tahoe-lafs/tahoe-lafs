
import os

from twisted.trial import unittest

from allmydata.util import fileutil
from allmydata.util.dbutil import IntegrityError
from allmydata.storage.leasedb import LeaseDB, LeaseInfo, NonExistentShareError, \
     SHARETYPE_IMMUTABLE


BASE_ACCOUNTS = set([(0, u"anonymous"), (1, u"starter")])

class DB(unittest.TestCase):
    def make(self, testname):
        basedir = os.path.join("leasedb", "DB", testname)
        fileutil.make_dirs(basedir)
        dbfilename = os.path.join(basedir, "leasedb.sqlite")
        return dbfilename

    def test_create(self):
        dbfilename = self.make("create")
        l = LeaseDB(dbfilename)
        self.failUnlessEqual(set(l.get_all_accounts()), BASE_ACCOUNTS)

        # should be able to open an existing one too
        l2 = LeaseDB(dbfilename)
        self.failUnlessEqual(set(l2.get_all_accounts()), BASE_ACCOUNTS)

    def test_basic(self):
        dbfilename = self.make("create")
        l = LeaseDB(dbfilename)

        l.add_new_share('si1', 0, 12345, SHARETYPE_IMMUTABLE)

        # lease for non-existant share
        self.failUnlessRaises(IntegrityError, l._cursor.execute,
                              "INSERT INTO `leases` VALUES(?,?,?,?,?)",
                              ('si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))

        self.failUnlessRaises(NonExistentShareError, l.add_starter_lease,
                              'si2', 0)
        self.failUnlessRaises(NonExistentShareError, l.add_or_renew_leases,
                              'si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)

        l.add_starter_lease('si1', 0)

        # updating the lease should succeed
        l.add_starter_lease('si1', 0)

        leaseinfo = l.get_leases('si1', LeaseDB.STARTER_LEASE_ACCOUNTID)

        self.failUnlessEqual(len(leaseinfo), 1)
        self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        self.failUnlessEqual(leaseinfo[0].shnum, 0)
        self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.STARTER_LEASE_ACCOUNTID)

        # adding a duplicate entry directly should fail
        self.failUnlessRaises(IntegrityError, l._cursor.execute,
                              "INSERT INTO `leases` VALUES(?,?,?,?,?)",
                              ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))

        # same for add_or_renew_leases
        l.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)

        # updating the lease should succeed
        l.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 1, 2)

        leaseinfo = l.get_leases('si1', LeaseDB.ANONYMOUS_ACCOUNTID)

        self.failUnlessEqual(len(leaseinfo), 1)
        self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        self.failUnlessEqual(leaseinfo[0].shnum, 0)
        self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.ANONYMOUS_ACCOUNTID)
        self.failUnlessEqual(leaseinfo[0].renewal_time, 1)
        self.failUnlessEqual(leaseinfo[0].expiration_time, 2)

        # adding a duplicate entry directly should fail
        self.failUnlessRaises(IntegrityError, l._cursor.execute,
                              "INSERT INTO `leases` VALUES(?,?,?,?,?)",
                              ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))
