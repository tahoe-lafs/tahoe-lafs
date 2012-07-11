
"""
This file manages the lease database, and runs the crawler which recovers
from lost-db conditions (both initial boot, DB failures, and shares being
added/removed out-of-band) by adding temporary 'starter leases'. It queries
the storage backend to enumerate existing shares (for each one it needs SI,
shnum, and size). It can also instruct the storage backend to delete a share
which has expired.
"""

import simplejson
import os, time, weakref, re
from zope.interface import implements
from twisted.application import service
from foolscap.api import Referenceable
from allmydata.interfaces import RIStorageServer
from allmydata.util import log, keyutil, dbutil
from allmydata.storage.crawler import ShareCrawler

class BadAccountName(Exception):
    pass
class BadShareID(Exception):
    pass

def int_or_none(s):
    if s is None:
        return s
    return int(s)

# try to get rid of all the AUTOINCREMENT keys, use things like "SI/shnum"
# and pubkey as the index
LEASE_SCHEMA_V1 = """
CREATE TABLE version
(
 version INTEGER -- contains one row, set to 1
);

CREATE TABLE shares
(
 `id` INTEGER PRIMARY KEY AUTOINCREMENT,
 `prefix` VARCHAR(2),
 `storage_index` VARCHAR(26),
 `shnum` INTEGER,
 `size` INTEGER,
 `garbage` INTEGER -- set after last lease is removed, before file is deleted
);

CREATE INDEX `prefix` ON shares (`prefix`);
CREATE UNIQUE INDEX `share_id` ON shares (`storage_index`,`shnum`);

CREATE TABLE leases
(
 `id` INTEGER PRIMARY KEY AUTOINCREMENT,
 -- FOREIGN KEY (`share_id`) REFERENCES shares(id), -- not enabled?
 -- FOREIGN KEY (`account_id`) REFERENCES accounts(id),
 `share_id` INTEGER,
 `account_id` INTEGER,
 `expiration_time` INTEGER
);

CREATE INDEX `account_id` ON `leases` (`account_id`);
CREATE INDEX `expiration_time` ON `leases` (`expiration_time`);

CREATE TABLE accounts
(
 `id` INTEGER PRIMARY KEY AUTOINCREMENT,
 -- do some performance testing. Z+DS propose using pubkey_vs as the primary
 -- key. That would increase the size of the DB and the index (repeated
 -- pubkeys instead of repeated small integers), right? Also, I think we
 -- actually want to retain the account.id as an abstraction barrier: you
 -- might have sub-accounts which are controlled by signed messages, for
 -- which there is no single pubkey associated with the account.
 `pubkey_vs` VARCHAR(52),
 `creation_time` INTEGER
);
CREATE UNIQUE INDEX `pubkey_vs` ON `accounts` (`pubkey_vs`);

CREATE TABLE account_attributes
(
 `id` INTEGER PRIMARY KEY AUTOINCREMENT,
 `account_id` INTEGER,
 `name` VARCHAR(20),
 `value` VARCHAR(20) -- actually anything: usually string, unicode, integer
 );
CREATE UNIQUE INDEX `account_attr` ON `account_attributes` (`account_id`, `name`);

INSERT INTO `accounts` VALUES (0, "anonymous", 0);
INSERT INTO `accounts` VALUES (1, "starter", 0);

"""

DAY = 24*60*60
MONTH = 30*DAY

class LeaseDB:
    STARTER_LEASE_ACCOUNTID = 1
    STARTER_LEASE_DURATION = 2*MONTH

    # for all methods that start by setting self._dirty=True, be sure to call
    # .commit() when you're done

    def __init__(self, dbfile):
        (self._sqlite,
         self._db) = dbutil.get_db(dbfile, create_version=(LEASE_SCHEMA_V1, 1))
        self._cursor = self._db.cursor()
        self._dirty = False

    # share management

    def get_shares_for_prefix(self, prefix):
        self._cursor.execute("SELECT `storage_index`,`shnum`"
                             " FROM `shares`"
                             " WHERE `prefix` == ?",
                             (prefix,))
        db_shares = set([(si,shnum) for (si,shnum) in self._cursor.fetchall()])
        return db_shares

    def add_new_share(self, prefix, storage_index, shnum, size):
        # XXX: when test_repairer.Repairer.test_repair_from_deletion_of_1
        # runs, it deletes the share from disk, then the repairer replaces it
        # (in the same place). That results in a duplicate entry in the
        # 'shares' table, which causes a sqlite.IntegrityError . The
        # add_new_share() code needs to tolerate surprises like this: the
        # share might have been manually deleted, and the crawler may not
        # have noticed it yet, so test for an existing entry and use it if
        # present. (and check the code paths carefully to make sure that
        # doesn't get too weird).
        print "ADD_NEW_SHARE", storage_index, shnum
        self._dirty = True
        self._cursor.execute("INSERT INTO `shares`"
                             " VALUES (?,?,?,?,?)",
                             (None, prefix, storage_index, shnum, size))
        shareid = self._cursor.lastrowid
        return shareid

    def add_starter_lease(self, shareid):
        self._dirty = True
        self._cursor.execute("INSERT INTO `leases`"
                             " VALUES (?,?,?,?)",
                             (None, shareid, self.STARTER_LEASE_ACCOUNTID,
                              int(time.time()+self.STARTER_LEASE_DURATION)))
        leaseid = self._cursor.lastrowid
        return leaseid

    def remove_deleted_shares(self, shareids):
        print "REMOVE_DELETED_SHARES", shareids
        # TODO: replace this with a sensible DELETE, join, and sub-SELECT
        shareids2 = []
        for deleted_shareid in shareids:
            storage_index, shnum = deleted_shareid
            self._cursor.execute("SELECT `id` FROM `shares`"
                                 " WHERE `storage_index`=? AND `shnum`=?",
                                 (storage_index, shnum))
            row = self._cursor.fetchone()
            if row:
                shareids2.append(row[0])
        for shareid2 in shareids2:
            self._dirty = True
            self._cursor.execute("DELETE FROM `leases`"
                                 " WHERE `share_id`=?",
                                 (shareid2,))

    def change_share_size(self, storage_index, shnum, size):
        self._dirty = True
        self._cursor.execute("UPDATE `shares` SET `size`=?"
                             " WHERE storage_index=? AND shnum=?",
                             (size, storage_index, shnum))

    # lease management

    def add_or_renew_leases(self, storage_index, shnum, ownerid,
                            expiration_time):
        # shnum=None means renew leases on all shares
        self._dirty = True
        if shnum is None:
            self._cursor.execute("SELECT `id` FROM `shares`"
                                 " WHERE `storage_index`=?",
                                 (storage_index,))
        else:
            self._cursor.execute("SELECT `id` FROM `shares`"
                                 " WHERE `storage_index`=? AND `shnum`=?",
                                 (storage_index, shnum))
        rows = self._cursor.fetchall()
        if not rows:
            raise BadShareID("can't find SI=%s shnum=%s in `shares` table"
                             % (storage_index, shnum))
        for (shareid,) in rows:
            self._cursor.execute("SELECT `id` FROM `leases`"
                                 " WHERE `share_id`=? AND `account_id`=?",
                                 (shareid, ownerid))
            row = self._cursor.fetchone()
            if row:
                leaseid = row[0]
                self._cursor.execute("UPDATE `leases` SET expiration_time=?"
                                     " WHERE `id`=?",
                                     (expiration_time, leaseid))
            else:
                self._cursor.execute("INSERT INTO `leases` VALUES (?,?,?,?)",
                                     (None, shareid, ownerid, expiration_time))

    # account management

    def get_account_usage(self, accountid):
        self._cursor.execute("SELECT SUM(`size`) FROM shares"
                             " WHERE `id` IN"
                             "  (SELECT DISTINCT `share_id` FROM `leases`"
                             "   WHERE `account_id`=?)",
                             (accountid,))
        row = self._cursor.fetchone()
        if not row or not row[0]: # XXX why did I need the second clause?
            return 0
        return row[0]

    def get_account_attribute(self, accountid, name):
        self._cursor.execute("SELECT `value` FROM `account_attributes`"
                             " WHERE account_id=? AND name=?",
                             (accountid, name))
        row = self._cursor.fetchone()
        if row:
            return row[0]
        return None

    def set_account_attribute(self, accountid, name, value):
        self._cursor.execute("SELECT `id` FROM `account_attributes`"
                             " WHERE `account_id`=? AND `name`=?",
                             (accountid, name))
        row = self._cursor.fetchone()
        if row:
            attrid = row[0]
            self._cursor.execute("UPDATE `account_attributes`"
                                 " SET `value`=?"
                                 " WHERE `id`=?",
                                 (value, attrid))
        else:
            self._cursor.execute("INSERT INTO `account_attributes`"
                                 " VALUES (?,?,?,?)",
                                 (None, accountid, name, value))
        self._db.commit()

    def get_or_allocate_ownernum(self, pubkey_vs):
        if not re.search(r'^[a-zA-Z0-9+-_]+$', pubkey_vs):
            raise BadAccountName("unacceptable characters in pubkey")
        self._cursor.execute("SELECT `id` FROM `accounts` WHERE `pubkey_vs`=?",
                             (pubkey_vs,))
        row = self._cursor.fetchone()
        if row:
            return row[0]
        self._cursor.execute("INSERT INTO `accounts` VALUES (?,?,?)",
                             (None, pubkey_vs, int(time.time())))
        accountid = self._cursor.lastrowid
        self._db.commit()
        return accountid

    def get_account_creation_time(self, owner_num):
        self._cursor.execute("SELECT `creation_time` from `accounts`"
                             " WHERE `id`=?",
                             (owner_num,))
        row = self._cursor.fetchone()
        if row:
            return row[0]
        return None

    def get_all_accounts(self):
        self._cursor.execute("SELECT `id`,`pubkey_vs`"
                             " FROM `accounts` ORDER BY `id` ASC")
        return self._cursor.fetchall()

    def commit(self):
        if self._dirty:
            self._db.commit()
            self._dirty = False


def size_of_disk_file(filename):
    # use new fileutil.? method
    s = os.stat(filename)
    sharebytes = s.st_size
    try:
        # note that stat(2) says that st_blocks is 512 bytes, and that
        # st_blksize is "optimal file sys I/O ops blocksize", which is
        # independent of the block-size that st_blocks uses.
        diskbytes = s.st_blocks * 512
    except AttributeError:
        # the docs say that st_blocks is only on linux. I also see it on
        # MacOS. But it isn't available on windows.
        diskbytes = sharebytes
    return diskbytes



class AccountingCrawler(ShareCrawler):
    """I manage a SQLite table of which leases are owned by which ownerid, to
    support efficient calculation of total space used per ownerid. The
    sharefiles (and their leaseinfo fields) is the canonical source: the
    database is merely a speedup, generated/corrected periodically by this
    crawler. The crawler both handles the initial DB creation, and fixes the
    DB when changes have been made outside the storage-server's awareness
    (e.g. when the admin deletes a sharefile with /bin/rm).
    """

    slow_start = 7 # XXX #*60 # wait 7 minutes after startup
    minimum_cycle_time = 12*60*60 # not more than twice per day

    def __init__(self, server, statefile, leasedb):
        ShareCrawler.__init__(self, server, statefile)
        self._leasedb = leasedb
        self._expire_time = None

    def process_prefixdir(self, cycle, prefix, prefixdir, buckets, start_slice):
        # assume that we can list every bucketdir in this prefix quickly.
        # Otherwise we have to retain more state between timeslices.

        # we define "shareid" as (SI,shnum)
        disk_shares = set() # shareid
        for storage_index in buckets:
            bucketdir = os.path.join(prefixdir, storage_index)
            for sharefile in os.listdir(bucketdir):
                try:
                    shnum = int(sharefile)
                except ValueError:
                    continue # non-numeric means not a sharefile
                shareid = (storage_index, shnum)
                disk_shares.add(shareid)

        # now check the database for everything in this prefix
        db_shares = self._leasedb.get_shares_for_prefix(prefix)

        # add new shares to the DB
        new_shares = (disk_shares - db_shares)
        for shareid in new_shares:
            storage_index, shnum = shareid
            filename = os.path.join(prefixdir, storage_index, str(shnum))
            size = size_of_disk_file(filename)
            sid = self._leasedb.add_new_share(prefix, storage_index,shnum, size)
            self._leasedb.add_starter_lease(sid)

        # remove deleted shares
        deleted_shares = (db_shares - disk_shares)
        self._leasedb.remove_deleted_shares(deleted_shares)

        self._leasedb.commit()


    # these methods are for outside callers to use

    def set_lease_expiration(self, enable, expire_time=None):
        """Arrange to remove all leases that are currently expired, and to
        delete all shares without remaining leases. The actual removals will
        be done later, as the crawler finishes each prefix."""
        self._do_expire = enable
        self._expire_time = expire_time

    def db_is_incomplete(self):
        # don't bother looking at the sqlite database: it's certainly not
        # complete.
        return self.state["last-cycle-finished"] is None
