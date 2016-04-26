
import time, struct
import cPickle as pickle

from twisted.internet import defer, reactor
from twisted.application import service

from allmydata.interfaces import IStorageBackend

from allmydata.storage.common import si_b2a
from allmydata.util import fileutil
from allmydata.util.assertutil import precondition
from allmydata.util.deferredutil import HookMixin, async_iterate


class TimeSliceExceeded(Exception):
    pass


class ShareCrawler(HookMixin, service.MultiService):
    """
    An instance of a subclass of ShareCrawler is attached to a storage
    backend, and periodically walks the backend's shares, processing them
    in some fashion. This crawl is rate-limited to reduce the I/O burden on
    the host, since large servers can easily have a terabyte of shares in
    several million files, which can take hours or days to read.

    Once the crawler starts a cycle, it will proceed at a rate limited by the
    allowed_cpu_proportion= and cpu_slice= parameters: yielding the reactor
    after it has worked for 'cpu_slice' seconds, and not resuming right away,
    always trying to use less than 'allowed_cpu_proportion'.

    Once the crawler finishes a cycle, it will put off starting the next one
    long enough to ensure that 'minimum_cycle_time' elapses between the start
    of two consecutive cycles.

    We assume that the normal upload/download/DYHB traffic of a Tahoe-LAFS
    grid will cause the prefixdir contents to be mostly cached in the kernel,
    or that the number of sharesets in each prefixdir will be small enough to
    load quickly. A 1TB allmydata.com server was measured to have 2.56 million
    sharesets, spread into the 1024 prefixes, with about 2500 sharesets per
    prefix. On this server, each prefix took 130ms-200ms to list the first
    time, and 17ms to list the second time.

    To implement a crawler, create a subclass that implements the
    process_prefix() method. This method may be asynchronous. It will be
    called with a string prefix. Any keys that it adds to self.state will be
    preserved. Override add_initial_state() to set up initial state keys.
    Override finished_cycle() to perform additional processing when the cycle
    is complete. Any status that the crawler produces should be put in the
    self.state dictionary. Status renderers (like a web page describing the
    accomplishments of your crawler) will use crawler.get_state() to retrieve
    this dictionary; they can present the contents as they see fit.

    Then create an instance, with a reference to a backend object providing
    the IStorageBackend interface, and a filename where it can store
    persistent state. The statefile is used to keep track of how far around
    the ring the process has travelled, as well as timing history to allow
    the pace to be predicted and controlled. The statefile will be updated
    and written to disk after each time slice (just before the crawler yields
    to the reactor), and also after each cycle is finished, and also when
    stopService() is called. Note that this means that a crawler that is
    interrupted with SIGKILL while it is in the middle of a time slice will
    lose progress: the next time the node is started, the crawler will repeat
    some unknown amount of work.

    The crawler instance must be started with startService() before it will
    do any work. To make it stop doing work, call stopService(). A crawler
    is usually a child service of a StorageServer, although it should not
    depend on that.

    For historical reasons, some dictionary key names use the term "bucket"
    for what is now preferably called a "shareset" (the set of shares that a
    server holds under a given storage index).

    Subclasses should measure time using self.clock.seconds(), rather than
    time.time(), in order to make themselves deterministically testable.
    """

    slow_start = 300 # don't start crawling for 5 minutes after startup
    # all three of these can be changed at any time
    allowed_cpu_proportion = .10 # use up to 10% of the CPU, on average
    cpu_slice = 1.0 # use up to 1.0 seconds before yielding
    minimum_cycle_time = 300 # don't run a cycle faster than this

    def __init__(self, backend, statefile, allowed_cpu_proportion=None, clock=None):
        precondition(IStorageBackend.providedBy(backend), backend)
        service.MultiService.__init__(self)
        self.backend = backend
        self.statefile = statefile
        if allowed_cpu_proportion is not None:
            self.allowed_cpu_proportion = allowed_cpu_proportion
        self.clock = clock or reactor
        self.prefixes = [si_b2a(struct.pack(">H", i << (16-10)))[:2]
                         for i in range(2**10)]
        self.prefixes.sort()
        self.timer = None
        self.current_sleep_time = None
        self.next_wake_time = None
        self.last_prefix_finished_time = None
        self.last_prefix_elapsed_time = None
        self.last_cycle_started_time = None
        self.last_cycle_elapsed_time = None
        self.load_state()

        # used by tests
        self._hooks = {'after_prefix': None, 'after_cycle': None, 'yield': None}

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
                This does not include the remaining time left in the current
                prefix, and it will be very inaccurate on fast crawlers
                (which can process a whole prefix in a single tick)
         estimated-time-per-cycle: float, seconds required to do a complete
                                   cycle

        If cycle-in-progress is False, the following keys are available::

         next-crawl-time: float, seconds-since-epoch when next crawl starts
         remaining-wait-time: float, seconds from now when next crawl starts
         estimated-time-per-cycle: float, seconds required to do a complete
                                   cycle
        """

        p = {}

        if self.state["current-cycle"] is None:
            p["cycle-in-progress"] = False
            p["next-crawl-time"] = self.next_wake_time
            p["remaining-wait-time"] = self.minus_or_none(self.next_wake_time,
                                                          time.time())
        else:
            p["cycle-in-progress"] = True
            pct = 100.0 * self.last_complete_prefix_index / len(self.prefixes)
            p["cycle-complete-percentage"] = pct
            remaining = None
            if self.last_prefix_elapsed_time is not None:
                left = len(self.prefixes) - self.last_complete_prefix_index
                remaining = left * self.last_prefix_elapsed_time

            p["estimated-cycle-complete-time-left"] = remaining
            # it's possible to call get_progress() from inside a crawler's
            # finished_prefix() function
            p["remaining-sleep-time"] = self.minus_or_none(self.next_wake_time,
                                                           self.clock.seconds())

        per_cycle = None
        if self.last_cycle_elapsed_time is not None:
            per_cycle = self.last_cycle_elapsed_time
        elif self.last_prefix_elapsed_time is not None:
            per_cycle = len(self.prefixes) * self.last_prefix_elapsed_time
        p["estimated-time-per-cycle"] = per_cycle
        return p

    def get_state(self):
        """I return the current state of the crawler. This is a copy of my
        state dictionary.

        Subclasses can override this to add computed keys to the return value,
        but don't forget to start with the upcall.
        """
        state = self.state.copy() # it isn't a deepcopy, so don't go crazy
        return state

    def load_state(self):
        # We use this to store state for both the crawler's internals and
        # anything the subclass-specific code needs. The state is stored
        # after each prefix is processed, and after a cycle is complete.
        # The internal keys we use are:
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
        try:
            pickled = fileutil.read(self.statefile)
        except Exception:
            state = {"version": 1,
                     "last-cycle-finished": None,
                     "current-cycle": None,
                     "last-complete-prefix": None,
                     }
        else:
            state = pickle.loads(pickled)

        state.setdefault("current-cycle-start-time", self.clock.seconds()) # approximate
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
        pickled = pickle.dumps(self.state)
        fileutil.write(self.statefile, pickled)

    def startService(self):
        # arrange things to look like we were just sleeping, so
        # status/progress values work correctly
        self.sleeping_between_cycles = True
        self.current_sleep_time = self.slow_start
        self.next_wake_time = self.clock.seconds() + self.slow_start
        self.timer = self.clock.callLater(self.slow_start, self.start_slice)
        service.MultiService.startService(self)

    def stopService(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.save_state()
        return service.MultiService.stopService(self)

    def start_slice(self):
        start_slice = self.clock.seconds()
        self.timer = None
        self.sleeping_between_cycles = False
        self.current_sleep_time = None
        self.next_wake_time = None

        d = self.start_current_prefix(start_slice)
        def _err(f):
            f.trap(TimeSliceExceeded)
            return False
        def _ok(ign):
            return True
        d.addCallbacks(_ok, _err)
        def _done(finished_cycle):
            self.save_state()
            if not self.running:
                # someone might have used stopService() to shut us down
                return

            # Either we finished a whole cycle, or we ran out of time.
            now = self.clock.seconds()
            this_slice = now - start_slice

            # this_slice/(this_slice+sleep_time) = percentage
            # this_slice/percentage = this_slice+sleep_time
            # sleep_time = (this_slice/percentage) - this_slice
            sleep_time = (this_slice / self.allowed_cpu_proportion) - this_slice

            # If the math gets weird, or a timequake happens, don't sleep
            # forever. Note that this means that, while a cycle is running, we
            # will process at least one prefix every 5 minutes, provided prefixes
            # do not take more than 5 minutes to process.
            sleep_time = max(0.0, min(sleep_time, 299))

            if finished_cycle:
                # how long should we sleep between cycles? Don't run faster than
                # allowed_cpu_proportion says, but also run faster than
                # minimum_cycle_time
                self.sleeping_between_cycles = True
                sleep_time = max(sleep_time, self.minimum_cycle_time)
            else:
                self.sleeping_between_cycles = False

            self.current_sleep_time = sleep_time # for status page
            self.next_wake_time = now + sleep_time
            self.yielding(sleep_time)
            self.timer = self.clock.callLater(sleep_time, self.start_slice)
        d.addCallback(_done)
        d.addBoth(self._call_hook, 'yield')
        return d

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

        def _prefix_loop(i):
            d2 = self._do_prefix(cycle, i, start_slice)
            d2.addBoth(self._call_hook, 'after_prefix')
            d2.addCallback(lambda ign: True)
            return d2
        d = async_iterate(_prefix_loop, xrange(self.last_complete_prefix_index + 1, len(self.prefixes)))

        def _cycle_done(ign):
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
            return cycle
        d.addCallback(_cycle_done)
        d.addBoth(self._call_hook, 'after_cycle')
        return d

    def _do_prefix(self, cycle, i, start_slice):
        prefix = self.prefixes[i]
        d = defer.maybeDeferred(self.process_prefix, cycle, prefix, start_slice)
        def _done(ign):
            self.last_complete_prefix_index = i

            now = time.time()
            if self.last_prefix_finished_time is not None:
                elapsed = now - self.last_prefix_finished_time
                self.last_prefix_elapsed_time = elapsed
            self.last_prefix_finished_time = now

            self.finished_prefix(cycle, prefix)

            if time.time() >= start_slice + self.cpu_slice:
                raise TimeSliceExceeded()

            return prefix
        d.addCallback(_done)
        return d

    def process_prefix(self, cycle, prefix, start_slice):
        """
        Called for each prefix.
        """
        return defer.succeed(None)

    # the remaining methods are explictly for subclasses to implement.

    def started_cycle(self, cycle):
        """Notify a subclass that the crawler is about to start a cycle.

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
        """
        Notify subclass that a cycle (one complete traversal of all
        prefixes) has just finished. 'cycle' is the number of the cycle
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
        """
        The crawler is about to sleep for 'sleep_time' seconds. This
        method is mostly for the convenience of unit tests.

        This method is for subclasses to override. No upcall is necessary.
        """
        pass


class BucketCountingCrawler(ShareCrawler):
    """
    I keep track of how many sharesets, each corresponding to a storage index,
    are being managed by this server. This is equivalent to the number of
    distributed files and directories for which I am providing storage. The
    actual number of files and directories in the full grid is probably higher
    (especially when there are more servers than 'N', the number of generated
    shares), because some files and directories will have shares on other
    servers instead of me. Also note that the number of sharesets will differ
    from the number of shares in small grids, when more than one share is
    placed on a single server.
    """

    minimum_cycle_time = 60*60 # we don't need this more than once an hour

    def add_initial_state(self):
        # ["bucket-counts"][cyclenum][prefix] = number
        # ["last-complete-cycle"] = cyclenum # maintained by base class
        # ["last-complete-bucket-count"] = number
        self.state.setdefault("bucket-counts", {})
        self.state.setdefault("last-complete-bucket-count", None)

    def process_prefix(self, cycle, prefix, start_slice):
        # We don't need to look at the individual sharesets.
        d = self.backend.get_sharesets_for_prefix(prefix)
        def _got_sharesets(sharesets):
            if cycle not in self.state["bucket-counts"]:
                self.state["bucket-counts"][cycle] = {}
            self.state["bucket-counts"][cycle][prefix] = len(sharesets)
        d.addCallback(_got_sharesets)
        return d

    def finished_cycle(self, cycle):
        last_counts = self.state["bucket-counts"].get(cycle, [])
        if len(last_counts) == len(self.prefixes):
            # great, we have a whole cycle.
            num_sharesets = sum(last_counts.values())
            self.state["last-complete-bucket-count"] = num_sharesets
            # get rid of old counts
            for old_cycle in list(self.state["bucket-counts"].keys()):
                if old_cycle != cycle:
                    del self.state["bucket-counts"][old_cycle]
