
import os, weakref

from twisted.application import service
from twisted.internet import defer, reactor

from zope.interface import implements
from allmydata.interfaces import IStatsProducer, IStorageBackend
from allmydata.util.assertutil import precondition
from allmydata.util import fileutil, idlib, log, time_format
import allmydata # for __full_version__

from allmydata.storage.common import si_b2a, si_a2b, storage_index_to_dir
_pyflakes_hush = [si_b2a, si_a2b, storage_index_to_dir] # re-exported
from allmydata.mutable.layout import MAX_MUTABLE_SHARE_SIZE
from allmydata.storage.crawler import BucketCountingCrawler
from allmydata.storage.accountant import Accountant
from allmydata.storage.expiration import ExpirationPolicy


class StorageServer(service.MultiService):
    implements(IStatsProducer)
    name = 'storage'
    BucketCounterClass = BucketCountingCrawler
    DEFAULT_EXPIRATION_POLICY = ExpirationPolicy(enabled=False)

    def __init__(self, serverid, backend, statedir,
                 stats_provider=None,
                 expiration_policy=None,
                 clock=None):
        service.MultiService.__init__(self)
        precondition(IStorageBackend.providedBy(backend), backend)
        precondition(isinstance(serverid, str), serverid)
        precondition(len(serverid) == 20, serverid)

        self._serverid = serverid
        self.clock = clock or reactor
        self.stats_provider = stats_provider
        if self.stats_provider:
            self.stats_provider.register_producer(self)

        self.backend = backend
        self.backend.setServiceParent(self)

        self._active_writers = weakref.WeakKeyDictionary()
        self._statedir = statedir
        fileutil.make_dirs(self._statedir)

        # we don't actually create the corruption-advisory dir until necessary
        self._corruption_advisory_dir = os.path.join(self._statedir,
                                                     "corruption-advisories")

        log.msg("StorageServer created", facility="tahoe.storage")

        self.latencies = {"allocate": [], # immutable
                          "write": [],
                          "close": [],
                          "read": [],
                          "get": [],
                          "writev": [], # mutable
                          "readv": [],
                          "add-lease": [], # both
                          "renew": [],
                          "cancel": [],
                          }

        self.init_bucket_counter()
        self.init_accountant(expiration_policy or self.DEFAULT_EXPIRATION_POLICY)

    def init_accountant(self, expiration_policy):
        dbfile = os.path.join(self._statedir, "leasedb.sqlite")
        statefile = os.path.join(self._statedir, "accounting_crawler.state")
        self.accountant = Accountant(self, dbfile, statefile, clock=self.clock)
        self.accountant.set_expiration_policy(expiration_policy)
        self.accountant.setServiceParent(self)

    def get_accountant(self):
        return self.accountant

    def get_accounting_crawler(self):
        return self.accountant.get_accounting_crawler()

    def get_expiration_policy(self):
        return self.accountant.get_accounting_crawler().get_expiration_policy()

    def get_bucket_counter(self):
        return self.bucket_counter

    def get_serverid(self):
        return self._serverid

    def __repr__(self):
        return "<StorageServer %s>" % (idlib.shortnodeid_b2a(self.get_serverid()),)

    def init_bucket_counter(self):
        statefile = os.path.join(self._statedir, "bucket_counter.state")
        self.bucket_counter = self.BucketCounterClass(self.backend, statefile,
                                                      clock=self.clock)
        self.bucket_counter.setServiceParent(self)

    def count(self, name, delta=1):
        if self.stats_provider:
            self.stats_provider.count("storage_server." + name, delta)

    def add_latency(self, category, latency):
        a = self.latencies[category]
        a.append(latency)
        if len(a) > 1000:
            self.latencies[category] = a[-1000:]

    def _add_latency(self, res, category, start):
        self.add_latency(category, self.clock.seconds() - start)
        return res

    def get_latencies(self):
        """Return a dict, indexed by category, that contains a dict of
        latency numbers for each category. If there are sufficient samples
        for unambiguous interpretation, each dict will contain the
        following keys: samplesize, mean, 01_0_percentile, 10_0_percentile,
        50_0_percentile (median), 90_0_percentile, 95_0_percentile,
        99_0_percentile, 99_9_percentile.  If there are insufficient
        samples for a given percentile to be interpreted unambiguously
        that percentile will be reported as None. If no samples have been
        collected for the given category, then that category name will
        not be present in the return value. """
        # note that Amazon's Dynamo paper says they use 99.9% percentile.
        output = {}
        for category in self.latencies:
            if not self.latencies[category]:
                continue
            stats = {}
            samples = self.latencies[category][:]
            count = len(samples)
            stats["samplesize"] = count
            samples.sort()
            if count > 1:
                stats["mean"] = sum(samples) / count
            else:
                stats["mean"] = None

            orderstatlist = [(0.01, "01_0_percentile", 100), (0.1, "10_0_percentile", 10),\
                             (0.50, "50_0_percentile", 10), (0.90, "90_0_percentile", 10),\
                             (0.95, "95_0_percentile", 20), (0.99, "99_0_percentile", 100),\
                             (0.999, "99_9_percentile", 1000)]

            for percentile, percentilestring, minnumtoobserve in orderstatlist:
                if count >= minnumtoobserve:
                    stats[percentilestring] = samples[int(percentile*count)]
                else:
                    stats[percentilestring] = None

            output[category] = stats
        return output

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.storage"
        return log.msg(*args, **kwargs)

    def get_stats(self):
        # remember: RIStatsProvider requires that our return dict
        # contains numeric, or None values.
        stats = { 'storage_server.allocated': self.allocated_size(), }
        for category,ld in self.get_latencies().items():
            for name,v in ld.items():
                stats['storage_server.latencies.%s.%s' % (category, name)] = v

        self.backend.fill_in_space_stats(stats)

        if self.bucket_counter:
            s = self.bucket_counter.get_state()
            bucket_count = s.get("last-complete-bucket-count")
            if bucket_count:
                stats['storage_server.total_bucket_count'] = bucket_count
        return stats

    def get_available_space(self):
        return self.backend.get_available_space()

    def allocated_size(self):
        space = 0
        for bw in self._active_writers:
            space += bw.allocated_size()
        return space

    # these methods can be invoked by our callers

    def client_get_version(self, account):
        remaining_space = self.backend.get_available_space()
        if remaining_space is None:
            # We're on a platform that has no API to get disk stats.
            remaining_space = 2**64

        version = { "http://allmydata.org/tahoe/protocols/storage/v1" :
                    { "maximum-immutable-share-size": remaining_space,
                      "maximum-mutable-share-size": MAX_MUTABLE_SHARE_SIZE,
                      "tolerates-immutable-read-overrun": True,
                      "delete-mutable-shares-with-zero-length-writev": True,
                      "fills-holes-with-zero-bytes": True,
                      "prevents-read-past-end-of-share-data": True,
                      "ignores-lease-renewal-and-cancel-secrets": True,
                      "has-immutable-readv": True,
                      },
                    "application-version": str(allmydata.__full_version__),
                    }
        return version

    def client_allocate_buckets(self, storage_index,
                                sharenums, allocated_data_length,
                                canary, account):
        start = self.clock.seconds()
        self.count("allocate")
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_s = si_b2a(storage_index)

        log.msg("storage: allocate_buckets %s" % si_s)

        remaining_space = self.get_available_space()
        limited = remaining_space is not None
        if limited:
            # This is a bit conservative, since some of this allocated_size()
            # has already been written to the backend, where it will show up in
            # get_available_space.
            remaining_space -= self.allocated_size()
            # If the backend is read-only, remaining_space will be <= 0.

        # Fill alreadygot with all shares that we have, not just the ones
        # they asked about: this will save them a lot of work. Leases will
        # be added or updated for all of them.
        alreadygot = set()
        shareset = self.backend.get_shareset(storage_index)
        d = shareset.get_shares()
        def _got_shares( (shares, corrupted) ):
            remaining = remaining_space
            for share in shares:
                # XXX do we need to explicitly add a lease here?
                alreadygot.add(share.get_shnum())

            d2 = defer.succeed(None)

            # We don't create BucketWriters for shnums where we have a share
            # that is corrupted. Is that right, or should we allow the corrupted
            # share to be clobbered? Note that currently the disk share classes
            # have assertions that prevent them from clobbering existing files.
            for shnum in set(sharenums) - alreadygot - corrupted:
                if shareset.has_incoming(shnum):
                    # Note that we don't create BucketWriters for shnums that
                    # have an incoming share, so if a second upload occurs while
                    # the first is still in progress, the second uploader will
                    # use different storage servers.
                    pass
                elif (not limited) or remaining >= allocated_data_length:
                    if limited:
                        remaining -= allocated_data_length

                    d2.addCallback(lambda ign, shnum=shnum:
                                   shareset.make_bucket_writer(account, shnum, allocated_data_length,
                                                               canary))
                    def _record_writer(bw, shnum=shnum):
                        bucketwriters[shnum] = bw
                        self._active_writers[bw] = 1
                    d2.addCallback(_record_writer)
                else:
                    # not enough space to accept this share
                    pass

            d2.addCallback(lambda ign: (alreadygot, bucketwriters))
            return d2
        d.addCallback(_got_shares)
        d.addBoth(self._add_latency, "allocate", start)
        return d

    def bucket_writer_closed(self, bw, consumed_size):
        if self.stats_provider:
            self.stats_provider.count('storage_server.bytes_added', consumed_size)
        del self._active_writers[bw]

    def client_get_buckets(self, storage_index, account):
        start = self.clock.seconds()
        self.count("get")
        si_s = si_b2a(storage_index)
        log.msg("storage: get_buckets %s" % si_s)
        bucketreaders = {} # k: sharenum, v: BucketReader

        shareset = self.backend.get_shareset(storage_index)
        d = shareset.get_shares()
        def _make_readers( (shares, corrupted) ):
            # We don't create BucketReaders for corrupted shares.
            for share in shares:
                assert not isinstance(share, defer.Deferred), share
                bucketreaders[share.get_shnum()] = shareset.make_bucket_reader(account, share)
            return bucketreaders
        d.addCallback(_make_readers)
        d.addBoth(self._add_latency, "get", start)
        return d

    def client_slot_testv_and_readv_and_writev(self, storage_index,
                                               write_enabler,
                                               test_and_write_vectors,
                                               read_vector, account):
        start = self.clock.seconds()
        self.count("writev")
        si_s = si_b2a(storage_index)
        log.msg("storage: slot_writev %s" % si_s)

        shareset = self.backend.get_shareset(storage_index)
        expiration_time = start + 31*24*60*60   # one month from now

        d = shareset.testv_and_readv_and_writev(write_enabler, test_and_write_vectors,
                                                read_vector, expiration_time, account)
        d.addBoth(self._add_latency, "writev", start)
        return d

    def client_slot_readv(self, storage_index, shares, readv, account):
        start = self.clock.seconds()
        self.count("readv")
        si_s = si_b2a(storage_index)
        log.msg("storage: slot_readv %s %s" % (si_s, shares),
                facility="tahoe.storage", level=log.OPERATIONAL)

        shareset = self.backend.get_shareset(storage_index)
        d = shareset.readv(shares, readv)
        d.addBoth(self._add_latency, "readv", start)
        return d

    def client_advise_corrupt_share(self, share_type, storage_index, shnum, reason, account):
        fileutil.make_dirs(self._corruption_advisory_dir)
        now = time_format.iso_utc(sep="T")
        si_s = si_b2a(storage_index)
        owner_num = account.get_owner_num()

        # windows can't handle colons in the filename
        fn = os.path.join(self._corruption_advisory_dir,
                          "%s--%s-%d" % (now, si_s, shnum)).replace(":","")
        f = open(fn, "w")
        try:
            f.write("report: Share Corruption\n")
            f.write("type: %s\n" % (share_type,))
            f.write("storage_index: %s\n" % (si_s,))
            f.write("share_number: %d\n" % (shnum,))
            f.write("owner_num: %s\n" % (owner_num,))
            f.write("\n")
            f.write(reason)
            f.write("\n")
        finally:
            f.close()

        log.msg(format=("client #%(owner_num)d claims corruption in (%(share_type)s) " +
                        "%(si)s-%(shnum)d: %(reason)s"),
                owner_num=owner_num, share_type=share_type, si=si_s, shnum=shnum, reason=reason,
                level=log.SCARY, umid="SGx2fA")
