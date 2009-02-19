
import os, time, struct, pickle
from twisted.internet import reactor
from twisted.application import service
from allmydata.storage.server import si_b2a

class TimeSliceExceeded(Exception):
    pass

class ShareCrawler(service.MultiService):
    """A ShareCrawler subclass is attached to a StorageServer, and
    periodically walks all of its shares, processing each one in some
    fashion. This crawl is rate-limited, to reduce the IO burden on the host,
    since large servers will have several million shares, which can take
    hours or days to read.

    We assume that the normal upload/download/get_buckets traffic of a tahoe
    grid will cause the prefixdir contents to be mostly cached, or that the
    number of buckets in each prefixdir will be small enough to load quickly.
    A 1TB allmydata.com server was measured to have 2.56M buckets, spread
    into the 1040 prefixdirs, with about 2460 buckets per prefix. On this
    server, each prefixdir took 130ms-200ms to list the first time, and 17ms
    to list the second time.

    To use this, create a subclass which implements the process_bucket()
    method. It will be called with a prefixdir and a base32 storage index
    string. process_bucket() should run synchronously.

    Then create an instance, with a reference to a StorageServer and a
    filename where it can store persistent state. The statefile is used to
    keep track of how far around the ring the process has travelled, as well
    as timing history to allow the pace to be predicted and controlled. The
    statefile will be updated and written to disk after every bucket is
    processed.

    The crawler instance must be started with startService() before it will
    do any work. To make it stop doing work, call stopService() and wait for
    the Deferred that it returns.
    """

    # use up to 10% of the CPU, on average. This can be changed at any time.
    allowed_cpu_percentage = .10
    # use up to 1.0 seconds before yielding. This can be changed at any time.
    cpu_slice = 1.0
    # don't run a cycle faster than this
    minimum_cycle_time = 300

    def __init__(self, server, statefile):
        service.MultiService.__init__(self)
        self.server = server
        self.sharedir = server.sharedir
        self.statefile = statefile
        self.prefixes = [si_b2a(struct.pack(">H", i << (16-10)))[:2]
                         for i in range(2**10)]
        self.prefixes.sort()
        self.timer = None
        self.bucket_cache = (None, [])
        self.first_cycle_finished = False

    def load_state(self):
        try:
            f = open(self.statefile, "rb")
            state = pickle.load(f)
            lcp = state["last-complete-prefix"]
            if lcp == None:
                self.last_complete_prefix_index = -1
            else:
                self.last_complete_prefix_index = self.prefixes.index(lcp)
            self.last_complete_bucket = state["last-complete-bucket"]
            self.first_cycle_finished = state["first-cycle-finished"]
            f.close()
        except EnvironmentError:
            self.last_complete_prefix_index = -1
            self.last_complete_bucket = None
            self.first_cycle_finished = False

    def save_state(self):
        lcpi = self.last_complete_prefix_index
        if lcpi == -1:
            last_complete_prefix = None
        else:
            last_complete_prefix = self.prefixes[lcpi]
        state = {"version": 1,
                 "last-complete-prefix": last_complete_prefix,
                 "last-complete-bucket": self.last_complete_bucket,
                 "first-cycle-finished": self.first_cycle_finished,
                 }
        tmpfile = self.statefile + ".tmp"
        f = open(tmpfile, "wb")
        pickle.dump(state, f)
        f.close()
        os.rename(tmpfile, self.statefile)

    def startService(self):
        self.load_state()
        self.timer = reactor.callLater(0, self.start_slice)
        service.MultiService.startService(self)

    def stopService(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        return service.MultiService.stopService(self)

    def start_slice(self):
        self.timer = None
        start_slice = time.time()
        try:
            self.start_current_prefix(start_slice)
            finished_cycle = True
        except TimeSliceExceeded:
            finished_cycle = False
        # either we finished a whole cycle, or we ran out of time
        this_slice = time.time() - start_slice
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
        self.yielding(sleep_time)
        self.timer = reactor.callLater(sleep_time, self.start_slice)

    def start_current_prefix(self, start_slice):
        for i in range(self.last_complete_prefix_index+1, len(self.prefixes)):
            if time.time() > start_slice + self.cpu_slice:
                raise TimeSliceExceeded()
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
            self.process_prefixdir(prefixdir, buckets, start_slice)
            self.last_complete_prefix_index = i
            self.save_state()
        # yay! we finished the whole cycle
        self.last_complete_prefix_index = -1
        self.last_complete_bucket = None
        self.first_cycle_finished = True
        self.save_state()
        self.finished_cycle()

    def process_prefixdir(self, prefixdir, buckets, start_slice):
        """This gets a list of bucket names (i.e. storage index strings,
        base32-encoded) in sorted order.

        Override this if your crawler doesn't care about the actual shares,
        for example a crawler which merely keeps track of how many buckets
        are being managed by this server.
        """
        for bucket in buckets:
            if bucket <= self.last_complete_bucket:
                continue
            if time.time() > start_slice + self.cpu_slice:
                raise TimeSliceExceeded()
            self.process_bucket(prefixdir, bucket)
            self.last_complete_bucket = bucket
            self.save_state()

    def process_bucket(self, prefixdir, storage_index_b32):
        pass

    def finished_cycle(self):
        pass

    def yielding(self, sleep_time):
        pass
