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
     actual (only if expire_leases=True):
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
    Histogram of lease ages, buckets = expiration_time/10
     cycle-to-date
     last 10 cycles <-- separate pickle

    All cycle-to-date values remain valid until the start of the next cycle.

    """

    slow_start = 360 # wait 6 minutes after startup
    minimum_cycle_time = 12*60*60 # not more than twice per day

    def __init__(self, server, statefile, historyfile,
                 expire_leases, expiration_time):
        self.historyfile = historyfile
        self.expire_leases = expire_leases
        self.age_limit = expiration_time
        ShareCrawler.__init__(self, server, statefile)

    def add_initial_state(self):
        # we fill ["cycle-to-date"] here (even though they will be reset in
        # self.started_cycle) just in case someone grabs our state before we
        # get started: unit tests do this
        so_far = self.create_empty_cycle_dict()
        self.state.setdefault("cycle-to-date", so_far)
        # in case we upgrade the code while a cycle is in progress, update
        # the keys individually
        for k in self.state["cycle-to-date"]:
            self.state["cycle-to-date"].setdefault(k, so_far[k])

        # initialize history
        if not os.path.exists(self.historyfile):
            history = {} # cyclenum -> dict
            f = open(self.historyfile, "wb")
            pickle.dump(history, f)
            f.close()

    def create_empty_cycle_dict(self):
        recovered = self.create_empty_recovered_dict()
        so_far = {"buckets-examined": 0,
                  "shares-examined": 0,
                  "corrupt-shares": [],
                  "space-recovered": recovered,
                  "lease-age-histogram": {}, # (minage,maxage)->count
                  "leases-per-share-histogram": {}, # leasecount->numshares
                  }
        return so_far

    def create_empty_recovered_dict(self):
        recovered = {}
        for a in ("actual", "original-leasetimer", "configured-leasetimer"):
            for b in ("numbuckets", "numshares", "sharebytes", "diskbytes"):
                recovered[a+"-"+b] = 0
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
                wks = (1, 1, 1)
            would_keep_shares.append(wks)
        recovered = self.state["cycle-to-date"]["space-recovered"]
        if sum([wks[0] for wks in would_keep_shares]) == 0:
            self.increment(recovered,
                           "original-leasetimer-diskbytes", bucket_diskbytes)
            self.increment(recovered, "original-leasetimer-numbuckets", 1)
        if sum([wks[1] for wks in would_keep_shares]) == 0:
            self.increment(recovered,
                           "configured-leasetimer-diskbytes", bucket_diskbytes)
            self.increment(recovered, "configured-leasetimer-numbuckets", 1)
        if sum([wks[2] for wks in would_keep_shares]) == 0:
            self.increment(recovered,
                           "actual-diskbytes", bucket_diskbytes)
            self.increment(recovered, "actual-numbuckets", 1)
        self.state["cycle-to-date"]["buckets-examined"] += 1

    def process_share(self, sharefilename):
        # first, find out what kind of a share it is
        sf = get_share_file(sharefilename)
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
            if age < self.age_limit:
                num_valid_leases_configured += 1
            else:
                expired_leases_configured.append(li)

        so_far = self.state["cycle-to-date"]
        self.increment(so_far["leases-per-share-histogram"], num_leases, 1)
        so_far["shares-examined"] += 1
        # TODO: accumulate share-sizes too, so we can display "the whole
        # cycle would probably recover x GB out of y GB total"

        would_keep_share = [1, 1, 1]

        if self.expire_leases:
            for li in expired_leases_configured:
                sf.cancel_lease(li.cancel_secret)

        if num_valid_leases_original == 0:
            would_keep_share[0] = 0
            self.increment_space("original-leasetimer", s)

        if num_valid_leases_configured == 0:
            would_keep_share[1] = 0
            self.increment_space("configured-leasetimer", s)
            if self.expire_leases:
                would_keep_share[2] = 0
                self.increment_space("actual", s)

        return would_keep_share

    def increment_space(self, a, s):
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
        self.increment(so_far_sr, a+"-numshares", 1)
        self.increment(so_far_sr, a+"-sharebytes", sharebytes)
        self.increment(so_far_sr, a+"-diskbytes", diskbytes)

    def increment(self, d, k, delta=1):
        if k not in d:
            d[k] = 0
        d[k] += delta

    def add_lease_age_to_histogram(self, age):
        bucket_interval = self.age_limit / 10.0
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
        h["expiration-enabled"] = self.expire_leases
        h["configured-expiration-time"] = self.age_limit

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
        h["buckets-examined"] = s["buckets-examined"]
        h["shares-examined"] = s["shares-examined"]
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
          configured-expiration-time
          lease-age-histogram (list of (minage,maxage,sharecount) tuples)
          leases-per-share-histogram
          corrupt-shares (list of (si_b32,shnum) tuples, minimal verification)
          buckets-examined
          shares-examined
          space-recovered

         estimated-remaining-cycle:
          # Values may be None if not enough data has been gathered to
          # produce an estimate.
          buckets-examined
          shares-examined
          space-recovered

         estimated-current-cycle:
          # cycle-to-date plus estimated-remaining. Values may be None if
          # not enough data has been gathered to produce an estimate.
          buckets-examined
          shares-examined
          space-recovered

         history: maps cyclenum to a dict with the following keys:
          cycle-start-finish-times
          expiration-enabled
          configured-expiration-time
          lease-age-histogram
          leases-per-share-histogram
          corrupt-shares
          buckets-examined
          shares-examined
          space-recovered

         The 'space-recovered' structure is a dictionary with the following
         keys:
          # 'actual' is what was actually deleted
          actual-numbuckets
          actual-numshares
          actual-sharebytes
          actual-diskbytes
          # would have been deleted, if the original lease timer was used
          original-leasetimer-numbuckets
          original-leasetimer-numshares
          original-leasetimer-sharebytes
          original-leasetimer-diskbytes
          # would have been deleted, if our configured max_age was used
          configured-leasetimer-numbuckets
          configured-leasetimer-numshares
          configured-leasetimer-sharebytes
          configured-leasetimer-diskbytes

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
        so_far["expiration-enabled"] = self.expire_leases
        so_far["configured-expiration-time"] = self.age_limit

        so_far_sr = so_far["space-recovered"]
        remaining_sr = {}
        remaining = {"space-recovered": remaining_sr}
        cycle_sr = {}
        cycle = {"space-recovered": cycle_sr}

        if progress["cycle-complete-percentage"] > 0.0:
            m = 100.0 / progress["cycle-complete-percentage"]
            for a in ("actual", "original-leasetimer", "configured-leasetimer"):
                for b in ("numbuckets", "numshares", "sharebytes", "diskbytes"):
                    k = a+"-"+b
                    remaining_sr[k] = m * so_far_sr[k]
                    cycle_sr[k] = so_far_sr[k] + remaining_sr[k]
            predshares = m * so_far["shares-examined"]
            remaining["shares-examined"] = predshares
            cycle["shares-examined"] = so_far["shares-examined"] + predshares
            predbuckets = m * so_far["buckets-examined"]
            remaining["buckets-examined"] = predbuckets
            cycle["buckets-examined"] = so_far["buckets-examined"] + predbuckets
        else:
            for a in ("actual", "original-leasetimer", "configured-leasetimer"):
                for b in ("numbuckets", "numshares", "sharebytes", "diskbytes"):
                    k = a+"-"+b
                    remaining_sr[k] = None
                    cycle_sr[k] = None
            remaining["shares-examined"] = None
            cycle["shares-examined"] = None
            remaining["buckets-examined"] = None
            cycle["buckets-examined"] = None

        state["estimated-remaining-cycle"] = remaining
        state["estimated-current-cycle"] = cycle
        return state
