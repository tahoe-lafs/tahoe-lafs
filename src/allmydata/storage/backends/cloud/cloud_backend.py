
import sys

from twisted.internet import defer

from zope.interface import implements
from allmydata.interfaces import IStorageBackend, IShareSet

from allmydata.node import InvalidValueError
from allmydata.util.assertutil import _assert
from allmydata.util.dictutil import NumDict
from allmydata.util.encodingutil import quote_output
from allmydata.storage.common import si_a2b, NUM_RE, CorruptStoredShareError
from allmydata.storage.bucket import BucketWriter
from allmydata.storage.backends.base import Backend, ShareSet
from allmydata.storage.backends.cloud.immutable import ImmutableCloudShareForReading, ImmutableCloudShareForWriting
from allmydata.storage.backends.cloud.mutable import MutableCloudShare
from allmydata.storage.backends.cloud.cloud_common import get_share_key, delete_chunks
from allmydata.mutable.layout import MUTABLE_MAGIC


CLOUD_INTERFACES = ("cloud.s3", "cloud.openstack", "cloud.googlestorage", "cloud.msazure")


def get_cloud_share(container, storage_index, shnum, total_size):
    key = get_share_key(storage_index, shnum)
    d = container.get_object(key)
    def _make_share(first_chunkdata):
        if first_chunkdata.startswith(MUTABLE_MAGIC):
            return MutableCloudShare(container, storage_index, shnum, total_size, first_chunkdata)
        else:
            # assume it's immutable
            return ImmutableCloudShareForReading(container, storage_index, shnum, total_size, first_chunkdata)
    d.addCallback(_make_share)
    return d


def configure_cloud_backend(storedir, config):
    if config.get_config("storage", "readonly", False, boolean=True):
        raise InvalidValueError("[storage]readonly is not supported by the cloud backend; "
                                "make the container read-only instead.")

    backendtype = config.get_config("storage", "backend", "disk")
    if backendtype == "s3":
        backendtype = "cloud.s3"

    if backendtype not in CLOUD_INTERFACES:
        raise InvalidValueError("%s is not supported by the cloud backend; it must be one of %s"
                                % (quote_output("[storage]backend = " + backendtype), CLOUD_INTERFACES) )

    pkgname = "allmydata.storage.backends." + backendtype
    __import__(pkgname)
    container = sys.modules[pkgname].configure_container(storedir, config)
    return CloudBackend(container)


class CloudBackend(Backend):
    implements(IStorageBackend)

    def __init__(self, container):
        Backend.__init__(self)
        self._container = container

        # set of (storage_index, shnum) of incoming shares
        self._incomingset = set()

    def get_sharesets_for_prefix(self, prefix):
        d = self._container.list_objects(prefix='shares/%s/' % (prefix,))
        def _get_sharesets(res):
            # XXX this enumerates all shares to get the set of SIs.
            # Is there a way to enumerate SIs more efficiently?
            si_strings = set()
            for item in res.contents:
                # XXX better error handling
                path = item.key.split('/')
                _assert(path[0:2] == ["shares", prefix], path=path, prefix=prefix)
                si_strings.add(path[2])

            # XXX we want this to be deterministic, so we return the sharesets sorted
            # by their si_strings, but we shouldn't need to explicitly re-sort them
            # because list_objects returns a sorted list.
            return [self.get_shareset(si_a2b(s)) for s in sorted(si_strings)]
        d.addCallback(_get_sharesets)
        return d

    def get_shareset(self, storage_index):
        return CloudShareSet(storage_index, self._get_lock(storage_index),
                             self._container, self._incomingset)

    def fill_in_space_stats(self, stats):
        # TODO: query space usage of container if supported.
        # TODO: query whether the container is read-only and set
        # accepting_immutable_shares accordingly.
        stats['storage_server.accepting_immutable_shares'] = 1

    def get_available_space(self):
        # TODO: query space usage of container if supported.
        return 2**64

    def create_container(self):
        return self._container.create()


class CloudShareSet(ShareSet):
    implements(IShareSet)

    def __init__(self, storage_index, lock, container, incomingset):
        ShareSet.__init__(self, storage_index, lock)
        self._container = container
        self._incomingset = incomingset
        self._key = get_share_key(storage_index)

    def get_overhead(self):
        return 0

    def _locked_get_shares(self):
        d = self._container.list_objects(prefix=self._key)
        def _get_shares(res):
            si = self.get_storage_index()
            shnum_to_total_size = NumDict()
            for item in res.contents:
                key = item.key
                _assert(key.startswith(self._key), key=key, self_key=self._key)
                path = key.split('/')
                if len(path) == 4:
                    (shnumstr, _, chunknumstr) = path[3].partition('.')
                    chunknumstr = chunknumstr or '0'
                    if NUM_RE.match(shnumstr) and NUM_RE.match(chunknumstr):
                        # The size is taken as the sum of sizes for all chunks, but for simplicity
                        # we don't check here that the individual chunk sizes match expectations.
                        # If they don't, that will cause an error on reading.
                        shnum_to_total_size.add_num(int(shnumstr), int(item.size))

            return defer.DeferredList([get_cloud_share(self._container, si, shnum, total_size)
                                       for (shnum, total_size) in shnum_to_total_size.items_sorted_by_key()],
                                      consumeErrors=True)
        d.addCallback(_get_shares)
        def _got_list(outcomes):
            # DeferredList gives us a list of (success, result) pairs, which we
            # convert to a pair (list of shares, set of corrupt shnums).
            shares = [share for (success, share) in outcomes if success]
            corrupted = set([f.value.shnum for (success, f) in outcomes
                             if not success and isinstance(f.value, CorruptStoredShareError)])
            return (shares, corrupted)
        d.addCallback(_got_list)
        return d

    def _locked_get_share(self, shnum):
        key = "%s%d" % (self._key, shnum)
        d = self._container.list_objects(prefix=key)
        def _get_share(res):
            total_size = 0
            for item in res.contents:
                total_size += item.size
            return get_cloud_share(self._container, self.get_storage_index(), shnum, total_size)
        d.addCallback(_get_share)
        return d

    def _locked_delete_share(self, shnum):
        key = "%s%d" % (self._key, shnum)
        return delete_chunks(self._container, key)

    def has_incoming(self, shnum):
        return (self.get_storage_index(), shnum) in self._incomingset

    def make_bucket_writer(self, account, shnum, allocated_data_length, canary):
        immsh = ImmutableCloudShareForWriting(self._container, self.get_storage_index(), shnum,
                                              allocated_data_length, self._incomingset)
        d = defer.succeed(None)
        d.addCallback(lambda ign: BucketWriter(account, immsh, canary))
        return d

    def _create_mutable_share(self, account, shnum, write_enabler):
        serverid = account.server.get_serverid()
        return MutableCloudShare.create_empty_share(self._container, serverid, write_enabler,
                                                    self.get_storage_index(), shnum, parent=account.server)

    def _clean_up_after_unlink(self):
        pass

    def _get_sharedir(self):
        # For use by tests, only with the mock cloud backend.
        # It is OK that _get_path doesn't exist on real container objects.
        return self._container._get_path(self._key)
