
import os, re, weakref, struct, time

from twisted.application import service

from zope.interface import implements
from allmydata.interfaces import IStatsProducer
from allmydata.util import fileutil, idlib, log, time_format
import allmydata # for __full_version__

from allmydata.storage.common import si_b2a, si_a2b, storage_index_to_dir
_pyflakes_hush = [si_b2a, si_a2b, storage_index_to_dir] # re-exported
from allmydata.storage.backends.disk.mutable import MutableShareFile, EmptyShare, \
     create_mutable_sharefile
from allmydata.mutable.layout import MAX_MUTABLE_SHARE_SIZE
from allmydata.storage.backends.disk.immutable import ShareFile, BucketWriter, BucketReader
from allmydata.storage.crawler import BucketCountingCrawler
from allmydata.storage.accountant import Accountant
from allmydata.storage.expiration import ExpirationPolicy
from allmydata.storage.leasedb import SHARETYPE_MUTABLE


# storage/
# storage/shares/incoming
#   incoming/ holds temp dirs named $START/$STORAGEINDEX/$SHARENUM which will
#   be moved to storage/shares/$START/$STORAGEINDEX/$SHARENUM upon success
# storage/shares/$START/$STORAGEINDEX
# storage/shares/$START/$STORAGEINDEX/$SHARENUM

# Where "$START" denotes the first 10 bits worth of $STORAGEINDEX (that's 2
# base-32 chars).

# $SHARENUM matches this regex:
NUM_RE=re.compile("^[0-9]+$")



class StorageServer(service.MultiService):
    implements(IStatsProducer)
    name = 'storage'
    BucketCounterClass = BucketCountingCrawler
    DEFAULT_EXPIRATION_POLICY = ExpirationPolicy(enabled=False)

    def __init__(self, storedir, nodeid, reserved_space=0,
                 readonly_storage=False,
                 stats_provider=None,
                 expiration_policy=None):
        service.MultiService.__init__(self)
        assert isinstance(nodeid, str)
        assert len(nodeid) == 20
        self.my_nodeid = nodeid
        self.storedir = storedir
        sharedir = os.path.join(storedir, "shares")
        fileutil.make_dirs(sharedir)
        self.sharedir = sharedir
        # we don't actually create the corruption-advisory dir until necessary
        self.corruption_advisory_dir = os.path.join(storedir,
                                                    "corruption-advisories")
        self.reserved_space = int(reserved_space)
        self.readonly_storage = readonly_storage
        self.stats_provider = stats_provider
        if self.stats_provider:
            self.stats_provider.register_producer(self)
        self.incomingdir = os.path.join(sharedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)
        self._active_writers = weakref.WeakKeyDictionary()
        log.msg("StorageServer created", facility="tahoe.storage")

        if reserved_space:
            if self.get_available_space() is None:
                log.msg("warning: [storage]reserved_space= is set, but this platform does not support an API to get disk statistics (statvfs(2) or GetDiskFreeSpaceEx), so this reservation cannot be honored",
                        umin="0wZ27w", level=log.UNUSUAL)

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
        self.add_bucket_counter()
        self.init_accountant(expiration_policy or self.DEFAULT_EXPIRATION_POLICY)

    def init_accountant(self, expiration_policy):
        dbfile = os.path.join(self.storedir, "leasedb.sqlite")
        statefile = os.path.join(self.storedir, "leasedb_crawler.state")
        self.accountant = Accountant(self, dbfile, statefile)
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

    def get_nodeid(self):
        return self.my_nodeid

    def __repr__(self):
        return "<StorageServer %s>" % (idlib.shortnodeid_b2a(self.my_nodeid),)

    def have_shares(self):
        # quick test to decide if we need to commit to an implicit
        # permutation-seed or if we should use a new one
        return bool(set(os.listdir(self.sharedir)) - set(["incoming"]))

    def add_bucket_counter(self):
        statefile = os.path.join(self.storedir, "bucket_counter.state")
        self.bucket_counter = BucketCountingCrawler(self, statefile)
        self.bucket_counter.setServiceParent(self)

    def count(self, name, delta=1):
        if self.stats_provider:
            self.stats_provider.count("storage_server." + name, delta)

    def add_latency(self, category, latency):
        a = self.latencies[category]
        a.append(latency)
        if len(a) > 1000:
            self.latencies[category] = a[-1000:]

    def get_latencies(self):
        """Return a dict, indexed by category, that contains a dict of
        latency numbers for each category. If there are sufficient samples
        for unambiguous interpretation, each dict will contain the
        following keys: mean, 01_0_percentile, 10_0_percentile,
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

    def _clean_incomplete(self):
        fileutil.rm_dir(self.incomingdir)

    def get_stats(self):
        # remember: RIStatsProvider requires that our return dict
        # contains numeric values.
        stats = { 'storage_server.allocated': self.allocated_size(), }
        stats['storage_server.reserved_space'] = self.reserved_space
        for category,ld in self.get_latencies().items():
            for name,v in ld.items():
                stats['storage_server.latencies.%s.%s' % (category, name)] = v

        try:
            disk = fileutil.get_disk_stats(self.sharedir, self.reserved_space)
            writeable = disk['avail'] > 0

            # spacetime predictors should use disk_avail / (d(disk_used)/dt)
            stats['storage_server.disk_total'] = disk['total']
            stats['storage_server.disk_used'] = disk['used']
            stats['storage_server.disk_free_for_root'] = disk['free_for_root']
            stats['storage_server.disk_free_for_nonroot'] = disk['free_for_nonroot']
            stats['storage_server.disk_avail'] = disk['avail']
        except AttributeError:
            writeable = True
        except EnvironmentError:
            log.msg("OS call to get disk statistics failed", level=log.UNUSUAL)
            writeable = False

        if self.readonly_storage:
            stats['storage_server.disk_avail'] = 0
            writeable = False

        stats['storage_server.accepting_immutable_shares'] = int(writeable)
        s = self.bucket_counter.get_state()
        bucket_count = s.get("last-complete-bucket-count")
        if bucket_count:
            stats['storage_server.total_bucket_count'] = bucket_count
        return stats

    def get_available_space(self):
        """Returns available space for share storage in bytes, or None if no
        API to get this information is available."""

        if self.readonly_storage:
            return 0
        return fileutil.get_available_space(self.sharedir, self.reserved_space)

    def allocated_size(self):
        space = 0
        for bw in self._active_writers:
            space += bw.allocated_size()
        return space

    # these methods can be invoked by our callers

    def client_get_version(self, account):
        remaining_space = self.get_available_space()
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
                      "accounting-v1": {},
                      },
                    "application-version": str(allmydata.__full_version__),
                    }
        return version

    def client_allocate_buckets(self, storage_index,
                                sharenums, allocated_size,
                                canary, account):
        start = time.time()
        self.count("allocate")
        alreadygot = set()
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_dir = storage_index_to_dir(storage_index)
        si_s = si_b2a(storage_index)

        log.msg("storage: allocate_buckets %s" % si_s)

        # Note that the lease should not be added until the BucketWriter has
        # been closed. This is handled in BucketWriter.close()

        max_space_per_bucket = allocated_size

        remaining_space = self.get_available_space()
        limited = remaining_space is not None
        if limited:
            # this is a bit conservative, since some of this allocated_size()
            # has already been written to disk, where it will show up in
            # get_available_space.
            remaining_space -= self.allocated_size()
        # self.readonly_storage causes remaining_space <= 0

        # fill alreadygot with all shares that we have, not just the ones
        # they asked about: this will save them a lot of work. Add or update
        # leases for all of them: if they want us to hold shares for this
        # file, they'll want us to hold leases for this file.
        for (shnum, fn) in self._get_bucket_shares(storage_index):
            alreadygot.add(shnum)

        for shnum in sharenums:
            incominghome = os.path.join(self.incomingdir, si_dir, "%d" % shnum)
            finalhome = os.path.join(self.sharedir, si_dir, "%d" % shnum)
            if os.path.exists(finalhome):
                # great! we already have it. easy.
                pass
            elif os.path.exists(incominghome):
                # Note that we don't create BucketWriters for shnums that
                # have a partial share (in incoming/), so if a second upload
                # occurs while the first is still in progress, the second
                # uploader will use different storage servers.
                pass
            elif (not limited) or (remaining_space >= max_space_per_bucket):
                # ok! we need to create the new share file.
                bw = BucketWriter(self, account, storage_index, shnum,
                                  incominghome, finalhome,
                                  max_space_per_bucket, canary)
                bucketwriters[shnum] = bw
                self._active_writers[bw] = 1
                if limited:
                    remaining_space -= max_space_per_bucket
            else:
                # bummer! not enough space to accept this bucket
                pass

        if bucketwriters:
            fileutil.make_dirs(os.path.join(self.sharedir, si_dir))

        self.add_latency("allocate", time.time() - start)
        return alreadygot, bucketwriters

    def _iter_share_files(self, storage_index):
        for shnum, filename in self._get_bucket_shares(storage_index):
            f = open(filename, 'rb')
            header = f.read(32)
            f.close()
            if header[:32] == MutableShareFile.MAGIC:
                sf = MutableShareFile(filename, self)
                # note: if the share has been migrated, the renew_lease()
                # call will throw an exception, with information to help the
                # client update the lease.
            elif header[:4] == struct.pack(">L", 1):
                sf = ShareFile(filename)
            else:
                continue # non-sharefile
            yield sf

    def bucket_writer_closed(self, bw, consumed_size):
        if self.stats_provider:
            self.stats_provider.count('storage_server.bytes_added', consumed_size)
        del self._active_writers[bw]

    def _get_bucket_shares(self, storage_index):
        """Return a list of (shnum, pathname) tuples for files that hold
        shares for this storage_index. In each tuple, 'shnum' will always be
        the integer form of the last component of 'pathname'."""
        storagedir = os.path.join(self.sharedir, storage_index_to_dir(storage_index))
        try:
            for f in os.listdir(storagedir):
                if NUM_RE.match(f):
                    filename = os.path.join(storagedir, f)
                    yield (int(f), filename)
        except OSError:
            # Commonly caused by there being no buckets at all.
            pass

    def client_get_buckets(self, storage_index):
        start = time.time()
        self.count("get")
        si_s = si_b2a(storage_index)
        log.msg("storage: get_buckets %s" % si_s)
        bucketreaders = {} # k: sharenum, v: BucketReader
        for shnum, filename in self._get_bucket_shares(storage_index):
            bucketreaders[shnum] = BucketReader(self, filename,
                                                storage_index, shnum)
        self.add_latency("get", time.time() - start)
        return bucketreaders

    def client_slot_testv_and_readv_and_writev(self, storage_index,
                                               write_enabler,
                                               test_and_write_vectors,
                                               read_vector, account):
        start = time.time()
        self.count("writev")
        si_s = si_b2a(storage_index)

        log.msg("storage: slot_writev %s" % si_s)
        si_dir = storage_index_to_dir(storage_index)

        # shares exist if there is a file for them
        bucketdir = os.path.join(self.sharedir, si_dir)
        shares = {}
        if os.path.isdir(bucketdir):
            for sharenum_s in os.listdir(bucketdir):
                try:
                    sharenum = int(sharenum_s)
                except ValueError:
                    continue
                filename = os.path.join(bucketdir, sharenum_s)
                msf = MutableShareFile(filename, self)
                msf.check_write_enabler(write_enabler, si_s)
                shares[sharenum] = msf
        # write_enabler is good for all existing shares.

        # Now evaluate test vectors.
        testv_is_good = True
        for sharenum in test_and_write_vectors:
            (testv, datav, new_length) = test_and_write_vectors[sharenum]
            if sharenum in shares:
                if not shares[sharenum].check_testv(testv):
                    self.log("testv failed: [%d]: %r" % (sharenum, testv))
                    testv_is_good = False
                    break
            else:
                # compare the vectors against an empty share, in which all
                # reads return empty strings.
                if not EmptyShare().check_testv(testv):
                    self.log("testv failed (empty): [%d] %r" % (sharenum,
                                                                testv))
                    testv_is_good = False
                    break

        # now gather the read vectors, before we do any writes
        read_data = {}
        for sharenum, share in shares.items():
            read_data[sharenum] = share.readv(read_vector)

        if testv_is_good:
            # now apply the write vectors
            for sharenum in test_and_write_vectors:
                (testv, datav, new_length) = test_and_write_vectors[sharenum]
                if new_length == 0:
                    if sharenum in shares:
                        shares[sharenum].unlink()
                        account.remove_share_and_leases(storage_index, sharenum)
                else:
                    if sharenum not in shares:
                        # allocate a new share
                        allocated_size = 2000 # arbitrary, really # REMOVE
                        share = self._allocate_slot_share(bucketdir,
                                                          write_enabler,
                                                          sharenum,
                                                          allocated_size)
                        shares[sharenum] = share
                        shares[sharenum].writev(datav, new_length)
                        account.add_share(storage_index, sharenum,
                                          shares[sharenum].get_used_space(), SHARETYPE_MUTABLE)
                    else:
                        # apply the write vector and update the lease
                        shares[sharenum].writev(datav, new_length)

                    account.add_or_renew_default_lease(storage_index, sharenum)
                    account.mark_share_as_stable(storage_index, sharenum,
                                                 shares[sharenum].get_used_space())

            if new_length == 0:
                # delete empty bucket directories
                if not os.listdir(bucketdir):
                    os.rmdir(bucketdir)

        # all done
        self.add_latency("writev", time.time() - start)
        return (testv_is_good, read_data)

    def _allocate_slot_share(self, bucketdir, write_enabler, sharenum, allocated_size):
        my_nodeid = self.my_nodeid
        fileutil.make_dirs(bucketdir)
        filename = os.path.join(bucketdir, "%d" % sharenum)
        share = create_mutable_sharefile(filename, my_nodeid, write_enabler,
                                         self)
        return share

    def delete_share(self, storage_index, shnum):
        si_dir = storage_index_to_dir(storage_index)
        filename = os.path.join(self.sharedir, si_dir, "%d" % (shnum,))
        os.unlink(filename)

    def client_slot_readv(self, storage_index, shares, readv, account):
        start = time.time()
        self.count("readv")
        si_s = si_b2a(storage_index)
        lp = log.msg("storage: slot_readv %s %s" % (si_s, shares),
                     facility="tahoe.storage", level=log.OPERATIONAL)
        si_dir = storage_index_to_dir(storage_index)
        # shares exist if there is a file for them
        bucketdir = os.path.join(self.sharedir, si_dir)
        if not os.path.isdir(bucketdir):
            self.add_latency("readv", time.time() - start)
            return {}
        datavs = {}
        for sharenum_s in os.listdir(bucketdir):
            try:
                sharenum = int(sharenum_s)
            except ValueError:
                continue
            if sharenum in shares or not shares:
                filename = os.path.join(bucketdir, sharenum_s)
                msf = MutableShareFile(filename, self)
                datavs[sharenum] = msf.readv(readv)
        log.msg("returning shares %s" % (datavs.keys(),),
                facility="tahoe.storage", level=log.NOISY, parent=lp)
        self.add_latency("readv", time.time() - start)
        return datavs

    def client_advise_corrupt_share(self, share_type, storage_index, shnum, reason):
        fileutil.make_dirs(self.corruption_advisory_dir)
        now = time_format.iso_utc(sep="T")
        si_s = si_b2a(storage_index)
        # windows can't handle colons in the filename
        fn = os.path.join(self.corruption_advisory_dir,
                          "%s--%s-%d" % (now, si_s, shnum)).replace(":","")
        f = open(fn, "w")
        f.write("report: Share Corruption\n")
        f.write("type: %s\n" % share_type)
        f.write("storage_index: %s\n" % si_s)
        f.write("share_number: %d\n" % shnum)
        f.write("\n")
        f.write(reason)
        f.write("\n")
        f.close()
        log.msg(format=("client claims corruption in (%(share_type)s) " +
                        "%(si)s-%(shnum)d: %(reason)s"),
                share_type=share_type, si=si_s, shnum=shnum, reason=reason,
                level=log.SCARY, umid="SGx2fA")
        return None
