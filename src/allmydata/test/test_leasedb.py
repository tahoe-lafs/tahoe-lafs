
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

    @defer.inlineCallbacks
    def setUp(self):
        basedir = os.path.join("leasedb", "DB", "db_test")
        fileutil.make_dirs(basedir)
        self.dbfilename = os.path.join(basedir, "leasedb.sqlite")
        self.db = yield create_lease_db(self.dbfilename)

    @defer.inlineCallbacks
    def tearDown(self):
        try:
            yield self.db.close()
        except:
            pass
        os.unlink(self.dbfilename)

    @defer.inlineCallbacks
    def test_create(self):
        accounts = yield self.db.get_all_accounts()
        self.failUnlessEqual(set(accounts), BASE_ACCOUNTS)
        yield self.db.close()

        # should be able to open an existing one too
        l2 = yield create_lease_db(self.dbfilename)
        accounts = yield l2.get_all_accounts()
        self.failUnlessEqual(set(accounts), BASE_ACCOUNTS)
        yield l2.close()

    @defer.inlineCallbacks
    def test_add(self):
        self.db.debug = True

        yield self.db.add_new_share('si1', 0, 12345, SHARETYPE_IMMUTABLE)

        if False:
            # lease for non-existant share
            yield self.shouldFail(
                IntegrityError, "", None, self.db._conn.runOperation,
                "INSERT INTO `leases` VALUES(?,?,?,?,?)",
                ('si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0),
            )

            yield self.shouldFail(NonExistentShareError, "", None, self.db.add_starter_lease, 'si2', 0)
            yield self.shouldFail(
                NonExistentShareError, "", None, self.db.add_or_renew_leases,
                ('si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0),
            )


        # # updating the lease should succeed
        yield self.db.add_starter_lease('si1', 0)

        # leaseinfo = yield self.db.get_leases('si1', LeaseDB.STARTER_LEASE_ACCOUNTID)

        # self.failUnlessEqual(len(leaseinfo), 1)
        # self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        # self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        # self.failUnlessEqual(leaseinfo[0].shnum, 0)
        # self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.STARTER_LEASE_ACCOUNTID)

        # # adding a duplicate entry directly should fail
        # self.shouldFail(IntegrityError, "", None, self.db._conn.runOperation,
        #                 "INSERT INTO `leases` VALUES(?,?,?,?,?)",
        #                 ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))

        # # same for add_or_renew_leases
        # yield self.db.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)

        # # updating the lease should succeed
        # yield self.db.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 1, 2)

        # leaseinfo = yield self.db.get_leases('si1', LeaseDB.ANONYMOUS_ACCOUNTID)

        # self.failUnlessEqual(len(leaseinfo), 1)
        # self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        # self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        # self.failUnlessEqual(leaseinfo[0].shnum, 0)
        # self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.ANONYMOUS_ACCOUNTID)
        # self.failUnlessEqual(leaseinfo[0].renewal_time, 1)
        # self.failUnlessEqual(leaseinfo[0].expiration_time, 2)

        # adding a duplicate entry directly should fail
        #self.shouldFail(IntegrityError, "", None, self.db._conn.runOperation,
        #                "INSERT INTO `leases` VALUES(?,?,?,?,?)",
        #                ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))
        #self.db._conn.runOperation("INSERT INTO `leases` VALUES(?,?,?,?,?)",
        #                     ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))


    @defer.inlineCallbacks
    def test_basic(self):
        yield self.db.add_new_share('si1', 0, 12345, SHARETYPE_IMMUTABLE)

        # lease for non-existant share
        yield self.shouldFail(
            IntegrityError, "", None, self.db._conn.runOperation,
            "INSERT INTO `leases` VALUES(?,?,?,?,?)",
            ('si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)
        )

        yield self.shouldFail(
            NonExistentShareError, "", None, self.db.add_starter_lease, 'si2', 0
        )
        yield self.shouldFail(
            NonExistentShareError, "", None, self.db.add_or_renew_leases,
            'si2', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0,
        )

        # updating the lease should succeed
        yield self.db.add_starter_lease('si1', 0)

        leaseinfo = yield self.db.get_leases('si1', LeaseDB.STARTER_LEASE_ACCOUNTID)

        self.failUnlessEqual(len(leaseinfo), 1)
        self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        self.failUnlessEqual(leaseinfo[0].shnum, 0)
        self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.STARTER_LEASE_ACCOUNTID)

        # adding a duplicate entry directly should fail
        self.shouldFail(IntegrityError, "", None, self.db._conn.runOperation,
                        "INSERT INTO `leases` VALUES(?,?,?,?,?)",
                        ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0))

        # same for add_or_renew_leases
        yield self.db.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0)

        # updating the lease should succeed
        yield self.db.add_or_renew_leases('si1', 0, LeaseDB.ANONYMOUS_ACCOUNTID, 1, 2)

        leaseinfo = yield self.db.get_leases('si1', LeaseDB.ANONYMOUS_ACCOUNTID)

        self.failUnlessEqual(len(leaseinfo), 1)
        self.failUnlessIsInstance(leaseinfo[0], LeaseInfo)
        self.failUnlessEqual(leaseinfo[0].storage_index, 'si1')
        self.failUnlessEqual(leaseinfo[0].shnum, 0)
        self.failUnlessEqual(leaseinfo[0].owner_num, LeaseDB.ANONYMOUS_ACCOUNTID)
        self.failUnlessEqual(leaseinfo[0].renewal_time, 1)
        self.failUnlessEqual(leaseinfo[0].expiration_time, 2)

        # adding a duplicate entry directly should fail
        yield self.shouldFail(
            IntegrityError, "", None, self.db._conn.runOperation,
            "INSERT INTO `leases` VALUES(?,?,?,?,?)",
            ('si1', 0,  LeaseDB.ANONYMOUS_ACCOUNTID, 0, 0),
        )
