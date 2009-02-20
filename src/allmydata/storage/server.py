import os, re, weakref, struct, time

from foolscap import Referenceable
from twisted.application import service

from zope.interface import implements
from allmydata.interfaces import RIStorageServer, IStatsProducer
from allmydata.util import base32, fileutil, log, time_format
import allmydata # for __full_version__

from allmydata.storage.lease import LeaseInfo
from allmydata.storage.mutable import MutableShareFile, EmptyShare, \
     create_mutable_sharefile
from allmydata.storage.immutable import ShareFile, BucketWriter, BucketReader

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

def si_b2a(storageindex):
    return base32.b2a(storageindex)

def si_a2b(ascii_storageindex):
    return base32.a2b(ascii_storageindex)

def storage_index_to_dir(storageindex):
    sia = si_b2a(storageindex)
    return os.path.join(sia[:2], sia)



class StorageServer(service.MultiService, Referenceable):
    implements(RIStorageServer, IStatsProducer)
    name = 'storage'

    def __init__(self, storedir, nodeid, reserved_space=0,
                 discard_storage=False, readonly_storage=False,
                 stats_provider=None):
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
        self.no_storage = discard_storage
        self.readonly_storage = readonly_storage
        self.stats_provider = stats_provider
        if self.stats_provider:
            self.stats_provider.register_producer(self)
        self.incomingdir = os.path.join(sharedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)
        self._active_writers = weakref.WeakKeyDictionary()
        lp = log.msg("StorageServer created", facility="tahoe.storage")

        if reserved_space:
            if self.get_available_space() is None:
                log.msg("warning: [storage]reserved_space= is set, but this platform does not support statvfs(2), so this reservation cannot be honored",
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
        latency numbers for each category. Each dict will contain the
        following keys: mean, 01_0_percentile, 10_0_percentile,
        50_0_percentile (median), 90_0_percentile, 95_0_percentile,
        99_0_percentile, 99_9_percentile. If no samples have been collected
        for the given category, then that category name will not be present
        in the return value."""
        # note that Amazon's Dynamo paper says they use 99.9% percentile.
        output = {}
        for category in self.latencies:
            if not self.latencies[category]:
                continue
            stats = {}
            samples = self.latencies[category][:]
            samples.sort()
            count = len(samples)
            stats["mean"] = sum(samples) / count
            stats["01_0_percentile"] = samples[int(0.01 * count)]
            stats["10_0_percentile"] = samples[int(0.1 * count)]
            stats["50_0_percentile"] = samples[int(0.5 * count)]
            stats["90_0_percentile"] = samples[int(0.9 * count)]
            stats["95_0_percentile"] = samples[int(0.95 * count)]
            stats["99_0_percentile"] = samples[int(0.99 * count)]
            stats["99_9_percentile"] = samples[int(0.999 * count)]
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
        stats["storage_server.reserved_space"] = self.reserved_space
        for category,ld in self.get_latencies().items():
            for name,v in ld.items():
                stats['storage_server.latencies.%s.%s' % (category, name)] = v
        writeable = True
        if self.readonly_storage:
            writeable = False
        try:
            s = os.statvfs(self.storedir)
            disk_total = s.f_bsize * s.f_blocks
            disk_used = s.f_bsize * (s.f_blocks - s.f_bfree)
            # spacetime predictors should look at the slope of disk_used.
            disk_avail = s.f_bsize * s.f_bavail # available to non-root users
            # include our local policy here: if we stop accepting shares when
            # the available space drops below 1GB, then include that fact in
            # disk_avail.
            disk_avail -= self.reserved_space
            disk_avail = max(disk_avail, 0)
            if self.readonly_storage:
                disk_avail = 0
            if disk_avail == 0:
                writeable = False

            # spacetime predictors should use disk_avail / (d(disk_used)/dt)
            stats["storage_server.disk_total"] = disk_total
            stats["storage_server.disk_used"] = disk_used
            stats["storage_server.disk_avail"] = disk_avail
        except AttributeError:
            # os.statvfs is available only on unix
            pass
        stats["storage_server.accepting_immutable_shares"] = int(writeable)
        return stats


    def stat_disk(self, d):
        s = os.statvfs(d)
        # s.f_bavail: available to non-root users
        disk_avail = s.f_bsize * s.f_bavail
        return disk_avail

    def get_available_space(self):
        # returns None if it cannot be measured (windows)
        try:
            disk_avail = self.stat_disk(self.storedir)
            disk_avail -= self.reserved_space
        except AttributeError:
            disk_avail = None
        if self.readonly_storage:
            disk_avail = 0
        return disk_avail

    def allocated_size(self):
        space = 0
        for bw in self._active_writers:
            space += bw.allocated_size()
        return space

    def remote_get_version(self):
        remaining_space = self.get_available_space()
        if remaining_space is None:
            # we're on a platform that doesn't have 'df', so make a vague
            # guess.
            remaining_space = 2**64
        version = { "http://allmydata.org/tahoe/protocols/storage/v1" :
                    { "maximum-immutable-share-size": remaining_space,
                      "tolerates-immutable-read-overrun": True,
                      "delete-mutable-shares-with-zero-length-writev": True,
                      },
                    "application-version": str(allmydata.__full_version__),
                    }
        return version

    def remote_allocate_buckets(self, storage_index,
                                renew_secret, cancel_secret,
                                sharenums, allocated_size,
                                canary, owner_num=0):
        # owner_num is not for clients to set, but rather it should be
        # curried into the PersonalStorageServer instance that is dedicated
        # to a particular owner.
        start = time.time()
        self.count("allocate")
        alreadygot = set()
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_dir = storage_index_to_dir(storage_index)
        si_s = si_b2a(storage_index)

        log.msg("storage: allocate_buckets %s" % si_s)

        # in this implementation, the lease information (including secrets)
        # goes into the share files themselves. It could also be put into a
        # separate database. Note that the lease should not be added until
        # the BucketWriter has been closed.
        expire_time = time.time() + 31*24*60*60
        lease_info = LeaseInfo(owner_num,
                               renew_secret, cancel_secret,
                               expire_time, self.my_nodeid)

        max_space_per_bucket = allocated_size

        remaining_space = self.get_available_space()
        limited = remaining_space is not None
        if limited:
            # this is a bit conservative, since some of this allocated_size()
            # has already been written to disk, where it will show up in
            # get_available_space.
            remaining_space -= self.allocated_size()

        # fill alreadygot with all shares that we have, not just the ones
        # they asked about: this will save them a lot of work. Add or update
        # leases for all of them: if they want us to hold shares for this
        # file, they'll want us to hold leases for this file.
        for (shnum, fn) in self._get_bucket_shares(storage_index):
            alreadygot.add(shnum)
            sf = ShareFile(fn)
            sf.add_or_renew_lease(lease_info)

        # self.readonly_storage causes remaining_space=0

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
                bw = BucketWriter(self, incominghome, finalhome,
                                  max_space_per_bucket, lease_info, canary)
                if self.no_storage:
                    bw.throw_out_all_data = True
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

    def remote_add_lease(self, storage_index, renew_secret, cancel_secret,
                         owner_num=1):
        start = time.time()
        self.count("add-lease")
        new_expire_time = time.time() + 31*24*60*60
        lease_info = LeaseInfo(owner_num,
                               renew_secret, cancel_secret,
                               new_expire_time, self.my_nodeid)
        for sf in self._iter_share_files(storage_index):
            sf.add_or_renew_lease(lease_info)
        self.add_latency("add-lease", time.time() - start)
        return None

    def remote_renew_lease(self, storage_index, renew_secret):
        start = time.time()
        self.count("renew")
        new_expire_time = time.time() + 31*24*60*60
        found_buckets = False
        for sf in self._iter_share_files(storage_index):
            found_buckets = True
            sf.renew_lease(renew_secret, new_expire_time)
        self.add_latency("renew", time.time() - start)
        if not found_buckets:
            raise IndexError("no such lease to renew")

    def remote_cancel_lease(self, storage_index, cancel_secret):
        start = time.time()
        self.count("cancel")

        total_space_freed = 0
        found_buckets = False
        for sf in self._iter_share_files(storage_index):
            # note: if we can't find a lease on one share, we won't bother
            # looking in the others. Unless something broke internally
            # (perhaps we ran out of disk space while adding a lease), the
            # leases on all shares will be identical.
            found_buckets = True
            # this raises IndexError if the lease wasn't present XXXX
            total_space_freed += sf.cancel_lease(cancel_secret)

        if found_buckets:
            storagedir = os.path.join(self.sharedir,
                                      storage_index_to_dir(storage_index))
            if not os.listdir(storagedir):
                os.rmdir(storagedir)

        if self.stats_provider:
            self.stats_provider.count('storage_server.bytes_freed',
                                      total_space_freed)
        self.add_latency("cancel", time.time() - start)
        if not found_buckets:
            raise IndexError("no such storage index")

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

    def remote_get_buckets(self, storage_index):
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

    def get_leases(self, storage_index):
        """Provide an iterator that yields all of the leases attached to this
        bucket. Each lease is returned as a tuple of (owner_num,
        renew_secret, cancel_secret, expiration_time).

        This method is not for client use.
        """

        # since all shares get the same lease data, we just grab the leases
        # from the first share
        try:
            shnum, filename = self._get_bucket_shares(storage_index).next()
            sf = ShareFile(filename)
            return sf.iter_leases()
        except StopIteration:
            return iter([])

    def remote_slot_testv_and_readv_and_writev(self, storage_index,
                                               secrets,
                                               test_and_write_vectors,
                                               read_vector):
        start = time.time()
        self.count("writev")
        si_s = si_b2a(storage_index)
        lp = log.msg("storage: slot_writev %s" % si_s)
        si_dir = storage_index_to_dir(storage_index)
        (write_enabler, renew_secret, cancel_secret) = secrets
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

        ownerid = 1 # TODO
        expire_time = time.time() + 31*24*60*60   # one month
        lease_info = LeaseInfo(ownerid,
                               renew_secret, cancel_secret,
                               expire_time, self.my_nodeid)

        if testv_is_good:
            # now apply the write vectors
            for sharenum in test_and_write_vectors:
                (testv, datav, new_length) = test_and_write_vectors[sharenum]
                if new_length == 0:
                    if sharenum in shares:
                        shares[sharenum].unlink()
                else:
                    if sharenum not in shares:
                        # allocate a new share
                        allocated_size = 2000 # arbitrary, really
                        share = self._allocate_slot_share(bucketdir, secrets,
                                                          sharenum,
                                                          allocated_size,
                                                          owner_num=0)
                        shares[sharenum] = share
                    shares[sharenum].writev(datav, new_length)
                    # and update the lease
                    shares[sharenum].add_or_renew_lease(lease_info)

            if new_length == 0:
                # delete empty bucket directories
                if not os.listdir(bucketdir):
                    os.rmdir(bucketdir)


        # all done
        self.add_latency("writev", time.time() - start)
        return (testv_is_good, read_data)

    def _allocate_slot_share(self, bucketdir, secrets, sharenum,
                             allocated_size, owner_num=0):
        (write_enabler, renew_secret, cancel_secret) = secrets
        my_nodeid = self.my_nodeid
        fileutil.make_dirs(bucketdir)
        filename = os.path.join(bucketdir, "%d" % sharenum)
        share = create_mutable_sharefile(filename, my_nodeid, write_enabler,
                                         self)
        return share

    def remote_slot_readv(self, storage_index, shares, readv):
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

    def remote_advise_corrupt_share(self, share_type, storage_index, shnum,
                                    reason):
        fileutil.make_dirs(self.corruption_advisory_dir)
        now = time_format.iso_utc(sep="T")
        si_s = base32.b2a(storage_index)
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

