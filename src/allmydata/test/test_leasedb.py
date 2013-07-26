
import os

from twisted.trial import unittest

from allmydata.util import fileutil
from allmydata.util import dbutil
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
        l.startService()
        self.failUnlessEqual(set(l.get_all_accounts()), BASE_ACCOUNTS)

        # should be able to open an existing one too
        l2 = LeaseDB(dbfilename)
        l2.startService()
        self.failUnlessEqual(set(l2.get_all_accounts()), BASE_ACCOUNTS)

    def test_basic(self):
        dbfilename = self.make("create")
        l = LeaseDB(dbfilename)
        l.startService()

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

        num_shares, total_leased_used_space = l.get_total_leased_sharecount_and_used_space()
        num_sharesets = l.get_number_of_sharesets()
        self.failUnlessEqual(total_leased_used_space, 12345)
        self.failUnlessEqual(num_shares, 1)
        self.failUnlessEqual(num_sharesets, 1)

        l.add_new_share('si1', 1, 12345, SHARETYPE_IMMUTABLE)
        l.add_starter_lease('si1', 1)
        num_shares, total_leased_used_space = l.get_total_leased_sharecount_and_used_space()
        num_sharesets = l.get_number_of_sharesets()
        self.failUnlessEqual(total_leased_used_space, 24690)
        self.failUnlessEqual(num_shares, 2)
        self.failUnlessEqual(num_sharesets, 1)

        l.add_new_share('si2', 0, 12345, SHARETYPE_IMMUTABLE)
        l.add_starter_lease('si2', 0)
        num_sharesets = l.get_number_of_sharesets()
        num_shares, total_leased_used_space = l.get_total_leased_sharecount_and_used_space()
        num_sharesets = l.get_number_of_sharesets()
        self.failUnlessEqual(total_leased_used_space, 37035)
        self.failUnlessEqual(num_shares, 3)
        self.failUnlessEqual(num_sharesets, 2)

class MockCursor:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

class MockDB:
    def __init__(self):
        self.closed = False

    def cursor(self):
        return MockCursor()

    def close(self):
        self.closed = True

class FD_Leak(unittest.TestCase):

    def create_leasedb(self, testname):
        basedir = os.path.join("leasedb", "FD_Leak", testname)
        fileutil.make_dirs(basedir)
        dbfilename = os.path.join(basedir, "leasedb.sqlite")
        return dbfilename

    def test_basic(self):
        # This test ensures that the db connection is closed by leasedb after
        # the service stops.
        def _call_get_db(*args, **kwargs):
            return None, MockDB()
        self.patch(dbutil, 'get_db', _call_get_db)
        dbfilename = self.create_leasedb("test_basic")
        l = LeaseDB(dbfilename)
        l.startService()
        db = l._db
        cursor = l._cursor
        l.stopService()
        self.failUnless(db.closed)
        self.failUnless(cursor.closed)
