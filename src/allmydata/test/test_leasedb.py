
import os

from twisted.trial import unittest
from twisted.internet import defer

from allmydata.test.common import ShouldFailMixin
from allmydata.util import fileutil
from allmydata.util.dbutil import IntegrityError
from allmydata.storage.leasedb import LeaseDB, LeaseInfo, NonExistentShareError, \
     SHARETYPE_IMMUTABLE, create_lease_db


BASE_ACCOUNTS = set([(0, u"anonymous"), (1, u"starter")])

class DB(unittest.TestCase, ShouldFailMixin):
    def make(self, testname):
        basedir = os.path.join("leasedb", "DB", testname)
        fileutil.make_dirs(basedir)
        dbfilename = os.path.join(basedir, "leasedb.sqlite")
        return dbfilename

    @defer.inlineCallbacks
    def test_create(self):
        dbfilename = self.make("create")
        l = yield create_lease_db(dbfilename)
        accounts = yield l.get_all_accounts()
        self.failUnlessEqual(set(accounts), BASE_ACCOUNTS)
        yield l.close()

        # should be able to open an existing one too
        l2 = yield create_lease_db(dbfilename)
        accounts = yield l2.get_all_accounts()
        self.failUnlessEqual(set(accounts), BASE_ACCOUNTS)
        yield l2.close()

    @defer.inlineCallbacks
    def test_add(self):
        dbfilename = self.make("create")
        l = yield create_lease_db(dbfilename)
        l.debug = True

        yield l.add_new_share('si1', 0, 12345, SHARETYPE_IMMUTABLE)

        # lease for non-existant share
        # self.shouldFail(IntegrityError, "", None, l._conn.runOperation,
        #                 "INSERT INTO `leases` VALUES(?,?,?,?,?)",
        #                 ('si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))

        # self.shouldFail(NonExistentShareError, "", None, l.add_starter_lease, 'si2', 0)
        # self.shouldFail(NonExistentShareError, "", None, l.add_or_renew_leases,
        #                 'si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)


        # # updating the lease should succeed
        yield l.add_starter_lease('si1', 0)

        # leaseinfo = yield l.get_leases('si1', LeaseDB.STARTER_LEASE_ACCOUNTID)

        # self.failUnlessEqual(len(leaseinfo), 1)
        # self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        # self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        # self.failUnlessEqual(leaseinfo[0].shnum, 0)
        # self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.STARTER_LEASE_ACCOUNTID)

        # # adding a duplicate entry directly should fail
        # self.shouldFail(IntegrityError, "", None, l._conn.runOperation,
        #                 "INSERT INTO `leases` VALUES(?,?,?,?,?)",
        #                 ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))

        # # same for add_or_renew_leases
        # yield l.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)

        # # updating the lease should succeed
        # yield l.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 1, 2)

        # leaseinfo = yield l.get_leases('si1', LeaseDB.ANONYMOUS_ACCOUNTID)

        # self.failUnlessEqual(len(leaseinfo), 1)
        # self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        # self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        # self.failUnlessEqual(leaseinfo[0].shnum, 0)
        # self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.ANONYMOUS_ACCOUNTID)
        # self.failUnlessEqual(leaseinfo[0].renewal_time, 1)
        # self.failUnlessEqual(leaseinfo[0].expiration_time, 2)

        # adding a duplicate entry directly should fail
        #self.shouldFail(IntegrityError, "", None, l._conn.runOperation,
        #                "INSERT INTO `leases` VALUES(?,?,?,?,?)",
        #                ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))
        #l._conn.runOperation("INSERT INTO `leases` VALUES(?,?,?,?,?)",
        #                     ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))


    @defer.inlineCallbacks
    def test_basic(self):
        dbfilename = self.make("create")
        l = yield create_lease_db(dbfilename)

        yield l.add_new_share('si1', 0, 12345, SHARETYPE_IMMUTABLE)

        # lease for non-existant share
        self.shouldFail(IntegrityError, "", None, l._conn.runOperation,
                        "INSERT INTO `leases` VALUES(?,?,?,?,?)",
                        ('si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))

        self.shouldFail(NonExistentShareError, "", None, l.add_starter_lease, 'si2', 0)
        self.shouldFail(NonExistentShareError, "", None, l.add_or_renew_leases,
                        'si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)

        # updating the lease should succeed
        yield l.add_starter_lease('si1', 0)

        leaseinfo = yield l.get_leases('si1', LeaseDB.STARTER_LEASE_ACCOUNTID)

        self.failUnlessEqual(len(leaseinfo), 1)
        self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        self.failUnlessEqual(leaseinfo[0].shnum, 0)
        self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.STARTER_LEASE_ACCOUNTID)

        # adding a duplicate entry directly should fail
        self.shouldFail(IntegrityError, "", None, l._conn.runOperation,
                        "INSERT INTO `leases` VALUES(?,?,?,?,?)",
                        ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))

        # same for add_or_renew_leases
        yield l.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)

        # updating the lease should succeed
        yield l.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 1, 2)

        leaseinfo = yield l.get_leases('si1', LeaseDB.ANONYMOUS_ACCOUNTID)

        self.failUnlessEqual(len(leaseinfo), 1)
        self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        self.failUnlessEqual(leaseinfo[0].shnum, 0)
        self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.ANONYMOUS_ACCOUNTID)
        self.failUnlessEqual(leaseinfo[0].renewal_time, 1)
        self.failUnlessEqual(leaseinfo[0].expiration_time, 2)

        # adding a duplicate entry directly should fail
        self.shouldFail(IntegrityError, "", None, l._conn.runOperation,
                        "INSERT INTO `leases` VALUES(?,?,?,?,?)",
                        ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))
