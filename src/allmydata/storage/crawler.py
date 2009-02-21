
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
    since large servers will have several million shares, which can take
    hours or days to read.

    Once the crawler starts a cycle, it will proceed at a rate limited by the
    allowed_cpu_percentage= and cpu_slice= parameters: yielding the reactor
    after it has worked for 'cpu_slice' seconds, and not resuming right away,
    always trying to use less than 'allowed_cpu_percentage'.

    Once the crawler finishes a cycle, it will put off starting the next one
    long enough to ensure that 'minimum_cycle_time' elapses between the start
    of two consecutive cycles.

    We assume that the normal upload/download/get_buckets traffic of a tahoe
    grid will cause the prefixdir contents to be mostly cached, or that the
    number of buckets in each prefixdir will be small enough to load quickly.
    A 1TB allmydata.com server was measured to have 2.56M buckets, spread
    into the 1024 prefixdirs, with about 2500 buckets per prefix. On this
    server, each prefixdir took 130ms-200ms to list the first time, and 17ms
    to list the second time.

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
    statefile will be updated and written to disk after every bucket is
    processed.

    The crawler instance must be started with startService() before it will
    do any work. To make it stop doing work, call stopService().
    """

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
        self.load_state()

    def get_progress(self):
        """I return information about how much progress the crawler is
        making. My return value is a dictionary. The primary key is
        'cycle-in-progress': True if the crawler is currently traversing the
        shares, False if it is idle between cycles.

        If cycle-in-progress is True, the following keys will be present::

         cycle-complete-percentage': float, from 0.0 to 100.0, indicating how
                                     far the crawler has progressed through
                                     the current cycle
         remaining-sleep-time: float, seconds from now when we do more work


        If cycle-in-progress is False, the following keys are available::

           next-crawl-time: float, seconds-since-epoch when next crawl starts

           remaining-wait-time: float, seconds from now when next crawl starts
        """

        d = {}
        if self.state["current-cycle"] is None:
            d["cycle-in-progress"] = False
            d["next-crawl-time"] = self.next_wake_time
            d["remaining-wait-time"] = self.next_wake_time - time.time()
        else:
            d["cycle-in-progress"] = True
            pct = 100.0 * self.last_complete_prefix_index / len(self.prefixes)
            d["cycle-complete-percentage"] = pct
            d["remaining-sleep-time"] = self.next_wake_time - time.time()
        return d

    def get_state(self):
        """I return the current state of the crawler. This is a copy of my
        state dictionary.

        If we are not currently sleeping (i.e. get_state() was called from
        inside the process_prefixdir, process_bucket, or finished_cycle()
        methods, or if startService has not yet been called on this crawler),
        these two keys will be None.
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
        self.current_sleep_time = 0
        self.next_wake_time = time.time()
        self.timer = reactor.callLater(0, self.start_slice)
        service.MultiService.startService(self)

    def stopService(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        return service.MultiService.stopService(self)

    def start_slice(self):
        self.timer = None
        self.sleeping_between_cycles = False
        self.current_sleep_time = None
        self.next_wake_time = None
        start_slice = time.time()
        try:
            s = self.last_complete_prefix_index
            self.start_current_prefix(start_slice)
            finished_cycle = True
        except TimeSliceExceeded:
            finished_cycle = False
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
        # if the math gets weird, or a timequake happens, don't sleep forever
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
            if state["last-cycle-finished"] is None:
                state["current-cycle"] = 0
            else:
                state["current-cycle"] = state["last-cycle-finished"] + 1
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
            self.save_state()
            if time.time() >= start_slice + self.cpu_slice:
                raise TimeSliceExceeded()
        # yay! we finished the whole cycle
        self.last_complete_prefix_index = -1
        state["last-complete-bucket"] = None
        state["last-cycle-finished"] = cycle
        state["current-cycle"] = None
        self.finished_cycle(cycle)
        self.save_state()

    def process_prefixdir(self, cycle, prefix, prefixdir, buckets, start_slice):
        """This gets a list of bucket names (i.e. storage index strings,
        base32-encoded) in sorted order.

        Override this if your crawler doesn't care about the actual shares,
        for example a crawler which merely keeps track of how many buckets
        are being managed by this server.
        """
        for bucket in buckets:
            if bucket <= self.state["last-complete-bucket"]:
                continue
            self.process_bucket(cycle, prefix, prefixdir, bucket)
            self.state["last-complete-bucket"] = bucket
            # note: saving the state after every bucket is somewhat
            # time-consuming, but lets us avoid losing more than one bucket's
            # worth of progress.
            self.save_state()
            if time.time() >= start_slice + self.cpu_slice:
                raise TimeSliceExceeded()

    def process_bucket(self, cycle, prefix, prefixdir, storage_index_b32):
        """Examine a single bucket. Subclasses should do whatever they want
        to do to the shares therein, then update self.state as necessary.

        This method will be called exactly once per share (per cycle), unless
        the crawler was interrupted (by node restart, for example), in which
        case it might be called a second time on a bucket which was processed
        during the previous node's incarnation. However, in that case, no
        changes to self.state will have been recorded.

        This method for subclasses to override. No upcall is necessary.
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

        This method for subclasses to override. No upcall is necessary.
        """
        pass

    def yielding(self, sleep_time):
        """The crawler is about to sleep for 'sleep_time' seconds. This
        method is mostly for the convenience of unit tests.

        This method for subclasses to override. No upcall is necessary.
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

