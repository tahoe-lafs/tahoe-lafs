import time, os, pickle, struct
from crawler import ShareCrawler
from shares import get_share_file
from common import UnknownMutableContainerVersionError, \
     UnknownImmutableContainerVersionError
from twisted.python import log as twlog

class LeaseCheckingCrawler(ShareCrawler):
    """I examine the leases on all shares, determining which are still valid
    and which have expired. I can remove the expired leases (if so
    configured), and the share will be deleted when the last lease is
    removed.

    I collect statistics on the leases and make these available to a web
    status page, including::

    Space recovered during this cycle-so-far:
     actual (only if expiration_enabled=True):
      num-buckets, num-shares, sum of share sizes, real disk usage
      ('real disk usage' means we use stat(fn).st_blocks*512 and include any
       space used by the directory)
     what it would have been with the original lease expiration time
     what it would have been with our configured expiration time

    Prediction of space that will be recovered during the rest of this cycle
    Prediction of space that will be recovered by the entire current cycle.

    Space recovered during the last 10 cycles  <-- saved in separate pickle

    Shares/buckets examined:
     this cycle-so-far
     prediction of rest of cycle
     during last 10 cycles <-- separate pickle
    start/finish time of last 10 cycles  <-- separate pickle
    expiration time used for last 10 cycles <-- separate pickle

    Histogram of leases-per-share:
     this-cycle-to-date
     last 10 cycles <-- separate pickle
    Histogram of lease ages, buckets = 1day
     cycle-to-date
     last 10 cycles <-- separate pickle

    All cycle-to-date values remain valid until the start of the next cycle.

    """

    slow_start = 360 # wait 6 minutes after startup
    minimum_cycle_time = 12*60*60 # not more than twice per day

    def __init__(self, server, statefile, historyfile,
                 expiration_enabled, mode,
                 override_lease_duration, # used if expiration_mode=="age"
                 cutoff_date, # used if expiration_mode=="cutoff-date"
                 sharetypes):
        self.historyfile = historyfile
        self.expiration_enabled = expiration_enabled
        self.mode = mode
        self.override_lease_duration = None
        self.cutoff_date = None
        if self.mode == "age":
            assert isinstance(override_lease_duration, (int, type(None)))
            self.override_lease_duration = override_lease_duration # seconds
        elif self.mode == "cutoff-date":
            assert isinstance(cutoff_date, int) # seconds-since-epoch
            assert cutoff_date is not None
            self.cutoff_date = cutoff_date
        else:
            raise ValueError("GC mode '%s' must be 'age' or 'cutoff-date'" % mode)
        self.sharetypes_to_expire = sharetypes
        ShareCrawler.__init__(self, server, statefile)

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

        # initialize history
        if not os.path.exists(self.historyfile):
            history = {} # cyclenum -> dict
            f = open(self.historyfile, "wb")
            pickle.dump(history, f)
            f.close()

    def create_empty_cycle_dict(self):
        recovered = self.create_empty_recovered_dict()
        so_far = {"corrupt-shares": [],
                  "space-recovered": recovered,
                  "lease-age-histogram": {}, # (minage,maxage)->count
                  "leases-per-share-histogram": {}, # leasecount->numshares
                  }
        return so_far

    def create_empty_recovered_dict(self):
        recovered = {}
        for a in ("actual", "original", "configured", "examined"):
            for b in ("buckets", "shares", "sharebytes", "diskbytes"):
                recovered[a+"-"+b] = 0
                recovered[a+"-"+b+"-mutable"] = 0
                recovered[a+"-"+b+"-immutable"] = 0
        return recovered

    def started_cycle(self, cycle):
        self.state["cycle-to-date"] = self.create_empty_cycle_dict()

    def stat(self, fn):
        return os.stat(fn)

    def process_bucket(self, cycle, prefix, prefixdir, storage_index_b32):
        bucketdir = os.path.join(prefixdir, storage_index_b32)
        try:
            bucket_diskbytes = self.stat(bucketdir).st_blocks * 512
        except AttributeError:
            bucket_diskbytes = 0 # no stat().st_blocks on windows
        would_keep_shares = []
        for fn in os.listdir(bucketdir):
            try:
                shnum = int(fn)
            except ValueError:
                continue # non-numeric means not a sharefile
            sharefile = os.path.join(bucketdir, fn)
            try:
                wks = self.process_share(sharefile)
            except (UnknownMutableContainerVersionError,
                    UnknownImmutableContainerVersionError,
                    struct.error):
                twlog.msg("lease-checker error processing %s" % sharefile)
                twlog.err()
                which = (storage_index_b32, shnum)
                self.state["cycle-to-date"]["corrupt-shares"].append(which)
                wks = (1, 1, 1, "unknown")
            would_keep_shares.append(wks)
        sharetype = None
        if wks:
            sharetype = wks[3]
        rec = self.state["cycle-to-date"]["space-recovered"]
        self.increment(rec, "examined-buckets", 1)
        if sharetype:
            self.increment(rec, "examined-buckets-"+sharetype, 1)

        if sum([wks[0] for wks in would_keep_shares]) == 0:
            self.increment(rec, "original-diskbytes", bucket_diskbytes)
            self.increment(rec, "original-diskbytes-"+sharetype, bucket_diskbytes)
            self.increment(rec, "original-buckets", 1)
            self.increment(rec, "original-buckets-"+sharetype, 1)
        if sum([wks[1] for wks in would_keep_shares]) == 0:
            self.increment(rec, "configured-diskbytes", bucket_diskbytes)
            self.increment(rec, "configured-diskbytes-"+sharetype, bucket_diskbytes)
            self.increment(rec, "configured-buckets", 1)
            self.increment(rec, "configured-buckets-"+sharetype, 1)
        if sum([wks[2] for wks in would_keep_shares]) == 0:
            self.increment(rec, "actual-diskbytes", bucket_diskbytes)
            self.increment(rec, "actual-diskbytes-"+sharetype, bucket_diskbytes)
            self.increment(rec, "actual-buckets", 1)
            self.increment(rec, "actual-buckets-"+sharetype, 1)

    def process_share(self, sharefilename):
        # first, find out what kind of a share it is
        sf = get_share_file(sharefilename)
        sharetype = sf.sharetype
        now = time.time()
        s = self.stat(sharefilename)

        num_leases = 0
        num_valid_leases_original = 0
        num_valid_leases_configured = 0
        expired_leases_configured = []

        for li in sf.get_leases():
            num_leases += 1
            original_expiration_time = li.get_expiration_time()
            grant_renew_time = li.get_grant_renew_time_time()
            age = li.get_age()
            self.add_lease_age_to_histogram(age)

            #  expired-or-not according to original expiration time
            if original_expiration_time > now:
                num_valid_leases_original += 1

            #  expired-or-not according to our configured age limit
            expired = False
            if self.mode == "age":
                age_limit = original_expiration_time
                if self.override_lease_duration is not None:
                    age_limit = self.override_lease_duration
                if age > age_limit:
                    expired = True
            else:
                assert self.mode == "cutoff-date"
                if grant_renew_time < self.cutoff_date:
                    expired = True
            if sharetype not in self.sharetypes_to_expire:
                expired = False

            if expired:
                expired_leases_configured.append(li)
            else:
                num_valid_leases_configured += 1

        so_far = self.state["cycle-to-date"]
        self.increment(so_far["leases-per-share-histogram"], num_leases, 1)
        self.increment_space("examined", s, sharetype)

        would_keep_share = [1, 1, 1, sharetype]

        if self.expiration_enabled:
            for li in expired_leases_configured:
                sf.cancel_lease(li.cancel_secret)

        if num_valid_leases_original == 0:
            would_keep_share[0] = 0
            self.increment_space("original", s, sharetype)

        if num_valid_leases_configured == 0:
            would_keep_share[1] = 0
            self.increment_space("configured", s, sharetype)
            if self.expiration_enabled:
                would_keep_share[2] = 0
                self.increment_space("actual", s, sharetype)

        return would_keep_share

    def increment_space(self, a, s, sharetype):
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
        so_far_sr = self.state["cycle-to-date"]["space-recovered"]
        self.increment(so_far_sr, a+"-shares", 1)
        self.increment(so_far_sr, a+"-shares-"+sharetype, 1)
        self.increment(so_far_sr, a+"-sharebytes", sharebytes)
        self.increment(so_far_sr, a+"-sharebytes-"+sharetype, sharebytes)
        self.increment(so_far_sr, a+"-diskbytes", diskbytes)
        self.increment(so_far_sr, a+"-diskbytes-"+sharetype, diskbytes)

    def increment(self, d, k, delta=1):
        if k not in d:
            d[k] = 0
        d[k] += delta

    def add_lease_age_to_histogram(self, age):
        bucket_interval = 24*60*60
        bucket_number = int(age/bucket_interval)
        bucket_start = bucket_number * bucket_interval
        bucket_end = bucket_start + bucket_interval
        k = (bucket_start, bucket_end)
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

    def finished_cycle(self, cycle):
        # add to our history state, prune old history
        h = {}

        start = self.state["current-cycle-start-time"]
        now = time.time()
        h["cycle-start-finish-times"] = (start, now)
        h["expiration-enabled"] = self.expiration_enabled
        h["configured-expiration-mode"] = (self.mode,
                                           self.override_lease_duration,
                                           self.cutoff_date,
                                           self.sharetypes_to_expire)

        s = self.state["cycle-to-date"]

        # state["lease-age-histogram"] is a dictionary (mapping
        # (minage,maxage) tuple to a sharecount), but we report
        # self.get_state()["lease-age-histogram"] as a list of
        # (min,max,sharecount) tuples, because JSON can handle that better.
        # We record the list-of-tuples form into the history for the same
        # reason.
        lah = self.convert_lease_age_histogram(s["lease-age-histogram"])
        h["lease-age-histogram"] = lah
        h["leases-per-share-histogram"] = s["leases-per-share-histogram"].copy()
        h["corrupt-shares"] = s["corrupt-shares"][:]
        # note: if ["shares-recovered"] ever acquires an internal dict, this
        # copy() needs to become a deepcopy
        h["space-recovered"] = s["space-recovered"].copy()

        history = pickle.load(open(self.historyfile, "rb"))
        history[cycle] = h
        while len(history) > 10:
            oldcycles = sorted(history.keys())
            del history[oldcycles[0]]
        f = open(self.historyfile, "wb")
        pickle.dump(history, f)
        f.close()

    def get_state(self):
        """In addition to the crawler state described in
        ShareCrawler.get_state(), I return the following keys which are
        specific to the lease-checker/expirer. Note that the non-history keys
        (with 'cycle' in their names) are only present if a cycle is
        currently running. If the crawler is between cycles, it appropriate
        to show the latest item in the 'history' key instead. Also note that
        each history item has all the data in the 'cycle-to-date' value, plus
        cycle-start-finish-times.

         cycle-to-date:
          expiration-enabled
          configured-expiration-mode
          lease-age-histogram (list of (minage,maxage,sharecount) tuples)
          leases-per-share-histogram
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
          leases-per-share-histogram
          corrupt-shares
          space-recovered

         The 'space-recovered' structure is a dictionary with the following
         keys:
          # 'examined' is what was looked at
          examined-buckets, examined-buckets-mutable, examined-buckets-immutable
          examined-shares, -mutable, -immutable
          examined-sharebytes, -mutable, -immutable
          examined-diskbytes, -mutable, -immutable

          # 'actual' is what was actually deleted
          actual-buckets, -mutable, -immutable
          actual-shares, -mutable, -immutable
          actual-sharebytes, -mutable, -immutable
          actual-diskbytes, -mutable, -immutable

          # would have been deleted, if the original lease timer was used
          original-buckets, -mutable, -immutable
          original-shares, -mutable, -immutable
          original-sharebytes, -mutable, -immutable
          original-diskbytes, -mutable, -immutable

          # would have been deleted, if our configured max_age was used
          configured-buckets, -mutable, -immutable
          configured-shares, -mutable, -immutable
          configured-sharebytes, -mutable, -immutable
          configured-diskbytes, -mutable, -immutable

        """
        progress = self.get_progress()

        state = ShareCrawler.get_state(self) # does a shallow copy
        history = pickle.load(open(self.historyfile, "rb"))
        state["history"] = history

        if not progress["cycle-in-progress"]:
            del state["cycle-to-date"]
            return state

        so_far = state["cycle-to-date"].copy()
        state["cycle-to-date"] = so_far

        lah = so_far["lease-age-histogram"]
        so_far["lease-age-histogram"] = self.convert_lease_age_histogram(lah)
        so_far["expiration-enabled"] = self.expiration_enabled
        so_far["configured-expiration-mode"] = (self.mode,
                                                self.override_lease_duration,
                                                self.cutoff_date,
                                                self.sharetypes_to_expire)

        so_far_sr = so_far["space-recovered"]
        remaining_sr = {}
        remaining = {"space-recovered": remaining_sr}
        cycle_sr = {}
        cycle = {"space-recovered": cycle_sr}

        if progress["cycle-complete-percentage"] > 0.0:
            pc = progress["cycle-complete-percentage"] / 100.0
            m = (1-pc)/pc
            for a in ("actual", "original", "configured", "examined"):
                for b in ("buckets", "shares", "sharebytes", "diskbytes"):
                    for c in ("", "-mutable", "-immutable"):
                        k = a+"-"+b+c
                        remaining_sr[k] = m * so_far_sr[k]
                        cycle_sr[k] = so_far_sr[k] + remaining_sr[k]
        else:
            for a in ("actual", "original", "configured", "examined"):
                for b in ("buckets", "shares", "sharebytes", "diskbytes"):
                    for c in ("", "-mutable", "-immutable"):
                        k = a+"-"+b+c
                        remaining_sr[k] = None
                        cycle_sr[k] = None

        state["estimated-remaining-cycle"] = remaining
        state["estimated-current-cycle"] = cycle
        return state
