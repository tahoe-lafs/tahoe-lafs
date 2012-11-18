
import os, time

from twisted.internet import defer
from twisted.python.filepath import FilePath

from allmydata.util.deferredutil import for_items
from allmydata.util.fileutil import get_used_space
from allmydata.util import log
from allmydata.storage.crawler import ShareCrawler
from allmydata.storage.common import si_a2b
from allmydata.storage.leasedb import SHARETYPES, SHARETYPE_UNKNOWN


class AccountingCrawler(ShareCrawler):
    """
    I perform the following functions:
    - Remove leases that are past their expiration time.
    - Delete objects containing unleased shares.
    - Discover shares that have been manually added to storage.
    - Discover shares that are present when a storage server is upgraded from
      a pre-leasedb version, and give them "starter leases".
    - Recover from a situation where the leasedb is lost or detectably
      corrupted. This is handled in the same way as upgrading.
    - Detect shares that have unexpectedly disappeared from storage.
    """

    slow_start = 600 # don't start crawling for 10 minutes after startup
    minimum_cycle_time = 12*60*60 # not more than twice per day

    def __init__(self, server, statefile, leasedb):
        ShareCrawler.__init__(self, server, statefile)
        self._leasedb = leasedb

    def process_prefixdir(self, cycle, prefix, prefixdir, buckets, start_slice):
        # assume that we can list every prefixdir in this prefix quickly.
        # Otherwise we have to retain more state between timeslices.

        # we define "shareid" as (SI string, shnum)
        disk_shares = set() # shareid
        for si_s in buckets:
            bucketdir = os.path.join(prefixdir, si_s)
            for sharefile in os.listdir(bucketdir):
                try:
                    shnum = int(sharefile)
                except ValueError:
                    continue # non-numeric means not a sharefile
                shareid = (si_s, shnum)
                disk_shares.add(shareid)

        # now check the database for everything in this prefix
        db_sharemap = self._leasedb.get_shares_for_prefix(prefix)
        db_shares = set(db_sharemap)

        rec = self.state["cycle-to-date"]["space-recovered"]
        examined_sharesets = [set() for st in xrange(len(SHARETYPES))]

        # The lease crawler used to calculate the lease age histogram while
        # crawling shares, and tests currently rely on that, but it would be
        # more efficient to maintain the histogram as leases are added,
        # updated, and removed.
        for key, value in db_sharemap.iteritems():
            (si_s, shnum) = key
            (used_space, sharetype) = value

            examined_sharesets[sharetype].add(si_s)

            for age in self._leasedb.get_lease_ages(si_a2b(si_s), shnum, start_slice):
                self.add_lease_age_to_histogram(age)

            self.increment(rec, "examined-shares", 1)
            self.increment(rec, "examined-sharebytes", used_space)
            self.increment(rec, "examined-shares-" + SHARETYPES[sharetype], 1)
            self.increment(rec, "examined-sharebytes-" + SHARETYPES[sharetype], used_space)

        self.increment(rec, "examined-buckets", sum([len(s) for s in examined_sharesets]))
        for st in SHARETYPES:
            self.increment(rec, "examined-buckets-" + SHARETYPES[st], len(examined_sharesets[st]))

        # add new shares to the DB
        new_shares = disk_shares - db_shares
        for (si_s, shnum) in new_shares:
            fp = FilePath(prefixdir).child(si_s).child(str(shnum))
            used_space = get_used_space(fp)
            # FIXME
            sharetype = SHARETYPE_UNKNOWN
            self._leasedb.add_new_share(si_a2b(si_s), shnum, used_space, sharetype)
            self._leasedb.add_starter_lease(si_s, shnum)

        # remove disappeared shares from DB
        disappeared_shares = db_shares - disk_shares
        for (si_s, shnum) in disappeared_shares:
            log.msg(format="share SI=%(si_s)s shnum=%(shnum)s unexpectedly disappeared",
                    si_s=si_s, shnum=shnum, level=log.WEIRD)
            self._leasedb.remove_deleted_share(si_a2b(si_s), shnum)

        recovered_sharesets = [set() for st in xrange(len(SHARETYPES))]

        def _delete_share(ign, key, value):
            (si_s, shnum) = key
            (used_space, sharetype) = value
            storage_index = si_a2b(si_s)
            d2 = defer.succeed(None)
            def _mark_and_delete(ign):
                self._leasedb.mark_share_as_going(storage_index, shnum)
                return self.server.delete_share(storage_index, shnum)
            d2.addCallback(_mark_and_delete)
            def _deleted(ign):
                self._leasedb.remove_deleted_share(storage_index, shnum)

                recovered_sharesets[sharetype].add(si_s)

                self.increment(rec, "actual-shares", 1)
                self.increment(rec, "actual-sharebytes", used_space)
                self.increment(rec, "actual-shares-" + SHARETYPES[sharetype], 1)
                self.increment(rec, "actual-sharebytes-" + SHARETYPES[sharetype], used_space)
            def _not_deleted(f):
                log.err(format="accounting crawler could not delete share SI=%(si_s)s shnum=%(shnum)s",
                        si_s=si_s, shnum=shnum, failure=f, level=log.WEIRD)
                try:
                    self._leasedb.mark_share_as_stable(storage_index, shnum)
                except Exception, e:
                    log.err(e)
                # discard the failure
            d2.addCallbacks(_deleted, _not_deleted)
            return d2

        unleased_sharemap = self._leasedb.get_unleased_shares_for_prefix(prefix)
        d = for_items(_delete_share, unleased_sharemap)

        def _inc_recovered_sharesets(ign):
            self.increment(rec, "actual-buckets", sum([len(s) for s in recovered_sharesets]))
            for st in SHARETYPES:
                self.increment(rec, "actual-buckets-" + SHARETYPES[st], len(recovered_sharesets[st]))
        d.addCallback(_inc_recovered_sharesets)
        return d

    # these methods are for outside callers to use

    def set_expiration_policy(self, policy):
        self._expiration_policy = policy

    def get_expiration_policy(self):
        return self._expiration_policy

    def is_expiration_enabled(self):
        return self._expiration_policy.is_enabled()

    def db_is_incomplete(self):
        # don't bother looking at the sqlite database: it's certainly not
        # complete.
        return self.state["last-cycle-finished"] is None

    def increment(self, d, k, delta=1):
        if k not in d:
            d[k] = 0
        d[k] += delta

    def add_lease_age_to_histogram(self, age):
        bin_interval = 24*60*60
        bin_number = int(age/bin_interval)
        bin_start = bin_number * bin_interval
        bin_end = bin_start + bin_interval
        k = (bin_start, bin_end)
        self.increment(self.state["cycle-to-date"]["lease-age-histogram"], k, 1)

    def convert_lease_age_histogram(self, lah):
        # convert { (minage,maxage) : count } into [ (minage,maxage,count) ]
        # since the former is not JSON-safe (JSON dictionaries must have
        # string keys).
        json_safe_lah = []
        for k in sorted(lah):
            (minage,maxage) = k
            json_safe_lah.append( (minage, maxage, lah[k]) )
        return json_safe_lah

    def add_initial_state(self):
        # we fill ["cycle-to-date"] here (even though they will be reset in
        # self.started_cycle) just in case someone grabs our state before we
        # get started: unit tests do this
        so_far = self.create_empty_cycle_dict()
        self.state.setdefault("cycle-to-date", so_far)
        # in case we upgrade the code while a cycle is in progress, update
        # the keys individually
        for k in so_far:
            self.state["cycle-to-date"].setdefault(k, so_far[k])

    def create_empty_cycle_dict(self):
        recovered = self.create_empty_recovered_dict()
        so_far = {"corrupt-shares": [],
                  "space-recovered": recovered,
                  "lease-age-histogram": {}, # (minage,maxage)->count
                  }
        return so_far

    def create_empty_recovered_dict(self):
        recovered = {}
        for a in ("actual", "examined"):
            for b in ("buckets", "shares", "diskbytes"):
                recovered["%s-%s" % (a, b)] = 0
                for st in SHARETYPES:
                    recovered["%s-%s-%s" % (a, b, SHARETYPES[st])] = 0
        return recovered

    def started_cycle(self, cycle):
        self.state["cycle-to-date"] = self.create_empty_cycle_dict()

        current_time = time.time()
        self._expiration_policy.remove_expired_leases(self._leasedb, current_time)

    def finished_cycle(self, cycle):
        # add to our history state, prune old history
        h = {}

        start = self.state["current-cycle-start-time"]
        now = time.time()
        h["cycle-start-finish-times"] = (start, now)
        ep = self.get_expiration_policy()
        h["expiration-enabled"] = ep.is_enabled()
        h["configured-expiration-mode"] = ep.get_parameters()

        s = self.state["cycle-to-date"]

        # state["lease-age-histogram"] is a dictionary (mapping
        # (minage,maxage) tuple to a sharecount), but we report
        # self.get_state()["lease-age-histogram"] as a list of
        # (min,max,sharecount) tuples, because JSON can handle that better.
        # We record the list-of-tuples form into the history for the same
        # reason.
        lah = self.convert_lease_age_histogram(s["lease-age-histogram"])
        h["lease-age-histogram"] = lah
        h["corrupt-shares"] = s["corrupt-shares"][:]
        # note: if ["shares-recovered"] ever acquires an internal dict, this
        # copy() needs to become a deepcopy
        h["space-recovered"] = s["space-recovered"].copy()

        self._leasedb.add_history_entry(cycle, h)

    def get_state(self):
        """In addition to the crawler state described in
        ShareCrawler.get_state(), I return the following keys which are
        specific to the lease-checker/expirer. Note that the non-history keys
        (with 'cycle' in their names) are only present if a cycle is currently
        running. If the crawler is between cycles, it is appropriate to show
        the latest item in the 'history' key instead. Also note that each
        history item has all the data in the 'cycle-to-date' value, plus
        cycle-start-finish-times.

         cycle-to-date:
          expiration-enabled
          configured-expiration-mode
          lease-age-histogram (list of (minage,maxage,sharecount) tuples)
          corrupt-shares (list of (si_b32,shnum) tuples, minimal verification)
          space-recovered

         estimated-remaining-cycle:
          # Values may be None if not enough data has been gathered to
          # produce an estimate.
          space-recovered

         estimated-current-cycle:
          # cycle-to-date plus estimated-remaining. Values may be None if
          # not enough data has been gathered to produce an estimate.
          space-recovered

         history: maps cyclenum to a dict with the following keys:
          cycle-start-finish-times
          expiration-enabled
          configured-expiration-mode
          lease-age-histogram
          corrupt-shares
          space-recovered

         The 'space-recovered' structure is a dictionary with the following
         keys:
          # 'examined' is what was looked at
          examined-buckets,     examined-buckets-$SHARETYPE
          examined-shares,      examined-shares-$SHARETYPE
          examined-diskbytes,   examined-diskbytes-$SHARETYPE

          # 'actual' is what was deleted
          actual-buckets,       actual-buckets-$SHARETYPE
          actual-shares,        actual-shares-$SHARETYPE
          actual-diskbytes,     actual-diskbytes-$SHARETYPE

        Note that the preferred terminology has changed since these keys
        were defined; "buckets" refers to what are now called sharesets,
        and "diskbytes" refers to bytes of used space on the storage backend,
        which is not necessarily the disk backend.

        The 'original-*' and 'configured-*' keys that were populated in
        pre-leasedb versions are no longer supported.
        The 'leases-per-share-histogram' is also no longer supported.
        """
        progress = self.get_progress()

        state = ShareCrawler.get_state(self) # does a shallow copy
        state["history"] = self._leasedb.get_history()

        if not progress["cycle-in-progress"]:
            del state["cycle-to-date"]
            return state

        so_far = state["cycle-to-date"].copy()
        state["cycle-to-date"] = so_far

        lah = so_far["lease-age-histogram"]
        so_far["lease-age-histogram"] = self.convert_lease_age_histogram(lah)
        so_far["expiration-enabled"] = self._expiration_policy.is_enabled()
        so_far["configured-expiration-mode"] = self._expiration_policy.get_parameters()

        so_far_sr = so_far["space-recovered"]
        remaining_sr = {}
        remaining = {"space-recovered": remaining_sr}
        cycle_sr = {}
        cycle = {"space-recovered": cycle_sr}

        if progress["cycle-complete-percentage"] > 0.0:
            pc = progress["cycle-complete-percentage"] / 100.0
            m = (1-pc)/pc
            for a in ("actual", "examined"):
                for b in ("buckets", "shares", "diskbytes"):
                    k = "%s-%s" % (a, b)
                    remaining_sr[k] = m * so_far_sr[k]
                    cycle_sr[k] = so_far_sr[k] + remaining_sr[k]
                    for st in SHARETYPES:
                        k = "%s-%s-%s" % (a, b, SHARETYPES[st])
                        remaining_sr[k] = m * so_far_sr[k]
                        cycle_sr[k] = so_far_sr[k] + remaining_sr[k]
        else:
            for a in ("actual", "examined"):
                for b in ("buckets", "shares", "diskbytes"):
                    k = "%s-%s" % (a, b)
                    remaining_sr[k] = None
                    cycle_sr[k] = None
                    for st in SHARETYPES:
                        k = "%s-%s-%s" % (a, b, SHARETYPES[st])
                        remaining_sr[k] = None
                        cycle_sr[k] = None

        state["estimated-remaining-cycle"] = remaining
        state["estimated-current-cycle"] = cycle
        return state
