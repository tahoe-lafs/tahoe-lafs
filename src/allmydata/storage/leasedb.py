
import time, simplejson

from allmydata.util.assertutil import _assert
from allmydata.util import dbutil
from allmydata.storage.common import si_b2a


class NonExistentShareError(Exception):
    def __init__(self, si_s, shnum):
        Exception.__init__(self, si_s, shnum)
        self.si_s = si_s
        self.shnum = shnum

    def __str__(self):
        return "can't find SI=%r shnum=%r in `shares` table" % (self.si_s, self.shnum)


class LeaseInfo(object):
    def __init__(self, storage_index, shnum, owner_num, renewal_time, expiration_time):
        self.storage_index = storage_index
        self.shnum = shnum
        self.owner_num = owner_num
        self.renewal_time = renewal_time
        self.expiration_time = expiration_time


def int_or_none(s):
    if s is None:
        return s
    return int(s)


SHARETYPE_IMMUTABLE  = 0
SHARETYPE_MUTABLE    = 1
SHARETYPE_CORRUPTED  = 2
SHARETYPE_UNKNOWN    = 3

SHARETYPES = { SHARETYPE_IMMUTABLE: 'immutable',
               SHARETYPE_MUTABLE:   'mutable',
               SHARETYPE_CORRUPTED: 'corrupted',
               SHARETYPE_UNKNOWN:   'unknown' }

STATE_COMING = 0
STATE_STABLE = 1
STATE_GOING  = 2


LEASE_SCHEMA_V1 = """
CREATE TABLE `version`
(
 version INTEGER -- contains one row, set to 1
);

CREATE TABLE `shares`
(
 `storage_index` VARCHAR(26) not null,
 `shnum` INTEGER not null,
 `prefix` VARCHAR(2) not null,
 `backend_key` VARCHAR,         -- not used by current backends; NULL means '$prefix/$storage_index/$shnum'
 `used_space` INTEGER not null,
 `sharetype` INTEGER not null,  -- SHARETYPE_*
 `state` INTEGER not null,      -- STATE_*
 PRIMARY KEY (`storage_index`, `shnum`)
);

CREATE INDEX `prefix` ON `shares` (`prefix`);
-- CREATE UNIQUE INDEX `share_id` ON `shares` (`storage_index`,`shnum`);

CREATE TABLE `leases`
(
 `id` INTEGER PRIMARY KEY AUTOINCREMENT,
 `storage_index` VARCHAR(26) not null,
 `shnum` INTEGER not null,
 `account_id` INTEGER not null,
 `renewal_time` INTEGER not null, -- duration is implicit: expiration-renewal
 `expiration_time` INTEGER,       -- seconds since epoch; NULL means the end of time
 FOREIGN KEY (`storage_index`, `shnum`) REFERENCES `shares` (`storage_index`, `shnum`),
 FOREIGN KEY (`account_id`) REFERENCES `accounts` (`id`)
);

CREATE INDEX `account_id` ON `leases` (`account_id`);
CREATE INDEX `expiration_time` ON `leases` (`expiration_time`);

CREATE TABLE accounts
(
 `id` INTEGER PRIMARY KEY AUTOINCREMENT,
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

CREATE TABLE crawler_history
(
 `cycle` INTEGER,
 `json` TEXT
);
CREATE UNIQUE INDEX `cycle` ON `crawler_history` (`cycle`);
"""

DAY = 24*60*60
MONTH = 30*DAY

class LeaseDB:
    ANONYMOUS_ACCOUNTID = 0
    STARTER_LEASE_ACCOUNTID = 1
    STARTER_LEASE_DURATION = 2*MONTH

    def __init__(self, dbfile):
        (self._sqlite,
         self._db) = dbutil.get_db(dbfile, create_version=(LEASE_SCHEMA_V1, 1))
        self._cursor = self._db.cursor()
        self.debug = False
        self.retained_history_entries = 10

    # share management

    def get_shares_for_prefix(self, prefix):
        """
        Returns a dict mapping (si_s, shnum) pairs to (used_space, sharetype) pairs.
        """
        self._cursor.execute("SELECT `storage_index`,`shnum`, `used_space`, `sharetype`"
                             " FROM `shares`"
                             " WHERE `prefix` == ?",
                             (prefix,))
        db_sharemap = dict([((str(si_s), int(shnum)), (int(used_space), int(sharetype)))
                           for (si_s, shnum, used_space, sharetype) in self._cursor.fetchall()])
        return db_sharemap

    def add_new_share(self, storage_index, shnum, used_space, sharetype):
        si_s = si_b2a(storage_index)
        prefix = si_s[:2]
        if self.debug: print "ADD_NEW_SHARE", prefix, si_s, shnum, used_space, sharetype
        backend_key = None
        # This needs to be an INSERT OR REPLACE because it is possible for add_new_share
        # to be called when this share is already in the database (but not on disk).
        self._cursor.execute("INSERT OR REPLACE INTO `shares`"
                             " VALUES (?,?,?,?,?,?,?)",
                             (si_s, shnum, prefix, backend_key, used_space, sharetype, STATE_COMING))

    def add_starter_lease(self, storage_index, shnum):
        si_s = si_b2a(storage_index)
        if self.debug: print "ADD_STARTER_LEASE", si_s, shnum
        self._dirty = True
        renewal_time = time.time()
        self._cursor.execute("INSERT INTO `leases`"
                             " VALUES (?,?,?,?,?,?)",
                             (None, si_s, shnum, self.STARTER_LEASE_ACCOUNTID,
                              int(renewal_time), int(renewal_time + self.STARTER_LEASE_DURATION)))
        self._db.commit()

    def mark_share_as_stable(self, storage_index, shnum, used_space=None, backend_key=None):
        """
        Call this method after adding a share to backend storage.
        """
        si_s = si_b2a(storage_index)
        if self.debug: print "MARK_SHARE_AS_STABLE", si_s, shnum, used_space
        self._dirty = True
        if used_space is not None:
            self._cursor.execute("UPDATE `shares` SET `state`=?, `used_space`=?, `backend_key`=?"
                                 " WHERE `storage_index`=? AND `shnum`=? AND `state`!=?",
                                 (STATE_STABLE, used_space, backend_key, si_s, shnum, STATE_GOING))
        else:
            _assert(backend_key is None, backend_key=backend_key)
            self._cursor.execute("UPDATE `shares` SET `state`=?"
                                 " WHERE `storage_index`=? AND `shnum`=? AND `state`!=?",
                                 (STATE_STABLE, si_s, shnum, STATE_GOING))
        self._db.commit()
        if self._cursor.rowcount < 1:
            raise NonExistentShareError(si_s, shnum)

    def mark_share_as_going(self, storage_index, shnum):
        """
        Call this method and commit before deleting a share from backend storage,
        then call remove_deleted_share.
        """
        si_s = si_b2a(storage_index)
        if self.debug: print "MARK_SHARE_AS_GOING", si_s, shnum
        self._cursor.execute("UPDATE `shares` SET `state`=?"
                             " WHERE `storage_index`=? AND `shnum`=? AND `state`!=?",
                             (STATE_GOING, si_s, shnum, STATE_COMING))
        self._db.commit()
        if self._cursor.rowcount < 1:
            raise NonExistentShareError(si_s, shnum)

    def remove_deleted_share(self, storage_index, shnum):
        si_s = si_b2a(storage_index)
        if self.debug: print "REMOVE_DELETED_SHARE", si_s, shnum
        # delete leases first to maintain integrity constraint
        self._cursor.execute("DELETE FROM `leases`"
                             " WHERE `storage_index`=? AND `shnum`=?",
                             (si_s, shnum))
        try:
            self._cursor.execute("DELETE FROM `shares`"
                                 " WHERE `storage_index`=? AND `shnum`=?",
                                 (si_s, shnum))
        except Exception:
            self._db.rollback()  # roll back the lease deletion
            raise
        else:
            self._db.commit()

    def change_share_space(self, storage_index, shnum, used_space):
        si_s = si_b2a(storage_index)
        if self.debug: print "CHANGE_SHARE_SPACE", si_s, shnum, used_space
        self._cursor.execute("UPDATE `shares` SET `used_space`=?"
                             " WHERE `storage_index`=? AND `shnum`=?",
                             (used_space, si_s, shnum))
        self._db.commit()
        if self._cursor.rowcount < 1:
            raise NonExistentShareError(si_s, shnum)

    # lease management

    def add_or_renew_leases(self, storage_index, shnum, ownerid,
                            renewal_time, expiration_time):
        """
        shnum=None means renew leases on all shares; do nothing if there are no shares for this storage_index in the `shares` table.

        Raises NonExistentShareError if a specific shnum is given and that share does not exist in the `shares` table.
        """
        si_s = si_b2a(storage_index)
        if self.debug: print "ADD_OR_RENEW_LEASES", si_s, shnum, ownerid, renewal_time, expiration_time
        if shnum is None:
            self._cursor.execute("SELECT `storage_index`, `shnum` FROM `shares`"
                                 " WHERE `storage_index`=?",
                                 (si_s,))
            rows = self._cursor.fetchall()
        else:
            self._cursor.execute("SELECT `storage_index`, `shnum` FROM `shares`"
                                 " WHERE `storage_index`=? AND `shnum`=?",
                                 (si_s, shnum))
            rows = self._cursor.fetchall()
            if not rows:
                raise NonExistentShareError(si_s, shnum)

        for (found_si_s, found_shnum) in rows:
            _assert(si_s == found_si_s, si_s=si_s, found_si_s=found_si_s)
            # XXX can we simplify this by using INSERT OR REPLACE?
            self._cursor.execute("SELECT `id` FROM `leases`"
                                 " WHERE `storage_index`=? AND `shnum`=? AND `account_id`=?",
                                 (si_s, found_shnum, ownerid))
            row = self._cursor.fetchone()
            if row:
                # Note that unlike the pre-LeaseDB code, this allows leases to be backdated.
                # There is currently no way for a client to specify lease duration, and so
                # backdating can only happen in normal operation if there is a timequake on
                # the server and time goes backward by more than 31 days. This needs to be
                # revisited for ticket #1816, which would allow the client to request a lease
                # duration.
                leaseid = row[0]
                self._cursor.execute("UPDATE `leases` SET `renewal_time`=?, `expiration_time`=?"
                                     " WHERE `id`=?",
                                     (renewal_time, expiration_time, leaseid))
            else:
                self._cursor.execute("INSERT INTO `leases` VALUES (?,?,?,?,?,?)",
                                     (None, si_s, found_shnum, ownerid, renewal_time, expiration_time))
            self._db.commit()

    def get_leases(self, storage_index, ownerid):
        si_s = si_b2a(storage_index)
        self._cursor.execute("SELECT `shnum`, `account_id`, `renewal_time`, `expiration_time` FROM `leases`"
                             " WHERE `storage_index`=? AND `account_id`=?",
                             (si_s, ownerid))
        rows = self._cursor.fetchall()
        def _to_LeaseInfo(row):
            (shnum, account_id, renewal_time, expiration_time) = tuple(row)
            return LeaseInfo(storage_index, int(shnum), int(account_id), float(renewal_time), float(expiration_time))
        return map(_to_LeaseInfo, rows)

    def get_lease_ages(self, storage_index, shnum, now):
        si_s = si_b2a(storage_index)
        self._cursor.execute("SELECT `renewal_time` FROM `leases`"
                             " WHERE `storage_index`=? AND `shnum`=?",
                             (si_s, shnum))
        rows = self._cursor.fetchall()
        def _to_age(row):
            return now - float(row[0])
        return map(_to_age, rows)

    def get_unleased_shares_for_prefix(self, prefix):
        if self.debug: print "GET_UNLEASED_SHARES_FOR_PREFIX", prefix
        # This would be simpler, but it doesn't work because 'NOT IN' doesn't support multiple columns.
        #query = ("SELECT `storage_index`, `shnum`, `used_space`, `sharetype` FROM `shares`"
        #         " WHERE (`storage_index`, `shnum`) NOT IN (SELECT DISTINCT `storage_index`, `shnum` FROM `leases`)")

        # This "negative join" should be equivalent.
        self._cursor.execute("SELECT DISTINCT s.storage_index, s.shnum, s.used_space, s.sharetype FROM `shares` s LEFT JOIN `leases` l"
                             " ON (s.storage_index = l.storage_index AND s.shnum = l.shnum)"
                             " WHERE s.prefix = ? AND l.storage_index IS NULL",
                             (prefix,))
        db_sharemap = dict([((str(si_s), int(shnum)), (int(used_space), int(sharetype)))
                           for (si_s, shnum, used_space, sharetype) in self._cursor.fetchall()])
        return db_sharemap

    def remove_leases_by_renewal_time(self, renewal_cutoff_time):
        if self.debug: print "REMOVE_LEASES_BY_RENEWAL_TIME", renewal_cutoff_time
        self._cursor.execute("DELETE FROM `leases` WHERE `renewal_time` < ?",
                             (renewal_cutoff_time,))
        self._db.commit()

    def remove_leases_by_expiration_time(self, expiration_cutoff_time):
        if self.debug: print "REMOVE_LEASES_BY_EXPIRATION_TIME", expiration_cutoff_time
        self._cursor.execute("DELETE FROM `leases` WHERE `expiration_time` IS NOT NULL AND `expiration_time` < ?",
                             (expiration_cutoff_time,))
        self._db.commit()

    # history

    def add_history_entry(self, cycle, entry):
        if self.debug: print "ADD_HISTORY_ENTRY", cycle, entry
        json = simplejson.dumps(entry)
        self._cursor.execute("SELECT `cycle` FROM `crawler_history`")
        rows = self._cursor.fetchall()
        if len(rows) >= self.retained_history_entries:
            first_cycle_to_retain = list(sorted(rows))[-(self.retained_history_entries - 1)][0]
            self._cursor.execute("DELETE FROM `crawler_history` WHERE `cycle` < ?",
                                 (first_cycle_to_retain,))
            self._db.commit()

        try:
            self._cursor.execute("INSERT OR REPLACE INTO `crawler_history` VALUES (?,?)",
                                 (cycle, json))
        except Exception:
            self._db.rollback()  # roll back the deletion of unretained entries
            raise
        else:
            self._db.commit()

    def get_history(self):
        self._cursor.execute("SELECT `cycle`,`json` FROM `crawler_history`")
        rows = self._cursor.fetchall()
        decoded = [(row[0], simplejson.loads(row[1])) for row in rows]
        return dict(decoded)

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
