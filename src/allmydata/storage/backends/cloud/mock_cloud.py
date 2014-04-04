
import os.path

from twisted.internet import defer, reactor

from allmydata.util.deferredutil import async_iterate

from zope.interface import implements

from allmydata.util.assertutil import _assert
from allmydata.storage.backends.base import ContainerItem, ContainerListing
from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     CloudServiceError, CommonContainerMixin, ContainerListMixin
from allmydata.util.time_format import iso_utc
from allmydata.util import fileutil


MAX_KEYS = 1000


def configure_mock_cloud_backend(storedir, config):
    from allmydata.storage.backends.cloud.cloud_backend import CloudBackend

    container = MockContainer(storedir)
    return CloudBackend(container)


def _not_implemented():
    raise NotImplementedError()

def hook_create_container():
    return defer.execute(_not_implemented)


class MockContainer(ContainerListMixin, CommonContainerMixin):
    implements(IContainer)
    """
    I represent a mock cloud container that stores its data in the local filesystem.
    I also keep track of the number of loads and stores.
    """

    def __init__(self, storagedir):
        self._storagedir = storagedir
        self.container_name = "MockContainer"
        self.ServiceError = CloudServiceError
        self._load_count = 0
        self._store_count = 0
        self._reactor = reactor
        fileutil.make_dirs(os.path.join(self._storagedir, "shares"))

    def __repr__(self):
        return ("<%s at %r>" % (self.__class__.__name__, self._storagedir,))

    def _create(self):
        return hook_create_container()

    def _delete(self):
        return defer.execute(_not_implemented)

    def _iterate_dirs(self):
        shares_dir = os.path.join(self._storagedir, "shares")
        for prefixstr in sorted(fileutil.listdir(shares_dir)):
            prefixkey = "shares/%s" % (prefixstr,)
            prefixdir = os.path.join(shares_dir, prefixstr)
            for sistr in sorted(fileutil.listdir(prefixdir)):
                sikey = "%s/%s" % (prefixkey, sistr)
                sidir = os.path.join(prefixdir, sistr)
                for shnumstr in sorted(fileutil.listdir(sidir)):
                    sharefile = os.path.join(sidir, shnumstr)
                    yield (sharefile, "%s/%s" % (sikey, shnumstr))

    def list_some_objects(self, **kwargs):
        return self._do_request('list objects', self._list_some_objects, **kwargs)

    def _list_some_objects(self, prefix='', marker=None, max_keys=None):
        if max_keys is None:
            max_keys = MAX_KEYS
        contents = []
        def _next_share(res):
            if res is None:
                return
            (sharefile, sharekey) = res
            # note that all strings are > None
            if sharekey.startswith(prefix) and sharekey > marker:
                stat_result = os.stat(sharefile)
                mtime_utc = iso_utc(stat_result.st_mtime, sep=' ')+'+00:00'
                item = ContainerItem(key=sharekey, modification_date=mtime_utc, etag="",
                                     size=stat_result.st_size, storage_class="STANDARD")
                contents.append(item)
            return len(contents) < max_keys

        d = async_iterate(_next_share, self._iterate_dirs())
        def _done(completed):
            contents.sort(key=lambda item: item.key)
            return ContainerListing(self.container_name, '', '', max_keys,
                                    is_truncated=str(not completed).lower(), contents=contents)
        d.addCallback(_done)
        return d

    def _get_path(self, object_name, must_exist=False):
        # This method is also called by tests.
        sharefile = os.path.join(self._storagedir, object_name)
        if must_exist and not os.path.exists(sharefile):
            raise self.ServiceError("", 404, "not found")
        return sharefile

    def _put_object(self, object_name, data, content_type, metadata):
        _assert(content_type == 'application/octet-stream', content_type=content_type)
        _assert(metadata == {}, metadata=metadata)
        sharefile = self._get_path(object_name)
        fileutil.make_dirs(os.path.dirname(sharefile))
        fileutil.write(sharefile, data)
        self._store_count += 1
        return defer.succeed(None)

    def _get_object(self, object_name):
        self._load_count += 1
        data = fileutil.read(self._get_path(object_name, must_exist=True))
        return defer.succeed(data)

    def _head_object(self, object_name):
        return defer.execute(_not_implemented)

    def _delete_object(self, object_name):
        fileutil.remove(self._get_path(object_name, must_exist=True))
        return defer.succeed(None)

    def reset_load_store_counts(self):
        self._load_count = 0
        self._store_count = 0

    def get_load_count(self):
        return self._load_count

    def get_store_count(self):
        return self._store_count
