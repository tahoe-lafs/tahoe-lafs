"""
Ported to Python 3.
"""
from __future__ import annotations

from future.utils import bytes_to_native_str
from typing import Iterable, Any

import os, re

from foolscap.api import Referenceable
from foolscap.ipb import IRemoteReference
from twisted.application import service
from twisted.internet import reactor

from zope.interface import implementer
from allmydata.interfaces import RIStorageServer, IStatsProducer
from allmydata.util import fileutil, idlib, log, time_format
import allmydata # for __full_version__

from allmydata.storage.common import si_b2a, si_a2b, storage_index_to_dir
_pyflakes_hush = [si_b2a, si_a2b, storage_index_to_dir] # re-exported
from allmydata.storage.lease import LeaseInfo
from allmydata.storage.mutable import MutableShareFile, EmptyShare, \
     create_mutable_sharefile
from allmydata.mutable.layout import MAX_MUTABLE_SHARE_SIZE
from allmydata.storage.immutable import (
    ShareFile, BucketWriter, BucketReader, FoolscapBucketWriter,
    FoolscapBucketReader,
)
from allmydata.storage.crawler import BucketCountingCrawler
from allmydata.storage.expirer import LeaseCheckingCrawler

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


# Number of seconds to add to expiration time on lease renewal.
# For now it's not actually configurable, but maybe someday.
DEFAULT_RENEWAL_TIME = 31 * 24 * 60 * 60


@implementer(IStatsProducer)
class StorageServer(service.MultiService):
    """
    Implement the business logic for the storage server.
    """
    # The type in Twisted for services is wrong in 22.10...
    # https://github.com/twisted/twisted/issues/10135
    name = 'storage'  # type: ignore[assignment]
    # only the tests change this to anything else
    LeaseCheckerClass = LeaseCheckingCrawler

    def __init__(self, storedir, nodeid, reserved_space=0,
                 discard_storage=False, readonly_storage=False,
                 stats_provider=None,
                 expiration_enabled=False,
                 expiration_mode="age",
                 expiration_override_lease_duration=None,
                 expiration_cutoff_date=None,
                 expiration_sharetypes=("mutable", "immutable"),
                 clock=reactor):
        service.MultiService.__init__(self)
        assert isinstance(nodeid, bytes)
        assert len(nodeid) == 20
        assert isinstance(nodeid, bytes)
        self.my_nodeid = nodeid
        self.storedir = storedir
        sharedir = os.path.join(storedir, "shares")
        fileutil.make_dirs(sharedir)
        self.sharedir = sharedir
        self.corruption_advisory_dir = os.path.join(storedir,
                                                    "corruption-advisories")
        fileutil.make_dirs(self.corruption_advisory_dir)
        self.reserved_space = int(reserved_space)
        self.no_storage = discard_storage
        self.readonly_storage = readonly_storage
        self.stats_provider = stats_provider
        if self.stats_provider:
            self.stats_provider.register_producer(self)
        self.incomingdir = os.path.join(sharedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)
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

        statefile = os.path.join(self.storedir, "lease_checker.state")
        historyfile = os.path.join(self.storedir, "lease_checker.history")
        klass = self.LeaseCheckerClass
        self.lease_checker = klass(self, statefile, historyfile,
                                   expiration_enabled, expiration_mode,
                                   expiration_override_lease_duration,
                                   expiration_cutoff_date,
                                   expiration_sharetypes)
        self.lease_checker.setServiceParent(self)
        self._clock = clock

        # Map in-progress filesystem path -> BucketWriter:
        self._bucket_writers = {}  # type: Dict[str,BucketWriter]

        # These callables will be called with BucketWriters that closed:
        self._call_on_bucket_writer_close = []

    def stopService(self):
        # Cancel any in-progress uploads:
        for bw in list(self._bucket_writers.values()):
            bw.disconnected()
        return service.MultiService.stopService(self)

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
        for bw in self._bucket_writers.values():
            space += bw.allocated_size()
        return space

    def get_version(self):
        remaining_space = self.get_available_space()
        if remaining_space is None:
            # We're on a platform that has no API to get disk stats.
            remaining_space = 2**64

        # Unicode strings might be nicer, but for now sticking to bytes since
        # this is what the wire protocol has always been.
        version = { b"http://allmydata.org/tahoe/protocols/storage/v1" :
                    { b"maximum-immutable-share-size": remaining_space,
                      b"maximum-mutable-share-size": MAX_MUTABLE_SHARE_SIZE,
                      b"available-space": remaining_space,
                      b"tolerates-immutable-read-overrun": True,
                      b"delete-mutable-shares-with-zero-length-writev": True,
                      b"fills-holes-with-zero-bytes": True,
                      b"prevents-read-past-end-of-share-data": True,
                      },
                    b"application-version": allmydata.__full_version__.encode("utf-8"),
                    }
        return version

    def allocate_buckets(self, storage_index,
                          renew_secret, cancel_secret,
                          sharenums, allocated_size,
                          owner_num=0, renew_leases=True):
        """
        Generic bucket allocation API.

        :param bool renew_leases: If and only if this is ``True`` then renew a
            secret-matching lease on (or, if none match, add a new lease to)
            existing shares in this bucket.  Any *new* shares are given a new
            lease regardless.
        """
        # owner_num is not for clients to set, but rather it should be
        # curried into the PersonalStorageServer instance that is dedicated
        # to a particular owner.
        start = self._clock.seconds()
        self.count("allocate")
        alreadygot = {}
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_dir = storage_index_to_dir(storage_index)
        si_s = si_b2a(storage_index)

        log.msg("storage: allocate_buckets %r" % si_s)

        # in this implementation, the lease information (including secrets)
        # goes into the share files themselves. It could also be put into a
        # separate database. Note that the lease should not be added until
        # the BucketWriter has been closed.
        expire_time = self._clock.seconds() + DEFAULT_RENEWAL_TIME
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
        # self.readonly_storage causes remaining_space <= 0

        # fill alreadygot with all shares that we have, not just the ones
        # they asked about: this will save them a lot of work. Add or update
        # leases for all of them: if they want us to hold shares for this
        # file, they'll want us to hold leases for this file.
        for (shnum, fn) in self.get_shares(storage_index):
            alreadygot[shnum] = ShareFile(fn)
        if renew_leases:
            self._add_or_renew_leases(alreadygot.values(), lease_info)

        for shnum in sharenums:
            incominghome = os.path.join(self.incomingdir, si_dir, "%d" % shnum)
            finalhome = os.path.join(self.sharedir, si_dir, "%d" % shnum)
            if os.path.exists(finalhome):
                # great! we already have it. easy.
                pass
            elif os.path.exists(incominghome):
                # For Foolscap we don't create BucketWriters for shnums that
                # have a partial share (in incoming/), so if a second upload
                # occurs while the first is still in progress, the second
                # uploader will use different storage servers.
                pass
            elif (not limited) or (remaining_space >= max_space_per_bucket):
                # ok! we need to create the new share file.
                bw = BucketWriter(self, incominghome, finalhome,
                                  max_space_per_bucket, lease_info,
                                  clock=self._clock)
                if self.no_storage:
                    # Really this should be done by having a separate class for
                    # this situation; see
                    # https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3862
                    bw.throw_out_all_data = True
                bucketwriters[shnum] = bw
                self._bucket_writers[incominghome] = bw
                if limited:
                    remaining_space -= max_space_per_bucket
            else:
                # bummer! not enough space to accept this bucket
                pass

        if bucketwriters:
            fileutil.make_dirs(os.path.join(self.sharedir, si_dir))

        self.add_latency("allocate", self._clock.seconds() - start)
        return set(alreadygot), bucketwriters

    def _iter_share_files(self, storage_index):
        for shnum, filename in self.get_shares(storage_index):
            with open(filename, 'rb') as f:
                header = f.read(32)
            if MutableShareFile.is_valid_header(header):
                sf = MutableShareFile(filename, self)
                # note: if the share has been migrated, the renew_lease()
                # call will throw an exception, with information to help the
                # client update the lease.
            elif ShareFile.is_valid_header(header):
                sf = ShareFile(filename)
            else:
                continue # non-sharefile
            yield sf

    def add_lease(self, storage_index, renew_secret, cancel_secret, owner_num=1):
        start = self._clock.seconds()
        self.count("add-lease")
        new_expire_time = self._clock.seconds() + DEFAULT_RENEWAL_TIME
        lease_info = LeaseInfo(owner_num,
                               renew_secret, cancel_secret,
                               new_expire_time, self.my_nodeid)
        self._add_or_renew_leases(
            self._iter_share_files(storage_index),
            lease_info,
        )
        self.add_latency("add-lease", self._clock.seconds() - start)
        return None

    def renew_lease(self, storage_index, renew_secret):
        start = self._clock.seconds()
        self.count("renew")
        new_expire_time = self._clock.seconds() + DEFAULT_RENEWAL_TIME
        found_buckets = False
        for sf in self._iter_share_files(storage_index):
            found_buckets = True
            sf.renew_lease(renew_secret, new_expire_time)
        self.add_latency("renew", self._clock.seconds() - start)
        if not found_buckets:
            raise IndexError("no such lease to renew")

    def bucket_writer_closed(self, bw, consumed_size):
        if self.stats_provider:
            self.stats_provider.count('storage_server.bytes_added', consumed_size)
        del self._bucket_writers[bw.incominghome]
        for handler in self._call_on_bucket_writer_close:
            handler(bw)

    def register_bucket_writer_close_handler(self, handler):
        """
        The handler will be called with any ``BucketWriter`` that closes.
        """
        self._call_on_bucket_writer_close.append(handler)

    def get_shares(self, storage_index) -> Iterable[tuple[int, str]]:
        """
        Return an iterable of (shnum, pathname) tuples for files that hold
        shares for this storage_index. In each tuple, 'shnum' will always be
        the integer form of the last component of 'pathname'.
        """
        storagedir = os.path.join(self.sharedir, storage_index_to_dir(storage_index))
        try:
            for f in os.listdir(storagedir):
                if NUM_RE.match(f):
                    filename = os.path.join(storagedir, f)
                    yield (int(f), filename)
        except OSError:
            # Commonly caused by there being no buckets at all.
            pass

    def get_buckets(self, storage_index):
        """
        Get ``BucketReaders`` for an immutable.
        """
        start = self._clock.seconds()
        self.count("get")
        si_s = si_b2a(storage_index)
        log.msg("storage: get_buckets %r" % si_s)
        bucketreaders = {} # k: sharenum, v: BucketReader
        for shnum, filename in self.get_shares(storage_index):
            bucketreaders[shnum] = BucketReader(self, filename,
                                                storage_index, shnum)
        self.add_latency("get", self._clock.seconds() - start)
        return bucketreaders

    def get_leases(self, storage_index):
        """Provide an iterator that yields all of the leases attached to this
        bucket. Each lease is returned as a LeaseInfo instance.

        This method is not for client use.

        :note: Only for immutable shares.
        """
        # since all shares get the same lease data, we just grab the leases
        # from the first share
        try:
            shnum, filename = next(self.get_shares(storage_index))
            sf = ShareFile(filename)
            return sf.get_leases()
        except StopIteration:
            return iter([])

    def get_slot_leases(self, storage_index):
        """
        This method is not for client use.

        :note: Only for mutable shares.

        :return: An iterable of the leases attached to this slot.
        """
        for _, share_filename in self.get_shares(storage_index):
            share = MutableShareFile(share_filename)
            return share.get_leases()
        return []

    def _collect_mutable_shares_for_storage_index(self, bucketdir, write_enabler, si_s):
        """
        Gather up existing mutable shares for the given storage index.

        :param bytes bucketdir: The filesystem path containing shares for the
            given storage index.

        :param bytes write_enabler: The write enabler secret for the shares.

        :param bytes si_s: The storage index in encoded (base32) form.

        :raise BadWriteEnablerError: If the write enabler is not correct for
            any of the collected shares.

        :return dict[int, MutableShareFile]: The collected shares in a mapping
            from integer share numbers to ``MutableShareFile`` instances.
        """
        shares = {}
        if os.path.isdir(bucketdir):
            # shares exist if there is a file for them
            for sharenum_s in os.listdir(bucketdir):
                try:
                    sharenum = int(sharenum_s)
                except ValueError:
                    continue
                filename = os.path.join(bucketdir, sharenum_s)
                msf = MutableShareFile(filename, self)
                msf.check_write_enabler(write_enabler, si_s)
                shares[sharenum] = msf
        return shares

    def _evaluate_test_vectors(self, test_and_write_vectors, shares):
        """
        Execute test vectors against share data.

        :param test_and_write_vectors: See
            ``allmydata.interfaces.TestAndWriteVectorsForShares``.

        :param dict[int, MutableShareFile] shares: The shares against which to
            execute the vectors.

        :return bool: ``True`` if and only if all of the test vectors succeed
            against the given shares.
        """
        for sharenum in test_and_write_vectors:
            (testv, datav, new_length) = test_and_write_vectors[sharenum]
            if sharenum in shares:
                if not shares[sharenum].check_testv(testv):
                    self.log("testv failed: [%d]: %r" % (sharenum, testv))
                    return False
            else:
                # compare the vectors against an empty share, in which all
                # reads return empty strings.
                if not EmptyShare().check_testv(testv):
                    self.log("testv failed (empty): [%d] %r" % (sharenum,
                                                                testv))
                    return False
        return True

    def _evaluate_read_vectors(self, read_vector, shares):
        """
        Execute read vectors against share data.

        :param read_vector: See ``allmydata.interfaces.ReadVector``.

        :param dict[int, MutableShareFile] shares: The shares against which to
            execute the vector.

        :return dict[int, bytes]: The data read from the shares.
        """
        read_data = {}
        for sharenum, share in shares.items():
            read_data[sharenum] = share.readv(read_vector)
        return read_data

    def _evaluate_write_vectors(self, bucketdir, secrets, test_and_write_vectors, shares):
        """
        Execute write vectors against share data.

        :param bytes bucketdir: The parent directory holding the shares.  This
            is removed if the last share is removed from it.  If shares are
            created, they are created in it.

        :param secrets: A tuple of ``WriteEnablerSecret``,
            ``LeaseRenewSecret``, and ``LeaseCancelSecret``.  These secrets
            are used to initialize new shares.

        :param test_and_write_vectors: See
            ``allmydata.interfaces.TestAndWriteVectorsForShares``.

        :param dict[int, MutableShareFile]: The shares against which to
            execute the vectors.

        :return dict[int, MutableShareFile]: The shares which still exist
            after applying the vectors.
        """
        remaining_shares = {}

        for sharenum in test_and_write_vectors:
            (testv, datav, new_length) = test_and_write_vectors[sharenum]
            if new_length == 0:
                if sharenum in shares:
                    shares[sharenum].unlink()
            else:
                if sharenum not in shares:
                    # allocate a new share
                    share = self._allocate_slot_share(bucketdir, secrets,
                                                      sharenum,
                                                      owner_num=0)
                    shares[sharenum] = share
                shares[sharenum].writev(datav, new_length)
                remaining_shares[sharenum] = shares[sharenum]

            if new_length == 0:
                # delete bucket directories that exist but are empty.  They
                # might not exist if a client showed up and asked us to
                # truncate a share we weren't even holding.
                if os.path.exists(bucketdir) and [] == os.listdir(bucketdir):
                    os.rmdir(bucketdir)
        return remaining_shares

    def _make_lease_info(self, renew_secret, cancel_secret):
        """
        :return LeaseInfo: Information for a new lease for a share.
        """
        ownerid = 1 # TODO
        expire_time = self._clock.seconds() + DEFAULT_RENEWAL_TIME
        lease_info = LeaseInfo(ownerid,
                               renew_secret, cancel_secret,
                               expire_time, self.my_nodeid)
        return lease_info

    def _add_or_renew_leases(self, shares, lease_info):
        """
        Put the given lease onto the given shares.

        :param Iterable[Union[MutableShareFile, ShareFile]] shares: The shares
            to put the lease onto.

        :param LeaseInfo lease_info: The lease to put on the shares.
        """
        for share in shares:
            share.add_or_renew_lease(self.get_available_space(), lease_info)

    def slot_testv_and_readv_and_writev(  # type: ignore # warner/foolscap#78
            self,
            storage_index,
            secrets,
            test_and_write_vectors,
            read_vector,
            renew_leases=True,
    ):
        """
        Read data from shares and conditionally write some data to them.

        :param bool renew_leases: If and only if this is ``True`` and the test
            vectors pass then shares mentioned in ``test_and_write_vectors``
            that still exist after the changes are made will also have a
            secret-matching lease renewed (or, if none match, a new lease
            added).

        See ``allmydata.interfaces.RIStorageServer`` for details about other
        parameters and return value.
        """
        start = self._clock.seconds()
        self.count("writev")
        si_s = si_b2a(storage_index)
        log.msg("storage: slot_writev %r" % si_s)
        si_dir = storage_index_to_dir(storage_index)
        (write_enabler, renew_secret, cancel_secret) = secrets
        bucketdir = os.path.join(self.sharedir, si_dir)

        # If collection succeeds we know the write_enabler is good for all
        # existing shares.
        shares = self._collect_mutable_shares_for_storage_index(
            bucketdir,
            write_enabler,
            si_s,
        )

        # Now evaluate test vectors.
        testv_is_good = self._evaluate_test_vectors(
            test_and_write_vectors,
            shares,
        )

        # now gather the read vectors, before we do any writes
        read_data = self._evaluate_read_vectors(
            read_vector,
            shares,
        )

        if testv_is_good:
            # now apply the write vectors
            remaining_shares = self._evaluate_write_vectors(
                bucketdir,
                secrets,
                test_and_write_vectors,
                shares,
            )
            if renew_leases:
                lease_info = self._make_lease_info(renew_secret, cancel_secret)
                self._add_or_renew_leases(remaining_shares.values(), lease_info)

        # all done
        self.add_latency("writev", self._clock.seconds() - start)
        return (testv_is_good, read_data)

    def _allocate_slot_share(self, bucketdir, secrets, sharenum,
                             owner_num=0):
        (write_enabler, renew_secret, cancel_secret) = secrets
        my_nodeid = self.my_nodeid
        fileutil.make_dirs(bucketdir)
        filename = os.path.join(bucketdir, "%d" % sharenum)
        share = create_mutable_sharefile(filename, my_nodeid, write_enabler,
                                         self)
        return share

    def enumerate_mutable_shares(self, storage_index: bytes) -> set[int]:
        """Return all share numbers for the given mutable."""
        si_dir = storage_index_to_dir(storage_index)
        # shares exist if there is a file for them
        bucketdir = os.path.join(self.sharedir, si_dir)
        if not os.path.isdir(bucketdir):
            return set()
        result = set()
        for sharenum_s in os.listdir(bucketdir):
            try:
                result.add(int(sharenum_s))
            except ValueError:
                continue
        return result

    def slot_readv(self, storage_index, shares, readv):
        start = self._clock.seconds()
        self.count("readv")
        si_s = si_b2a(storage_index)
        lp = log.msg("storage: slot_readv %r %r" % (si_s, shares),
                     facility="tahoe.storage", level=log.OPERATIONAL)
        si_dir = storage_index_to_dir(storage_index)
        # shares exist if there is a file for them
        bucketdir = os.path.join(self.sharedir, si_dir)
        if not os.path.isdir(bucketdir):
            self.add_latency("readv", self._clock.seconds() - start)
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
        log.msg("returning shares %s" % (list(datavs.keys()),),
                facility="tahoe.storage", level=log.NOISY, parent=lp)
        self.add_latency("readv", self._clock.seconds() - start)
        return datavs

    def _share_exists(self, storage_index, shnum):
        """
        Check local share storage to see if a matching share exists.

        :param bytes storage_index: The storage index to inspect.
        :param int shnum: The share number to check for.

        :return bool: ``True`` if a share with the given number exists at the
            given storage index, ``False`` otherwise.
        """
        for existing_sharenum, ignored in self.get_shares(storage_index):
            if existing_sharenum == shnum:
                return True
        return False

    def advise_corrupt_share(self, share_type, storage_index, shnum,
                             reason):
        # Previously this had to be bytes for legacy protocol backwards
        # compatibility reasons. Now that Foolscap layer has been abstracted
        # out, we can probably refactor this to be unicode...
        assert isinstance(share_type, bytes)
        assert isinstance(reason, bytes), "%r is not bytes" % (reason,)

        si_s = si_b2a(storage_index)

        if not self._share_exists(storage_index, shnum):
            log.msg(
                format=(
                    "discarding client corruption claim for %(si)s/%(shnum)d "
                    "which I do not have"
                ),
                si=si_s,
                shnum=shnum,
            )
            return

        log.msg(format=("client claims corruption in (%(share_type)s) " +
                        "%(si)s-%(shnum)d: %(reason)s"),
                share_type=share_type, si=si_s, shnum=shnum, reason=reason,
                level=log.SCARY, umid="SGx2fA")

        report = render_corruption_report(share_type, si_s, shnum, reason)
        if len(report) > self.get_available_space():
            return None

        now = time_format.iso_utc(sep="T")
        report_path = get_corruption_report_path(
            self.corruption_advisory_dir,
            now,
            si_s,
            shnum,
        )
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)

        return None

    def get_immutable_share_length(self, storage_index: bytes, share_number: int) -> int:
        """Returns the length (in bytes) of an immutable."""
        si_dir = storage_index_to_dir(storage_index)
        path = os.path.join(self.sharedir, si_dir, str(share_number))
        return ShareFile(path).get_length()

    def get_mutable_share_length(self, storage_index: bytes, share_number: int) -> int:
        """Returns the length (in bytes) of a mutable."""
        si_dir = storage_index_to_dir(storage_index)
        path = os.path.join(self.sharedir, si_dir, str(share_number))
        if not os.path.exists(path):
            raise KeyError("No such storage index or share number")
        return MutableShareFile(path).get_length()


@implementer(RIStorageServer)
class FoolscapStorageServer(Referenceable):  # type: ignore # warner/foolscap#78
    """
    A filesystem-based implementation of ``RIStorageServer``.

    For Foolscap, BucketWriter lifetime is tied to connection: when
    disconnection happens, the BucketWriters are removed.
    """
    name = 'storage'

    def __init__(self, storage_server):  # type: (StorageServer) -> None
        self._server = storage_server

        # Canaries and disconnect markers for BucketWriters created via Foolscap:
        self._bucket_writer_disconnect_markers : dict[BucketWriter, tuple[IRemoteReference, Any]] = {}

        self._server.register_bucket_writer_close_handler(self._bucket_writer_closed)

    def _bucket_writer_closed(self, bw):
        if bw in self._bucket_writer_disconnect_markers:
            canary, disconnect_marker = self._bucket_writer_disconnect_markers.pop(bw)
            canary.dontNotifyOnDisconnect(disconnect_marker)

    def remote_get_version(self):
        return self._server.get_version()

    def remote_allocate_buckets(self, storage_index,
                                renew_secret, cancel_secret,
                                sharenums, allocated_size,
                                canary, owner_num=0):
        """Foolscap-specific ``allocate_buckets()`` API."""
        alreadygot, bucketwriters = self._server.allocate_buckets(
            storage_index, renew_secret, cancel_secret, sharenums, allocated_size,
            owner_num=owner_num, renew_leases=True,
        )

        # Abort BucketWriters if disconnection happens.
        for bw in bucketwriters.values():
            disconnect_marker = canary.notifyOnDisconnect(bw.disconnected)
            self._bucket_writer_disconnect_markers[bw] = (canary, disconnect_marker)

        # Wrap BucketWriters with Foolscap adapter:
        bucketwriters = {
            k: FoolscapBucketWriter(bw)
            for (k, bw) in bucketwriters.items()
        }

        return alreadygot, bucketwriters

    def remote_add_lease(self, storage_index, renew_secret, cancel_secret,
                         owner_num=1):
        return self._server.add_lease(storage_index, renew_secret, cancel_secret)

    def remote_renew_lease(self, storage_index, renew_secret):
        return self._server.renew_lease(storage_index, renew_secret)

    def remote_get_buckets(self, storage_index):
        return {
            k: FoolscapBucketReader(bucket)
            for (k, bucket) in self._server.get_buckets(storage_index).items()
        }

    def remote_slot_testv_and_readv_and_writev(self, storage_index,
                                               secrets,
                                               test_and_write_vectors,
                                               read_vector):
        return self._server.slot_testv_and_readv_and_writev(
            storage_index,
            secrets,
            test_and_write_vectors,
            read_vector,
            renew_leases=True,
        )

    def remote_slot_readv(self, storage_index, shares, readv):
        return self._server.slot_readv(storage_index, shares, readv)

    def remote_advise_corrupt_share(self, share_type, storage_index, shnum,
                                    reason):
        return self._server.advise_corrupt_share(share_type, storage_index, shnum,
                                                 reason)


CORRUPTION_REPORT_FORMAT = """\
report: Share Corruption
type: {type}
storage_index: {storage_index}
share_number: {share_number}

{reason}

"""

def render_corruption_report(share_type, si_s, shnum, reason):
    """
    Create a string that explains a corruption report using freeform text.

    :param bytes share_type: The type of the share which the report is about.

    :param bytes si_s: The encoded representation of the storage index which
        the report is about.

    :param int shnum: The share number which the report is about.

    :param bytes reason: The reason given by the client for the corruption
        report.
    """
    return CORRUPTION_REPORT_FORMAT.format(
        type=bytes_to_native_str(share_type),
        storage_index=bytes_to_native_str(si_s),
        share_number=shnum,
        reason=bytes_to_native_str(reason),
    )

def get_corruption_report_path(base_dir, now, si_s, shnum):
    """
    Determine the path to which a certain corruption report should be written.

    :param str base_dir: The directory beneath which to construct the path.

    :param str now: The time of the report.

    :param str si_s: The encoded representation of the storage index which the
        report is about.

    :param int shnum: The share number which the report is about.

    :return str: A path to which the report can be written.
    """
    # windows can't handle colons in the filename
    return os.path.join(
        base_dir,
        ("%s--%s-%d" % (now, str(si_s, "utf-8"), shnum)).replace(":","")
    )
