
import os.path

from twisted.internet import defer
from twisted.web.error import Error
from allmydata.util.deferredutil import async_iterate

from zope.interface import implements

from allmydata.storage.backends.cloud.cloud_common import IContainer, \
     ContainerRetryMixin, ContainerListMixin
from allmydata.util.time_format import iso_utc
from allmydata.util import fileutil


MAX_KEYS = 1000


def configure_mock_cloud_backend(storedir, config):
    from allmydata.storage.backends.cloud.cloud_backend import CloudBackend

    container = MockContainer(storedir)
    return CloudBackend(container)


class MockContainer(ContainerRetryMixin, ContainerListMixin):
    implements(IContainer)
    """
    I represent a mock cloud container that stores its data in the local filesystem.
    I also keep track of the number of loads and stores.
    """

    def __init__(self, storagedir):
        self._storagedir = storagedir
        self.container_name = "MockContainer"
        self.ServiceError = MockServiceError
        self._load_count = 0
        self._store_count = 0

        fileutil.make_dirs(os.path.join(self._storagedir, "shares"))

    def __repr__(self):
        return ("<%s at %r>" % (self.__class__.__name__, self._storagedir,))

    def _create(self):
        return defer.execute(self._not_implemented)

    def _delete(self):
        return defer.execute(self._not_implemented)

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

    def _list_some_objects(self, ign, prefix='', marker=None, max_keys=None):
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
            raise MockServiceError("", 404, "not found")
        return sharefile

    def _put_object(self, ign, object_name, data, content_type, metadata):
        assert content_type is None, content_type
        assert metadata == {}, metadata
        sharefile = self._get_path(object_name)
        fileutil.make_dirs(os.path.dirname(sharefile))
        fileutil.write(sharefile, data)
        self._store_count += 1
        return defer.succeed(None)

    def _get_object(self, ign, object_name):
        self._load_count += 1
        data = fileutil.read(self._get_path(object_name, must_exist=True))
        return defer.succeed(data)

    def _head_object(self, ign, object_name):
        return defer.execute(self._not_implemented)

    def _delete_object(self, ign, object_name):
        fileutil.remove(self._get_path(object_name, must_exist=True))
        return defer.succeed(None)

    def _not_implemented(self):
        raise NotImplementedError

    # methods that use error handling from ContainerRetryMixin

    def create(self):
        return self._do_request('create bucket', self._create, self.container_name)

    def delete(self):
        return self._do_request('delete bucket', self._delete, self.container_name)

    def list_some_objects(self, **kwargs):
        return self._do_request('list objects', self._list_some_objects, self.container_name, **kwargs)

    def put_object(self, object_name, data, content_type=None, metadata={}):
        return self._do_request('PUT object', self._put_object, self.container_name, object_name,
                                data, content_type, metadata)

    def get_object(self, object_name):
        return self._do_request('GET object', self._get_object, self.container_name, object_name)

    def head_object(self, object_name):
        return self._do_request('HEAD object', self._head_object, self.container_name, object_name)

    def delete_object(self, object_name):
        return self._do_request('DELETE object', self._delete_object, self.container_name, object_name)

    def reset_load_store_counts(self):
        self._load_count = 0
        self._store_count = 0

    def get_load_count(self):
        return self._load_count

    def get_store_count(self):
        return self._store_count


class MockServiceError(Error):
    """
    A error class similar to txaws' S3Error.
    """
    def __init__(self, xml_bytes, status, message=None, response=None, request_id="", host_id=""):
        Error.__init__(self, status, message, response)
        self.original = xml_bytes
        self.status = str(status)
        self.message = str(message)
        self.request_id = request_id
        self.host_id = host_id

    def get_error_code(self):
        return self.status

    def get_error_message(self):
        return self.message

    def parse(self, xml_bytes=""):
        raise NotImplementedError

    def has_error(self, errorString):
        raise NotImplementedError

    def get_error_codes(self):
        raise NotImplementedError

    def get_error_messages(self):
        raise NotImplementedError


# Originally from txaws.s3.model (under different class names), which was under the MIT / Expat licence.

class ContainerItem(object):
    """
    An item in a listing of cloud objects.
    """
    def __init__(self, key, modification_date, etag, size, storage_class,
                 owner=None):
        self.key = key
        self.modification_date = modification_date
        self.etag = etag
        self.size = size
        self.storage_class = storage_class
        self.owner = owner

    def __repr__(self):
        return "<ContainerItem %r>" % ({
                   "key": self.key,
                   "modification_date": self.modification_date,
                   "etag": self.etag,
                   "size": self.size,
                   "storage_class": self.storage_class,
                   "owner": self.owner,
               },)


class ContainerListing(object):
    def __init__(self, name, prefix, marker, max_keys, is_truncated,
                 contents=None, common_prefixes=None):
        self.name = name
        self.prefix = prefix
        self.marker = marker
        self.max_keys = max_keys
        self.is_truncated = is_truncated
        self.contents = contents
        self.common_prefixes = common_prefixes

    def __repr__(self):
        return "<ContainerListing %r>" % ({
                   "name": self.name,
                   "prefix": self.prefix,
                   "marker": self.marker,
                   "max_keys": self.max_keys,
                   "is_truncated": self.is_truncated,
                   "contents": self.contents,
                   "common_prefixes": self.common_prefixes,
               })
