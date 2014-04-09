
import struct, os.path

from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import IStorageBackend, IShareSet
from allmydata.util import fileutil, log
from allmydata.storage.common import si_b2a, si_a2b, NUM_RE, \
     UnknownMutableContainerVersionError, UnknownImmutableContainerVersionError
from allmydata.storage.bucket import BucketWriter
from allmydata.storage.backends.base import Backend, ShareSet
from allmydata.storage.backends.disk.immutable import load_immutable_disk_share, create_immutable_disk_share
from allmydata.storage.backends.disk.mutable import load_mutable_disk_share, create_mutable_disk_share
from allmydata.mutable.layout import MUTABLE_MAGIC


# storage/
# storage/shares/incoming
#   incoming/ holds temp dirs named $PREFIX/$STORAGEINDEX/$SHNUM which will
#   be moved to storage/shares/$PREFIX/$STORAGEINDEX/$SHNUM upon success
# storage/shares/$PREFIX/$STORAGEINDEX
# storage/shares/$PREFIX/$STORAGEINDEX/$SHNUM

# where "$PREFIX" denotes the first 10 bits worth of $STORAGEINDEX (that's 2
# base-32 chars).


def si_si2dir(startdir, storage_index):
    sia = si_b2a(storage_index)
    return os.path.join(startdir, sia[:2], sia)

def get_disk_share(home, storage_index=None, shnum=None):
    f = open(home, 'rb')
    try:
        prefix = f.read(len(MUTABLE_MAGIC))
    finally:
        f.close()

    if prefix == MUTABLE_MAGIC:
        return load_mutable_disk_share(home, storage_index, shnum)
    else:
        # assume it's immutable
        return load_immutable_disk_share(home, storage_index, shnum)


def configure_disk_backend(storedir, config):
    readonly = config.get_config("storage", "readonly", False, boolean=True)
    reserved_space = config.get_config_size("storage", "reserved_space", "0")

    return DiskBackend(storedir, readonly, reserved_space)


class DiskBackend(Backend):
    implements(IStorageBackend)

    def __init__(self, storedir, readonly=False, reserved_space=0):
        Backend.__init__(self)
        self._storedir = storedir
        self._readonly = readonly
        self._reserved_space = int(reserved_space)
        self._sharedir = os.path.join(self._storedir, 'shares')
        fileutil.make_dirs(self._sharedir)
        self._incomingdir = os.path.join(self._sharedir, 'incoming')
        self._clean_incomplete()
        if self._reserved_space and (self.get_available_space() is None):
            log.msg("warning: [storage]reserved_space= is set, but this platform does not support an API to get disk statistics (statvfs(2) or GetDiskFreeSpaceEx), so this reservation cannot be honored",
                    umid="0wZ27w", level=log.UNUSUAL)

    def _clean_incomplete(self):
        fileutil.rm_dir(self._incomingdir)
        fileutil.make_dirs(self._incomingdir)

    def get_sharesets_for_prefix(self, prefix):
        prefixdir = os.path.join(self._sharedir, prefix)
        sharesets = [self.get_shareset(si_a2b(si_s))
                     for si_s in sorted(fileutil.listdir(prefixdir))]
        return defer.succeed(sharesets)

    def get_shareset(self, storage_index):
        sharehomedir = si_si2dir(self._sharedir, storage_index)
        incominghomedir = si_si2dir(self._incomingdir, storage_index)
        return DiskShareSet(storage_index, sharehomedir, incominghomedir)

    def fill_in_space_stats(self, stats):
        stats['storage_server.reserved_space'] = self._reserved_space
        try:
            disk = fileutil.get_disk_stats(self._sharedir, self._reserved_space)
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

        if self._readonly:
            stats['storage_server.disk_avail'] = 0
            writeable = False

        stats['storage_server.accepting_immutable_shares'] = int(writeable)

    def get_available_space(self):
        if self._readonly:
            return 0
        try:
            return fileutil.get_available_space(self._sharedir, self._reserved_space)
        except EnvironmentError:
            return 0

    def must_use_tubid_as_permutation_seed(self):
        # A disk backend with existing shares must assume that it was around before #466,
        # so must use its TubID as a permutation-seed.
        return bool(set(fileutil.listdir(self._sharedir)) - set(["incoming"]))


class DiskShareSet(ShareSet):
    implements(IShareSet)

    def __init__(self, storage_index, sharehomedir, incominghomedir=None):
        ShareSet.__init__(self, storage_index)
        self._sharehomedir = sharehomedir
        self._incominghomedir = incominghomedir

    def get_overhead(self):
        return (fileutil.get_used_space(self._sharehomedir) +
                fileutil.get_used_space(self._incominghomedir))

    def get_shares(self):
        si = self.get_storage_index()
        shares = {}
        corrupted = set()
        for shnumstr in fileutil.listdir(self._sharehomedir, filter=NUM_RE):
            shnum = int(shnumstr)
            sharefile = os.path.join(self._sharehomedir, shnumstr)
            try:
                shares[shnum] = get_disk_share(sharefile, si, shnum)
            except (UnknownMutableContainerVersionError,
                    UnknownImmutableContainerVersionError,
                    struct.error):
                corrupted.add(shnum)

        valid = [shares[shnum] for shnum in sorted(shares.keys())]
        return defer.succeed( (valid, corrupted) )

    def get_share(self, shnum):
        return get_disk_share(os.path.join(self._sharehomedir, str(shnum)),
                              self.get_storage_index(), shnum)

    def delete_share(self, shnum):
        fileutil.remove(os.path.join(self._sharehomedir, str(shnum)))
        return defer.succeed(None)

    def has_incoming(self, shnum):
        if self._incominghomedir is None:
            return False
        return os.path.exists(os.path.join(self._incominghomedir, str(shnum)))

    def make_bucket_writer(self, account, shnum, allocated_data_length, canary):
        finalhome = os.path.join(self._sharehomedir, str(shnum))
        incominghome = os.path.join(self._incominghomedir, str(shnum))
        immsh = create_immutable_disk_share(incominghome, finalhome, allocated_data_length,
                                            self.get_storage_index(), shnum)
        bw = BucketWriter(account, immsh, canary)
        return bw

    def _create_mutable_share(self, account, shnum, write_enabler):
        fileutil.make_dirs(self._sharehomedir)
        sharehome = os.path.join(self._sharehomedir, str(shnum))
        serverid = account.server.get_serverid()
        return create_mutable_disk_share(sharehome, serverid, write_enabler,
                                         self.get_storage_index(), shnum, parent=account.server)

    def _clean_up_after_unlink(self):
        fileutil.rmdir_if_empty(self._sharehomedir)

    def _get_sharedir(self):
        return self._sharehomedir
