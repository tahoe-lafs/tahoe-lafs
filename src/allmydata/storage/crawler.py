
import os, time, struct
import cPickle as pickle
from twisted.internet import reactor
from twisted.application import service
from allmydata.storage.common import si_b2a
from allmydata.util import fileutil

class TimeSliceExceeded(Exception):
    pass

class ShareCrawler(service.MultiService):
    """A ShareCrawler subclass is attached to a StorageServer, and
    periodically walks all of its shares, processing each one in some
    fashion. This crawl is rate-limited, to reduce the IO burden on the host,
    since large servers can easily have a terabyte of shares, in several
    million files, which can take hours or days to read.

    Once the crawler starts a cycle, it will proceed at a rate limited by the
    allowed_cpu_percentage= and cpu_slice= parameters: yielding the reactor
    after it has worked for 'cpu_slice' seconds, and not resuming right away,
    always trying to use less than 'allowed_cpu_percentage'.

    Once the crawler finishes a cycle, it will put off starting the next one
    long enough to ensure that 'minimum_cycle_time' elapses between the start
    of two consecutive cycles.

    We assume that the normal upload/download/get_buckets traffic of a tahoe
    grid will cause the prefixdir contents to be mostly cached in the kernel,
    or that the number of buckets in each prefixdir will be small enough to
    load quickly. A 1TB allmydata.com server was measured to have 2.56M
    buckets, spread into the 1024 prefixdirs, with about 2500 buckets per
    prefix. On this server, each prefixdir took 130ms-200ms to list the first
    time, and 17ms to list the second time.

    To use a crawler, create a subclass which implements the process_bucket()
    method. It will be called with a prefixdir and a base32 storage index
    string. process_bucket() must run synchronously. Any keys added to
    self.state will be preserved. Override add_initial_state() to set up
    initial state keys. Override finished_cycle() to perform additional
    processing when the cycle is complete. Any status that the crawler
    produces should be put in the self.state dictionary. Status renderers
    (like a web page which describes the accomplishments of your crawler)
    will use crawler.get_state() to retrieve this dictionary; they can
    present the contents as they see fit.

    Then create an instance, with a reference to a StorageServer and a
    filename where it can store persistent state. The statefile is used to
    keep track of how far around the ring the process has travelled, as well
    as timing history to allow the pace to be predicted and controlled. The
    statefile will be updated and written to disk after each time slice (just
    before the crawler yields to the reactor), and also after each cycle is
    finished, and also when stopService() is called. Note that this means
    that a crawler which is interrupted with SIGKILL while it is in the
    middle of a time slice will lose progress: the next time the node is
    started, the crawler will repeat some unknown amount of work.

    The crawler instance must be started with startService() before it will
    do any work. To make it stop doing work, call stopService().
    """

    slow_start = 300 # don't start crawling for 5 minutes after startup
    # all three of these can be changed at any time
    allowed_cpu_percentage = .10 # use up to 10% of the CPU, on average
    cpu_slice = 1.0 # use up to 1.0 seconds before yielding
    minimum_cycle_time = 300 # don't run a cycle faster than this

    def __init__(self, server, statefile, allowed_cpu_percentage=None):
        service.MultiService.__init__(self)
        if allowed_cpu_percentage is not None:
            self.allowed_cpu_percentage = allowed_cpu_percentage
        self.server = server
        self.sharedir = server.sharedir
        self.statefile = statefile
        self.prefixes = [si_b2a(struct.pack(">H", i << (16-10)))[:2]
                         for i in range(2**10)]
        self.prefixes.sort()
        self.timer = None
        self.bucket_cache = (None, [])
        self.current_sleep_time = None
        self.next_wake_time = None
        self.last_prefix_finished_time = None
        self.last_prefix_elapsed_time = None
        self.last_cycle_started_time = None
        self.last_cycle_elapsed_time = None
        self.load_state()

    def minus_or_none(self, a, b):
        if a is None:
            return None
        return a-b

    def get_progress(self):
        """I return information about how much progress the crawler is
        making. My return value is a dictionary. The primary key is
        'cycle-in-progress': True if the crawler is currently traversing the
        shares, False if it is idle between cycles.

        Note that any of these 'time' keys could be None if I am called at
        certain moments, so application code must be prepared to tolerate
        this case. The estimates will also be None if insufficient data has
        been gatherered to form an estimate.

        If cycle-in-progress is True, the following keys will be present::

         cycle-complete-percentage': float, from 0.0 to 100.0, indicating how
                                     far the crawler has progressed through
                                     the current cycle
         remaining-sleep-time: float, seconds from now when we do more work
         estimated-cycle-complete-time-left:
                float, seconds remaining until the current cycle is finished.
                TODO: this does not yet include the remaining time left in
                the current prefixdir, and it will be very inaccurate on fast
                crawlers (which can process a whole prefix in a single tick)
         estimated-time-per-cycle: float, seconds required to do a complete
                                   cycle

        If cycle-in-progress is False, the following keys are available::

         next-crawl-time: float, seconds-since-epoch when next crawl starts
         remaining-wait-time: float, seconds from now when next crawl starts
         estimated-time-per-cycle: float, seconds required to do a complete
                                   cycle
        """

        d = {}

        if self.state["current-cycle"] is None:
            d["cycle-in-progress"] = False
            d["next-crawl-time"] = self.next_wake_time
            d["remaining-wait-time"] = self.minus_or_none(self.next_wake_time,
                                                          time.time())
        else:
            d["cycle-in-progress"] = True
            pct = 100.0 * self.last_complete_prefix_index / len(self.prefixes)
            d["cycle-complete-percentage"] = pct
            remaining = None
            if self.last_prefix_elapsed_time is not None:
                left = len(self.prefixes) - self.last_complete_prefix_index
                remaining = left * self.last_prefix_elapsed_time
                # TODO: remainder of this prefix: we need to estimate the
                # per-bucket time, probably by measuring the time spent on
                # this prefix so far, divided by the number of buckets we've
                # processed.
            d["estimated-cycle-complete-time-left"] = remaining
            # it's possible to call get_progress() from inside a crawler's
            # finished_prefix() function
            d["remaining-sleep-time"] = self.minus_or_none(self.next_wake_time,
                                                           time.time())
        per_cycle = None
        if self.last_cycle_elapsed_time is not None:
            per_cycle = self.last_cycle_elapsed_time
        elif self.last_prefix_elapsed_time is not None:
            per_cycle = len(self.prefixes) * self.last_prefix_elapsed_time
        d["estimated-time-per-cycle"] = per_cycle
        return d

    def get_state(self):
        """I return the current state of the crawler. This is a copy of my
        state dictionary.

        If we are not currently sleeping (i.e. get_state() was called from
        inside the process_prefixdir, process_bucket, or finished_cycle()
        methods, or if startService has not yet been called on this crawler),
        these two keys will be None.

        Subclasses can override this to add computed keys to the return value,
        but don't forget to start with the upcall.
        """
        state = self.state.copy() # it isn't a deepcopy, so don't go crazy
        return state

    def load_state(self):
        # we use this to store state for both the crawler's internals and
        # anything the subclass-specific code needs. The state is stored
        # after each bucket is processed, after each prefixdir is processed,
        # and after a cycle is complete. The internal keys we use are:
        #  ["version"]: int, always 1
        #  ["last-cycle-finished"]: int, or None if we have not yet finished
        #                           any cycle
        #  ["current-cycle"]: int, or None if we are sleeping between cycles
        #  ["current-cycle-start-time"]: int, seconds-since-epoch of when this
        #                                cycle was started, possibly by an earlier
        #                                process
        #  ["last-complete-prefix"]: str, two-letter name of the last prefixdir
        #                            that was fully processed, or None if we
        #                            are sleeping between cycles, or if we
        #                            have not yet finished any prefixdir since
        #                            a cycle was started
        #  ["last-complete-bucket"]: str, base32 storage index bucket name
        #                            of the last bucket to be processed, or
        #                            None if we are sleeping between cycles
        try:
            f = open(self.statefile, "rb")
            state = pickle.load(f)
            f.close()
        except EnvironmentError:
            state = {"version": 1,
                     "last-cycle-finished": None,
                     "current-cycle": None,
                     "last-complete-prefix": None,
                     "last-complete-bucket": None,
                     }
        state.setdefault("current-cycle-start-time", time.time()) # approximate
        self.state = state
        lcp = state["last-complete-prefix"]
        if lcp == None:
            self.last_complete_prefix_index = -1
        else:
            self.last_complete_prefix_index = self.prefixes.index(lcp)
        self.add_initial_state()

    def add_initial_state(self):
        """Hook method to add extra keys to self.state when first loaded.

        The first time this Crawler is used, or when the code has been
        upgraded, the saved state file may not contain all the keys you
        expect. Use this method to add any missing keys. Simply modify
        self.state as needed.

        This method for subclasses to override. No upcall is necessary.
        """
        pass

    def save_state(self):
        lcpi = self.last_complete_prefix_index
        if lcpi == -1:
            last_complete_prefix = None
        else:
            last_complete_prefix = self.prefixes[lcpi]
        self.state["last-complete-prefix"] = last_complete_prefix
        tmpfile = self.statefile + ".tmp"
        f = open(tmpfile, "wb")
        pickle.dump(self.state, f)
        f.close()
        fileutil.move_into_place(tmpfile, self.statefile)

    def startService(self):
        # arrange things to look like we were just sleeping, so
        # status/progress values work correctly
        self.sleeping_between_cycles = True
        self.current_sleep_time = self.slow_start
        self.next_wake_time = time.time() + self.slow_start
        self.timer = reactor.callLater(self.slow_start, self.start_slice)
        service.MultiService.startService(self)

    def stopService(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.save_state()
        return service.MultiService.stopService(self)

    def start_slice(self):
        start_slice = time.time()
        self.timer = None
        self.sleeping_between_cycles = False
        self.current_sleep_time = None
        self.next_wake_time = None
        try:
            self.start_current_prefix(start_slice)
            finished_cycle = True
        except TimeSliceExceeded:
            finished_cycle = False
        self.save_state()
        if not self.running:
            # someone might have used stopService() to shut us down
            return
        # either we finished a whole cycle, or we ran out of time
        now = time.time()
        this_slice = now - start_slice
        # this_slice/(this_slice+sleep_time) = percentage
        # this_slice/percentage = this_slice+sleep_time
        # sleep_time = (this_slice/percentage) - this_slice
        sleep_time = (this_slice / self.allowed_cpu_percentage) - this_slice
        # if the math gets weird, or a timequake happens, don't sleep
        # forever. Note that this means that, while a cycle is running, we
        # will process at least one bucket every 5 minutes, no matter how
        # long that bucket takes.
        sleep_time = max(0.0, min(sleep_time, 299))
        if finished_cycle:
            # how long should we sleep between cycles? Don't run faster than
            # allowed_cpu_percentage says, but also run faster than
            # minimum_cycle_time
            self.sleeping_between_cycles = True
            sleep_time = max(sleep_time, self.minimum_cycle_time)
        else:
            self.sleeping_between_cycles = False
        self.current_sleep_time = sleep_time # for status page
        self.next_wake_time = now + sleep_time
        self.yielding(sleep_time)
        self.timer = reactor.callLater(sleep_time, self.start_slice)

    def start_current_prefix(self, start_slice):
        state = self.state
        if state["current-cycle"] is None:
            self.last_cycle_started_time = time.time()
            state["current-cycle-start-time"] = self.last_cycle_started_time
            if state["last-cycle-finished"] is None:
                state["current-cycle"] = 0
            else:
                state["current-cycle"] = state["last-cycle-finished"] + 1
            self.started_cycle(state["current-cycle"])
        cycle = state["current-cycle"]

        for i in range(self.last_complete_prefix_index+1, len(self.prefixes)):
            # if we want to yield earlier, just raise TimeSliceExceeded()
            prefix = self.prefixes[i]
            prefixdir = os.path.join(self.sharedir, prefix)
            if i == self.bucket_cache[0]:
                buckets = self.bucket_cache[1]
            else:
                try:
                    buckets = os.listdir(prefixdir)
                    buckets.sort()
                except EnvironmentError:
                    buckets = []
                self.bucket_cache = (i, buckets)
            self.process_prefixdir(cycle, prefix, prefixdir,
                                   buckets, start_slice)
            self.last_complete_prefix_index = i

            now = time.time()
            if self.last_prefix_finished_time is not None:
                elapsed = now - self.last_prefix_finished_time
                self.last_prefix_elapsed_time = elapsed
            self.last_prefix_finished_time = now

            self.finished_prefix(cycle, prefix)
            if time.time() >= start_slice + self.cpu_slice:
                raise TimeSliceExceeded()

        # yay! we finished the whole cycle
        self.last_complete_prefix_index = -1
        self.last_prefix_finished_time = None # don't include the sleep
        now = time.time()
        if self.last_cycle_started_time is not None:
            self.last_cycle_elapsed_time = now - self.last_cycle_started_time
        state["last-complete-bucket"] = None
        state["last-cycle-finished"] = cycle
        state["current-cycle"] = None
        self.finished_cycle(cycle)
        self.save_state()

    def process_prefixdir(self, cycle, prefix, prefixdir, buckets, start_slice):
        """This gets a list of bucket names (i.e. storage index strings,
        base32-encoded) in sorted order.

        You can override this if your crawler doesn't care about the actual
        shares, for example a crawler which merely keeps track of how many
        buckets are being managed by this server.

        Subclasses which *do* care about actual bucket should leave this
        method along, and implement process_bucket() instead.
        """

        for bucket in buckets:
            if bucket <= self.state["last-complete-bucket"]:
                continue
            self.process_bucket(cycle, prefix, prefixdir, bucket)
            self.state["last-complete-bucket"] = bucket
            if time.time() >= start_slice + self.cpu_slice:
                raise TimeSliceExceeded()

    # the remaining methods are explictly for subclasses to implement.

    def started_cycle(self, cycle):
        """Notify a subclass that the crawler is about to start a cycle.

        This method is for subclasses to override. No upcall is necessary.
        """
        pass

    def process_bucket(self, cycle, prefix, prefixdir, storage_index_b32):
        """Examine a single bucket. Subclasses should do whatever they want
        to do to the shares therein, then update self.state as necessary.

        If the crawler is never interrupted by SIGKILL, this method will be
        called exactly once per share (per cycle). If it *is* interrupted,
        then the next time the node is started, some amount of work will be
        duplicated, according to when self.save_state() was last called. By
        default, save_state() is called at the end of each timeslice, and
        after finished_cycle() returns, and when stopService() is called.

        To reduce the chance of duplicate work (i.e. to avoid adding multiple
        records to a database), you can call save_state() at the end of your
        process_bucket() method. This will reduce the maximum duplicated work
        to one bucket per SIGKILL. It will also add overhead, probably 1-20ms
        per bucket (and some disk writes), which will count against your
        allowed_cpu_percentage, and which may be considerable if
        process_bucket() runs quickly.

        This method is for subclasses to override. No upcall is necessary.
        """
        pass

    def finished_prefix(self, cycle, prefix):
        """Notify a subclass that the crawler has just finished processing a
        prefix directory (all buckets with the same two-character/10bit
        prefix). To impose a limit on how much work might be duplicated by a
        SIGKILL that occurs during a timeslice, you can call
        self.save_state() here, but be aware that it may represent a
        significant performance hit.

        This method is for subclasses to override. No upcall is necessary.
        """
        pass

    def finished_cycle(self, cycle):
        """Notify subclass that a cycle (one complete traversal of all
        prefixdirs) has just finished. 'cycle' is the number of the cycle
        that just finished. This method should perform summary work and
        update self.state to publish information to status displays.

        One-shot crawlers, such as those used to upgrade shares to a new
        format or populate a database for the first time, can call
        self.stopService() (or more likely self.disownServiceParent()) to
        prevent it from running a second time. Don't forget to set some
        persistent state so that the upgrader won't be run again the next
        time the node is started.

        This method is for subclasses to override. No upcall is necessary.
        """
        pass

    def yielding(self, sleep_time):
        """The crawler is about to sleep for 'sleep_time' seconds. This
        method is mostly for the convenience of unit tests.

        This method is for subclasses to override. No upcall is necessary.
        """
        pass


class BucketCountingCrawler(ShareCrawler):
    """I keep track of how many buckets are being managed by this server.
    This is equivalent to the number of distributed files and directories for
    which I am providing storage. The actual number of files+directories in
    the full grid is probably higher (especially when there are more servers
    than 'N', the number of generated shares), because some files+directories
    will have shares on other servers instead of me. Also note that the
    number of buckets will differ from the number of shares in small grids,
    when more than one share is placed on a single server.
    """

    minimum_cycle_time = 60*60 # we don't need this more than once an hour

    def __init__(self, server, statefile, num_sample_prefixes=1):
        ShareCrawler.__init__(self, server, statefile)
        self.num_sample_prefixes = num_sample_prefixes

    def add_initial_state(self):
        # ["bucket-counts"][cyclenum][prefix] = number
        # ["last-complete-cycle"] = cyclenum # maintained by base class
        # ["last-complete-bucket-count"] = number
        # ["storage-index-samples"][prefix] = (cyclenum,
        #                                      list of SI strings (base32))
        self.state.setdefault("bucket-counts", {})
        self.state.setdefault("last-complete-bucket-count", None)
        self.state.setdefault("storage-index-samples", {})

    def process_prefixdir(self, cycle, prefix, prefixdir, buckets, start_slice):
        # we override process_prefixdir() because we don't want to look at
        # the individual buckets. We'll save state after each one. On my
        # laptop, a mostly-empty storage server can process about 70
        # prefixdirs in a 1.0s slice.
        if cycle not in self.state["bucket-counts"]:
            self.state["bucket-counts"][cycle] = {}
        self.state["bucket-counts"][cycle][prefix] = len(buckets)
        if prefix in self.prefixes[:self.num_sample_prefixes]:
            self.state["storage-index-samples"][prefix] = (cycle, buckets)

    def finished_cycle(self, cycle):
        last_counts = self.state["bucket-counts"].get(cycle, [])
        if len(last_counts) == len(self.prefixes):
            # great, we have a whole cycle.
            num_buckets = sum(last_counts.values())
            self.state["last-complete-bucket-count"] = num_buckets
            # get rid of old counts
            for old_cycle in list(self.state["bucket-counts"].keys()):
                if old_cycle != cycle:
                    del self.state["bucket-counts"][old_cycle]
        # get rid of old samples too
        for prefix in list(self.state["storage-index-samples"].keys()):
            old_cycle,buckets = self.state["storage-index-samples"][prefix]
            if old_cycle != cycle:
                del self.state["storage-index-samples"][prefix]

