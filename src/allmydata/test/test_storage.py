
import time, os.path, platform, re, simplejson, struct, itertools, urllib
from collections import deque
from cStringIO import StringIO
import thread

import mock
from twisted.trial import unittest

from twisted.internet import defer
from twisted.internet.task import Clock
from allmydata.util.deferredutil import for_items
from twisted.web.iweb import IBodyProducer, UNKNOWN_LENGTH
from twisted.web.http_headers import Headers
from twisted.protocols.ftp import FileConsumer
from twisted.web.client import ResponseDone

from twisted.python.failure import Failure
from foolscap.logging.log import OPERATIONAL, INFREQUENT, WEIRD
from foolscap.logging.web import LogEvent

from allmydata import interfaces
from allmydata.util.assertutil import precondition
from allmydata.util import fileutil, hashutil, base32, time_format
from allmydata.storage.server import StorageServer
from allmydata.storage.backends.null.null_backend import NullBackend
from allmydata.storage.backends.disk.disk_backend import DiskBackend
from allmydata.storage.backends.disk.immutable import load_immutable_disk_share, \
     create_immutable_disk_share, ImmutableDiskShare
from allmydata.storage.backends.disk.mutable import create_mutable_disk_share, MutableDiskShare
from allmydata.storage.backends.cloud.cloud_backend import CloudBackend
from allmydata.storage.backends.cloud.cloud_common import CloudError, CloudServiceError, \
     ContainerItem, ContainerListing
from allmydata.storage.backends.cloud.mutable import MutableCloudShare
from allmydata.storage.backends.cloud import mock_cloud, cloud_common
from allmydata.storage.backends.cloud.mock_cloud import MockContainer
from allmydata.storage.backends.cloud.openstack import openstack_container
from allmydata.storage.backends.cloud.googlestorage import googlestorage_container
from allmydata.storage.backends.cloud.msazure import msazure_container
from allmydata.storage.bucket import BucketWriter, BucketReader
from allmydata.storage.common import DataTooLargeError, storage_index_to_dir
from allmydata.storage.leasedb import SHARETYPE_IMMUTABLE, SHARETYPE_MUTABLE
from allmydata.storage.expiration import ExpirationPolicy
from allmydata.immutable.layout import WriteBucketProxy, WriteBucketProxy_v2, \
     ReadBucketProxy
from allmydata.mutable.layout import MDMFSlotWriteProxy, MDMFSlotReadProxy, \
                                     LayoutInvalid, MDMFSIGNABLEHEADER, \
                                     SIGNED_PREFIX, MDMFHEADER, \
                                     MDMFOFFSETS, SDMFSlotWriteProxy, \
                                     PRIVATE_KEY_SIZE, \
                                     SIGNATURE_SIZE, \
                                     VERIFICATION_KEY_SIZE, \
                                     SHARE_HASH_CHAIN_SIZE
from allmydata.interfaces import BadWriteEnablerError, RIStorageServer
from allmydata.test.common import LoggingServiceParent, ShouldFailMixin, CrawlerTestMixin, \
     FakeCanary
from allmydata.test.common_util import ReallyEqualMixin
from allmydata.test.common_web import WebRenderingMixin
from allmydata.test.no_network import NoNetworkServer
from allmydata.web.storage import StorageStatus, remove_prefix


class FakeAccount:
    def __init__(self, server):
        self.server = server
    def add_share(self, storage_index, shnum, used_space, sharetype, commit=True):
        pass
    def add_or_renew_default_lease(self, storage_index, shnum, commit=True):
        pass
    def mark_share_as_stable(self, storage_index, shnum, used_space, commit=True):
        pass

class FakeStatsProvider:
    def count(self, name, delta=1):
        pass
    def register_producer(self, producer):
        pass


class ServiceParentMixin:
    def setUp(self):
        self.sparent = LoggingServiceParent()
        self.sparent.startService()
        self._lease_secret = itertools.count()

    def tearDown(self):
        return self.sparent.stopService()


class WorkdirMixin:
    def workdir(self, name):
        return os.path.join("storage", self.__class__.__name__, name)


class BucketTestMixin(WorkdirMixin):
    def make_workdir(self, name):
        basedir = self.workdir(name)
        tmpdir = os.path.join(basedir, "tmp")
        incoming = os.path.join(tmpdir, "bucket")
        final = os.path.join(basedir, "bucket")
        fileutil.make_dirs(tmpdir)
        return incoming, final

    def bucket_writer_closed(self, bw, consumed):
        pass

    def add_latency(self, category, latency):
        pass

    def count(self, name, delta=1):
        pass


class Bucket(BucketTestMixin, unittest.TestCase):
    def test_create(self):
        incoming, final = self.make_workdir("test_create")
        account = FakeAccount(self)
        d = defer.succeed(None)
        d.addCallback(lambda ign: create_immutable_disk_share(incoming, final, allocated_data_length=200,
                                                              storage_index="si1", shnum=0))
        def _got_share(share):
            bw = BucketWriter(account, share, FakeCanary())
            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: bw.remote_write(0, "a"*25))
            d2.addCallback(lambda ign: bw.remote_write(25, "b"*25))
            d2.addCallback(lambda ign: bw.remote_write(50, "c"*25))
            d2.addCallback(lambda ign: bw.remote_write(75, "d"*7))
            d2.addCallback(lambda ign: bw.remote_close())
            return d2
        d.addCallback(_got_share)
        return d

    def test_readwrite(self):
        incoming, final = self.make_workdir("test_readwrite")
        account = FakeAccount(self)
        d = defer.succeed(None)
        d.addCallback(lambda ign: create_immutable_disk_share(incoming, final, allocated_data_length=200,
                                                              storage_index="si1", shnum=0))
        def _got_share(share):
            bw = BucketWriter(account, share, FakeCanary())
            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: bw.remote_write(0, "a"*25))
            d2.addCallback(lambda ign: bw.remote_write(25, "b"*25))
            d2.addCallback(lambda ign: bw.remote_write(50, "c"*7)) # last block may be short
            d2.addCallback(lambda ign: bw.remote_close())

            # now read from it
            def _read(ign):
                br = BucketReader(account, share)
                d3 = defer.succeed(None)
                d3.addCallback(lambda ign: br.remote_read(0, 25))
                d3.addCallback(lambda res: self.failUnlessEqual(res, "a"*25))
                d3.addCallback(lambda ign: br.remote_read(25, 25))
                d3.addCallback(lambda res: self.failUnlessEqual(res, "b"*25))
                d3.addCallback(lambda ign: br.remote_read(50, 7))
                d3.addCallback(lambda res: self.failUnlessEqual(res, "c"*7))
                return d3
            d2.addCallback(_read)
            return d2
        d.addCallback(_got_share)
        return d

    def test_read_past_end_of_share_data(self):
        # test vector for immutable files (hard-coded contents of an immutable share
        # file):

        containerdata = struct.pack('>LLL', 1, 1, 1)

        # A Tahoe-LAFS storage client would send as the share_data a
        # complicated string involving hash trees and a URI Extension Block
        # -- see allmydata/immutable/layout.py . This test, which is
        # simulating a client, just sends 'a'.
        share_data = 'a'
        extra_data = 'b' * ImmutableDiskShare.LEASE_SIZE
        share_file_data = containerdata + share_data + extra_data

        incoming, final = self.make_workdir("test_read_past_end_of_share_data")

        fileutil.write(final, share_file_data)
        d = defer.succeed(None)
        d.addCallback(lambda ign: load_immutable_disk_share(final))
        def _got_share(share):
            mockstorageserver = mock.Mock()
            account = FakeAccount(mockstorageserver)

            # Now read from it.
            br = BucketReader(account, share)

            d2 = br.remote_read(0, len(share_data))
            d2.addCallback(lambda res: self.failUnlessEqual(res, share_data))

            # Read past the end of share data to get the cancel secret.
            read_length = len(share_data) + len(extra_data)
            d2.addCallback(lambda ign: br.remote_read(0, read_length))
            d2.addCallback(lambda res: self.failUnlessEqual(res, share_data))

            # Read past the end of share data by 1 byte.
            d2.addCallback(lambda ign: br.remote_read(0, len(share_data)+1))
            d2.addCallback(lambda res: self.failUnlessEqual(res, share_data))
            return d2
        d.addCallback(_got_share)
        return d


class RemoteBucket:
    def __init__(self):
        self.read_count = 0
        self.write_count = 0

    def callRemote(self, methname, *args, **kwargs):
        def _call():
            meth = getattr(self.target, "remote_" + methname)
            return meth(*args, **kwargs)

        if methname == "slot_readv":
            self.read_count += 1
        if "writev" in methname:
            self.write_count += 1

        return defer.maybeDeferred(_call)


class BucketProxy(BucketTestMixin, unittest.TestCase):
    def make_bucket(self, name, size):
        incoming, final = self.make_workdir(name)
        account = FakeAccount(self)

        d = defer.succeed(None)
        d.addCallback(lambda ign: create_immutable_disk_share(incoming, final, size,
                                                              storage_index="si1", shnum=0))
        def _got_share(share):
            bw = BucketWriter(account, share, FakeCanary())
            rb = RemoteBucket()
            rb.target = bw
            return bw, rb, final
        d.addCallback(_got_share)
        return d

    def test_create(self):
        d = self.make_bucket("test_create", 500)
        def _made_bucket( (bw, rb, sharefile) ):
            bp = WriteBucketProxy(rb, None,
                                  data_size=300,
                                  block_size=10,
                                  num_segments=5,
                                  num_share_hashes=3,
                                  uri_extension_size_max=500)
            self.failUnless(interfaces.IStorageBucketWriter.providedBy(bp), bp)
        d.addCallback(_made_bucket)
        return d

    def _do_test_readwrite(self, name, header_size, wbp_class, rbp_class):
        # Let's pretend each share has 100 bytes of data, and that there are
        # 4 segments (25 bytes each), and 8 shares total. So the two
        # per-segment merkle trees (crypttext_hash_tree,
        # block_hashes) will have 4 leaves and 7 nodes each. The per-share
        # merkle tree (share_hashes) has 8 leaves and 15 nodes, and we need 3
        # nodes. Furthermore, let's assume the uri_extension is 500 bytes
        # long. That should make the whole share:
        #
        # 0x24 + 100 + 7*32 + 7*32 + 7*32 + 3*(2+32) + 4+500 = 1414 bytes long
        # 0x44 + 100 + 7*32 + 7*32 + 7*32 + 3*(2+32) + 4+500 = 1446 bytes long

        sharesize = header_size + 100 + 7*32 + 7*32 + 7*32 + 3*(2+32) + 4+500

        crypttext_hashes = [hashutil.tagged_hash("crypt", "bar%d" % i)
                            for i in range(7)]
        block_hashes = [hashutil.tagged_hash("block", "bar%d" % i)
                        for i in range(7)]
        share_hashes = [(i, hashutil.tagged_hash("share", "bar%d" % i))
                        for i in (1,9,13)]
        uri_extension = "s" + "E"*498 + "e"

        d = self.make_bucket(name, sharesize)
        def _made_bucket( (bw, rb, sharefile) ):
            bp = wbp_class(rb, None,
                           data_size=95,
                           block_size=25,
                           num_segments=4,
                           num_share_hashes=3,
                           uri_extension_size_max=len(uri_extension))

            d2 = bp.put_header()
            d2.addCallback(lambda ign: bp.put_block(0, "a"*25))
            d2.addCallback(lambda ign: bp.put_block(1, "b"*25))
            d2.addCallback(lambda ign: bp.put_block(2, "c"*25))
            d2.addCallback(lambda ign: bp.put_block(3, "d"*20))
            d2.addCallback(lambda ign: bp.put_crypttext_hashes(crypttext_hashes))
            d2.addCallback(lambda ign: bp.put_block_hashes(block_hashes))
            d2.addCallback(lambda ign: bp.put_share_hashes(share_hashes))
            d2.addCallback(lambda ign: bp.put_uri_extension(uri_extension))
            d2.addCallback(lambda ign: bp.close())

            d2.addCallback(lambda ign: load_immutable_disk_share(sharefile))
            return d2
        d.addCallback(_made_bucket)

        # now read everything back
        def _start_reading(share):
            br = BucketReader(FakeAccount(self), share)
            rb = RemoteBucket()
            rb.target = br
            server = NoNetworkServer("abc", None)
            rbp = rbp_class(rb, server, storage_index="")
            self.failUnlessIn("to peer", repr(rbp))
            self.failUnless(interfaces.IStorageBucketReader.providedBy(rbp), rbp)

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: rbp.get_block_data(0, 25, 25))
            d2.addCallback(lambda res: self.failUnlessEqual(res, "a"*25))
            d2.addCallback(lambda ign: rbp.get_block_data(1, 25, 25))
            d2.addCallback(lambda res: self.failUnlessEqual(res, "b"*25))
            d2.addCallback(lambda ign: rbp.get_block_data(2, 25, 25))
            d2.addCallback(lambda res: self.failUnlessEqual(res, "c"*25))
            d2.addCallback(lambda ign: rbp.get_block_data(3, 25, 20))
            d2.addCallback(lambda res: self.failUnlessEqual(res, "d"*20))

            d2.addCallback(lambda ign: rbp.get_crypttext_hashes())
            d2.addCallback(lambda res: self.failUnlessEqual(res, crypttext_hashes))
            d2.addCallback(lambda ign: rbp.get_block_hashes(set(range(4))))
            d2.addCallback(lambda res: self.failUnlessEqual(res, block_hashes))
            d2.addCallback(lambda ign: rbp.get_share_hashes())
            d2.addCallback(lambda res: self.failUnlessEqual(res, share_hashes))
            d2.addCallback(lambda ign: rbp.get_uri_extension())
            d2.addCallback(lambda res: self.failUnlessEqual(res, uri_extension))
            return d2
        d.addCallback(_start_reading)
        return d

    def test_readwrite_v1(self):
        return self._do_test_readwrite("test_readwrite_v1",
                                       0x24, WriteBucketProxy, ReadBucketProxy)

    def test_readwrite_v2(self):
        return self._do_test_readwrite("test_readwrite_v2",
                                       0x44, WriteBucketProxy_v2, ReadBucketProxy)


class Seek(unittest.TestCase, WorkdirMixin):
    def test_seek(self):
        basedir = self.workdir("test_seek")
        fileutil.make_dirs(basedir)
        filename = os.path.join(basedir, "testfile")
        fileutil.write(filename, "start")

        # mode="w" allows seeking-to-create-holes, but truncates pre-existing
        # files. mode="a" preserves previous contents but does not allow
        # seeking-to-create-holes. mode="r+" allows both.
        f = open(filename, "rb+")
        try:
            f.seek(100)
            f.write("100")
        finally:
            f.close()

        filelen = os.stat(filename).st_size
        self.failUnlessEqual(filelen, 100+3)
        f2 = open(filename, "rb")
        try:
            self.failUnlessEqual(f2.read(5), "start")
        finally:
            f2.close()


class CloudCommon(unittest.TestCase, ShouldFailMixin, WorkdirMixin):
    def test_concat(self):
        x = deque([[1, 2], (), xrange(3, 6)])
        self.failUnlessEqual(cloud_common.concat(x), [1, 2, 3, 4, 5])

    def test_list_objects_truncated_badly(self):
        # If a container misbehaves by not producing listings with increasing keys,
        # that should cause an incident.
        basedir = self.workdir("test_list_objects_truncated_badly")
        fileutil.make_dirs(basedir)

        class BadlyTruncatingMockContainer(MockContainer):
            def _list_some_objects(self, container_name, prefix='', marker=None):
                contents = [ContainerItem("", None, "", 0, None, None)]
                return defer.succeed(ContainerListing(container_name, "", "", 0, "true", contents))

        s = {"level": 0}
        def call_log_msg(*args, **kwargs):
            s["level"] = max(s["level"], kwargs["level"])
        self.patch(cloud_common.log, 'msg', call_log_msg)

        container = BadlyTruncatingMockContainer(basedir)
        d = self.shouldFail(AssertionError,
                            'truncated badly', "Not making progress in list_objects",
                            lambda: container.list_objects(prefix=""))
        d.addCallback(lambda ign: self.failUnless(s["level"] >= WEIRD, s["level"]))
        return d

    def test_cloud_share_base(self):
        basedir = self.workdir("test_cloud_share_base")
        fileutil.make_dirs(basedir)

        container = MockContainer(basedir)
        base = cloud_common.CloudShareBase(container, "si1", 1)
        base._data_length = 42
        base._total_size = 100

        self.failUnlessIn("CloudShareBase", repr(base))
        self.failUnlessEqual(base.get_storage_index(), "si1")
        self.failUnlessEqual(base.get_storage_index_string(), "onutc")
        self.failUnlessEqual(base.get_shnum(), 1)
        self.failUnlessEqual(base.get_data_length(), 42)
        self.failUnlessEqual(base.get_size(), 100)
        self.failUnlessEqual(os.path.normpath(base._get_path()),
                             os.path.normpath(os.path.join(basedir, "shares", "on", "onutc", "1")))

    # TODO: test cloud_common.delete_chunks


class OpenStackCloudBackend(ServiceParentMixin, WorkdirMixin, ShouldFailMixin, unittest.TestCase):
    PROVIDER = "rackspace.com"
    AUTH_SERVICE_URL = "auth_service_url"
    USERNAME = "username"
    CONTAINER = "container"
    API_KEY = "api_key"
    PUBLIC_STORAGE_URL = "https://public.storage.example/a"
    INTERNAL_STORAGE_URL = "https://internal.storage.example/a"
    AUTH_TOKEN = "auth_token"

    TEST_SHARE_PREFIX = "shares/te/"
    TEST_SHARE_NAME = TEST_SHARE_PREFIX + "test"
    TEST_SHARE_MODIFIED = "2013-02-14T21:30:00Z"
    TEST_SHARE_DATA = "share"
    TEST_SHARE_HASH = "sharehash"
    TEST_LISTING_JSON = ('[{"name": "%s", "bytes": %d, "last_modified": "%s", "hash": "%s"}]'
                         % (TEST_SHARE_NAME, len(TEST_SHARE_DATA), TEST_SHARE_MODIFIED, TEST_SHARE_HASH))

    def _patch_agent(self):
        self._requests = {}

        class MockResponse(object):
            def __init__(mock_self, response_code, response_phrase, response_headers, response_body):
                mock_self.code = response_code
                mock_self.phrase = response_phrase
                mock_self.headers = Headers(response_headers)
                mock_self._body = response_body

            def deliverBody(mock_self, protocol):
                protocol.dataReceived(mock_self._body)
                protocol.connectionLost(Failure(ResponseDone()))

        class MockAgent(object):
            def __init__(mock_self, reactor, pool=None, connectTimeout=None):
                pass

            def request(mock_self, method, url, headers, bodyProducer=None):
                self.failUnlessIn((method, url), self._requests)
                (expected_headers, expected_body,
                 response_code, response_phrase, response_headers, response_body) = self._requests[(method, url)]

                self.failUnlessIsInstance(headers, Headers)
                for (key, values) in expected_headers.iteritems():
                    self.failUnlessEqual(headers.getRawHeaders(key), values, str((headers, key)))

                d = defer.succeed(None)
                if bodyProducer is None:
                    self.failUnlessEqual(expected_body, "")
                else:
                    self.failUnless(IBodyProducer.providedBy(bodyProducer))
                    body = StringIO()
                    d = bodyProducer.startProducing(FileConsumer(body))
                    d.addCallback(lambda ign: self.failUnlessEqual(body.getvalue(), expected_body))
                    d.addCallback(lambda ign: self.failUnlessIn(bodyProducer.length,
                                                                (len(expected_body), UNKNOWN_LENGTH)))
                d.addCallback(lambda ign: MockResponse(response_code, response_phrase, response_headers, response_body))
                return d

        self.patch(openstack_container, 'Agent', MockAgent)
        self.patch(cloud_common, 'Agent', MockAgent)

    def _set_request(self, method, url, expected_headers, expected_body,
                           response_code, response_phrase, response_headers, response_body):
        precondition(isinstance(expected_headers, dict), expected_headers)
        precondition(isinstance(response_headers, dict), response_headers)
        self._requests[(method, url)] = (expected_headers, expected_body,
                                         response_code, response_phrase, response_headers, response_body)

    def _make_server(self, name):
        # This is for the v1 auth protocol.
        #self._set_request('GET', self.AUTH_SERVICE_URL, {
        #                    'X-Auth-User': [self.USERNAME],
        #                    'X-Auth-Key': [self.API_KEY],
        #                  }, "",
        #                  204, "No Content", {
        #                    'X-Storage-Url': [self.STORAGE_URL],
        #                    'X-Auth-Token': [self.AUTH_TOKEN],
        #                  }, "")

        self._set_request('POST', self.AUTH_SERVICE_URL, {
                            'Content-Type': ['application/json'],
                          }, '{"auth": {"RAX-KSKEY:apiKeyCredentials": {"username": "username", "apiKey": "api_key"}}}',
                          200, "OK", {
                          }, '''
                          {"access": {"token": {"id": "%s"},
                                      "serviceCatalog": [{"endpoints": [{"region": "FOO", "publicURL": "%s", "internalURL": "%s"}],
                                                          "type": "object-store"}],
                                      "user": {"RAX-AUTH:defaultRegion": "", "name": "%s"}
                                     }
                          }''' % (self.AUTH_TOKEN, self.PUBLIC_STORAGE_URL, self.INTERNAL_STORAGE_URL, self.USERNAME))

        storage_config = {
            'openstack.provider': self.PROVIDER,
            'openstack.url': self.AUTH_SERVICE_URL,
            'openstack.username': self.USERNAME,
            'openstack.container': self.CONTAINER,
        }
        from allmydata.node import _None
        class MockConfig(object):
            def get_config(mock_self, section, option, default=_None, boolean=False):
                self.failUnlessEqual(section, "storage")
                if default is _None:
                    self.failUnlessIn(option, storage_config)
                return storage_config.get(option, default)
            def get_private_config(mock_self, filename):
                return fileutil.read(os.path.join(privatedir, filename))

        self.workdir = self.workdir(name)
        privatedir = os.path.join(self.workdir, "private")
        fileutil.make_dirs(privatedir)
        fileutil.write(os.path.join(privatedir, "openstack_api_key"), self.API_KEY)

        self.config = MockConfig()
        self.clock = Clock()
        self.container = openstack_container.configure_openstack_container(self.workdir, self.config)
        backend = CloudBackend(self.container)
        self.server = StorageServer("\x00" * 20, backend, self.workdir,
                                    stats_provider=FakeStatsProvider(), clock=self.clock)
        self.server.setServiceParent(self.sparent)
        self.failUnless(self.server.backend._container is self.container,
                        (self.server.backend._container, self.container))

    def _shutdown(self, res):
        # avoid unclean reactor error
        self.container._auth_client.shutdown()
        return res


    def test_authentication_client(self):
        self._patch_agent()
        self._make_server("test_authentication_client")

        d = self.container._auth_client.get_auth_info()
        def _check(auth_info):
            self.failUnlessEqual(auth_info.public_storage_url, self.PUBLIC_STORAGE_URL)
            self.failUnlessEqual(auth_info.internal_storage_url, self.INTERNAL_STORAGE_URL)
            self.failUnlessEqual(auth_info.auth_token, self.AUTH_TOKEN)
        d.addCallback(_check)
        d.addBoth(self._shutdown)
        return d

    def test_openstack_container(self):
        self._patch_agent()

        # Set up the requests that we expect to receive.
        self._set_request('GET', "/".join((self.PUBLIC_STORAGE_URL, self.CONTAINER, "unexpected")), {
                            'X-Auth-Token': [self.AUTH_TOKEN],
                          }, "",
                          404, "Not Found", {}, "")

        self._set_request('PUT', "/".join((self.PUBLIC_STORAGE_URL, self.CONTAINER, self.TEST_SHARE_NAME)), {
                            'X-Auth-Token': [self.AUTH_TOKEN],
                            'Content-Type': ['application/octet-stream'],
                            #'Content-Length': [len(self.TEST_SHARE_DATA)],
                          }, self.TEST_SHARE_DATA,
                          204, "No Content", {}, "")

        quoted_prefix = urllib.quote(self.TEST_SHARE_PREFIX, safe='')
        self._set_request('GET', "%s/%s?format=json&prefix=%s"
                                   % (self.PUBLIC_STORAGE_URL, self.CONTAINER, quoted_prefix), {
                            'X-Auth-Token': [self.AUTH_TOKEN],
                          }, "",
                          200, "OK", {}, self.TEST_LISTING_JSON)

        self._set_request('GET', "/".join((self.PUBLIC_STORAGE_URL, self.CONTAINER, self.TEST_SHARE_NAME)), {
                            'X-Auth-Token': [self.AUTH_TOKEN],
                          }, "",
                          200, "OK", {}, self.TEST_SHARE_DATA)

        self._make_server("test_openstack_container")

        d = defer.succeed(None)
        d.addCallback(lambda ign: self.shouldFail(CloudError, "404", None,
                                                  self.container.get_object, "unexpected"))

        d.addCallback(lambda ign: self.container.put_object(self.TEST_SHARE_NAME, self.TEST_SHARE_DATA))
        d.addCallback(lambda res: self.failUnless(res is None, res))

        d.addCallback(lambda ign: self.container.list_objects(prefix=self.TEST_SHARE_PREFIX))
        def _check_listing(listing):
            self.failUnlessEqual(listing.name, self.CONTAINER)
            self.failUnlessEqual(listing.prefix, self.TEST_SHARE_PREFIX)
            self.failUnlessEqual(listing.is_truncated, "false")
            self.failUnlessEqual(len(listing.contents), 1)
            item = listing.contents[0]
            self.failUnlessEqual(item.key, self.TEST_SHARE_NAME)
            self.failUnlessEqual(item.modification_date, self.TEST_SHARE_MODIFIED)
            self.failUnlessEqual(item.etag, self.TEST_SHARE_HASH)
            self.failUnlessEqual(item.size, len(self.TEST_SHARE_DATA))
        d.addCallback(_check_listing)

        d.addCallback(lambda ign: self.container.get_object(self.TEST_SHARE_NAME))
        d.addCallback(lambda res: self.failUnlessEqual(res, self.TEST_SHARE_DATA))

        def _set_up_delete(ign):
            self._set_request('DELETE', "/".join((self.PUBLIC_STORAGE_URL, self.CONTAINER, self.TEST_SHARE_NAME)), {
                                'X-Auth-Token': [self.AUTH_TOKEN],
                              }, "",
                              204, "No Content", {}, "")

            # this changes the response to the request set up above
            self._set_request('GET', "%s/%s?format=json&prefix=%s"
                                       % (self.PUBLIC_STORAGE_URL, self.CONTAINER, quoted_prefix), {
                                'X-Auth-Token': [self.AUTH_TOKEN],
                              }, "",
                              200, "OK", {}, "[]")
        d.addCallback(_set_up_delete)

        d.addCallback(lambda ign: self.container.delete_object(self.TEST_SHARE_NAME))
        d.addCallback(lambda res: self.failUnless(res is None, res))

        d.addCallback(lambda ign: self.container.list_objects(prefix=self.TEST_SHARE_PREFIX))
        def _check_listing_after_delete(listing):
            self.failUnlessEqual(listing.name, self.CONTAINER)
            self.failUnlessEqual(listing.prefix, self.TEST_SHARE_PREFIX)
            self.failUnlessEqual(listing.is_truncated, "false")
            self.failUnlessEqual(len(listing.contents), 0)
        d.addCallback(_check_listing_after_delete)

        d.addBoth(self._shutdown)
        return d



class GoogleStorageAuthenticationClient(unittest.TestCase):
    """
    Tests for the Google Storage API authentication.

    All code references in docstrings/comments are to classes/functions in
    allmydata.storage.backends.cloud.googlestorage.googlestorage_container
    unless noted otherwise.
    """

    if not googlestorage_container.oauth2client_available:
        skip = "Google Storage requires oauth2client"

    def test_credentials(self):
        """
        AuthenticationClient.get_authorization_header() initializes a
        SignedJwtAssertionCredentials with the correct parameters.
        """
        # Somewhat fragile tests, but better than nothing.
        auth = googlestorage_container.AuthenticationClient("u@example.com", "xxx123")
        self.failUnlessEqual(auth._credentials.service_account_name, "u@example.com")
        self.failUnlessEqual(auth._credentials.private_key, "xxx123".encode("base64").strip())

    def test_initial(self):
        """
        When AuthenticationClient() is created, it refreshes its access token.
        """
        from oauth2client.client import SignedJwtAssertionCredentials
        auth = googlestorage_container.AuthenticationClient(
            "u@example.com", "xxx123",
            _credentialsClass=mock.create_autospec(SignedJwtAssertionCredentials),
            _deferToThread=defer.maybeDeferred)
        self.failUnlessEqual(auth._credentials.refresh.call_count, 1)

    def test_expired(self):
        """
        AuthenticationClient.get_authorization_header() refreshes its
        credentials if the access token has expired.
        """
        from oauth2client.client import SignedJwtAssertionCredentials
        auth = googlestorage_container.AuthenticationClient(
            "u@example.com", "xxx123",
            _credentialsClass=mock.create_autospec(SignedJwtAssertionCredentials),
            _deferToThread=defer.maybeDeferred)
        auth._credentials.apply = lambda d: d.__setitem__('Authorization', 'xxx')
        auth._credentials.access_token_expired = True
        auth.get_authorization_header()
        self.failUnlessEqual(auth._credentials.refresh.call_count, 2)

    def test_no_refresh(self):
        """
        AuthenticationClient.get_authorization_header() does not refresh its
        credentials if the access token has not expired.
        """
        from oauth2client.client import SignedJwtAssertionCredentials
        auth = googlestorage_container.AuthenticationClient(
            "u@example.com", "xxx123",
            _credentialsClass=mock.create_autospec(SignedJwtAssertionCredentials),
            _deferToThread=defer.maybeDeferred)
        auth._credentials.apply = lambda d: d.__setitem__('Authorization', 'xxx')
        auth._credentials.access_token_expired = False
        auth.get_authorization_header()
        self.failUnlessEqual(auth._credentials.refresh.call_count, 1)

    def test_header(self):
        """
        AuthenticationClient.get_authorization_header() returns a value to be
        used for the Authorization header, which is ASCII-encoded if
        necessary.
        """
        from oauth2client.client import SignedJwtAssertionCredentials
        class NoNetworkCreds(SignedJwtAssertionCredentials):
            def refresh(self, http):
                self.access_token = u"xxx"
        auth = googlestorage_container.AuthenticationClient(
            "u@example.com", "xxx123",
            _credentialsClass=NoNetworkCreds,
            _deferToThread=defer.maybeDeferred)
        result = []
        auth.get_authorization_header().addCallback(result.append)
        self.failUnlessEqual(result, ["Bearer xxx"])
        self.failUnlessIsInstance(result[0], bytes)

    def test_one_refresh(self):
        """
        AuthenticationClient._refresh_if_necessary() only runs one refresh
        request at a time.
        """
        # The second call shouldn't happen until the first Deferred fires!
        results = [defer.Deferred(), defer.succeed(None)]
        first = results[0]

        def fakeDeferToThread(f, *args):
            return results.pop(0)

        from oauth2client.client import SignedJwtAssertionCredentials
        auth = googlestorage_container.AuthenticationClient(
            "u@example.com", "xxx123",
            _credentialsClass=mock.create_autospec(SignedJwtAssertionCredentials),
            _deferToThread=fakeDeferToThread)
        # Initial authorization call happens...
        self.failUnlessEqual(len(results), 1)
        # ... and still isn't finished, so next one doesn't run yet:
        auth._refresh_if_necessary(force=True)
        self.failUnlessEqual(len(results), 1)
        # When first one finishes, second one can run:
        first.callback(None)
        self.failUnlessEqual(len(results), 0)

    def test_refresh_call(self):
        """
        AuthenticationClient._refresh_if_necessary() runs the
        authentication refresh in a thread, since it blocks, with a
        httplib2.Http instance.
        """
        from httplib2 import Http
        from oauth2client.client import SignedJwtAssertionCredentials
        class NoNetworkCreds(SignedJwtAssertionCredentials):
            def refresh(cred_self, http):
                cred_self.access_token = "xxx"
                self.failUnlessIsInstance(http, Http)
                self.thread_id = thread.get_ident()
        auth = googlestorage_container.AuthenticationClient(
            "u@example.com", "xxx123",
            _credentialsClass=NoNetworkCreds)

        def gotResult(ignore):
            self.failIfEqual(thread.get_ident(), self.thread_id)
        return auth.get_authorization_header().addCallback(gotResult)


class CloudStorageBackendMixin(object):
    """
    Utility functionality for testing cloud storage backends.
    """
    class Response(object):
        def __init__(self, code, headers={}):
            self.code = code
            self.headers = headers

    def mock_http_request(self):
        """
        Override the container's _http_request with a mock whose result is a
        Deferred which can be fired by the caller.
        """
        d = defer.Deferred()
        self.container._http_request = mock.create_autospec(
            self.container._http_request, return_value=d)
        return d


class ContainerRetryTests(unittest.TestCase, CloudStorageBackendMixin):
    """
    Tests for ContainerRetryMixin.
    """
    def setUp(self):
        from allmydata.storage.backends.cloud.cloud_common import ContainerRetryMixin
        self.reactor = Clock()
        self.container = ContainerRetryMixin()
        self.container._reactor = self.reactor
        self.container.ServiceError = CloudServiceError
        # We don't just use mock.Mock, but do this silly thing so we can use
        # create_autospec, because create_autospec is the only safe way to use
        # mock.
        self.container._http_request = (lambda description, method, url, headers,
                                        body=None, need_response_body=False: None)

    def test_retry_response_code(self):
        """
        If an HTTP response code is server error or an authentication error,
        the request will try again after a delay.
        """
        first, second = defer.Deferred(), defer.Deferred()
        self.container._http_request = mock.create_autospec(
            self.container._http_request, side_effect=[first, second])
        result = []
        self.container._do_request("test", self.container._http_request,
                                   "test", "GET", "http://example", {}, body=None,
                                   need_response_body=True).addCallback(result.append)
        # No response from first request yet:
        self.failIf(result)
        self.failUnlessEqual(self.container._http_request.call_count, 1)
        self.container._http_request.assert_called_with(
            "test", "GET", "http://example", {},
            body=None, need_response_body=True)

        # First response fails:
        first.errback(CloudServiceError(None, 500))
        self.failIf(result, result)
        self.failUnlessEqual(self.container._http_request.call_count, 1)
        self.reactor.advance(0.1)
        self.failUnlessEqual(self.container._http_request.call_count, 2)
        self.container._http_request.assert_called_with(
            "test", "GET", "http://example", {},
            body=None, need_response_body=True)

        # Second response succeeds:
        done = object()
        second.callback(done)
        self.failUnlessEqual(result, [done])

    def test_retry_random_exception(self):
        """
        If a HTTP request fails with any exception at all, retry.
        """
        class NewException(Exception):
            pass
        first, second = defer.Deferred(), defer.Deferred()
        self.container._http_request = mock.create_autospec(
            self.container._http_request, side_effect=[first, second])
        result = []
        self.container._do_request("test", self.container._http_request,
                                   "test", "GET", "http://example", {}, body=None,
                                   need_response_body=True).addCallback(result.append)

        # No response from first request yet:
        self.failIf(result)
        self.failUnlessEqual(self.container._http_request.call_count, 1)
        self.container._http_request.assert_called_with(
            "test", "GET", "http://example", {},
            body=None, need_response_body=True)

        # First response fails:
        first.errback(NewException())
        self.failIf(result, result)
        self.failUnlessEqual(self.container._http_request.call_count, 1)
        self.reactor.advance(0.1)
        self.failUnlessEqual(self.container._http_request.call_count, 2)
        self.container._http_request.assert_called_with(
            "test", "GET", "http://example", {},
            body=None, need_response_body=True)


class GoogleStorageBackend(unittest.TestCase, CloudStorageBackendMixin):
    """
    Tests for the Google Storage API container.

    All code references in docstrings/comments are to classes/functions in
    allmydata.storage.backends.cloud.googlestorage.googlestorage_container
    unless noted otherwise.
    """
    if not googlestorage_container.oauth2client_available:
        skip = "Google Storage requires oauth2client"

    def setUp(self):
        self.reactor = Clock()
        class FakeAuthenticationClient(object):
            def get_authorization_header(self):
                return defer.succeed("Bearer thetoken")
        self.auth = FakeAuthenticationClient()
        self.container = googlestorage_container.GoogleStorageContainer(
            self.auth, "123", "thebucket", self.reactor)

    def test_create(self):
        """
        GoogleStorageContainer.create() sends the appropriate HTTP command to
        create the bucket, and parses the response to match the expected
        result documented in the IContainer interface.
        """
        raise NotImplementedError()
    test_create.skip = "may not be necessary"

    def test_delete(self):
        """
        GoogleStorageContainer.delete() sends the appropriate HTTP command to
        delete the bucket, and parses the response to match the expected
        result documented in the IContainer interface.
        """
        raise NotImplementedError()
    test_delete.skip = "may not be necessary"

    def test_list_objects(self):
        """
        GoogleStorageContainer.list_objects() sends the appropriate HTTP
        command to list the objects in the bucket, and parses the response to
        match the expected result documented in the IContainer interface.
        """
        LIST_RESPONSE = """\
<?xml version='1.0' encoding='utf-8'?>
<ListBucketResult xmlns='http://doc.s3.amazonaws.com/2006-03-01'>
  <Name>thebucket</Name>
  <Prefix>xxx xxx</Prefix>
  <Marker>themark</Marker>
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>xxx xxx1</Key>
    <Generation>1234</Generation>
    <MetaGeneration>1</MetaGeneration>
    <LastModified>2013-01-27T01:23:45.678Z</LastModified>
    <ETag>"abc"</ETag>
    <Size>123</Size>
    <Owner>
      <ID>something</ID>
      <DisplayName></DisplayName>
    </Owner>
  </Contents>
  <Contents>
    <Key>xxx xxx2</Key>
    <Generation>1234</Generation>
    <MetaGeneration>1</MetaGeneration>
    <LastModified>2013-01-28T01:23:45.678Z</LastModified>
    <ETag>"def"</ETag>
    <Size>456</Size>
    <Owner>
      <ID>something</ID>
      <DisplayName></DisplayName>
    </Owner>
  </Contents>
  <CommonPrefixes>
    <Prefix>xxx</Prefix>
  </CommonPrefixes>
  <CommonPrefixes>
    <Prefix>xxx xxx</Prefix>
  </CommonPrefixes>
  <XXX />
  <RandomGarbage />
</ListBucketResult>
"""
        http_response = self.mock_http_request()
        done = []
        self.container.list_objects(prefix='xxx xxx').addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "Google Storage list objects", "GET",
            "https://storage.googleapis.com/thebucket?prefix=xxx%20xxx",
            {"Authorization": ["Bearer thetoken"],
             "x-goog-api-version": ["2"],
             "x-goog-project-id": ["123"],
             },
            body=None,
            need_response_body=True)
        http_response.callback((self.Response(200), LIST_RESPONSE))
        listing = done[0]
        self.failUnlessEqual(listing.name, "thebucket")
        self.failUnlessEqual(listing.prefix, "xxx xxx")
        self.failUnlessEqual(listing.marker, "themark")
        self.failUnlessEqual(listing.max_keys, None)
        self.failUnlessEqual(listing.is_truncated, "false")
        self.failUnlessEqual(listing.common_prefixes, ["xxx", "xxx xxx"])
        item1, item2 = listing.contents
        self.failUnlessEqual(item1.key, "xxx xxx1")
        self.failUnlessEqual(item1.modification_date, "2013-01-27T01:23:45.678Z")
        self.failUnlessEqual(item1.etag, '"abc"')
        self.failUnlessEqual(item1.size, 123)
        self.failUnlessEqual(item1.owner, None) # meh, who cares
        self.failUnlessEqual(item2.key, "xxx xxx2")
        self.failUnlessEqual(item2.modification_date, "2013-01-28T01:23:45.678Z")
        self.failUnlessEqual(item2.etag, '"def"')
        self.failUnlessEqual(item2.size, 456)
        self.failUnlessEqual(item2.owner, None) # meh, who cares

    def test_put_object(self):
        """
        GoogleStorageContainer.put_object() sends the appropriate HTTP command
        to upload an object to the bucket, and parses the response to match
        the expected result documented in the IContainer interface.
        """
        http_response = self.mock_http_request()
        done = []
        self.container.put_object("theobj", "the body").addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "Google Storage PUT object", "PUT",
            "https://storage.googleapis.com/thebucket/theobj",
            {"Authorization": ["Bearer thetoken"],
             "x-goog-api-version": ["2"],
             "Content-Type": ["application/octet-stream"],
             },
            body="the body",
            need_response_body=False)
        http_response.callback((self.Response(200), None))
        self.failUnless(done)

    def test_put_object_additional(self):
        """
        GoogleStorageContainer.put_object() sends the appropriate HTTP command
        to upload an object to the bucket with custom content type and
        metadata, and parses the response to match the expected result
        documented in the IContainer interface.
        """
        http_response = self.mock_http_request()
        done = []
        self.container.put_object("theobj", "the body",
                                  "text/plain",
                                  {"key": "value"}).addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "Google Storage PUT object", "PUT",
            "https://storage.googleapis.com/thebucket/theobj",
            {"Authorization": ["Bearer thetoken"],
             "x-goog-api-version": ["2"],
             "Content-Type": ["text/plain"],
             "x-goog-meta-key": ["value"], # the metadata
             },
            body="the body",
            need_response_body=False)
        http_response.callback((self.Response(200), None))
        self.failUnless(done)

    def test_get_object(self):
        """
        GoogleStorageContainer.get_object() sends the appropriate HTTP command
        to get an object from the bucket, and parses the response to match the
        expected result documented in the IContainer interface.
        """
        http_response = self.mock_http_request()
        done = []
        self.container.get_object("theobj").addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "Google Storage GET object", "GET",
            "https://storage.googleapis.com/thebucket/theobj",
            {"Authorization": ["Bearer thetoken"],
             "x-goog-api-version": ["2"],
             },
            body=None,
            need_response_body=True)
        http_response.callback((self.Response(200), "the body"))
        self.failUnlessEqual(done, ["the body"])

    def test_delete_object(self):
        """
        GoogleStorageContainer.delete_object() sends the appropriate HTTP
        command to delete an object from the bucket, and parses the response
        to match the expected result documented in the IContainer interface.
        """
        http_response = self.mock_http_request()
        done = []
        self.container.delete_object("theobj").addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "Google Storage DELETE object", "DELETE",
            "https://storage.googleapis.com/thebucket/theobj",
            {"Authorization": ["Bearer thetoken"],
             "x-goog-api-version": ["2"],
             },
            body=None,
            need_response_body=False)
        http_response.callback((self.Response(200), None))
        self.failUnless(done)

    def test_retry(self):
        """
        If an HTTP response code is server error or an authentication error,
        the request will try again after a delay.
        """
        first, second, third = defer.Deferred(), defer.Deferred(), defer.Deferred()
        self.container._http_request = mock.create_autospec(
            self.container._http_request, side_effect=[first, second, third])
        result = []
        self.container._do_request("test", self.container._http_request,
                                   "test", "GET", "http://example", {}, body=None,
                                   need_response_body=True).addCallback(result.append)
        # No response from first request yet:
        self.failIf(result)
        self.failUnlessEqual(self.container._http_request.call_count, 1)
        self.container._http_request.assert_called_with(
            "test", "GET", "http://example", {},
            body=None, need_response_body=True)

        # First response fails:
        first.errback(CloudServiceError(None, 500))
        self.failIf(result, result)
        self.failUnlessEqual(self.container._http_request.call_count, 1)
        self.reactor.advance(0.1)
        self.failUnlessEqual(self.container._http_request.call_count, 2)
        self.container._http_request.assert_called_with(
            "test", "GET", "http://example", {},
            body=None, need_response_body=True)

        # Second response fails:
        second.errback(CloudServiceError(None, 401)) # Unauthorized
        self.failIf(result)
        self.failUnlessEqual(self.container._http_request.call_count, 2)
        self.reactor.advance(2)
        self.failUnlessEqual(self.container._http_request.call_count, 3)
        self.container._http_request.assert_called_with(
            "test", "GET", "http://example", {},
            body=None, need_response_body=True)

        # Third response succeeds:
        done = object()
        third.callback(done)
        self.failUnlessEqual(result, [done])

    def test_react_to_error(self):
        """
        GoogleStorageContainer._react_to_error() will return True (i.e. retry)
        for any response code between 400 and 599.
        """
        self.failIf(self.container._react_to_error(399))
        self.failIf(self.container._react_to_error(600))
        for i in range(400, 600):
            self.failUnless(self.container._react_to_error(i))

    def test_head_object(self):
        """
        GoogleStorageContainer.head_object() sends the appropriate HTTP
        command to get an object's metadata from the bucket, and parses the
        response to match the expected result documented in the IContainer
        interface.
        """
        raise NotImplementedError()
    test_head_object.skip = "May not be necessary"



class MSAzureAuthentication(unittest.TestCase):
    """
    Tests for Microsoft Azure Blob Storage authentication.
    """
    class FakeRequest:
        """
        Emulate request objects used by azure library.
        """
        def __init__(self, method, url, headers):
            from urlparse import urlparse, parse_qs
            self.headers = [
                (key.lower(), value[0]) for key, value in headers.items()]
            url = urlparse(url)
            self.path = url.path
            self.query = url.query
            self.method = method
            self.query = [(k, v[0]) for k, v in parse_qs(url.query, keep_blank_values=True).items()]

    def setUp(self):
        self.container = msazure_container.MSAzureStorageContainer(
            "account", "key".encode("base64"), "thebucket")

    def failUnlessSignatureEqual(self, method, url, headers, result, azure_buggy=False):
        """
        Assert the given HTTP request parameters produce a value to be signed
        equal to the given result.

        If possible, assert the signature calculation matches the Microsoft
        reference implementation.
        """
        self.failUnlessEqual(
                self.container._calculate_presignature(method, url, headers),
                result)
        if azure_buggy:
            # The reference client is buggy in this case, skip it
            raise unittest.SkipTest("Azure reference client is buggy in this case.")

        # Now, compare our result to that of the Microsoft-provided
        # implementation, if available:
        try:
            from azure.storage import _sign_storage_blob_request
        except ImportError:
            raise unittest.SkipTest("""No azure installed.
The 'azure' package is not used by the Azure support in the cloud backend; it is only
used (optionally) by tests to confirm compatibility with Microsoft's reference client.""")

        request = self.FakeRequest(method, url, headers)
        self.failUnlessEqual(
            _sign_storage_blob_request(request,
                                       self.container._account_name,
                                       self.container._account_key.encode("base64")),
            self.container._calculate_signature(method, url, headers))

    def test_method(self):
        """
        The correct HTTP method is included in the signature.
        """
        self.failUnlessSignatureEqual(
            "HEAD", "http://x/", {"x-ms-date": ["Sun, 11 Oct 2009 21:49:13 GMT"]},
            "HEAD\n\n\n\n\n\n\n\n\n\n\n\n"
            "x-ms-date:Sun, 11 Oct 2009 21:49:13 GMT\n"
            "/account/")

    def test_standard_headers(self):
        """
        A specific set of headers are included in the signature, except for
        Date which is ignored in favor of x-ms-date.
        """
        headers = {"Content-Encoding": ["ce"],
                   "Content-Language": ["cl"],
                   "Content-Length": ["cl2"],
                   "Content-MD5": ["cm"],
                   "Content-Type": ["ct"],
                   "Date": ["d"],
                   "If-Modified-Since": ["ims"],
                   "If-Match": ["im"],
                   "If-None-Match": ["inm"],
                   "If-Unmodified-Since": ["ius"],
                   "Range": ["r"],
                   "Other": ["o"],
                   "x-ms-date": ["xmd"]}
        self.failUnlessSignatureEqual("GET", "http://x/", headers,
                                      "GET\n"
                                      "ce\n"
                                      "cl\n"
                                      "cl2\n"
                                      "cm\n"
                                      "ct\n"
                                      "\n" # Date value is ignored!
                                      "ims\n"
                                      "im\n"
                                      "inm\n"
                                      "ius\n"
                                      "r\n"
                                      "x-ms-date:xmd\n"
                                      "/account/", True)

    def test_xms_headers(self):
        """
        Headers starting with x-ms are included in the signature.
        """
        headers = {"x-ms-foo": ["a"],
                   "x-ms-z": ["b"],
                   "x-ms-date": ["c"]}
        self.failUnlessSignatureEqual("GET", "http://x/", headers,
                                      "GET\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "\n"
                                      "x-ms-date:c\n"
                                      "x-ms-foo:a\n"
                                      "x-ms-z:b\n"
                                      "/account/")

    def test_xmsdate_required(self):
        """
        The x-ms-date header is mandatory.
        """
        self.failUnlessRaises(ValueError,
                              self.failUnlessSignatureEqual, "GET", "http://x/", {}, "")

    def test_path_and_account(self):
        """
        The URL path and account name is included.
        """
        self.container._account_name = "theaccount"
        self.failUnlessSignatureEqual(
            "HEAD", "http://x/foo/bar", {"x-ms-date": ["d"]},
            "HEAD\n\n\n\n\n\n\n\n\n\n\n\n"
            "x-ms-date:d\n"
            "/theaccount/foo/bar")

    def test_query(self):
        """
        The query arguments are included.
        """
        value = "hello%20there"
        self.failUnlessSignatureEqual(
            "HEAD", "http://x/?z=%s&y=abc" % (value,), {"x-ms-date": ["d"]},
            "HEAD\n\n\n\n\n\n\n\n\n\n\n\n"
            "x-ms-date:d\n"
            "/account/\n"
            "y:abc\n"
            "z:hello there")


class MSAzureStorageBackendTests(unittest.TestCase, CloudStorageBackendMixin):
    """
    Tests for the Microsoft Azure Blob API container.

    All code references in docstrings/comments are to classes/functions in
    allmydata.storage.backends.cloud.msazure.msazure_container
    unless noted otherwise.
    """
    def setUp(self):
        self.reactor = Clock()
        self.container = msazure_container.MSAzureStorageContainer(
            "theaccount", "thekey".encode("base64"), "thebucket", self.reactor)
        # Simplify the expected Authorization header:
        self.container._calculate_signature = lambda *args: "signature"
        self.authorization = "signature"
        # Hardcode the time of date header:
        self.container._time = lambda: 123
        self.date = "Thu, 01 Jan 1970 00:02:03 GMT"

    def test_list_objects_no_prefix(self):
        """
        MSAzureStorageContainer.list_objects() with no prefix omits it from
        the query.
        """
        self.mock_http_request()
        self.container.list_objects()
        self.container._http_request.assert_called_once_with(
            "MS Azure list objects", "GET",
            "https://theaccount.blob.core.windows.net/thebucket?comp=list&restype=container",
            {"Authorization": [self.authorization],
             "x-ms-version": ["2012-02-12"],
             "x-ms-date": [self.date],
             },
            body=None,
            need_response_body=True)

    def test_list_objects(self):
        """
        MSAzureStorageContainer.list_objects() sends the appropriate HTTP
        command to list the objects in the bucket, and parses the response to
        match the expected result documented in the IContainer interface.
        """
        LIST_RESPONSE = """\
<?xml version="1.0" encoding="utf-8"?>
<EnumerationResults ContainerName="http://theaccount.blob.core.windows.net/thebucket">
  <MaxResults>4</MaxResults>
  <Blobs>
    <Blob>
      <Name>xxx xxx/firstblob</Name>
      <Url>http://theaccount.blob.core.windows.net/thebucket/xxx%20xxx/firstblob</Url>
      <Properties>
        <Last-Modified>Mon, 30 Jan 2013 01:23:45 GMT</Last-Modified>
        <Etag>abc</Etag>
        <Content-Length>123</Content-Length>
        <Content-Type>text/plain</Content-Type>
        <Content-Encoding />
        <Content-Language>en-US</Content-Language>
        <Content-MD5 />
        <Cache-Control>no-cache</Cache-Control>
        <BlobType>BlockBlob</BlobType>
        <LeaseStatus>unlocked</LeaseStatus>
      </Properties>
    </Blob>
    <Blob>
      <Name>xxx xxx/secondblob</Name>
      <Url>http://myaccount.blob.core.windows.net/mycontainer/xxx%20xxx/secondblob</Url>
      <Properties>
        <Last-Modified>Mon, 30 Jan 2013 01:23:46 GMT</Last-Modified>
        <Etag>def</Etag>
        <Content-Length>100</Content-Length>
        <Content-Type>text/html</Content-Type>
        <Content-Encoding />
        <Content-Language />
        <Content-MD5 />
        <Cache-Control>no-cache</Cache-Control>
        <BlobType>BlockBlob</BlobType>
        <LeaseStatus>unlocked</LeaseStatus>
      </Properties>
    </Blob>
    <Garbage />
  </Blobs>
  <Garbage />
  <NextMarker>xxx xxx/foo</NextMarker>
</EnumerationResults>"""
        http_response = self.mock_http_request()
        done = []
        self.container.list_objects(prefix='xxx xxx/').addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "MS Azure list objects", "GET",
            "https://theaccount.blob.core.windows.net/thebucket?comp=list&restype=container&prefix=xxx%20xxx%2F",
            {"Authorization": [self.authorization],
             "x-ms-version": ["2012-02-12"],
             "x-ms-date": [self.date],
             },
            body=None,
            need_response_body=True)
        http_response.callback((self.Response(200), LIST_RESPONSE))
        listing = done[0]
        self.failUnlessEqual(listing.name, "thebucket")
        self.failUnlessEqual(listing.prefix, "xxx xxx/")
        self.failUnlessEqual(listing.marker, "xxx xxx/foo")
        self.failUnlessEqual(listing.max_keys, None)
        self.failUnlessEqual(listing.is_truncated, "false")
        item1, item2 = listing.contents
        self.failUnlessEqual(item1.key, "xxx xxx/firstblob")
        self.failUnlessEqual(item1.modification_date, "Mon, 30 Jan 2013 01:23:45 GMT")
        self.failUnlessEqual(item1.etag, 'abc')
        self.failUnlessEqual(item1.size, 123)
        self.failUnlessEqual(item1.owner, None) # meh, who cares
        self.failUnlessEqual(item2.key, "xxx xxx/secondblob")
        self.failUnlessEqual(item2.modification_date, "Mon, 30 Jan 2013 01:23:46 GMT")
        self.failUnlessEqual(item2.etag, 'def')
        self.failUnlessEqual(item2.size, 100)
        self.failUnlessEqual(item2.owner, None) # meh, who cares

    def test_put_object(self):
        """
        MSAzureStorageContainer.put_object() sends the appropriate HTTP command
        to upload an object to the bucket, and parses the response to match
        the expected result documented in the IContainer interface.
        """
        http_response = self.mock_http_request()
        done = []
        self.container.put_object("theobj", "the body").addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "MS Azure PUT object", "PUT",
            "https://theaccount.blob.core.windows.net/thebucket/theobj",
            {"Authorization": [self.authorization],
             "x-ms-version": ["2012-02-12"],
             "Content-Type": ["application/octet-stream"],
             "Content-Length": [str(len("the body"))],
             "x-ms-date": [self.date],
             "x-ms-blob-type": ["BlockBlob"],
             },
            body="the body",
            need_response_body=False)
        http_response.callback((self.Response(200), None))
        self.failUnless(done)

    def test_put_object_additional(self):
        """
        MSAzureStorageContainer.put_object() sends the appropriate HTTP command
        to upload an object to the bucket with custom content type and
        metadata, and parses the response to match the expected result
        documented in the IContainer interface.
        """
        http_response = self.mock_http_request()
        done = []
        self.container.put_object("theobj", "the body",
                                  "text/plain",
                                  {"key": "value"}).addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "MS Azure PUT object", "PUT",
            "https://theaccount.blob.core.windows.net/thebucket/theobj",
            {"Authorization": [self.authorization],
             "x-ms-version": ["2012-02-12"],
             "Content-Type": ["text/plain"],
             "Content-Length": [str(len("the body"))],
             "x-ms-meta-key": ["value"],
             "x-ms-date": [self.date],
             "x-ms-blob-type": ["BlockBlob"],
             },
            body="the body",
            need_response_body=False)
        http_response.callback((self.Response(200), None))
        self.failUnless(done)

    def test_get_object(self):
        """
        MSAzureStorageContainer.get_object() sends the appropriate HTTP command
        to get an object from the bucket, and parses the response to match the
        expected result documented in the IContainer interface.
        """
        http_response = self.mock_http_request()
        done = []
        self.container.get_object("theobj").addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "MS Azure GET object", "GET",
            "https://theaccount.blob.core.windows.net/thebucket/theobj",
            {"Authorization": [self.authorization],
             "x-ms-version": ["2012-02-12"],
             "x-ms-date": [self.date],
             },
            body=None,
            need_response_body=True)
        http_response.callback((self.Response(200), "the body"))
        self.failUnlessEqual(done, ["the body"])

    def test_delete_object(self):
        """
        MSAzureStorageContainer.delete_object() sends the appropriate HTTP
        command to delete an object from the bucket, and parses the response
        to match the expected result documented in the IContainer interface.
        """
        http_response = self.mock_http_request()
        done = []
        self.container.delete_object("theobj").addCallback(done.append)
        self.failIf(done)
        self.container._http_request.assert_called_once_with(
            "MS Azure DELETE object", "DELETE",
            "https://theaccount.blob.core.windows.net/thebucket/theobj",
            {"Authorization": [self.authorization],
             "x-ms-version": ["2012-02-12"],
             "x-ms-date": [self.date],
             },
            body=None,
            need_response_body=False)
        http_response.callback((self.Response(200), None))
        self.failUnless(done)


class ServerMixin:
    def allocate(self, account, storage_index, sharenums, size, canary=None):
        # These secrets are not used, but clients still provide them.
        renew_secret = hashutil.tagged_hash("blah", "%d" % self._lease_secret.next())
        cancel_secret = hashutil.tagged_hash("blah", "%d" % self._lease_secret.next())
        if not canary:
            canary = FakeCanary()
        return defer.maybeDeferred(account.remote_allocate_buckets,
                                   storage_index, renew_secret, cancel_secret,
                                   sharenums, size, canary)

    def _write_and_close(self, ign, i, bw):
        d = defer.succeed(None)
        d.addCallback(lambda ign: bw.remote_write(0, "%25d" % i))
        d.addCallback(lambda ign: bw.remote_close())
        return d

    def _close_writer(self, ign, i, bw):
        return bw.remote_close()

    def _abort_writer(self, ign, i, bw):
        return bw.remote_abort()


class ServerTest(ServerMixin, ShouldFailMixin):
    def test_create(self):
        server = self.create("test_create")
        aa = server.get_accountant().get_anonymous_account()
        self.failUnless(RIStorageServer.providedBy(aa), aa)

    def test_declares_fixed_1528(self):
        server = self.create("test_declares_fixed_1528")
        aa = server.get_accountant().get_anonymous_account()

        ver = aa.remote_get_version()
        sv1 = ver['http://allmydata.org/tahoe/protocols/storage/v1']
        self.failUnless(sv1.get('prevents-read-past-end-of-share-data'), sv1)

    def test_has_immutable_readv(self):
        server = self.create("test_has_immutable_readv")
        aa = server.get_accountant().get_anonymous_account()

        ver = aa.remote_get_version()
        sv1 = ver['http://allmydata.org/tahoe/protocols/storage/v1']
        self.failUnless(sv1.get('has-immutable-readv'), sv1)

        # TODO: test that we actually support it

    def test_declares_maximum_share_sizes(self):
        server = self.create("test_declares_maximum_share_sizes")
        aa = server.get_accountant().get_anonymous_account()

        ver = aa.remote_get_version()
        sv1 = ver['http://allmydata.org/tahoe/protocols/storage/v1']
        self.failUnlessIn('maximum-immutable-share-size', sv1)
        self.failUnlessIn('maximum-mutable-share-size', sv1)

    def test_create_share(self):
        server = self.create("test_create_share")
        backend = server.backend
        aa = server.get_accountant().get_anonymous_account()

        d = self.allocate(aa, "si1", [0], 75)
        def _allocated( (already, writers) ):
            self.failUnlessEqual(already, set())
            self.failUnlessEqual(set(writers.keys()), set([0]))

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: writers[0].remote_write(0, "data"))
            d2.addCallback(lambda ign: writers[0].remote_close())

            d2.addCallback(lambda ign: backend.get_shareset("si1").get_share(0))
            d2.addCallback(lambda share: self.failUnless(interfaces.IShareForReading.providedBy(share)))

            d2.addCallback(lambda ign: backend.get_shareset("si1").get_shares())
            def _check( (shares, corrupted) ):
                self.failUnlessEqual(len(shares), 1, str(shares))
                self.failUnlessEqual(len(corrupted), 0, str(corrupted))
            d2.addCallback(_check)
            return d2
        d.addCallback(_allocated)
        return d

    def test_dont_overfill_dirs(self):
        """
        This test asserts that if you add a second share whose storage index
        share lots of leading bits with an extant share (but isn't the exact
        same storage index), this won't add an entry to the share directory.
        """
        server = self.create("test_dont_overfill_dirs")
        aa = server.get_accountant().get_anonymous_account()
        storedir = os.path.join(self.workdir("test_dont_overfill_dirs"),
                                "shares")

        def _write_and_get_children( (already, writers) ):
            d = for_items(self._write_and_close, writers)
            d.addCallback(lambda ign: sorted(fileutil.listdir(storedir)))
            return d

        d = self.allocate(aa, "storageindex", [0], 25)
        d.addCallback(_write_and_get_children)

        def _got_children(children_of_storedir):
            # Now store another one under another storageindex that has leading
            # chars the same as the first storageindex.
            d2 = self.allocate(aa, "storageindey", [0], 25)
            d2.addCallback(_write_and_get_children)
            d2.addCallback(lambda res: self.failUnlessEqual(res, children_of_storedir))
            return d2
        d.addCallback(_got_children)
        return d

    def OFF_test_allocate(self):
        server = self.create("test_allocate")
        aa = server.get_accountant().get_anonymous_account()

        self.failUnlessEqual(aa.remote_get_buckets("allocate"), {})

        already,writers = self.allocate(aa, "allocate", [0,1,2], 75)
        self.failUnlessEqual(already, set())
        self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

        # while the buckets are open, they should not count as readable
        self.failUnlessEqual(aa.remote_get_buckets("allocate"), {})

        # close the buckets
        for i,wb in writers.items():
            wb.remote_write(0, "%25d" % i)
            wb.remote_close()
            # aborting a bucket that was already closed is a no-op
            wb.remote_abort()

        # now they should be readable
        b = aa.remote_get_buckets("allocate")
        self.failUnlessEqual(set(b.keys()), set([0,1,2]))
        self.failUnlessEqual(b[0].remote_read(0, 25), "%25d" % 0)
        b_str = str(b[0])
        self.failUnlessIn("BucketReader", b_str)
        self.failUnlessIn("mfwgy33dmf2g 0", b_str)

        # now if we ask about writing again, the server should offer those
        # three buckets as already present. It should offer them even if we
        # don't ask about those specific ones.
        already,writers = self.allocate(aa, "allocate", [2,3,4], 75)
        self.failUnlessEqual(already, set([0,1,2]))
        self.failUnlessEqual(set(writers.keys()), set([3,4]))

        # while those two buckets are open for writing, the server should
        # refuse to offer them to uploaders

        already2,writers2 = self.allocate(aa, "allocate", [2,3,4,5], 75)
        self.failUnlessEqual(already2, set([0,1,2]))
        self.failUnlessEqual(set(writers2.keys()), set([5]))

        # aborting the writes should remove the tempfiles
        for i,wb in writers2.items():
            wb.remote_abort()
        already2,writers2 = self.allocate(aa, "allocate", [2,3,4,5], 75)
        self.failUnlessEqual(already2, set([0,1,2]))
        self.failUnlessEqual(set(writers2.keys()), set([5]))

        for i,wb in writers2.items():
            wb.remote_abort()
        for i,wb in writers.items():
            wb.remote_abort()

    # The following share file content was generated with
    # storage.immutable.ShareFile from Tahoe-LAFS v1.8.2
    # with share data == 'a'. The total size of this input
    # is 85 bytes.
    shareversionnumber = '\x00\x00\x00\x01'
    sharedatalength = '\x00\x00\x00\x01'
    numberofleases = '\x00\x00\x00\x01'
    shareinputdata = 'a'
    ownernumber = '\x00\x00\x00\x00'
    renewsecret  = 'x'*32
    cancelsecret = 'y'*32
    expirationtime = '\x00(\xde\x80'
    nextlease = ''
    containerdata = shareversionnumber + sharedatalength + numberofleases
    client_data = (shareinputdata + ownernumber + renewsecret +
                   cancelsecret + expirationtime + nextlease)
    share_data = containerdata + client_data
    testnodeid = 'testnodeidxxxxxxxxxx'

    def test_write_and_read_share(self):
        """
        Write a new share, read it, and test the server and backends'
        handling of simultaneous and successive attempts to write the same
        share.
        """
        server = self.create("test_write_and_read_share")
        aa = server.get_accountant().get_anonymous_account()
        canary = FakeCanary()

        shareset = server.backend.get_shareset('teststorage_index')
        self.failIf(shareset.has_incoming(0))

        # Populate incoming with the sharenum: 0.
        d = aa.remote_allocate_buckets('teststorage_index', 'x'*32, 'y'*32, frozenset((0,)), 1, canary)
        def _allocated( (already, writers) ):
            # This is a white-box test: Inspect incoming and fail unless the sharenum: 0 is listed there.
            self.failUnless(shareset.has_incoming(0))

            # Attempt to create a second share writer with the same sharenum.
            d2 = aa.remote_allocate_buckets('teststorage_index', 'x'*32, 'y'*32, frozenset((0,)), 1, canary)

            # Show that no sharewriter results from a remote_allocate_buckets
            # with the same si and sharenum, until BucketWriter.remote_close()
            # has been called.
            d2.addCallback(lambda (already2, writers2): self.failIf(writers2))

            # Test allocated size.
            d2.addCallback(lambda ign: server.allocated_size())
            d2.addCallback(lambda space: self.failUnlessEqual(space, 1))

            # Write 'a' to shnum 0. Only tested together with close and read.
            d2.addCallback(lambda ign: writers[0].remote_write(0, 'a'))

            # Preclose: Inspect final, failUnless nothing there.
            d2.addCallback(lambda ign: server.backend.get_shareset('teststorage_index').get_shares())
            def _check( (shares, corrupted) ):
                self.failUnlessEqual(len(shares), 0, str(shares))
                self.failUnlessEqual(len(corrupted), 0, str(corrupted))
            d2.addCallback(_check)

            d2.addCallback(lambda ign: writers[0].remote_close())

            # Postclose: fail unless written data is in final.
            d2.addCallback(lambda ign: server.backend.get_shareset('teststorage_index').get_shares())
            def _got_shares( (sharesinfinal, corrupted) ):
                self.failUnlessEqual(len(sharesinfinal), 1, str(sharesinfinal))
                self.failUnlessEqual(len(corrupted), 0, str(corrupted))

                d3 = defer.succeed(None)
                d3.addCallback(lambda ign: sharesinfinal[0].read_share_data(0, 73))
                d3.addCallback(lambda contents: self.failUnlessEqual(contents, self.shareinputdata))
                return d3
            d2.addCallback(_got_shares)

            # Exercise the case that the share we're asking to allocate is
            # already (completely) uploaded.
            d2.addCallback(lambda ign: aa.remote_allocate_buckets('teststorage_index',
                                                                  'x'*32, 'y'*32, set((0,)), 1, canary))
            return d2
        d.addCallback(_allocated)
        return d

    def test_read_old_share(self):
        """
        This tests whether the code correctly finds and reads shares written out by
        pre-pluggable-backends (Tahoe-LAFS <= v1.8.2) servers. There is a similar test
        in test_download, but that one is from the perspective of the client and exercises
        a deeper stack of code. This one is for exercising just the StorageServer and backend.
        """
        server = self.create("test_read_old_share")
        aa = server.get_accountant().get_anonymous_account()

        # Contruct a file with the appropriate contents.
        datalen = len(self.share_data)
        sharedir = server.backend.get_shareset('teststorage_index')._get_sharedir()
        fileutil.make_dirs(sharedir)
        fileutil.write(os.path.join(sharedir, "0"), self.share_data)

        # Now begin the test.
        d = aa.remote_get_buckets('teststorage_index')
        def _got_buckets(bs):
            self.failUnlessEqual(len(bs), 1)
            self.failUnlessIn(0, bs)
            b = bs[0]

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: b.remote_read(0, datalen))
            d2.addCallback(lambda res: self.failUnlessEqual(res, self.shareinputdata))

            # If you try to read past the end you get as much input data as is there.
            d2.addCallback(lambda ign: b.remote_read(0, datalen+20))
            d2.addCallback(lambda res: self.failUnlessEqual(res, self.shareinputdata))

            # If you start reading past the end of the file you get the empty string.
            d2.addCallback(lambda ign: b.remote_read(datalen+1, 3))
            d2.addCallback(lambda res: self.failUnlessEqual(res, ''))
            return d2
        d.addCallback(_got_buckets)
        return d

    def test_bad_container_version(self):
        server = self.create("test_bad_container_version")
        aa = server.get_accountant().get_anonymous_account()

        d = self.allocate(aa, "allocate", [0,1], 20)
        def _allocated( (already, writers) ):
            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: writers[0].remote_write(0, "\xff"*10))
            d2.addCallback(lambda ign: writers[0].remote_close())
            d2.addCallback(lambda ign: writers[1].remote_write(1, "\xaa"*10))
            d2.addCallback(lambda ign: writers[1].remote_close())
            return d2
        d.addCallback(_allocated)

        d.addCallback(lambda ign: server.backend.get_shareset("allocate").get_share(0))
        def _write_invalid_version(share0):
            f = open(share0._get_path(), "rb+")
            try:
                f.seek(0)
                f.write(struct.pack(">L", 0)) # this is invalid: minimum used is v1
            finally:
                f.close()
        d.addCallback(_write_invalid_version)

        # This should ignore the corrupted share; see ticket #1566.
        d.addCallback(lambda ign: aa.remote_get_buckets("allocate"))
        d.addCallback(lambda b: self.failUnlessEqual(set(b.keys()), set([1])))

        # Also if there are only corrupted shares.
        d.addCallback(lambda ign: server.backend.get_shareset("allocate").get_share(1))
        d.addCallback(lambda share: share.unlink())
        d.addCallback(lambda ign: aa.remote_get_buckets("allocate"))
        d.addCallback(lambda b: self.failUnlessEqual(b, {}))
        return d

    def test_advise_corruption(self):
        server = self.create("test_advise_corruption")
        aa = server.get_accountant().get_anonymous_account()

        si0_s = base32.b2a("si0")
        aa.remote_advise_corrupt_share("immutable", "si0", 0,
                                       "This share smells funny.\n")
        reportdir = os.path.join(server._statedir, "corruption-advisories")
        self.failUnless(os.path.exists(reportdir), reportdir)
        reports = fileutil.listdir(reportdir)
        self.failUnlessEqual(len(reports), 1)
        report_si0 = reports[0]
        self.failUnlessIn(si0_s, str(report_si0))
        report = fileutil.read(os.path.join(reportdir, report_si0))

        self.failUnlessIn("type: immutable", report)
        self.failUnlessIn("storage_index: %s" % si0_s, report)
        self.failUnlessIn("share_number: 0", report)
        self.failUnlessIn("This share smells funny.", report)

        # test the RIBucketWriter version too
        si1_s = base32.b2a("si1")
        d = self.allocate(aa, "si1", [1], 75)
        def _allocated( (already, writers) ):
            self.failUnlessEqual(already, set())
            self.failUnlessEqual(set(writers.keys()), set([1]))

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: writers[1].remote_write(0, "data"))
            d2.addCallback(lambda ign: writers[1].remote_close())

            d2.addCallback(lambda ign: aa.remote_get_buckets("si1"))
            def _got_buckets(b):
                self.failUnlessEqual(set(b.keys()), set([1]))
                b[1].remote_advise_corrupt_share("This share tastes like dust.\n")

                reports = fileutil.listdir(reportdir)
                self.failUnlessEqual(len(reports), 2)
                report_si1 = [r for r in reports if si1_s in r][0]
                report = fileutil.read(os.path.join(reportdir, report_si1))

                self.failUnlessIn("type: immutable", report)
                self.failUnlessIn("storage_index: %s" % (si1_s,), report)
                self.failUnlessIn("share_number: 1", report)
                self.failUnlessIn("This share tastes like dust.", report)
            d2.addCallback(_got_buckets)
            return d2
        d.addCallback(_allocated)
        return d

    def compare_leases(self, leases_a, leases_b, with_timestamps=True):
        self.failUnlessEqual(len(leases_a), len(leases_b))
        for i in range(len(leases_a)):
            a = leases_a[i]
            b = leases_b[i]
            self.failUnlessEqual(a.owner_num, b.owner_num)
            if with_timestamps:
                self.failUnlessEqual(a.renewal_time, b.renewal_time)
                self.failUnlessEqual(a.expiration_time, b.expiration_time)

    def OFF_test_immutable_leases(self):
        server = self.create("test_immutable_leases")
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        canary = FakeCanary()
        sharenums = range(5)
        size = 100

        # create a random non-numeric file in the bucket directory, to
        # exercise the code that's supposed to ignore those.
        bucket_dir = os.path.join(self.workdir("test_leases"),
                                  "shares", storage_index_to_dir("six"))
        os.makedirs(bucket_dir)
        fileutil.write(os.path.join(bucket_dir, "ignore_me.txt"),
                       "you ought to be ignoring me\n")

        already,writers = aa.remote_allocate_buckets("si1", "", "",
                                                     sharenums, size, canary)
        self.failUnlessEqual(len(already), 0)
        self.failUnlessEqual(len(writers), 5)
        for wb in writers.values():
            wb.remote_close()

        leases = aa.get_leases("si1")
        self.failUnlessEqual(len(leases), 5)

        aa.add_share("six", 0, 0, SHARETYPE_IMMUTABLE)
        # adding a share does not immediately add a lease
        self.failUnlessEqual(len(aa.get_leases("six")), 0)

        aa.add_or_renew_default_lease("six", 0)
        self.failUnlessEqual(len(aa.get_leases("six")), 1)

        # add-lease on a missing storage index is silently ignored
        self.failUnlessEqual(aa.remote_add_lease("si18", "", ""), None)
        self.failUnlessEqual(len(aa.get_leases("si18")), 0)

        all_leases = aa.get_leases("si1")

        # renew the lease directly
        aa.remote_renew_lease("si1", "")
        self.failUnlessEqual(len(aa.get_leases("si1")), 5)
        self.compare_leases(all_leases, aa.get_leases("si1"), with_timestamps=False)

        # Now allocate more leases using a different account.
        # A new lease should be allocated for every share in the shareset.
        sa.remote_renew_lease("si1", "")
        self.failUnlessEqual(len(aa.get_leases("si1")), 5)
        self.failUnlessEqual(len(sa.get_leases("si1")), 5)

        all_leases2 = sa.get_leases("si1")

        sa.remote_renew_lease("si1", "")
        self.compare_leases(all_leases2, sa.get_leases("si1"), with_timestamps=False)


class MutableServerMixin:
    def write_enabler(self, we_tag):
        return hashutil.tagged_hash("we_blah", we_tag)

    def renew_secret(self, tag):
        return hashutil.tagged_hash("renew_blah", str(tag))

    def cancel_secret(self, tag):
        return hashutil.tagged_hash("cancel_blah", str(tag))

    def allocate(self, aa, storage_index, we_tag, sharenums, size):
        write_enabler = self.write_enabler(we_tag)

        # These secrets are not used, but clients still provide them.
        lease_tag = "%d" % (self._lease_secret.next(),)
        renew_secret = self.renew_secret(lease_tag)
        cancel_secret = self.cancel_secret(lease_tag)

        rstaraw = aa.remote_slot_testv_and_readv_and_writev
        testandwritev = dict( [ (shnum, ([], [], None) )
                                for shnum in sharenums ] )
        readv = []

        d = defer.succeed(None)
        d.addCallback(lambda ign: rstaraw(storage_index,
                                          (write_enabler, renew_secret, cancel_secret),
                                          testandwritev,
                                          readv))
        def _check( (did_write, readv_data) ):
            self.failUnless(did_write)
            self.failUnless(isinstance(readv_data, dict))
            self.failUnlessEqual(len(readv_data), 0)
        d.addCallback(_check)
        return d


class MutableServerTest(MutableServerMixin, ShouldFailMixin):
    def test_create(self):
        server = self.create("test_create")
        aa = server.get_accountant().get_anonymous_account()
        self.failUnless(RIStorageServer.providedBy(aa), aa)

    def test_bad_magic(self):
        server = self.create("test_bad_magic")
        aa = server.get_accountant().get_anonymous_account()
        read = aa.remote_slot_readv

        d = self.allocate(aa, "si1", "we1", set([0,1]), 10)
        d.addCallback(lambda ign: server.backend.get_shareset("si1").get_share(0))
        def _got_share(share0):
            f = open(share0._get_path(), "rb+")
            try:
                f.seek(0)
                f.write("BAD MAGIC")
            finally:
                f.close()
        d.addCallback(_got_share)

        # This should ignore the corrupted share; see ticket #1566.
        d.addCallback(lambda ign: read("si1", [0,1], [(0,10)]) )
        d.addCallback(lambda res: self.failUnlessEqual(res, {1: ['']}))

        # Also if there are only corrupted shares.
        d.addCallback(lambda ign: server.backend.get_shareset("si1").get_share(1))
        d.addCallback(lambda share: share.unlink())
        d.addCallback(lambda ign: read("si1", [0], [(0,10)]) )
        d.addCallback(lambda res: self.failUnlessEqual(res, {}))
        return d

    def test_container_size(self):
        server = self.create("test_container_size")
        aa = server.get_accountant().get_anonymous_account()
        read = aa.remote_slot_readv
        rstaraw = aa.remote_slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])

        d = self.allocate(aa, "si1", "we1", set([0,1,2]), 100)
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [(0,data)], len(data)+12)},
                                          []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))

        # Trying to make the container too large (by sending a write vector
        # whose offset is too high) will raise an exception.
        TOOBIG = MutableDiskShare.MAX_SIZE + 10
        d.addCallback(lambda ign: self.shouldFail(DataTooLargeError,
                                                  'make container too large', None,
                                                  lambda: rstaraw("si1", secrets,
                                                                  {0: ([], [(TOOBIG,data)], None)},
                                                                  []) ))

        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [(0,data)], None)},
                                          []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))

        d.addCallback(lambda ign: read("si1", [0], [(0,10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data[:10]]}))

        # Sending a new_length shorter than the current length truncates the
        # data.
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [], 9)},
                                          []))
        d.addCallback(lambda ign: read("si1", [0], [(0,10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data[:9]]}))

        # Sending a new_length longer than the current length doesn't change
        # the data.
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [], 20)},
                                          []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0, 20)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data[:9]]}))

        # Sending a write vector whose start is after the end of the current
        # data doesn't reveal "whatever was there last time" (palimpsest),
        # but instead fills with zeroes.

        # To test this, we fill the data area with a recognizable pattern.
        pattern = ''.join([chr(i) for i in range(100)])
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [(0, pattern)], None)},
                                          []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))
        # Then truncate the data...
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [], 20)},
                                          []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))
        # Just confirm that you get an empty string if you try to read from
        # past the (new) endpoint now.
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [], None)},
                                          [(20, 1980)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[''],1:[''],2:['']}) ))

        # Then the extend the file by writing a vector which starts out past
        # the end...
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [(50, 'hellothere')], None)},
                                          []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))
        # Now if you read the stuff between 20 (where we earlier truncated)
        # and 50, it had better be all zeroes.
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [], None)},
                                          [(20, 30)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:['\x00'*30],1:[''],2:['']}) ))

        # Also see if the server explicitly declares that it supports this
        # feature.
        d.addCallback(lambda ign: aa.remote_get_version())
        def _check_declaration(ver):
            storage_v1_ver = ver["http://allmydata.org/tahoe/protocols/storage/v1"]
            self.failUnless(storage_v1_ver.get("fills-holes-with-zero-bytes"))
        d.addCallback(_check_declaration)

        # If the size is dropped to zero the share is deleted.
        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [(0,data)], 0)},
                                          []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))

        d.addCallback(lambda ign: read("si1", [0], [(0,10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {}))
        return d

    def test_allocate(self):
        server = self.create("test_allocate")
        aa = server.get_accountant().get_anonymous_account()
        read = aa.remote_slot_readv
        write = aa.remote_slot_testv_and_readv_and_writev

        d = self.allocate(aa, "si1", "we1", set([0,1,2]), 100)

        d.addCallback(lambda ign: read("si1", [0], [(0, 10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [""]}))
        d.addCallback(lambda ign: read("si1", [], [(0, 10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [""], 1: [""], 2: [""]}))
        d.addCallback(lambda ign: read("si1", [0], [(100, 10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [""]}))

        # try writing to one
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])

        d.addCallback(lambda ign: write("si1", secrets,
                                        {0: ([], [(0,data)], None)},
                                        []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))

        d.addCallback(lambda ign: read("si1", [0], [(0,20)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["00000000001111111111"]}))
        d.addCallback(lambda ign: read("si1", [0], [(95,10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["99999"]}))
        #d.addCallback(lambda ign: s0.remote_get_length())
        #d.addCallback(lambda res: self.failUnlessEqual(res, 100))

        bad_secrets = ("bad write enabler", secrets[1], secrets[2])
        d.addCallback(lambda ign: self.shouldFail(BadWriteEnablerError, 'bad write enabler',
                                                  "The write enabler was recorded by nodeid "
                                                  "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'.",
                                                  lambda: write("si1", bad_secrets, {}, []) ))

        # this testv should fail
        d.addCallback(lambda ign: write("si1", secrets,
                                        {0: ([(0, 12, "eq", "444444444444"),
                                              (20, 5, "eq", "22222"),],
                                             [(0, "x"*100)],
                                             None)},
                                        [(0,12), (20,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False,
                                                             {0: ["000000000011", "22222"],
                                                              1: ["", ""],
                                                              2: ["", ""]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))

        # as should this one
        d.addCallback(lambda ign: write("si1", secrets,
                                        {0: ([(10, 5, "lt", "11111"),],
                                             [(0, "x"*100)],
                                             None)},
                                        [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False,
                                                             {0: ["11111"],
                                                              1: [""],
                                                              2: [""]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        return d

    def test_operators(self):
        # test operators, the data we're comparing is '11111' in all cases.
        # test both fail+pass, reset data after each one.
        server = self.create("test_operators")
        aa = server.get_accountant().get_anonymous_account()

        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = aa.remote_slot_testv_and_readv_and_writev
        read = aa.remote_slot_readv

        def _reset(ign):
            return write("si1", secrets,
                         {0: ([], [(0,data)], None)},
                         [])

        d = defer.succeed(None)
        d.addCallback(_reset)

        #  lt
        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "lt", "11110"),],
                                                             [(0, "x"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(lambda ign: read("si1", [], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "lt", "11111"),],
                                                             [(0, "x"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "lt", "11112"),],
                                                             [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["y"*100]}))
        d.addCallback(_reset)

        #  le
        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "le", "11110"),],
                                                             [(0, "x"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "le", "11111"),],
                                                             [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["y"*100]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "le", "11112"),],
                                                             [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["y"*100]}))
        d.addCallback(_reset)

        #  eq
        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "eq", "11112"),],
                                                             [(0, "x"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "eq", "11111"),],
                                                             [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["y"*100]}))
        d.addCallback(_reset)

        #  ne
        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "ne", "11111"),],
                                                             [(0, "x"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "ne", "11112"),],
                                                              [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["y"*100]}))
        d.addCallback(_reset)

        #  ge
        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "ge", "11110"),],
                                                             [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["y"*100]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "ge", "11111"),],
                                                             [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["y"*100]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "ge", "11112"),],
                                                             [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)

        #  gt
        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "gt", "11110"),],
                                                             [(0, "y"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["y"*100]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "gt", "11111"),],
                                                             [(0, "x"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)

        d.addCallback(lambda ign: write("si1", secrets, {0: ([(10, 5, "gt", "11112"),],
                                                             [(0, "x"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)

        # finally, test some operators against empty shares
        d.addCallback(lambda ign: write("si1", secrets, {1: ([(10, 5, "eq", "11112"),],
                                                             [(0, "x"*100)],
                                                             None,
                                                            )}, [(10,5)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, (False, {0: ["11111"]}) ))
        d.addCallback(lambda ign: read("si1", [0], [(0,100)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        d.addCallback(_reset)
        return d

    def test_readv(self):
        server = self.create("test_readv")
        aa = server.get_accountant().get_anonymous_account()

        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        write = aa.remote_slot_testv_and_readv_and_writev
        read = aa.remote_slot_readv
        data = [("%d" % i) * 100 for i in range(3)]

        d = defer.succeed(None)
        d.addCallback(lambda ign: write("si1", secrets,
                                        {0: ([], [(0,data[0])], None),
                                         1: ([], [(0,data[1])], None),
                                         2: ([], [(0,data[2])], None),
                                        }, []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {}) ))

        d.addCallback(lambda ign: read("si1", [], [(0, 10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: ["0"*10],
                                                             1: ["1"*10],
                                                             2: ["2"*10]}))
        return d

    def test_writev(self):
        # This is run for both the disk and cloud backends, but it is particularly
        # designed to exercise the cloud backend's implementation of chunking for
        # mutable shares, assuming that PREFERRED_CHUNK_SIZE has been patched to 500.
        # Note that the header requires 472 bytes, so only the first 28 bytes of data are
        # in the first chunk.

        server = self.create("test_writev")
        aa = server.get_accountant().get_anonymous_account()
        read = aa.remote_slot_readv
        rstaraw = aa.remote_slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )

        def _check(ign, writev, expected_data, expected_write_loads, expected_write_stores,
                   expected_read_loads, should_exist):
            d2 = rstaraw("si1", secrets, {0: writev}, [])
            if should_exist:
                d2.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[]}) ))
            else:
                d2.addCallback(lambda res: self.failUnlessEqual(res, (True, {}) ))
            d2.addCallback(lambda ign: self.check_load_store_counts(expected_write_loads,
                                                                    expected_write_stores))
            d2.addCallback(lambda ign: self.reset_load_store_counts())

            d2.addCallback(lambda ign: read("si1", [0], [(0, len(expected_data) + 1)]))
            if expected_data == "":
                d2.addCallback(lambda res: self.failUnlessEqual(res, {}))
            else:
                d2.addCallback(lambda res: self.failUnlessEqual(res, {0: [expected_data]}))
            d2.addCallback(lambda ign: self.check_load_store_counts(expected_read_loads, 0))
            d2.addCallback(lambda ign: self.reset_load_store_counts())
            return d2

        self.reset_load_store_counts()
        d = self.allocate(aa, "si1", "we1", set([0]), 2725)
        d.addCallback(_check, ([], [(0, "a"*10)], None),
                              "a"*10,
                              1, 2, 1, True)
        d.addCallback(_check, ([], [(20, "b"*18)], None),
                              "a"*10 + "\x00"*10 + "b"*18,
                              1, 2, 2, True)
        d.addCallback(_check, ([], [(1038, "c")], None),
                              "a"*10 + "\x00"*10 + "b"*18 + "\x00"*(490+500+10) + "c",
                              2, 4, 4, True)
        d.addCallback(_check, ([], [(0, "d"*1038)], None),
                              "d"*1038 + "c",
                              2, 4, 4, True)
        d.addCallback(_check, ([], [(2167, "a"*54)], None),
                              "d"*1038 + "c" + "\x00"*1128 + "a"*54,
                              2, 4, 6, True)
        # This pattern was observed from the MDMF publisher in v1.9.1.
        # Notice the duplicated write of length 41 bytes at offset 0.
        d.addCallback(_check, ([], [(2167, "e"*54), (123, "f"*347), (2221, "g"*32), (470, "h"*136),
                                    (0, "i"*41), (606, "j"*66), (672, "k"*93), (59, "l"*64),
                                    (41, "m"*18), (0, "i"*41)], None),
                              "i"*41 + "m"*18 + "l"*64 + "f"*347 + "h"*136 + "j"*66 + "k"*93 + "d"*273 + "c" + "\x00"*1128 +
                              "e"*54 + "g"*32,
                              4, 4, 6, True)
        # This should delete all chunks.
        d.addCallback(_check, ([], [], 0),
                              "",
                              1, 0, 0, True)
        d.addCallback(_check, ([], [(2167, "e"*54), (123, "f"*347), (2221, "g"*32), (470, "h"*136),
                                    (0, "i"*41), (606, "j"*66), (672, "k"*93), (59, "l"*64),
                                    (41, "m"*18), (0, "i"*41)], None),
                              "i"*41 + "m"*18 + "l"*64 + "f"*347 + "h"*136 + "j"*66 + "k"*93 + "\x00"*1402 +
                              "e"*54 + "g"*32,
                              0, 7, 6, False)
        return d

    def test_remove(self):
        server = self.create("test_remove")
        aa = server.get_accountant().get_anonymous_account()
        readv = aa.remote_slot_readv
        writev = aa.remote_slot_testv_and_readv_and_writev
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )

        d = defer.succeed(None)
        d.addCallback(lambda ign: self.allocate(aa, "si1", "we1", set([0,1,2]), 100))
        # delete sh0 by setting its size to zero
        d.addCallback(lambda ign: writev("si1", secrets,
                                         {0: ([], [], 0)},
                                         []))
        # the answer should mention all the shares that existed before the
        # write
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))
        # but a new read should show only sh1 and sh2
        d.addCallback(lambda ign: readv("si1", [], [(0,10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {1: [""], 2: [""]}))

        # delete sh1 by setting its size to zero
        d.addCallback(lambda ign: writev("si1", secrets,
                                         {1: ([], [], 0)},
                                         []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {1:[],2:[]}) ))
        d.addCallback(lambda ign: readv("si1", [], [(0,10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {2: [""]}))

        # delete sh2 by setting its size to zero
        d.addCallback(lambda ign: writev("si1", secrets,
                                         {2: ([], [], 0)},
                                         []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {2:[]}) ))
        d.addCallback(lambda ign: readv("si1", [], [(0,10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {}))

        d.addCallback(lambda ign: server.backend.get_shareset("si1").get_overhead())
        d.addCallback(lambda overhead: self.failUnlessEqual(overhead, 0))

        # and the shareset directory should now be gone. This check is only
        # applicable to the disk backend.
        def _check_gone(ign):
            si = base32.b2a("si1")
            # note: this is a detail of the disk backend, and may change in the future
            prefix = si[:2]
            prefixdir = os.path.join(self.workdir("test_remove"), "shares", prefix)
            sidir = os.path.join(prefixdir, si)
            self.failUnless(os.path.exists(prefixdir), prefixdir)
            self.failIf(os.path.exists(sidir), sidir)

        if isinstance(server.backend, DiskBackend):
            d.addCallback(_check_gone)
        return d

    def compare_leases(self, leases_a, leases_b, with_timestamps=True):
        self.failUnlessEqual(len(leases_a), len(leases_b))
        for i in range(len(leases_a)):
            a = leases_a[i]
            b = leases_b[i]
            self.failUnlessEqual(a.owner_num, b.owner_num)
            if with_timestamps:
                self.failUnlessEqual(a.renewal_time, b.renewal_time)
                self.failUnlessEqual(a.expiration_time, b.expiration_time)

    def test_mutable_leases(self):
        server = self.create("test_mutable_leases")
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        def secrets(n):
            return ( self.write_enabler("we1"),
                     self.renew_secret("we1-%d" % n),
                     self.cancel_secret("we1-%d" % n) )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])
        aa_write = aa.remote_slot_testv_and_readv_and_writev
        sa_write = sa.remote_slot_testv_and_readv_and_writev
        read = aa.remote_slot_readv

        # There is no such method as remote_cancel_lease -- see ticket #1528.
        self.failIf(hasattr(aa, 'remote_cancel_lease'),
                    "aa should not have a 'remote_cancel_lease' method/attribute")

        # create a random non-numeric file in the bucket directory, to
        # exercise the code that's supposed to ignore those.
        bucket_dir = os.path.join(self.workdir("test_leases"),
                                  "shares", storage_index_to_dir("six"))
        os.makedirs(bucket_dir)
        fileutil.write(os.path.join(bucket_dir, "ignore_me.txt"),
                       "you ought to be ignoring me\n")

        create_mutable_disk_share(os.path.join(bucket_dir, "0"), server.get_serverid(),
                                  secrets(0)[0], storage_index="six", shnum=0)

        aa.add_share("six", 0, 0, SHARETYPE_MUTABLE)
        # adding a share does not immediately add a lease
        self.failUnlessEqual(len(aa.get_leases("six")), 0)

        aa.add_or_renew_default_lease("six", 0)
        self.failUnlessEqual(len(aa.get_leases("six")), 1)

        d = defer.succeed(None)

        d.addCallback(lambda ign: aa_write("si0", secrets(1), {0: ([], [(0,data)], None)}, []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {})))

        # add-lease on a missing storage index is silently ignored
        d.addCallback(lambda ign: aa.remote_add_lease("si18", "", ""))
        d.addCallback(lambda res: self.failUnless(res is None, res))
        d.addCallback(lambda ign: self.failUnlessEqual(len(aa.get_leases("si18")), 0))

        # create a lease by writing
        d.addCallback(lambda ign: aa_write("si1", secrets(2), {0: ([], [(0,data)], None)}, []))
        d.addCallback(lambda ign: self.failUnlessEqual(len(aa.get_leases("si1")), 1))

        # renew it directly
        d.addCallback(lambda ign: aa.remote_renew_lease("si1", secrets(2)[1]))
        d.addCallback(lambda ign: self.failUnlessEqual(len(aa.get_leases("si1")), 1))

        # now allocate another lease using a different account
        d.addCallback(lambda ign: sa_write("si1", secrets(3), {0: ([], [(0,data)], None)}, []))
        def _check(ign):
            aa_leases = aa.get_leases("si1")
            sa_leases = sa.get_leases("si1")

            self.failUnlessEqual(len(aa_leases), 1)
            self.failUnlessEqual(len(sa_leases), 1)

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: aa.remote_renew_lease("si1", secrets(2)[1]))
            d2.addCallback(lambda ign: self.compare_leases(aa_leases, aa.get_leases("si1"),
                                                           with_timestamps=False))

            d2.addCallback(lambda ign: sa.remote_renew_lease("si1", "shouldn't matter"))
            d2.addCallback(lambda ign: self.compare_leases(sa_leases, sa.get_leases("si1"),
                                                           with_timestamps=False))

            # Get a new copy of the leases, with the current timestamps. Reading
            # data should leave the timestamps alone.
            d2.addCallback(lambda ign: aa.get_leases("si1"))
            def _check2(new_aa_leases):
                # reading shares should not modify the timestamp
                d3 = read("si1", [], [(0, 200)])
                d3.addCallback(lambda ign: self.compare_leases(new_aa_leases, aa.get_leases("si1"),
                                                               with_timestamps=False))

                d3.addCallback(lambda ign: aa_write("si1", secrets(2),
                      {0: ([], [(500, "make me bigger")], None)}, []))
                d3.addCallback(lambda ign: self.compare_leases(new_aa_leases, aa.get_leases("si1"),
                                                               with_timestamps=False))
                return d3
            d2.addCallback(_check2)
            return d2
        d.addCallback(_check)
        return d

    def test_shareset_locking(self):
        server = self.create("test_shareset_locking")
        aa = server.get_accountant().get_anonymous_account()
        rstaraw = aa.remote_slot_testv_and_readv_and_writev
        read = aa.remote_slot_readv
        secrets = ( self.write_enabler("we1"),
                    self.renew_secret("we1"),
                    self.cancel_secret("we1") )
        data = "".join([ ("%d" % i) * 10 for i in range(10) ])

        # Assert that the lock is held while share methods are called.
        was_called = {}
        def make_patched_share_method(old_method):
            def _call(*args, **kwargs):
                was_called[old_method.__name__] = True
                self.failUnless(server.backend._get_lock("si1").locked)
                return old_method(*args, **kwargs)
            return _call

        ShareClass = self.get_mutable_share_class()
        _old_init_share = ShareClass.__init__
        def _init_share(share, *args, **kwargs):
            _old_init_share(share, *args, **kwargs)
            self.patch(share, 'readv', make_patched_share_method(share.readv))
            self.patch(share, 'writev', make_patched_share_method(share.writev))
            self.patch(share, 'check_testv', make_patched_share_method(share.check_testv))
        self.patch(ShareClass, '__init__', _init_share)

        d = self.allocate(aa, "si1", "we1", set([0,1,2]), 100)

        d.addCallback(lambda ign: rstaraw("si1", secrets,
                                          {0: ([], [(0,data)], None)},
                                          []))
        d.addCallback(lambda res: self.failUnlessEqual(res, (True, {0:[],1:[],2:[]}) ))

        d.addCallback(lambda ign: read("si1", [0], [(0,10)]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data[:10]]}))

        d.addCallback(lambda ign:
                      self.failUnlessEqual(was_called, {'readv': True, 'writev': True, 'check_testv': True}))
        return d


class ServerWithNullBackend(ServiceParentMixin, WorkdirMixin, ServerMixin, unittest.TestCase):
    def test_null_backend(self):
        workdir = self.workdir("test_null_backend")
        backend = NullBackend()
        server = StorageServer("\x00" * 20, backend, workdir)
        server.setServiceParent(self.sparent)
        aa = server.get_accountant().get_anonymous_account()

        d = self.allocate(aa, "vid", [0,1,2], 75)
        def _allocated( (already, writers) ):
            self.failUnlessEqual(already, set())
            self.failUnlessEqual(set(writers.keys()), set([0,1,2]))

            d2 = for_items(self._write_and_close, writers)

            # The shares should be present but have no data.
            d2.addCallback(lambda ign: aa.remote_get_buckets("vid"))
            def _check(buckets):
                self.failUnlessEqual(set(buckets.keys()), set([0,1,2]))
                d3 = defer.succeed(None)
                d3.addCallback(lambda ign: buckets[0].remote_read(0, 25))
                d3.addCallback(lambda res: self.failUnlessEqual(res, ""))
                return d3
            d2.addCallback(_check)
            return d2
        d.addCallback(_allocated)
        return d


class WithMockCloudBackend(ServiceParentMixin, WorkdirMixin):
    def create(self, name, detached=False, readonly=False, reserved_space=0, klass=StorageServer):
        assert not readonly
        workdir = self.workdir(name)
        self._container = MockContainer(workdir)
        backend = CloudBackend(self._container)
        server = klass("\x00" * 20, backend, workdir,
                       stats_provider=FakeStatsProvider())
        if not detached:
            server.setServiceParent(self.sparent)
        return server

    def reset_load_store_counts(self):
        self._container.reset_load_store_counts()

    def check_load_store_counts(self, expected_load_count, expected_store_count):
        self.failUnlessEqual((self._container.get_load_count(), self._container.get_store_count()),
                             (expected_load_count, expected_store_count))

    def get_mutable_share_class(self):
        return MutableCloudShare


class WithDiskBackend(ServiceParentMixin, WorkdirMixin):
    def create(self, name, detached=False, readonly=False, reserved_space=0, klass=StorageServer):
        workdir = self.workdir(name)
        backend = DiskBackend(workdir, readonly=readonly, reserved_space=reserved_space)
        server = klass("\x00" * 20, backend, workdir,
                       stats_provider=FakeStatsProvider())
        if not detached:
            server.setServiceParent(self.sparent)
        return server

    def reset_load_store_counts(self):
        pass

    def check_load_store_counts(self, expected_loads, expected_stores):
        pass

    def get_mutable_share_class(self):
        return MutableDiskShare


class ServerWithMockCloudBackend(WithMockCloudBackend, ServerTest, unittest.TestCase):
    def setUp(self):
        ServiceParentMixin.setUp(self)

        # A smaller chunk size causes the tests to exercise more cases in the chunking implementation.
        self.patch(cloud_common, 'PREFERRED_CHUNK_SIZE', 500)

        # This causes ContainerListMixin to be exercised.
        self.patch(mock_cloud, 'MAX_KEYS', 2)


    def _describe_level(self, level):
        return getattr(LogEvent, 'LEVELMAP', {}).get(level, str(level))

    def _test_cloud_retry(self, name, failure_count, levels):
        self.patch(cloud_common, 'BACKOFF_SECONDS_BEFORE_RETRY', (0, 0.1, 0.2))

        t = {'count': 0}
        old_put_object = MockContainer._put_object
        def call_put_object(self, ign, object_name, data, content_type=None, metadata={}):
            t['count'] += 1
            if t['count'] <= failure_count:
                return defer.fail(CloudServiceError("XML", 500, "Internal error", "response"))
            else:
                return old_put_object(self, ign, object_name, data, content_type=content_type, metadata=metadata)
        self.patch(MockContainer, '_put_object', call_put_object)

        def call_log_msg(*args, **kwargs):
            # the log message and parameters should not include the data
            self.failIfIn("%25d" % (0,), repr( (args, kwargs) ))
            level = kwargs.get("level", OPERATIONAL)
            if level > OPERATIONAL:
                levels.append(level)
        self.patch(cloud_common.log, 'msg', call_log_msg)

        server = self.create(name)
        aa = server.get_accountant().get_anonymous_account()

        d = self.allocate(aa, "vid", [0], 75)
        d.addCallback(lambda (already, writers): for_items(self._write_and_close, writers))
        return d

    def test_cloud_retry_fail(self):
        levels = [] # list of logging levels above OPERATIONAL for calls to log.msg
        d = self._test_cloud_retry("test_cloud_retry_fail", 4, levels)
        # shouldFail would check repr(res.value.args[0]) which is not what we want
        def done(res):
            if isinstance(res, Failure):
                res.trap(cloud_common.CloudError)
                self.failUnlessIn(", 500, 'Internal error', 'response')", str(res.value))
                # the stringified exception should not include the data
                self.failIfIn("%25d" % (0,), str(res.value))
                desc = ", ".join(map(self._describe_level, levels))
                self.failUnlessEqual(levels, [INFREQUENT]*4 + [WEIRD], desc)
            else:
                self.fail("was supposed to raise CloudError, not get %r" % (res,))
        d.addBoth(done)
        return d

    def test_cloud_retry_succeed(self):
        levels = [] # list of logging levels above OPERATIONAL for calls to log.msg
        d = self._test_cloud_retry("test_cloud_retry_succeed", 3, levels)
        def done(res):
            desc = ", ".join(map(self._describe_level, levels))
            self.failUnlessEqual(levels, [INFREQUENT]*3 + [WEIRD], desc)
        d.addCallback(done)
        return d


class ServerWithDiskBackend(WithDiskBackend, ServerTest, unittest.TestCase):
    # The following tests are for behaviour that is only supported by a disk backend.

    def test_readonly(self):
        server = self.create("test_readonly", readonly=True)
        aa = server.get_accountant().get_anonymous_account()

        d = self.allocate(aa, "vid", [0,1,2], 75)
        def _allocated( (already, writers) ):
            self.failUnlessEqual(already, set())
            self.failUnlessEqual(writers, {})

            stats = server.get_stats()
            self.failUnlessEqual(stats["storage_server.accepting_immutable_shares"], 0)
            if "storage_server.disk_avail" in stats:
                # Some platforms may not have an API to get disk stats.
                # But if there are stats, readonly_storage means disk_avail=0
                self.failUnlessEqual(stats["storage_server.disk_avail"], 0)
        d.addCallback(_allocated)
        return d

    def test_large_share(self):
        syslow = platform.system().lower()
        if 'cygwin' in syslow or 'windows' in syslow or 'darwin' in syslow:
            raise unittest.SkipTest("If your filesystem doesn't support efficient sparse files then it is very expensive (Mac OS X and Windows don't support efficient sparse files).")

        avail = fileutil.get_available_space('.', 512*2**20)
        if avail <= 4*2**30:
            raise unittest.SkipTest("This test will spuriously fail if you have less than 4 GiB free on your filesystem.")

        server = self.create("test_large_share")
        aa = server.get_accountant().get_anonymous_account()

        d = self.allocate(aa, "allocate", [0], 2**32+2)
        def _allocated( (already, writers) ):
            self.failUnlessEqual(already, set())
            self.failUnlessEqual(set(writers.keys()), set([0]))

            shnum, bucket = writers.items()[0]

            # This test is going to hammer your filesystem if it doesn't make a sparse file for this.  :-(
            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: bucket.remote_write(2**32, "ab"))
            d2.addCallback(lambda ign: bucket.remote_close())

            d2.addCallback(lambda ign: aa.remote_get_buckets("allocate"))
            d2.addCallback(lambda readers: readers[shnum].remote_read(2**32, 2))
            d2.addCallback(lambda res: self.failUnlessEqual(res, "ab"))
            return d2
        d.addCallback(_allocated)
        return d

    def test_remove_incoming(self):
        server = self.create("test_remove_incoming")
        aa = server.get_accountant().get_anonymous_account()

        d = self.allocate(aa, "vid", range(3), 25)
        def _write_and_check( (already, writers) ):
            d2 = defer.succeed(None)
            for i, bw in sorted(writers.items()):
                incoming_share_home = bw._share._get_path()
                d2.addCallback(self._write_and_close, i, bw)

            def _check(ign):
                incoming_si_dir = os.path.dirname(incoming_share_home)
                incoming_prefix_dir = os.path.dirname(incoming_si_dir)
                incoming_dir = os.path.dirname(incoming_prefix_dir)

                self.failIf(os.path.exists(incoming_si_dir), incoming_si_dir)
                self.failIf(os.path.exists(incoming_prefix_dir), incoming_prefix_dir)
                self.failUnless(os.path.exists(incoming_dir), incoming_dir)
            d2.addCallback(_check)
            return d2
        d.addCallback(_write_and_check)
        return d

    def test_abort(self):
        # remote_abort, when called on a writer, should make sure that
        # the allocated size of the bucket is not counted by the storage
        # server when accounting for space.
        server = self.create("test_abort")
        aa = server.get_accountant().get_anonymous_account()

        d = self.allocate(aa, "allocate", [0, 1, 2], 150)
        def _allocated( (already, writers) ):
            self.failIfEqual(server.allocated_size(), 0)

            # Now abort the writers.
            d2 = for_items(self._abort_writer, writers)
            d2.addCallback(lambda ign: self.failUnlessEqual(server.allocated_size(), 0))
            return d2
        d.addCallback(_allocated)
        return d

    def test_disconnect(self):
        # simulate a disconnection
        server = self.create("test_disconnect")
        aa = server.get_accountant().get_anonymous_account()
        canary = FakeCanary()

        d = self.allocate(aa, "disconnect", [0,1,2], 75, canary)
        def _allocated( (already, writers) ):
            self.failUnlessEqual(already, set())
            self.failUnlessEqual(set(writers.keys()), set([0,1,2]))
            for (f,args,kwargs) in canary.disconnectors.values():
                f(*args, **kwargs)
        d.addCallback(_allocated)

        # returning from _allocated ought to delete the incoming shares
        d.addCallback(lambda ign: self.allocate(aa, "disconnect", [0,1,2], 75))
        def _allocated2( (already, writers) ):
            self.failUnlessEqual(already, set())
            self.failUnlessEqual(set(writers.keys()), set([0,1,2]))
        d.addCallback(_allocated2)
        return d

    @mock.patch('allmydata.util.fileutil.get_disk_stats')
    def test_reserved_space(self, mock_get_disk_stats):
        reserved_space=10000
        mock_get_disk_stats.return_value = {
            'free_for_nonroot': 15000,
            'avail': max(15000 - reserved_space, 0),
            }

        server = self.create("test_reserved_space", reserved_space=reserved_space)
        aa = server.get_accountant().get_anonymous_account()

        # 15k available, 10k reserved, leaves 5k for shares

        # a newly created and filled share incurs this much overhead, beyond
        # the size we request.
        OVERHEAD = 3*4
        LEASE_SIZE = 4+32+32+4
        canary = FakeCanary(True)

        d = self.allocate(aa, "vid1", [0,1,2], 1000, canary)
        def _allocated( (already, writers) ):
            self.failUnlessEqual(len(writers), 3)
            # now the StorageServer should have 3000 bytes provisionally
            # allocated, allowing only 2000 more to be claimed
            self.failUnlessEqual(len(server._active_writers), 3)
            self.writers = writers
            del already

            # allocating 1001-byte shares only leaves room for one
            d2 = self.allocate(aa, "vid2", [0,1,2], 1001, canary)
            def _allocated2( (already2, writers2) ):
                self.failUnlessEqual(len(writers2), 1)
                self.failUnlessEqual(len(server._active_writers), 4)

                # we abandon the first set, so their provisional allocation should be
                # returned
                d3 = for_items(self._abort_writer, self.writers)
                #def _del_writers(ign):
                #    del self.writers
                #d3.addCallback(_del_writers)
                d3.addCallback(lambda ign: self.failUnlessEqual(len(server._active_writers), 1))

                # and we close the second set, so their provisional allocation should
                # become real, long-term allocation, and grows to include the
                # overhead.
                d3.addCallback(lambda ign: for_items(self._write_and_close, writers2))
                d3.addCallback(lambda ign: self.failUnlessEqual(len(server._active_writers), 0))
                return d3
            d2.addCallback(_allocated2)

            allocated = 1001 + OVERHEAD + LEASE_SIZE

            # we have to manually increase available, since we're not doing real
            # disk measurements
            def _mock(ign):
                mock_get_disk_stats.return_value = {
                    'free_for_nonroot': 15000 - allocated,
                    'avail': max(15000 - allocated - reserved_space, 0),
                    }
            d2.addCallback(_mock)

            # now there should be ALLOCATED=1001+12+72=1085 bytes allocated, and
            # 5000-1085=3915 free, therefore we can fit 39 100byte shares
            d2.addCallback(lambda ign: self.allocate(aa, "vid3", range(100), 100, canary))
            def _allocated3( (already3, writers3) ):
                self.failUnlessEqual(len(writers3), 39)
                self.failUnlessEqual(len(server._active_writers), 39)

                d3 = for_items(self._abort_writer, writers3)
                d3.addCallback(lambda ign: self.failUnlessEqual(len(server._active_writers), 0))
                d3.addCallback(lambda ign: server.disownServiceParent())
                return d3
            d2.addCallback(_allocated3)
        d.addCallback(_allocated)
        return d

    def OFF_test_immutable_leases(self):
        server = self.create("test_immutable_leases")
        aa = server.get_accountant().get_anonymous_account()
        canary = FakeCanary()
        sharenums = range(5)
        size = 100

        rs = []
        cs = []
        for i in range(6):
            rs.append(hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()))
            cs.append(hashutil.tagged_hash("blah", "%d" % self._lease_secret.next()))

        d = aa.remote_allocate_buckets("si0", rs[0], cs[0],
                                       sharenums, size, canary)
        def _allocated( (already, writers) ):
            self.failUnlessEqual(len(already), 0)
            self.failUnlessEqual(len(writers), 5)

            d2 = for_items(self._close_writer, writers)

            d2.addCallback(lambda ign: list(aa.get_leases("si0")))
            d2.addCallback(lambda leases: self.failUnlessEqual(len(leases), 1))

            d2.addCallback(lambda ign: aa.remote_allocate_buckets("si1", rs[1], cs[1],
                                                                  sharenums, size, canary))
            return d2
        d.addCallback(_allocated)

        def _allocated2( (already, writers) ):
            d2 = for_items(self._close_writer, writers)

            # take out a second lease on si1
            d2.addCallback(lambda ign: aa.remote_allocate_buckets("si1", rs[2], cs[2],
                                                                  sharenums, size, canary))
            return d2
        d.addCallback(_allocated2)

        def _allocated2a( (already, writers) ):
            self.failUnlessEqual(len(already), 5)
            self.failUnlessEqual(len(writers), 0)

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: list(aa.get_leases("si1")))
            d2.addCallback(lambda leases: self.failUnlessEqual(len(leases), 2))

            # and a third lease, using add-lease
            d2.addCallback(lambda ign: aa.remote_add_lease("si1", rs[3], cs[3]))

            d2.addCallback(lambda ign: list(aa.get_leases("si1")))
            d2.addCallback(lambda leases: self.failUnlessEqual(len(leases), 3))

            # add-lease on a missing storage index is silently ignored
            d2.addCallback(lambda ign: aa.remote_add_lease("si18", "", ""))
            d2.addCallback(lambda res: self.failUnlessEqual(res, None))

            # check that si0 is readable
            d2.addCallback(lambda ign: aa.remote_get_buckets("si0"))
            d2.addCallback(lambda readers: self.failUnlessEqual(len(readers), 5))

            # renew the first lease. Only the proper renew_secret should work
            d2.addCallback(lambda ign: aa.remote_renew_lease("si0", rs[0]))
            d2.addCallback(lambda ign: self.shouldFail(IndexError, 'wrong secret 1', None,
                                                       lambda: aa.remote_renew_lease("si0", cs[0]) ))
            d2.addCallback(lambda ign: self.shouldFail(IndexError, 'wrong secret 2', None,
                                                       lambda: aa.remote_renew_lease("si0", rs[1]) ))

            # check that si0 is still readable
            d2.addCallback(lambda ign: aa.remote_get_buckets("si0"))
            d2.addCallback(lambda readers: self.failUnlessEqual(len(readers), 5))

            # There is no such method as remote_cancel_lease for now -- see
            # ticket #1528.
            d2.addCallback(lambda ign: self.failIf(hasattr(aa, 'remote_cancel_lease'),
                                                   "aa should not have a 'remote_cancel_lease' method/attribute"))

            # test overlapping uploads
            d2.addCallback(lambda ign: aa.remote_allocate_buckets("si3", rs[4], cs[4],
                                                                  sharenums, size, canary))
            return d2
        d.addCallback(_allocated2a)

        def _allocated4( (already, writers) ):
            self.failUnlessEqual(len(already), 0)
            self.failUnlessEqual(len(writers), 5)

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: aa.remote_allocate_buckets("si3", rs[5], cs[5],
                                                                  sharenums, size, canary))
            def _allocated5( (already2, writers2) ):
                self.failUnlessEqual(len(already2), 0)
                self.failUnlessEqual(len(writers2), 0)

                d3 = for_items(self._close_writer, writers)

                d3.addCallback(lambda ign: list(aa.get_leases("si3")))
                d3.addCallback(lambda leases: self.failUnlessEqual(len(leases), 1))

                d3.addCallback(lambda ign: aa.remote_allocate_buckets("si3", rs[5], cs[5],
                                                                      sharenums, size, canary))
                return d3
            d2.addCallback(_allocated5)

            def _allocated6( (already3, writers3) ):
                self.failUnlessEqual(len(already3), 5)
                self.failUnlessEqual(len(writers3), 0)

                d3 = defer.succeed(None)
                d3.addCallback(lambda ign: list(aa.get_leases("si3")))
                d3.addCallback(lambda leases: self.failUnlessEqual(len(leases), 2))
                return d3
            d2.addCallback(_allocated6)
            return d2
        d.addCallback(_allocated4)
        return d


class MutableServerWithMockCloudBackend(WithMockCloudBackend, MutableServerTest, unittest.TestCase):
    def setUp(self):
        ServiceParentMixin.setUp(self)

        # A smaller chunk size causes the tests to exercise more cases in the chunking implementation.
        self.patch(cloud_common, 'PREFERRED_CHUNK_SIZE', 500)

        # This causes ContainerListMixin to be exercised.
        self.patch(mock_cloud, 'MAX_KEYS', 2)


class MutableServerWithDiskBackend(WithDiskBackend, MutableServerTest, unittest.TestCase):
    # There are no mutable tests specific to a disk backend.
    pass


class MDMFProxies(WithDiskBackend, ShouldFailMixin, unittest.TestCase):
    def init(self, name):
        self._lease_secret = itertools.count()
        self.server = self.create(name)
        self.aa = self.server.get_accountant().get_anonymous_account()
        self.rref = RemoteBucket()
        self.rref.target = self.aa
        self.secrets = (self.write_enabler("we_secret"),
                        self.renew_secret("renew_secret"),
                        self.cancel_secret("cancel_secret"))
        self.segment = "aaaaaa"
        self.block = "aa"
        self.salt = "a" * 16
        self.block_hash = "a" * 32
        self.block_hash_tree = [self.block_hash for i in xrange(6)]
        self.share_hash = self.block_hash
        self.share_hash_chain = dict([(i, self.share_hash) for i in xrange(6)])
        self.signature = "foobarbaz"
        self.verification_key = "vvvvvv"
        self.encprivkey = "private"
        self.root_hash = self.block_hash
        self.salt_hash = self.root_hash
        self.salt_hash_tree = [self.salt_hash for i in xrange(6)]
        self.block_hash_tree_s = self.serialize_blockhashes(self.block_hash_tree)
        self.share_hash_chain_s = self.serialize_sharehashes(self.share_hash_chain)
        # blockhashes and salt hashes are serialized in the same way,
        # only we lop off the first element and store that in the
        # header.
        self.salt_hash_tree_s = self.serialize_blockhashes(self.salt_hash_tree[1:])

    def write_enabler(self, we_tag):
        return hashutil.tagged_hash("we_blah", we_tag)

    def renew_secret(self, tag):
        return hashutil.tagged_hash("renew_blah", str(tag))

    def cancel_secret(self, tag):
        return hashutil.tagged_hash("cancel_blah", str(tag))

    def build_test_mdmf_share(self, tail_segment=False, empty=False):
        # Start with the checkstring
        data = struct.pack(">BQ32s",
                           1,
                           0,
                           self.root_hash)
        self.checkstring = data
        # Next, the encoding parameters
        if tail_segment:
            data += struct.pack(">BBQQ",
                                3,
                                10,
                                6,
                                33)
        elif empty:
            data += struct.pack(">BBQQ",
                                3,
                                10,
                                0,
                                0)
        else:
            data += struct.pack(">BBQQ",
                                3,
                                10,
                                6,
                                36)
        # Now we'll build the offsets.
        sharedata = ""
        if not tail_segment and not empty:
            for i in xrange(6):
                sharedata += self.salt + self.block
        elif tail_segment:
            for i in xrange(5):
                sharedata += self.salt + self.block
            sharedata += self.salt + "a"

        # The encrypted private key comes after the shares + salts
        offset_size = struct.calcsize(MDMFOFFSETS)
        encrypted_private_key_offset = len(data) + offset_size
        # The share has chain comes after the private key
        sharehashes_offset = encrypted_private_key_offset + \
            len(self.encprivkey)

        # The signature comes after the share hash chain.
        signature_offset = sharehashes_offset + len(self.share_hash_chain_s)

        verification_key_offset = signature_offset + len(self.signature)
        verification_key_end = verification_key_offset + \
            len(self.verification_key)

        share_data_offset = offset_size
        share_data_offset += PRIVATE_KEY_SIZE
        share_data_offset += SIGNATURE_SIZE
        share_data_offset += VERIFICATION_KEY_SIZE
        share_data_offset += SHARE_HASH_CHAIN_SIZE

        blockhashes_offset = share_data_offset + len(sharedata)
        eof_offset = blockhashes_offset + len(self.block_hash_tree_s)

        data += struct.pack(MDMFOFFSETS,
                            encrypted_private_key_offset,
                            sharehashes_offset,
                            signature_offset,
                            verification_key_offset,
                            verification_key_end,
                            share_data_offset,
                            blockhashes_offset,
                            eof_offset)

        self.offsets = {}
        self.offsets['enc_privkey'] = encrypted_private_key_offset
        self.offsets['block_hash_tree'] = blockhashes_offset
        self.offsets['share_hash_chain'] = sharehashes_offset
        self.offsets['signature'] = signature_offset
        self.offsets['verification_key'] = verification_key_offset
        self.offsets['share_data'] = share_data_offset
        self.offsets['verification_key_end'] = verification_key_end
        self.offsets['EOF'] = eof_offset

        # the private key,
        data += self.encprivkey
        # the sharehashes
        data += self.share_hash_chain_s
        # the signature,
        data += self.signature
        # and the verification key
        data += self.verification_key
        # Then we'll add in gibberish until we get to the right point.
        nulls = "".join([" " for i in xrange(len(data), share_data_offset)])
        data += nulls

        # Then the share data
        data += sharedata
        # the blockhashes
        data += self.block_hash_tree_s
        return data

    def write_test_share_to_server(self,
                                   storage_index,
                                   tail_segment=False,
                                   empty=False):
        """
        I write some data for the read tests to read to self.aa

        If tail_segment=True, then I will write a share that has a
        smaller tail segment than other segments.
        """
        write = self.aa.remote_slot_testv_and_readv_and_writev
        data = self.build_test_mdmf_share(tail_segment, empty)
        # Finally, we write the whole thing to the storage server in one
        # pass.
        testvs = [(0, 1, "eq", "")]
        tws = {}
        tws[0] = (testvs, [(0, data)], None)
        readv = [(0, 1)]
        d = write(storage_index, self.secrets, tws, readv)
        d.addCallback(lambda res: self.failUnless(res[0]))
        return d

    def build_test_sdmf_share(self, empty=False):
        if empty:
            sharedata = ""
        else:
            sharedata = self.segment * 6
        self.sharedata = sharedata
        blocksize = len(sharedata) / 3
        block = sharedata[:blocksize]
        self.blockdata = block
        prefix = struct.pack(">BQ32s16s BBQQ",
                             0, # version,
                             0,
                             self.root_hash,
                             self.salt,
                             3,
                             10,
                             len(sharedata),
                             len(sharedata),
                            )
        post_offset = struct.calcsize(">BQ32s16sBBQQLLLLQQ")
        signature_offset = post_offset + len(self.verification_key)
        sharehashes_offset = signature_offset + len(self.signature)
        blockhashes_offset = sharehashes_offset + len(self.share_hash_chain_s)
        sharedata_offset = blockhashes_offset + len(self.block_hash_tree_s)
        encprivkey_offset = sharedata_offset + len(block)
        eof_offset = encprivkey_offset + len(self.encprivkey)
        offsets = struct.pack(">LLLLQQ",
                              signature_offset,
                              sharehashes_offset,
                              blockhashes_offset,
                              sharedata_offset,
                              encprivkey_offset,
                              eof_offset)
        final_share = "".join([prefix,
                           offsets,
                           self.verification_key,
                           self.signature,
                           self.share_hash_chain_s,
                           self.block_hash_tree_s,
                           block,
                           self.encprivkey])
        self.offsets = {}
        self.offsets['signature'] = signature_offset
        self.offsets['share_hash_chain'] = sharehashes_offset
        self.offsets['block_hash_tree'] = blockhashes_offset
        self.offsets['share_data'] = sharedata_offset
        self.offsets['enc_privkey'] = encprivkey_offset
        self.offsets['EOF'] = eof_offset
        return final_share

    def write_sdmf_share_to_server(self,
                                   storage_index,
                                   empty=False):
        # Some tests need SDMF shares to verify that we can still
        # read them. This method writes one, which resembles but is not
        assert self.rref
        write = self.aa.remote_slot_testv_and_readv_and_writev
        share = self.build_test_sdmf_share(empty)
        testvs = [(0, 1, "eq", "")]
        tws = {}
        tws[0] = (testvs, [(0, share)], None)
        readv = []
        d = write(storage_index, self.secrets, tws, readv)
        d.addCallback(lambda res: self.failUnless(res[0]))
        return d


    def test_read(self):
        self.init("test_read")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1"))

        # Check that every method equals what we expect it to.
        def _check_block_and_salt((block, salt)):
            self.failUnlessEqual(block, self.block)
            self.failUnlessEqual(salt, self.salt)

        for i in xrange(6):
            d.addCallback(lambda ignored, i=i:
                mr.get_block_and_salt(i))
            d.addCallback(_check_block_and_salt)

        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.failUnlessEqual(self.encprivkey, encprivkey))

        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.failUnlessEqual(self.block_hash_tree, blockhashes))

        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.failUnlessEqual(self.share_hash_chain, sharehashes))

        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.failUnlessEqual(signature, self.signature))

        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.failUnlessEqual(verification_key, self.verification_key))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0))

        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(self.root_hash, root_hash))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(0, seqnum))

        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters((k, n, segsize, datalen)):
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
        d.addCallback(_check_encoding_parameters)

        d.addCallback(lambda ignored:
            mr.get_checkstring())
        d.addCallback(lambda checkstring:
            self.failUnlessEqual(checkstring, checkstring))
        return d

    def test_read_with_different_tail_segment_size(self):
        self.init("test_read_with_different_tail_segment_size")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1", tail_segment=True))

        d.addCallback(lambda ign: mr.get_block_and_salt(5))
        def _check_tail_segment(results):
            block, salt = results
            self.failUnlessEqual(len(block), 1)
            self.failUnlessEqual(block, "a")
        d.addCallback(_check_tail_segment)
        return d

    def test_get_block_with_invalid_segnum(self):
        self.init("test_get_block_with_invalid_segnum")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1"))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test invalid segnum",
                            None,
                            mr.get_block_and_salt, 7))
        return d

    def test_get_encoding_parameters_first(self):
        self.init("test_get_encoding_parameters_first")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1"))
        d.addCallback(lambda ign: mr.get_encoding_parameters())
        def _check_encoding_parameters((k, n, segment_size, datalen)):
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segment_size, 6)
            self.failUnlessEqual(datalen, 36)
        d.addCallback(_check_encoding_parameters)
        return d

    def test_get_seqnum_first(self):
        self.init("test_get_seqnum_first")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1"))
        d.addCallback(lambda ign: mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0))
        return d

    def test_get_root_hash_first(self):
        self.init("test_root_hash_first")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1"))
        d.addCallback(lambda ign: mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(root_hash, self.root_hash))
        return d

    def test_get_checkstring_first(self):
        self.init("test_checkstring_first")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1"))
        d.addCallback(lambda ign: mr.get_checkstring())
        d.addCallback(lambda checkstring:
            self.failUnlessEqual(checkstring, self.checkstring))
        return d

    def test_write_read_vectors(self):
        self.init("test_write_read_vectors")

        # When writing for us, the storage server will return to us a
        # read vector, along with its result. If a write fails because
        # the test vectors failed, this read vector can help us to
        # diagnose the problem. This test ensures that the read vector
        # is working appropriately.
        mw = self._make_new_mw("si1", 0)

        for i in xrange(6):
            mw.put_block(self.block, i, self.salt)
        mw.put_encprivkey(self.encprivkey)
        mw.put_blockhashes(self.block_hash_tree)
        mw.put_sharehashes(self.share_hash_chain)
        mw.put_root_hash(self.root_hash)
        mw.put_signature(self.signature)
        mw.put_verification_key(self.verification_key)

        d = mw.finish_publishing()
        def _then(results):
            self.failUnless(len(results), 2)
            result, readv = results
            self.failUnless(result)
            self.failIf(readv)
            self.old_checkstring = mw.get_checkstring()
            mw.set_checkstring("")
        d.addCallback(_then)
        d.addCallback(lambda ignored:
            mw.finish_publishing())
        def _then_again(results):
            self.failUnlessEqual(len(results), 2)
            result, readvs = results
            self.failIf(result)
            self.failUnlessIn(0, readvs)
            readv = readvs[0][0]
            self.failUnlessEqual(readv, self.old_checkstring)
        d.addCallback(_then_again)
        # The checkstring remains the same for the rest of the process.
        return d

    def test_private_key_after_share_hash_chain(self):
        self.init("test_private_key_after_share_hash_chain")

        mw = self._make_new_mw("si1", 0)
        d = defer.succeed(None)
        for i in xrange(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            mw.put_encprivkey(self.encprivkey))
        d.addCallback(lambda ignored:
            mw.put_sharehashes(self.share_hash_chain))

        # Now try to put the private key again.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test repeat private key",
                            None,
                            mw.put_encprivkey, self.encprivkey))
        return d

    def test_signature_after_verification_key(self):
        self.init("test_signature_after_verification_key")

        mw = self._make_new_mw("si1", 0)
        d = defer.succeed(None)
        # Put everything up to and including the verification key.
        for i in xrange(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            mw.put_encprivkey(self.encprivkey))
        d.addCallback(lambda ignored:
            mw.put_blockhashes(self.block_hash_tree))
        d.addCallback(lambda ignored:
            mw.put_sharehashes(self.share_hash_chain))
        d.addCallback(lambda ignored:
            mw.put_root_hash(self.root_hash))
        d.addCallback(lambda ignored:
            mw.put_signature(self.signature))
        d.addCallback(lambda ignored:
            mw.put_verification_key(self.verification_key))
        # Now try to put the signature again. This should fail
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "signature after verification",
                            None,
                            mw.put_signature, self.signature))
        return d

    def test_uncoordinated_write(self):
        self.init("test_uncoordinated_write")

        # Make two mutable writers, both pointing to the same storage
        # server, both at the same storage index, and try writing to the
        # same share.
        mw1 = self._make_new_mw("si1", 0)
        mw2 = self._make_new_mw("si1", 0)

        def _check_success(results):
            result, readvs = results
            self.failUnless(result)

        def _check_failure(results):
            result, readvs = results
            self.failIf(result)

        def _write_share(mw):
            for i in xrange(6):
                mw.put_block(self.block, i, self.salt)
            mw.put_encprivkey(self.encprivkey)
            mw.put_blockhashes(self.block_hash_tree)
            mw.put_sharehashes(self.share_hash_chain)
            mw.put_root_hash(self.root_hash)
            mw.put_signature(self.signature)
            mw.put_verification_key(self.verification_key)
            return mw.finish_publishing()
        d = _write_share(mw1)
        d.addCallback(_check_success)
        d.addCallback(lambda ignored:
            _write_share(mw2))
        d.addCallback(_check_failure)
        return d

    def test_invalid_salt_size(self):
        self.init("test_invalid_salt_size")

        # Salts need to be 16 bytes in size. Writes that attempt to
        # write more or less than this should be rejected.
        mw = self._make_new_mw("si1", 0)
        invalid_salt = "a" * 17 # 17 bytes
        another_invalid_salt = "b" * 15 # 15 bytes
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "salt too big",
                            None,
                            mw.put_block, self.block, 0, invalid_salt))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "salt too small",
                            None,
                            mw.put_block, self.block, 0,
                            another_invalid_salt))
        return d

    def test_write_test_vectors(self):
        self.init("test_write_test_vectors")

        # If we give the write proxy a bogus test vector at
        # any point during the process, it should fail to write when we
        # tell it to write.
        def _check_failure(results):
            self.failUnlessEqual(len(results), 2)
            res, d = results
            self.failIf(res)

        def _check_success(results):
            self.failUnlessEqual(len(results), 2)
            res, d = results
            self.failUnless(results)

        mw = self._make_new_mw("si1", 0)
        mw.set_checkstring("this is a lie")
        for i in xrange(6):
            mw.put_block(self.block, i, self.salt)
        mw.put_encprivkey(self.encprivkey)
        mw.put_blockhashes(self.block_hash_tree)
        mw.put_sharehashes(self.share_hash_chain)
        mw.put_root_hash(self.root_hash)
        mw.put_signature(self.signature)
        mw.put_verification_key(self.verification_key)
        d = mw.finish_publishing()
        d.addCallback(_check_failure)
        d.addCallback(lambda ignored:
            mw.set_checkstring(""))
        d.addCallback(lambda ignored:
            mw.finish_publishing())
        d.addCallback(_check_success)
        return d


    def serialize_blockhashes(self, blockhashes):
        return "".join(blockhashes)

    def serialize_sharehashes(self, sharehashes):
        ret = "".join([struct.pack(">H32s", i, sharehashes[i])
                        for i in sorted(sharehashes.keys())])
        return ret


    def test_write(self):
        self.init("test_write")

        # This translates to a file with 6 6-byte segments, and with 2-byte
        # blocks.
        mw = self._make_new_mw("si1", 0)
        # Test writing some blocks.
        read = self.aa.remote_slot_readv
        expected_private_key_offset = struct.calcsize(MDMFHEADER)
        expected_sharedata_offset = struct.calcsize(MDMFHEADER) + \
                                    PRIVATE_KEY_SIZE + \
                                    SIGNATURE_SIZE + \
                                    VERIFICATION_KEY_SIZE + \
                                    SHARE_HASH_CHAIN_SIZE
        written_block_size = 2 + len(self.salt)
        written_block = self.block + self.salt
        for i in xrange(6):
            mw.put_block(self.block, i, self.salt)

        mw.put_encprivkey(self.encprivkey)
        mw.put_blockhashes(self.block_hash_tree)
        mw.put_sharehashes(self.share_hash_chain)
        mw.put_root_hash(self.root_hash)
        mw.put_signature(self.signature)
        mw.put_verification_key(self.verification_key)

        d = mw.finish_publishing()
        d.addCallback(lambda (result, ign): self.failUnless(result, "publish failed"))

        for i in xrange(6):
            d.addCallback(lambda ign, i=i: read("si1", [0],
                                                [(expected_sharedata_offset + (i * written_block_size),
                                                  written_block_size)]))
            d.addCallback(lambda res: self.failUnlessEqual(res, {0: [written_block]}))

            d.addCallback(lambda ign: self.failUnlessEqual(len(self.encprivkey), 7))
            d.addCallback(lambda ign: read("si1", [0], [(expected_private_key_offset, 7)]))
            d.addCallback(lambda res: self.failUnlessEqual(res, {0: [self.encprivkey]}))

            expected_block_hash_offset = expected_sharedata_offset + (6 * written_block_size)
            d.addCallback(lambda ign: self.failUnlessEqual(len(self.block_hash_tree_s), 32 * 6))
            d.addCallback(lambda ign, ebho=expected_block_hash_offset:
                                      read("si1", [0], [(ebho, 32 * 6)]))
            d.addCallback(lambda res: self.failUnlessEqual(res, {0: [self.block_hash_tree_s]}))

            expected_share_hash_offset = expected_private_key_offset + len(self.encprivkey)
            d.addCallback(lambda ign, esho=expected_share_hash_offset:
                                      read("si1", [0], [(esho, (32 + 2) * 6)]))
            d.addCallback(lambda res: self.failUnlessEqual(res, {0: [self.share_hash_chain_s]}))

            d.addCallback(lambda ign: read("si1", [0], [(9, 32)]))
            d.addCallback(lambda res: self.failUnlessEqual(res,  {0: [self.root_hash]}))

            expected_signature_offset = expected_share_hash_offset + len(self.share_hash_chain_s)
            d.addCallback(lambda ign: self.failUnlessEqual(len(self.signature), 9))
            d.addCallback(lambda ign, esigo=expected_signature_offset:
                                      read("si1", [0], [(esigo, 9)]))
            d.addCallback(lambda res: self.failUnlessEqual(res, {0: [self.signature]}))

            expected_verification_key_offset = expected_signature_offset + len(self.signature)
            d.addCallback(lambda ign: self.failUnlessEqual(len(self.verification_key), 6))
            d.addCallback(lambda ign, evko=expected_verification_key_offset:
                                      read("si1", [0], [(evko, 6)]))
            d.addCallback(lambda res: self.failUnlessEqual(res, {0: [self.verification_key]}))

            def _check_other_fields(ign, ebho=expected_block_hash_offset,
                                         esho=expected_share_hash_offset,
                                         esigo=expected_signature_offset,
                                         evko=expected_verification_key_offset):
                signable = mw.get_signable()
                verno, seq, roothash, k, N, segsize, datalen = struct.unpack(">BQ32sBBQQ",
                                                                             signable)
                self.failUnlessEqual(verno, 1)
                self.failUnlessEqual(seq, 0)
                self.failUnlessEqual(roothash, self.root_hash)
                self.failUnlessEqual(k, 3)
                self.failUnlessEqual(N, 10)
                self.failUnlessEqual(segsize, 6)
                self.failUnlessEqual(datalen, 36)

                def _check_field(res, offset, fmt, which, value):
                    encoded = struct.pack(fmt, value)
                    d3 = defer.succeed(None)
                    d3.addCallback(lambda ign: read("si1", [0], [(offset, len(encoded))]))
                    d3.addCallback(lambda res: self.failUnlessEqual(res, {0: [encoded]}, which))
                    return d3

                d2 = defer.succeed(None)
                d2.addCallback(_check_field,   0, ">B", "version number", verno)
                d2.addCallback(_check_field,   1, ">Q", "sequence number", seq)
                d2.addCallback(_check_field,  41, ">B", "k", k)
                d2.addCallback(_check_field,  42, ">B", "N", N)
                d2.addCallback(_check_field,  43, ">Q", "segment size", segsize)
                d2.addCallback(_check_field,  51, ">Q", "data length", datalen)
                d2.addCallback(_check_field,  59, ">Q", "private key offset",
                                             expected_private_key_offset)
                d2.addCallback(_check_field,  67, ">Q", "share hash offset", esho)
                d2.addCallback(_check_field,  75, ">Q", "signature offset", esigo)
                d2.addCallback(_check_field,  83, ">Q", "verification key offset", evko)
                d2.addCallback(_check_field,  91, ">Q", "end of verification key",
                                             evko + len(self.verification_key))
                d2.addCallback(_check_field,  99, ">Q", "sharedata offset",
                                             expected_sharedata_offset)
                d2.addCallback(_check_field, 107, ">Q", "block hash offset", ebho)
                d2.addCallback(_check_field, 115, ">Q", "eof offset",
                                             ebho + len(self.block_hash_tree_s))
                return d2
            d.addCallback(_check_other_fields)

        return d


    def _make_new_mw(self, si, share, datalength=36):
        # This is a file of size 36 bytes. Since it has a segment
        # size of 6, we know that it has 6 byte segments, which will
        # be split into blocks of 2 bytes because our FEC k
        # parameter is 3.
        mw = MDMFSlotWriteProxy(share, self.rref, si, self.secrets, 0, 3, 10,
                                6, datalength)
        return mw

    def test_write_rejected_with_too_many_blocks(self):
        self.init("test_write_rejected_with_too_many_blocks")

        mw = self._make_new_mw("si0", 0)

        # Try writing too many blocks. We should not be able to write
        # more than 6
        # blocks into each share.
        d = defer.succeed(None)
        for i in xrange(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "too many blocks",
                            None,
                            mw.put_block, self.block, 7, self.salt))
        return d

    def test_write_rejected_with_invalid_salt(self):
        self.init("test_write_rejected_with_invalid_salt")

        # Try writing an invalid salt. Salts are 16 bytes -- any more or
        # less should cause an error.
        mw = self._make_new_mw("si1", 0)
        bad_salt = "a" * 17 # 17 bytes
        d = defer.succeed(None)
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test_invalid_salt",
                            None, mw.put_block, self.block, 7, bad_salt))
        return d

    def test_write_rejected_with_invalid_root_hash(self):
        self.init("test_write_rejected_with_invalid_root_hash")

        # Try writing an invalid root hash. This should be SHA256d, and
        # 32 bytes long as a result.
        mw = self._make_new_mw("si2", 0)
        # 17 bytes != 32 bytes
        invalid_root_hash = "a" * 17
        d = defer.succeed(None)
        # Before this test can work, we need to put some blocks + salts,
        # a block hash tree, and a share hash tree. Otherwise, we'll see
        # failures that match what we are looking for, but are caused by
        # the constraints imposed on operation ordering.
        for i in xrange(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            mw.put_encprivkey(self.encprivkey))
        d.addCallback(lambda ignored:
            mw.put_blockhashes(self.block_hash_tree))
        d.addCallback(lambda ignored:
            mw.put_sharehashes(self.share_hash_chain))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "invalid root hash",
                            None, mw.put_root_hash, invalid_root_hash))
        return d

    def test_write_rejected_with_invalid_blocksize(self):
        self.init("test_write_rejected_with_invalid_blocksize")

        # The blocksize implied by the writer that we get from
        # _make_new_mw is 2bytes -- any more or any less than this
        # should be cause for failure, unless it is the tail segment, in
        # which case it may not be failure.
        invalid_block = "a"
        mw = self._make_new_mw("si3", 0, 33) # implies a tail segment with
                                             # one byte blocks
        # 1 bytes != 2 bytes
        d = defer.succeed(None)
        d.addCallback(lambda ignored, invalid_block=invalid_block:
            self.shouldFail(LayoutInvalid, "test blocksize too small",
                            None, mw.put_block, invalid_block, 0,
                            self.salt))
        invalid_block = invalid_block * 3
        # 3 bytes != 2 bytes
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test blocksize too large",
                            None,
                            mw.put_block, invalid_block, 0, self.salt))
        for i in xrange(5):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        # Try to put an invalid tail segment
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test invalid tail segment",
                            None,
                            mw.put_block, self.block, 5, self.salt))
        valid_block = "a"
        d.addCallback(lambda ignored:
            mw.put_block(valid_block, 5, self.salt))
        return d

    def test_write_enforces_order_constraints(self):
        self.init("test_write_enforces_order_constraints")

        # We require that the MDMFSlotWriteProxy be interacted with in a
        # specific way.
        # That way is:
        # 0: __init__
        # 1: write blocks and salts
        # 2: Write the encrypted private key
        # 3: Write the block hashes
        # 4: Write the share hashes
        # 5: Write the root hash and salt hash
        # 6: Write the signature and verification key
        # 7: Write the file.
        #
        # Some of these can be performed out-of-order, and some can't.
        # The dependencies that I want to test here are:
        #  - Private key before block hashes
        #  - share hashes and block hashes before root hash
        #  - root hash before signature
        #  - signature before verification key
        mw0 = self._make_new_mw("si0", 0)
        # Write some shares
        d = defer.succeed(None)
        for i in xrange(6):
            d.addCallback(lambda ign, i=i:
                          mw0.put_block(self.block, i, self.salt))

        # Try to write the share hash chain without writing the
        # encrypted private key
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "share hash chain before "
                                           "private key",
                            None,
                            lambda: mw0.put_sharehashes(self.share_hash_chain) ))

        # Write the private key.
        d.addCallback(lambda ign: mw0.put_encprivkey(self.encprivkey))

        # Now write the block hashes and try again
        d.addCallback(lambda ignored:
            mw0.put_blockhashes(self.block_hash_tree))

        # We haven't yet put the root hash on the share, so we shouldn't
        # be able to sign it.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "signature before root hash",
                            None,
                            lambda: mw0.put_signature(self.signature) ))

        d.addCallback(lambda ignored:
            self.failUnlessRaises(LayoutInvalid, mw0.get_signable))

        # ..and, since that fails, we also shouldn't be able to put the
        # verification key.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "key before signature",
                            None,
                            lambda: mw0.put_verification_key(self.verification_key) ))

        # Now write the share hashes.
        d.addCallback(lambda ign: mw0.put_sharehashes(self.share_hash_chain))

        # We should be able to write the root hash now too
        d.addCallback(lambda ign: mw0.put_root_hash(self.root_hash))

        # We should still be unable to put the verification key
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "key before signature",
                            None,
                            lambda: mw0.put_verification_key(self.verification_key) ))

        d.addCallback(lambda ign: mw0.put_signature(self.signature))

        # We shouldn't be able to write the offsets to the remote server
        # until the offset table is finished; IOW, until we have written
        # the verification key.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "offsets before verification key",
                            None,
                            mw0.finish_publishing))

        d.addCallback(lambda ignored:
            mw0.put_verification_key(self.verification_key))
        return d

    def test_end_to_end(self):
        self.init("test_end_to_end")

        mw = self._make_new_mw("si1", 0)
        # Write a share using the mutable writer, and make sure that the
        # reader knows how to read everything back to us.
        d = defer.succeed(None)
        for i in xrange(6):
            d.addCallback(lambda ignored, i=i:
                mw.put_block(self.block, i, self.salt))
        d.addCallback(lambda ignored:
            mw.put_encprivkey(self.encprivkey))
        d.addCallback(lambda ignored:
            mw.put_blockhashes(self.block_hash_tree))
        d.addCallback(lambda ignored:
            mw.put_sharehashes(self.share_hash_chain))
        d.addCallback(lambda ignored:
            mw.put_root_hash(self.root_hash))
        d.addCallback(lambda ignored:
            mw.put_signature(self.signature))
        d.addCallback(lambda ignored:
            mw.put_verification_key(self.verification_key))
        d.addCallback(lambda ignored:
            mw.finish_publishing())

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        def _check_block_and_salt((block, salt)):
            self.failUnlessEqual(block, self.block)
            self.failUnlessEqual(salt, self.salt)

        for i in xrange(6):
            d.addCallback(lambda ignored, i=i:
                mr.get_block_and_salt(i))
            d.addCallback(_check_block_and_salt)

        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.failUnlessEqual(self.encprivkey, encprivkey))

        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.failUnlessEqual(self.block_hash_tree, blockhashes))

        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.failUnlessEqual(self.share_hash_chain, sharehashes))

        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.failUnlessEqual(signature, self.signature))

        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.failUnlessEqual(verification_key, self.verification_key))

        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0))

        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(self.root_hash, root_hash))

        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters((k, n, segsize, datalen)):
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
        d.addCallback(_check_encoding_parameters)

        d.addCallback(lambda ignored:
            mr.get_checkstring())
        d.addCallback(lambda checkstring:
            self.failUnlessEqual(checkstring, mw.get_checkstring()))
        return d

    def test_is_sdmf(self):
        self.init("test_is_sdmf")

        # The MDMFSlotReadProxy should also know how to read SDMF files,
        # since it will encounter them on the grid. Callers use the
        # is_sdmf method to test this.
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_sdmf_share_to_server("si1"))
        d.addCallback(lambda ign: mr.is_sdmf())
        d.addCallback(lambda issdmf: self.failUnless(issdmf))
        return d

    def test_reads_sdmf(self):
        self.init("test_reads_sdmf")

        # The slot read proxy should, naturally, know how to tell us
        # about data in the SDMF format
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_sdmf_share_to_server("si1"))
        d.addCallback(lambda ign: mr.is_sdmf())
        d.addCallback(lambda issdmf: self.failUnless(issdmf))

        # What do we need to read?
        #  - The sharedata
        #  - The salt
        d.addCallback(lambda ignored:
            mr.get_block_and_salt(0))
        def _check_block_and_salt(results):
            block, salt = results
            # Our original file is 36 bytes long. Then each share is 12
            # bytes in size. The share is composed entirely of the
            # letter a. self.block contains 2 as, so 6 * self.block is
            # what we are looking for.
            self.failUnlessEqual(block, self.block * 6)
            self.failUnlessEqual(salt, self.salt)
        d.addCallback(_check_block_and_salt)

        #  - The blockhashes
        d.addCallback(lambda ignored:
            mr.get_blockhashes())
        d.addCallback(lambda blockhashes:
            self.failUnlessEqual(self.block_hash_tree,
                                 blockhashes,
                                 blockhashes))
        #  - The sharehashes
        d.addCallback(lambda ignored:
            mr.get_sharehashes())
        d.addCallback(lambda sharehashes:
            self.failUnlessEqual(self.share_hash_chain,
                                 sharehashes))
        #  - The keys
        d.addCallback(lambda ignored:
            mr.get_encprivkey())
        d.addCallback(lambda encprivkey:
            self.failUnlessEqual(encprivkey, self.encprivkey, encprivkey))
        d.addCallback(lambda ignored:
            mr.get_verification_key())
        d.addCallback(lambda verification_key:
            self.failUnlessEqual(verification_key,
                                 self.verification_key,
                                 verification_key))
        #  - The signature
        d.addCallback(lambda ignored:
            mr.get_signature())
        d.addCallback(lambda signature:
            self.failUnlessEqual(signature, self.signature, signature))

        #  - The sequence number
        d.addCallback(lambda ignored:
            mr.get_seqnum())
        d.addCallback(lambda seqnum:
            self.failUnlessEqual(seqnum, 0, seqnum))

        #  - The root hash
        d.addCallback(lambda ignored:
            mr.get_root_hash())
        d.addCallback(lambda root_hash:
            self.failUnlessEqual(root_hash, self.root_hash, root_hash))
        return d

    def test_only_reads_one_segment_sdmf(self):
        self.init("test_only_reads_one_segment_sdmf")

        # SDMF shares have only one segment, so it doesn't make sense to
        # read more segments than that. The reader should know this and
        # complain if we try to do that.
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_sdmf_share_to_server("si1"))
        d.addCallback(lambda ign: mr.is_sdmf())
        d.addCallback(lambda issdmf: self.failUnless(issdmf))
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "test bad segment",
                            None,
                            mr.get_block_and_salt, 1))
        return d

    def test_read_with_prefetched_mdmf_data(self):
        self.init("test_read_with_prefetched_mdmf_data")

        # The MDMFSlotReadProxy will prefill certain fields if you pass
        # it data that you have already fetched. This is useful for
        # cases like the Servermap, which prefetches ~2kb of data while
        # finding out which shares are on the remote peer so that it
        # doesn't waste round trips.
        mdmf_data = self.build_test_mdmf_share()
        def _make_mr(ignored, length):
            mr = MDMFSlotReadProxy(self.rref, "si1", 0, mdmf_data[:length])
            return mr

        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1"))

        # This should be enough to fill in both the encoding parameters
        # and the table of offsets, which will complete the version
        # information tuple.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.failUnless(verinfo)
            self.failUnlessEqual(len(verinfo), 9)
            (seqnum,
             root_hash,
             salt_hash,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.failUnlessEqual(seqnum, 0)
            self.failUnlessEqual(root_hash, self.root_hash)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            expected_prefix = struct.pack(MDMFSIGNABLEHEADER,
                                          1,
                                          seqnum,
                                          root_hash,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.failUnlessEqual(expected_prefix, prefix)
            self.failUnlessEqual(self.rref.read_count, 0)
        d.addCallback(_check_verinfo)

        # This is not enough data to read a block and a share, so the
        # wrapper should attempt to read this from the remote server.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        def _check_block_and_salt((block, salt)):
            self.failUnlessEqual(block, self.block)
            self.failUnlessEqual(salt, self.salt)
            self.failUnlessEqual(self.rref.read_count, 1)

        # This should be enough data to read one block.
        d.addCallback(_make_mr, 123 + PRIVATE_KEY_SIZE + SIGNATURE_SIZE + VERIFICATION_KEY_SIZE + SHARE_HASH_CHAIN_SIZE + 140)
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        d.addCallback(_check_block_and_salt)
        return d

    def test_read_with_prefetched_sdmf_data(self):
        self.init("test_read_with_prefetched_sdmf_data")

        sdmf_data = self.build_test_sdmf_share()
        def _make_mr(ignored, length):
            mr = MDMFSlotReadProxy(self.rref, "si1", 0, sdmf_data[:length])
            return mr

        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_sdmf_share_to_server("si1"))

        # This should be enough to get us the encoding parameters,
        # offset table, and everything else we need to build a verinfo
        # string.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.failUnless(verinfo)
            self.failUnlessEqual(len(verinfo), 9)
            (seqnum,
             root_hash,
             salt,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.failUnlessEqual(seqnum, 0)
            self.failUnlessEqual(root_hash, self.root_hash)
            self.failUnlessEqual(salt, self.salt)
            self.failUnlessEqual(segsize, 36)
            self.failUnlessEqual(datalen, 36)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            expected_prefix = struct.pack(SIGNED_PREFIX,
                                          0,
                                          seqnum,
                                          root_hash,
                                          salt,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.failUnlessEqual(expected_prefix, prefix)
            self.failUnlessEqual(self.rref.read_count, 0)
        d.addCallback(_check_verinfo)
        # This shouldn't be enough to read any share data.
        d.addCallback(_make_mr, 123)
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        def _check_block_and_salt((block, salt)):
            self.failUnlessEqual(block, self.block * 6)
            self.failUnlessEqual(salt, self.salt)
            # TODO: Fix the read routine so that it reads only the data
            #       that it has cached if it can't read all of it.
            self.failUnlessEqual(self.rref.read_count, 2)

        # This should be enough to read share data.
        d.addCallback(_make_mr, self.offsets['share_data'])
        d.addCallback(lambda mr:
            mr.get_block_and_salt(0))
        d.addCallback(_check_block_and_salt)
        return d

    def test_read_with_empty_mdmf_file(self):
        self.init("test_read_with_empty_mdmf_file")

        # Some tests upload a file with no contents to test things
        # unrelated to the actual handling of the content of the file.
        # The reader should behave intelligently in these cases.
        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1", empty=True))
        # We should be able to get the encoding parameters, and they
        # should be correct.
        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters(params):
            self.failUnlessEqual(len(params), 4)
            k, n, segsize, datalen = params
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 0)
            self.failUnlessEqual(datalen, 0)
        d.addCallback(_check_encoding_parameters)

        # We should not be able to fetch a block, since there are no
        # blocks to fetch
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "get block on empty file",
                            None,
                            mr.get_block_and_salt, 0))
        return d

    def test_read_with_empty_sdmf_file(self):
        self.init("test_read_with_empty_sdmf_file")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_sdmf_share_to_server("si1", empty=True))
        # We should be able to get the encoding parameters, and they
        # should be correct
        d.addCallback(lambda ignored:
            mr.get_encoding_parameters())
        def _check_encoding_parameters(params):
            self.failUnlessEqual(len(params), 4)
            k, n, segsize, datalen = params
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            self.failUnlessEqual(segsize, 0)
            self.failUnlessEqual(datalen, 0)
        d.addCallback(_check_encoding_parameters)

        # It does not make sense to get a block in this format, so we
        # should not be able to.
        d.addCallback(lambda ignored:
            self.shouldFail(LayoutInvalid, "get block on an empty file",
                            None,
                            mr.get_block_and_salt, 0))
        return d

    def test_verinfo_with_sdmf_file(self):
        self.init("test_verinfo_with_sdmf_file")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        # We should be able to get the version information.
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_sdmf_share_to_server("si1"))
        d.addCallback(lambda ignored:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.failUnless(verinfo)
            self.failUnlessEqual(len(verinfo), 9)
            (seqnum,
             root_hash,
             salt,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.failUnlessEqual(seqnum, 0)
            self.failUnlessEqual(root_hash, self.root_hash)
            self.failUnlessEqual(salt, self.salt)
            self.failUnlessEqual(segsize, 36)
            self.failUnlessEqual(datalen, 36)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            expected_prefix = struct.pack(">BQ32s16s BBQQ",
                                          0,
                                          seqnum,
                                          root_hash,
                                          salt,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.failUnlessEqual(prefix, expected_prefix)
            self.failUnlessEqual(offsets, self.offsets)
        d.addCallback(_check_verinfo)
        return d

    def test_verinfo_with_mdmf_file(self):
        self.init("test_verinfo_with_mdmf_file")

        mr = MDMFSlotReadProxy(self.rref, "si1", 0)
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_test_share_to_server("si1"))
        d.addCallback(lambda ignored:
            mr.get_verinfo())
        def _check_verinfo(verinfo):
            self.failUnless(verinfo)
            self.failUnlessEqual(len(verinfo), 9)
            (seqnum,
             root_hash,
             IV,
             segsize,
             datalen,
             k,
             n,
             prefix,
             offsets) = verinfo
            self.failUnlessEqual(seqnum, 0)
            self.failUnlessEqual(root_hash, self.root_hash)
            self.failIf(IV, IV)
            self.failUnlessEqual(segsize, 6)
            self.failUnlessEqual(datalen, 36)
            self.failUnlessEqual(k, 3)
            self.failUnlessEqual(n, 10)
            expected_prefix = struct.pack(">BQ32s BBQQ",
                                          1,
                                          seqnum,
                                          root_hash,
                                          k,
                                          n,
                                          segsize,
                                          datalen)
            self.failUnlessEqual(prefix, expected_prefix)
            self.failUnlessEqual(offsets, self.offsets)
        d.addCallback(_check_verinfo)
        return d

    def test_sdmf_writer(self):
        self.init("test_sdmf_writer")

        # Go through the motions of writing an SDMF share to the storage
        # server. Then read the storage server to see that the share got
        # written in the way that we think it should have.

        # We do this first so that the necessary instance variables get
        # set the way we want them for the tests below.
        data = self.build_test_sdmf_share()
        sdmfr = SDMFSlotWriteProxy(0,
                                   self.rref,
                                   "si1",
                                   self.secrets,
                                   0, 3, 10, 36, 36)
        # Put the block and salt.
        sdmfr.put_block(self.blockdata, 0, self.salt)

        # Put the encprivkey
        sdmfr.put_encprivkey(self.encprivkey)

        # Put the block and share hash chains
        sdmfr.put_blockhashes(self.block_hash_tree)
        sdmfr.put_sharehashes(self.share_hash_chain)
        sdmfr.put_root_hash(self.root_hash)

        # Put the signature
        sdmfr.put_signature(self.signature)

        # Put the verification key
        sdmfr.put_verification_key(self.verification_key)

        # Now check to make sure that nothing has been written yet.
        self.failUnlessEqual(self.rref.write_count, 0)

        # Now finish publishing
        d = sdmfr.finish_publishing()
        d.addCallback(lambda ign: self.failUnlessEqual(self.rref.write_count, 1))
        d.addCallback(lambda ign: self.aa.remote_slot_readv("si1", [0], [(0, len(data))]))
        d.addCallback(lambda res: self.failUnlessEqual(res, {0: [data]}))
        return d

    def test_sdmf_writer_preexisting_share(self):
        self.init("test_sdmf_writer_preexisting_share")

        data = self.build_test_sdmf_share()
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.write_sdmf_share_to_server("si1"))
        def _written(ign):
            # Now there is a share on the storage server. To successfully
            # write, we need to set the checkstring correctly. When we
            # don't, no write should occur.
            sdmfw = SDMFSlotWriteProxy(0,
                                       self.rref,
                                       "si1",
                                       self.secrets,
                                       1, 3, 10, 36, 36)
            sdmfw.put_block(self.blockdata, 0, self.salt)

            # Put the encprivkey
            sdmfw.put_encprivkey(self.encprivkey)

            # Put the block and share hash chains
            sdmfw.put_blockhashes(self.block_hash_tree)
            sdmfw.put_sharehashes(self.share_hash_chain)

            # Put the root hash
            sdmfw.put_root_hash(self.root_hash)

            # Put the signature
            sdmfw.put_signature(self.signature)

            # Put the verification key
            sdmfw.put_verification_key(self.verification_key)

            # We shouldn't have a checkstring yet
            self.failUnlessEqual(sdmfw.get_checkstring(), "")

            d2 = sdmfw.finish_publishing()
            def _then(results):
                self.failIf(results[0])
                # this is the correct checkstring
                self._expected_checkstring = results[1][0][0]
                return self._expected_checkstring
            d2.addCallback(_then)
            d2.addCallback(sdmfw.set_checkstring)
            d2.addCallback(lambda ign: sdmfw.get_checkstring())
            d2.addCallback(lambda checkstring: self.failUnlessEqual(checkstring,
                                                                    self._expected_checkstring))
            d2.addCallback(lambda ign: sdmfw.finish_publishing())
            d2.addCallback(lambda res: self.failUnless(res[0], res))
            d2.addCallback(lambda ign: self.aa.remote_slot_readv("si1", [0], [(1, 8)]))
            d2.addCallback(lambda res: self.failUnlessEqual(res, {0: [struct.pack(">Q", 1)]}))
            d2.addCallback(lambda ign: self.aa.remote_slot_readv("si1", [0], [(9, len(data) - 9)]))
            d2.addCallback(lambda res: self.failUnlessEqual(res, {0: [data[9:]]}))
            return d2
        d.addCallback(_written)
        return d


class Stats(WithDiskBackend, unittest.TestCase):
    def test_latencies(self):
        server = self.create("test_latencies")
        for i in range(10000):
            server.add_latency("allocate", 1.0 * i)
        for i in range(1000):
            server.add_latency("renew", 1.0 * i)
        for i in range(20):
            server.add_latency("write", 1.0 * i)
        for i in range(10):
            server.add_latency("cancel", 2.0 * i)
        server.add_latency("get", 5.0)

        output = server.get_latencies()

        self.failUnlessEqual(sorted(output.keys()),
                             sorted(["allocate", "renew", "cancel", "write", "get"]))
        self.failUnlessEqual(len(server.latencies["allocate"]), 1000)
        self.failUnless(abs(output["allocate"]["mean"] - 9500) < 1, output)
        self.failUnless(abs(output["allocate"]["01_0_percentile"] - 9010) < 1, output)
        self.failUnless(abs(output["allocate"]["10_0_percentile"] - 9100) < 1, output)
        self.failUnless(abs(output["allocate"]["50_0_percentile"] - 9500) < 1, output)
        self.failUnless(abs(output["allocate"]["90_0_percentile"] - 9900) < 1, output)
        self.failUnless(abs(output["allocate"]["95_0_percentile"] - 9950) < 1, output)
        self.failUnless(abs(output["allocate"]["99_0_percentile"] - 9990) < 1, output)
        self.failUnless(abs(output["allocate"]["99_9_percentile"] - 9999) < 1, output)

        self.failUnlessEqual(len(server.latencies["renew"]), 1000)
        self.failUnless(abs(output["renew"]["mean"] - 500) < 1, output)
        self.failUnless(abs(output["renew"]["01_0_percentile"] -  10) < 1, output)
        self.failUnless(abs(output["renew"]["10_0_percentile"] - 100) < 1, output)
        self.failUnless(abs(output["renew"]["50_0_percentile"] - 500) < 1, output)
        self.failUnless(abs(output["renew"]["90_0_percentile"] - 900) < 1, output)
        self.failUnless(abs(output["renew"]["95_0_percentile"] - 950) < 1, output)
        self.failUnless(abs(output["renew"]["99_0_percentile"] - 990) < 1, output)
        self.failUnless(abs(output["renew"]["99_9_percentile"] - 999) < 1, output)

        self.failUnlessEqual(len(server.latencies["write"]), 20)
        self.failUnless(abs(output["write"]["mean"] - 9) < 1, output)
        self.failUnless(output["write"]["01_0_percentile"] is None, output)
        self.failUnless(abs(output["write"]["10_0_percentile"] -  2) < 1, output)
        self.failUnless(abs(output["write"]["50_0_percentile"] - 10) < 1, output)
        self.failUnless(abs(output["write"]["90_0_percentile"] - 18) < 1, output)
        self.failUnless(abs(output["write"]["95_0_percentile"] - 19) < 1, output)
        self.failUnless(output["write"]["99_0_percentile"] is None, output)
        self.failUnless(output["write"]["99_9_percentile"] is None, output)

        self.failUnlessEqual(len(server.latencies["cancel"]), 10)
        self.failUnless(abs(output["cancel"]["mean"] - 9) < 1, output)
        self.failUnless(output["cancel"]["01_0_percentile"] is None, output)
        self.failUnless(abs(output["cancel"]["10_0_percentile"] -  2) < 1, output)
        self.failUnless(abs(output["cancel"]["50_0_percentile"] - 10) < 1, output)
        self.failUnless(abs(output["cancel"]["90_0_percentile"] - 18) < 1, output)
        self.failUnless(output["cancel"]["95_0_percentile"] is None, output)
        self.failUnless(output["cancel"]["99_0_percentile"] is None, output)
        self.failUnless(output["cancel"]["99_9_percentile"] is None, output)

        self.failUnlessEqual(len(server.latencies["get"]), 1)
        self.failUnless(output["get"]["mean"] is None, output)
        self.failUnless(output["get"]["01_0_percentile"] is None, output)
        self.failUnless(output["get"]["10_0_percentile"] is None, output)
        self.failUnless(output["get"]["50_0_percentile"] is None, output)
        self.failUnless(output["get"]["90_0_percentile"] is None, output)
        self.failUnless(output["get"]["95_0_percentile"] is None, output)
        self.failUnless(output["get"]["99_0_percentile"] is None, output)
        self.failUnless(output["get"]["99_9_percentile"] is None, output)


def remove_tags(s):
    s = re.sub(r'<[^>]*>', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


class BucketCounterTest(WithDiskBackend, CrawlerTestMixin, ReallyEqualMixin, unittest.TestCase):
    def test_bucket_counter(self):
        server = self.create("test_bucket_counter", detached=True)
        bucket_counter = server.bucket_counter

        # finish as fast as possible
        bucket_counter.slow_start = 0
        bucket_counter.cpu_slice = 100.0

        d = server.bucket_counter.set_hook('after_prefix')

        server.setServiceParent(self.sparent)

        w = StorageStatus(server)

        # this sample is before the crawler has started doing anything
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: Yes", s)
        self.failUnlessIn("Reserved space: - 0 B (0)", s)
        self.failUnlessIn("Total sharesets: Not computed yet", s)
        self.failUnlessIn("Next crawl in", s)

        def _after_first_prefix(prefix):
            server.bucket_counter.save_state()
            state = bucket_counter.get_state()
            self.failUnlessEqual(prefix, state["last-complete-prefix"])
            self.failUnlessEqual(prefix, bucket_counter.prefixes[0])

            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn(" Current crawl ", s)
            self.failUnlessIn(" (next work in ", s)

            return bucket_counter.set_hook('after_cycle')
        d.addCallback(_after_first_prefix)

        def _after_first_cycle(cycle):
            self.failUnlessEqual(cycle, 0)
            progress = bucket_counter.get_progress()
            self.failUnlessReallyEqual(progress["cycle-in-progress"], False)
        d.addCallback(_after_first_cycle)
        d.addBoth(self._wait_for_yield, bucket_counter)

        def _after_yield(ign):
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("Total sharesets: 0 (the number of", s)
            self.failUnless("Next crawl in 59 minutes" in s or "Next crawl in 60 minutes" in s, s)
        d.addCallback(_after_yield)
        return d

    def test_bucket_counter_cleanup(self):
        server = self.create("test_bucket_counter_cleanup", detached=True)
        bucket_counter = server.bucket_counter

        # finish as fast as possible
        bucket_counter.slow_start = 0
        bucket_counter.cpu_slice = 100.0

        d = bucket_counter.set_hook('after_prefix')

        server.setServiceParent(self.sparent)

        def _after_first_prefix(prefix):
            bucket_counter.save_state()
            state = bucket_counter.state
            self.failUnlessEqual(prefix, state["last-complete-prefix"])
            self.failUnlessEqual(prefix, bucket_counter.prefixes[0])

            # now sneak in and mess with its state, to make sure it cleans up
            # properly at the end of the cycle
            state["bucket-counts"][-12] = {}
            bucket_counter.save_state()

            return bucket_counter.set_hook('after_cycle')
        d.addCallback(_after_first_prefix)

        def _after_first_cycle(cycle):
            self.failUnlessEqual(cycle, 0)
            progress = bucket_counter.get_progress()
            self.failUnlessReallyEqual(progress["cycle-in-progress"], False)

            s = bucket_counter.get_state()
            self.failIf(-12 in s["bucket-counts"], s["bucket-counts"].keys())
        d.addCallback(_after_first_cycle)
        d.addBoth(self._wait_for_yield, bucket_counter)
        return d

    def test_bucket_counter_eta(self):
        server = self.create("test_bucket_counter_eta", detached=True)
        bucket_counter = server.bucket_counter

        # finish as fast as possible
        bucket_counter.slow_start = 0
        bucket_counter.cpu_slice = 100.0

        d = bucket_counter.set_hook('after_prefix')

        server.setServiceParent(self.sparent)

        w = StorageStatus(server)

        def _check_1(prefix1):
            # no ETA is available yet
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("complete (next work", s)

            return bucket_counter.set_hook('after_prefix')
        d.addCallback(_check_1)

        def _check_2(prefix2):
            # an ETA based upon elapsed time should be available.
            html = w.renderSynchronously()
            s = remove_tags(html)
            self.failUnlessIn("complete (ETA ", s)
        d.addCallback(_check_2)
        d.addBoth(self._wait_for_yield, bucket_counter)
        return d


class AccountingCrawlerTest(CrawlerTestMixin, WebRenderingMixin, ReallyEqualMixin):
    def make_shares(self, server):
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        def make(si):
            return (si, hashutil.tagged_hash("renew", si),
                    hashutil.tagged_hash("cancel", si))
        def make_mutable(si):
            return (si, hashutil.tagged_hash("renew", si),
                    hashutil.tagged_hash("cancel", si),
                    hashutil.tagged_hash("write-enabler", si))
        def make_extra_lease(si, num):
            return (hashutil.tagged_hash("renew-%d" % num, si),
                    hashutil.tagged_hash("cancel-%d" % num, si))

        writev = aa.remote_slot_testv_and_readv_and_writev

        immutable_si_0, rs0, cs0 = make("\x00" * 16)
        immutable_si_1, rs1, cs1 = make("\x01" * 16)
        rs1a, cs1a = make_extra_lease(immutable_si_1, 1)
        mutable_si_2, rs2, cs2, we2 = make_mutable("\x02" * 16)
        mutable_si_3, rs3, cs3, we3 = make_mutable("\x03" * 16)
        rs3a, cs3a = make_extra_lease(mutable_si_3, 1)
        sharenums = [0]
        canary = FakeCanary()
        # note: 'tahoe debug dump-share' will not handle this file, since the
        # inner contents are not a valid CHK share
        data = "\xff" * 1000

        self.sis = [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3]
        self.renew_secrets = [rs0, rs1, rs1a, rs2, rs3, rs3a]
        self.cancel_secrets = [cs0, cs1, cs1a, cs2, cs3, cs3a]

        d = defer.succeed(None)
        d.addCallback(lambda ign: aa.remote_allocate_buckets(immutable_si_0, rs0, cs0, sharenums,
                                                             1000, canary))
        def _got_buckets( (a, w) ):
            w[0].remote_write(0, data)
            w[0].remote_close()
        d.addCallback(_got_buckets)

        d.addCallback(lambda ign: aa.remote_allocate_buckets(immutable_si_1, rs1, cs1, sharenums,
                                                             1000, canary))
        d.addCallback(_got_buckets)
        d.addCallback(lambda ign: sa.remote_add_lease(immutable_si_1, rs1a, cs1a))

        d.addCallback(lambda ign: writev(mutable_si_2, (we2, rs2, cs2),
                                         {0: ([], [(0,data)], len(data))}, []))
        d.addCallback(lambda ign: writev(mutable_si_3, (we3, rs3, cs3),
                                         {0: ([], [(0,data)], len(data))}, []))
        d.addCallback(lambda ign: sa.remote_add_lease(mutable_si_3, rs3a, cs3a))
        return d

    def test_basic(self):
        server = self.create("test_basic", detached=True)

        ep = ExpirationPolicy(enabled=False)
        server.get_accountant().set_expiration_policy(ep)
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        # finish as fast as possible
        ac = server.get_accounting_crawler()
        ac.slow_start = 0
        ac.cpu_slice = 500

        webstatus = StorageStatus(server)

        # create a few shares, with some leases on them
        d = self.make_shares(server)
        def _do_test(ign):
            [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

            if isinstance(server.backend, DiskBackend):
                # add a non-sharefile to exercise another code path
                fn = os.path.join(server.backend._sharedir,
                                  storage_index_to_dir(immutable_si_0),
                                  "not-a-share")
                fileutil.write(fn, "I am not a share.\n")

            # this is before the crawl has started, so we're not in a cycle yet
            initial_state = ac.get_state()
            self.failIf(ac.get_progress()["cycle-in-progress"])
            self.failIfIn("cycle-to-date", initial_state)
            self.failIfIn("estimated-remaining-cycle", initial_state)
            self.failIfIn("estimated-current-cycle", initial_state)
            self.failUnlessIn("history", initial_state)
            self.failUnlessEqual(initial_state["history"], {})

            server.setServiceParent(self.sparent)

            DAY = 24*60*60

            # now examine the state right after the 'aa' prefix has been processed.
            d2 = self._after_prefix(None, 'aa', ac)
            def _after_aa_prefix(state):
                self.failUnlessIn("cycle-to-date", state)
                self.failUnlessIn("estimated-remaining-cycle", state)
                self.failUnlessIn("estimated-current-cycle", state)
                self.failUnlessIn("history", state)
                self.failUnlessEqual(state["history"], {})

                so_far = state["cycle-to-date"]
                self.failUnlessEqual(so_far["expiration-enabled"], False)
                self.failUnlessIn("configured-expiration-mode", so_far)
                self.failUnlessIn("lease-age-histogram", so_far)
                lah = so_far["lease-age-histogram"]
                self.failUnlessEqual(type(lah), list)
                self.failUnlessEqual(len(lah), 1)
                self.failUnlessEqual(lah, [ (0.0, DAY, 1) ] )
                self.failUnlessEqual(so_far["corrupt-shares"], [])
                sr1 = so_far["space-recovered"]
                self.failUnlessEqual(sr1["examined-buckets"], 1)
                self.failUnlessEqual(sr1["examined-shares"], 1)
                self.failUnlessEqual(sr1["actual-shares"], 0)
                left = state["estimated-remaining-cycle"]
                sr2 = left["space-recovered"]
                self.failUnless(sr2["examined-buckets"] > 0, sr2["examined-buckets"])
                self.failUnless(sr2["examined-shares"] > 0, sr2["examined-shares"])
                self.failIfEqual(sr2["actual-shares"], None)
            d2.addCallback(_after_aa_prefix)

            d2.addCallback(lambda ign: self.render1(webstatus))
            def _check_html_in_cycle(html):
                s = remove_tags(html)
                self.failUnlessIn("So far, this cycle has examined "
                                  "1 shares in 1 sharesets (0 mutable / 1 immutable) ", s)
                self.failUnlessIn("and has recovered: "
                                  "0 shares, 0 sharesets (0 mutable / 0 immutable), "
                                  "0 B (0 B / 0 B)", s)

                return ac.set_hook('after_cycle')
            d2.addCallback(_check_html_in_cycle)

            def _after_first_cycle(cycle):
                # After the first cycle, nothing should have been removed.
                self.failUnlessEqual(cycle, 0)
                progress = ac.get_progress()
                self.failUnlessReallyEqual(progress["cycle-in-progress"], False)

                s = ac.get_state()
                self.failIf("cycle-to-date" in s)
                self.failIf("estimated-remaining-cycle" in s)
                self.failIf("estimated-current-cycle" in s)
                last = s["history"][0]
                self.failUnlessEqual(type(last), dict, repr(last))
                self.failUnlessIn("cycle-start-finish-times", last)
                self.failUnlessEqual(type(last["cycle-start-finish-times"]), list, repr(last))
                self.failUnlessEqual(last["expiration-enabled"], False)
                self.failUnlessIn("configured-expiration-mode", last)

                self.failUnlessIn("lease-age-histogram", last)
                lah = last["lease-age-histogram"]
                self.failUnlessEqual(type(lah), list)
                self.failUnlessEqual(len(lah), 1)
                self.failUnlessEqual(tuple(lah[0]), (0.0, DAY, 6) )

                self.failUnlessEqual(last["corrupt-shares"], [])

                rec = last["space-recovered"]
                self.failUnlessEqual(rec["examined-buckets"], 4)
                self.failUnlessEqual(rec["examined-shares"], 4)
                self.failUnlessEqual(rec["actual-buckets"], 0)
                self.failUnlessEqual(rec["actual-shares"], 0)
                self.failUnlessEqual(rec["actual-diskbytes"], 0)

                def count_leases(si):
                    return (len(aa.get_leases(si)), len(sa.get_leases(si)))
                self.failUnlessEqual(count_leases(immutable_si_0), (1, 0))
                self.failUnlessEqual(count_leases(immutable_si_1), (1, 1))
                self.failUnlessEqual(count_leases(mutable_si_2), (1, 0))
                self.failUnlessEqual(count_leases(mutable_si_3), (1, 1))
            d2.addCallback(_after_first_cycle)

            d2.addCallback(lambda ign: self.render1(webstatus))
            def _check_html_after_cycle(html):
                s = remove_tags(html)
                self.failUnlessIn("recovered: 0 shares, 0 sharesets "
                                  "(0 mutable / 0 immutable), 0 B (0 B / 0 B) ", s)
                self.failUnlessIn("and saw a total of 4 shares, 4 sharesets "
                                  "(2 mutable / 2 immutable),", s)
                self.failUnlessIn("but expiration was not enabled", s)
            d2.addCallback(_check_html_after_cycle)

            d2.addCallback(lambda ign: self.render_json(webstatus))
            def _check_json_after_cycle(json):
                data = simplejson.loads(json)
                self.failUnlessIn("lease-checker", data)
                self.failUnlessIn("lease-checker-progress", data)
            d2.addCallback(_check_json_after_cycle)
            d2.addBoth(self._wait_for_yield, ac)
            return d2
        d.addCallback(_do_test)
        return d

    def _assert_sharecount(self, server, si, expected):
        d = defer.succeed(None)
        d.addCallback(lambda ign: server.backend.get_shareset(si).get_shares())
        def _got_shares( (shares, corrupted) ):
            self.failUnlessEqual(len(shares), expected, "share count for %r" % (si,))
            self.failUnlessEqual(len(corrupted), 0, str(corrupted))
        d.addCallback(_got_shares)
        return d

    def _assert_leasecount(self, server, si, expected):
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()
        self.failUnlessEqual((len(aa.get_leases(si)), len(sa.get_leases(si))),
                             expected)

    def test_expire_age(self):
        server = self.create("test_expire_age", detached=True)

        # setting expiration_time to 2000 means that any lease which is more
        # than 2000s old will be expired.
        now = time.time()
        ep = ExpirationPolicy(enabled=True, mode="age", override_lease_duration=2000)
        server.get_accountant().set_expiration_policy(ep)
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        # finish as fast as possible
        ac = server.get_accounting_crawler()
        ac.slow_start = 0
        ac.cpu_slice = 500

        webstatus = StorageStatus(server)

        # create a few shares, with some leases on them
        d = self.make_shares(server)
        def _do_test(ign):
            [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: self._assert_sharecount(server, immutable_si_0, 1))
            d2.addCallback(lambda ign: self._assert_leasecount(server, immutable_si_0, (1, 0)))
            d2.addCallback(lambda ign: self._assert_sharecount(server, immutable_si_1, 1))
            d2.addCallback(lambda ign: self._assert_leasecount(server, immutable_si_1, (1, 1)))
            d2.addCallback(lambda ign: self._assert_sharecount(server, mutable_si_2,   1))
            d2.addCallback(lambda ign: self._assert_leasecount(server, mutable_si_2,   (1, 0)))
            d2.addCallback(lambda ign: self._assert_sharecount(server, mutable_si_3,   1))
            d2.addCallback(lambda ign: self._assert_leasecount(server, mutable_si_3,   (1, 1)))

            def _then(ign):
                # artificially crank back the renewal time on the first lease of each
                # share to 3000s ago, and set the expiration time to 31 days later.
                new_renewal_time = now - 3000
                new_expiration_time = new_renewal_time + 31*24*60*60

                # Some shares have an extra lease which is set to expire at the
                # default time in 31 days from now (age=31days). We then run the
                # crawler, which will expire the first lease, making some shares get
                # deleted and others stay alive (with one remaining lease)

                aa.add_or_renew_lease(immutable_si_0, 0, new_renewal_time, new_expiration_time)

                # immutable_si_1 gets an extra lease
                sa.add_or_renew_lease(immutable_si_1, 0, new_renewal_time, new_expiration_time)

                aa.add_or_renew_lease(mutable_si_2,   0, new_renewal_time, new_expiration_time)

                # mutable_si_3 gets an extra lease
                sa.add_or_renew_lease(mutable_si_3,   0, new_renewal_time, new_expiration_time)

                server.setServiceParent(self.sparent)

                # now examine the web status right after the 'aa' prefix has been processed.
                d3 = self._after_prefix(None, 'aa', ac)
                d3.addCallback(lambda ign: self.render1(webstatus))
                def _check_html_in_cycle(html):
                    s = remove_tags(html)
                    # the first shareset encountered gets deleted, and its prefix
                    # happens to be about 1/5th of the way through the ring, so the
                    # predictor thinks we'll have 5 shares and that we'll delete them
                    # all. This part of the test depends upon the SIs landing right
                    # where they do now.
                    self.failUnlessIn("The remainder of this cycle is expected to "
                                      "recover: 4 shares, 4 sharesets", s)
                    self.failUnlessIn("The whole cycle is expected to examine "
                                      "5 shares in 5 sharesets and to recover: "
                                      "5 shares, 5 sharesets", s)

                    return ac.set_hook('after_cycle')
                d3.addCallback(_check_html_in_cycle)

                d3.addCallback(lambda ign: self._assert_sharecount(server, immutable_si_0, 0))
                d3.addCallback(lambda ign: self._assert_sharecount(server, immutable_si_1, 1))
                d3.addCallback(lambda ign: self._assert_leasecount(server, immutable_si_1, (1, 0)))
                d3.addCallback(lambda ign: self._assert_sharecount(server, mutable_si_2,   0))
                d3.addCallback(lambda ign: self._assert_sharecount(server, mutable_si_3,   1))
                d3.addCallback(lambda ign: self._assert_leasecount(server, mutable_si_3,   (1, 0)))

                def _after_first_cycle(ignored):
                    s = ac.get_state()
                    last = s["history"][0]

                    self.failUnlessEqual(last["expiration-enabled"], True)
                    cem = last["configured-expiration-mode"]
                    self.failUnlessEqual(cem[0], "age")
                    self.failUnlessEqual(cem[1], 2000)
                    self.failUnlessEqual(cem[2], None)
                    self.failUnlessEqual(cem[3][0], "mutable")
                    self.failUnlessEqual(cem[3][1], "immutable")

                    rec = last["space-recovered"]
                    self.failUnlessEqual(rec["examined-buckets"], 4)
                    self.failUnlessEqual(rec["examined-shares"], 4)
                    self.failUnlessEqual(rec["actual-buckets"], 2)
                    self.failUnlessEqual(rec["actual-shares"], 2)
                    # different platforms have different notions of "blocks used by
                    # this file", so merely assert that it's a number
                    self.failUnless(rec["actual-diskbytes"] >= 0,
                                    rec["actual-diskbytes"])
                d3.addCallback(_after_first_cycle)

                d3.addCallback(lambda ign: self.render1(webstatus))
                def _check_html_after_cycle(html):
                    s = remove_tags(html)
                    self.failUnlessIn("Expiration Enabled: expired leases will be removed", s)
                    self.failUnlessIn("Leases created or last renewed more than 33 minutes ago will be considered expired.", s)
                    self.failUnlessIn(" recovered: 2 shares, 2 sharesets (1 mutable / 1 immutable), ", s)
                d3.addCallback(_check_html_after_cycle)
                d3.addBoth(self._wait_for_yield, ac)
                return d3
            d2.addCallback(_then)
            return d2
        d.addCallback(_do_test)
        return d

    def test_expire_cutoff_date(self):
        server = self.create("test_expire_cutoff_date", detached=True)

        # setting cutoff-date to 2000 seconds ago means that any lease which
        # is more than 2000s old will be expired.
        now = time.time()
        then = int(now - 2000)
        ep = ExpirationPolicy(enabled=True, mode="cutoff-date", cutoff_date=then)
        server.get_accountant().set_expiration_policy(ep)
        aa = server.get_accountant().get_anonymous_account()
        sa = server.get_accountant().get_starter_account()

        # finish as fast as possible
        ac = server.get_accounting_crawler()
        ac.slow_start = 0
        ac.cpu_slice = 500

        webstatus = StorageStatus(server)

        # create a few shares, with some leases on them
        d = self.make_shares(server)
        def _do_test(ign):
            [immutable_si_0, immutable_si_1, mutable_si_2, mutable_si_3] = self.sis

            d2 = defer.succeed(None)
            d2.addCallback(lambda ign: self._assert_sharecount(server, immutable_si_0, 1))
            d2.addCallback(lambda ign: self._assert_leasecount(server, immutable_si_0, (1, 0)))
            d2.addCallback(lambda ign: self._assert_sharecount(server, immutable_si_1, 1))
            d2.addCallback(lambda ign: self._assert_leasecount(server, immutable_si_1, (1, 1)))
            d2.addCallback(lambda ign: self._assert_sharecount(server, mutable_si_2,   1))
            d2.addCallback(lambda ign: self._assert_leasecount(server, mutable_si_2,   (1, 0)))
            d2.addCallback(lambda ign: self._assert_sharecount(server, mutable_si_3,   1))
            d2.addCallback(lambda ign: self._assert_leasecount(server, mutable_si_3,   (1, 1)))

            def _then(ign):
                # artificially crank back the renewal time on the first lease of each
                # share to 3000s ago, and set the expiration time to 31 days later.
                new_renewal_time = now - 3000
                new_expiration_time = new_renewal_time + 31*24*60*60

                # Some shares have an extra lease which is set to expire at the
                # default time in 31 days from now (age=31days). We then run the
                # crawler, which will expire the first lease, making some shares get
                # deleted and others stay alive (with one remaining lease)

                aa.add_or_renew_lease(immutable_si_0, 0, new_renewal_time, new_expiration_time)

                # immutable_si_1 gets an extra lease
                sa.add_or_renew_lease(immutable_si_1, 0, new_renewal_time, new_expiration_time)

                aa.add_or_renew_lease(mutable_si_2,   0, new_renewal_time, new_expiration_time)

                # mutable_si_3 gets an extra lease
                sa.add_or_renew_lease(mutable_si_3,   0, new_renewal_time, new_expiration_time)

                server.setServiceParent(self.sparent)

                # now examine the web status right after the 'aa' prefix has been processed.
                d3 = self._after_prefix(None, 'aa', ac)
                d3.addCallback(lambda ign: self.render1(webstatus))
                def _check_html_in_cycle(html):
                    s = remove_tags(html)
                    # the first bucket encountered gets deleted, and its prefix
                    # happens to be about 1/5th of the way through the ring, so the
                    # predictor thinks we'll have 5 shares and that we'll delete them
                    # all. This part of the test depends upon the SIs landing right
                    # where they do now.
                    self.failUnlessIn("The remainder of this cycle is expected to "
                                      "recover: 4 shares, 4 sharesets", s)
                    self.failUnlessIn("The whole cycle is expected to examine "
                                      "5 shares in 5 sharesets and to recover: "
                                      "5 shares, 5 sharesets", s)

                    return ac.set_hook('after_cycle')
                d3.addCallback(_check_html_in_cycle)

                d3.addCallback(lambda ign: self._assert_sharecount(server, immutable_si_0, 0))
                d3.addCallback(lambda ign: self._assert_sharecount(server, immutable_si_1, 1))
                d3.addCallback(lambda ign: self._assert_leasecount(server, immutable_si_1, (1, 0)))
                d3.addCallback(lambda ign: self._assert_sharecount(server, mutable_si_2,   0))
                d3.addCallback(lambda ign: self._assert_sharecount(server, mutable_si_3,   1))
                d3.addCallback(lambda ign: self._assert_leasecount(server, mutable_si_3,   (1, 0)))

                def _after_first_cycle(ignored):
                    s = ac.get_state()
                    last = s["history"][0]

                    self.failUnlessEqual(last["expiration-enabled"], True)
                    cem = last["configured-expiration-mode"]
                    self.failUnlessEqual(cem[0], "cutoff-date")
                    self.failUnlessEqual(cem[1], None)
                    self.failUnlessEqual(cem[2], then)
                    self.failUnlessEqual(cem[3][0], "mutable")
                    self.failUnlessEqual(cem[3][1], "immutable")

                    rec = last["space-recovered"]
                    self.failUnlessEqual(rec["examined-buckets"], 4)
                    self.failUnlessEqual(rec["examined-shares"], 4)
                    self.failUnlessEqual(rec["actual-buckets"], 2)
                    self.failUnlessEqual(rec["actual-shares"], 2)
                    # different platforms have different notions of "blocks used by
                    # this file", so merely assert that it's a number
                    self.failUnless(rec["actual-diskbytes"] >= 0,
                                    rec["actual-diskbytes"])
                d3.addCallback(_after_first_cycle)

                d3.addCallback(lambda ign: self.render1(webstatus))
                def _check_html_after_cycle(html):
                    s = remove_tags(html)
                    self.failUnlessIn("Expiration Enabled:"
                                      " expired leases will be removed", s)
                    date = time.strftime("%Y-%m-%d (%d-%b-%Y) UTC", time.gmtime(then))
                    substr = "Leases created or last renewed before %s will be considered expired." % date
                    self.failUnlessIn(substr, s)
                    self.failUnlessIn(" recovered: 2 shares, 2 sharesets (1 mutable / 1 immutable), ", s)
                d3.addCallback(_check_html_after_cycle)
                d3.addBoth(self._wait_for_yield, ac)
                return d3
            d2.addCallback(_then)
            return d2
        d.addCallback(_do_test)
        return d

    def test_bad_mode(self):
        e = self.failUnlessRaises(AssertionError,
                                  ExpirationPolicy, enabled=True, mode="bogus")
        self.failUnlessIn("GC mode 'bogus' must be 'age' or 'cutoff-date'", str(e))

    def test_parse_duration(self):
        DAY = 24*60*60
        MONTH = 31*DAY
        YEAR = 365*DAY
        p = time_format.parse_duration
        self.failUnlessEqual(p("7days"), 7*DAY)
        self.failUnlessEqual(p("31day"), 31*DAY)
        self.failUnlessEqual(p("60 days"), 60*DAY)
        self.failUnlessEqual(p("2mo"), 2*MONTH)
        self.failUnlessEqual(p("3 month"), 3*MONTH)
        self.failUnlessEqual(p("2years"), 2*YEAR)
        e = self.failUnlessRaises(ValueError, p, "2kumquats")
        self.failUnlessIn("no unit (like day, month, or year) in '2kumquats'", str(e))

    def test_parse_date(self):
        p = time_format.parse_date
        self.failUnless(isinstance(p("2009-03-18"), int), p("2009-03-18"))
        self.failUnlessEqual(p("2009-03-18"), 1237334400)

    def test_limited_history(self):
        server = self.create("test_limited_history", detached=True)

        # finish as fast as possible
        RETAINED = 2
        CYCLES = 4
        ac = server.get_accounting_crawler()
        ac._leasedb.retained_history_entries = RETAINED
        ac.slow_start = 0
        ac.cpu_slice = 500
        ac.allowed_cpu_proportion = 1.0
        ac.minimum_cycle_time = 0

        # create a few shares, with some leases on them
        d = self.make_shares(server)
        def _do_test(ign):
            server.setServiceParent(self.sparent)

            d2 = ac.set_hook('after_cycle')
            def _after_cycle(cycle):
                if cycle < CYCLES:
                    return ac.set_hook('after_cycle').addCallback(_after_cycle)

                state = ac.get_state()
                self.failUnlessIn("history", state)
                h = state["history"]
                self.failUnlessEqual(len(h), RETAINED)
                self.failUnlessEqual(max(h.keys()), CYCLES)
                self.failUnlessEqual(min(h.keys()), CYCLES-RETAINED+1)
            d2.addCallback(_after_cycle)
            d2.addBoth(self._wait_for_yield, ac)
        d.addCallback(_do_test)
        return d

    def render_json(self, page):
        d = self.render1(page, args={"t": ["json"]})
        return d


class AccountingCrawlerWithDiskBackend(WithDiskBackend, AccountingCrawlerTest, unittest.TestCase):
    pass


#class AccountingCrawlerWithMockCloudBackend(WithMockCloudBackend, AccountingCrawlerTest, unittest.TestCase):
#    pass


class WebStatusWithDiskBackend(WithDiskBackend, WebRenderingMixin, unittest.TestCase):
    def test_no_server(self):
        w = StorageStatus(None)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>No Storage Server Running</h1>", html)

    def test_status(self):
        server = self.create("test_status")

        w = StorageStatus(server, "nickname")
        d = self.render1(w)
        def _check_html(html):
            self.failUnlessIn("<h1>Storage Server Status</h1>", html)
            s = remove_tags(html)
            self.failUnlessIn("Server Nickname: nickname", s)
            self.failUnlessIn("Server Nodeid: %s"  % base32.b2a(server.get_serverid()), s)
            self.failUnlessIn("Accepting new shares: Yes", s)
            self.failUnlessIn("Reserved space: - 0 B (0)", s)
        d.addCallback(_check_html)
        d.addCallback(lambda ign: self.render_json(w))
        def _check_json(json):
            data = simplejson.loads(json)
            s = data["stats"]
            self.failUnlessEqual(s["storage_server.accepting_immutable_shares"], 1)
            self.failUnlessEqual(s["storage_server.reserved_space"], 0)
            self.failUnlessIn("bucket-counter", data)
            self.failUnlessIn("lease-checker", data)
        d.addCallback(_check_json)
        return d

    @mock.patch('allmydata.util.fileutil.get_disk_stats')
    def test_status_no_disk_stats(self, mock_get_disk_stats):
        mock_get_disk_stats.side_effect = AttributeError()

        # Some platforms may have no disk stats API. Make sure the code can handle that
        # (test runs on all platforms).
        server = self.create("test_status_no_disk_stats")

        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: Yes", s)
        self.failUnlessIn("Total disk space: ?", s)
        self.failUnlessIn("Space Available to Tahoe: ?", s)
        self.failUnless(server.get_available_space() is None)

    @mock.patch('allmydata.util.fileutil.get_disk_stats')
    def test_status_bad_disk_stats(self, mock_get_disk_stats):
        mock_get_disk_stats.side_effect = OSError()

        # If the API to get disk stats exists but a call to it fails, then the status should
        # show that no shares will be accepted, and get_available_space() should be 0.
        server = self.create("test_status_bad_disk_stats")

        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: No", s)
        self.failUnlessIn("Total disk space: ?", s)
        self.failUnlessIn("Space Available to Tahoe: ?", s)
        self.failUnlessEqual(server.get_available_space(), 0)

    @mock.patch('allmydata.util.fileutil.get_disk_stats')
    def test_status_right_disk_stats(self, mock_get_disk_stats):
        GB = 1000000000
        total            = 5*GB
        free_for_root    = 4*GB
        free_for_nonroot = 3*GB
        reserved_space   = 1*GB
        used = total - free_for_root
        avail = max(free_for_nonroot - reserved_space, 0)
        mock_get_disk_stats.return_value = {
            'total': total,
            'free_for_root': free_for_root,
            'free_for_nonroot': free_for_nonroot,
            'used': used,
            'avail': avail,
        }

        server = self.create("test_status_right_disk_stats", reserved_space=GB)
        expecteddir = server.backend._sharedir

        w = StorageStatus(server)
        html = w.renderSynchronously()

        self.failIf([True for args in mock_get_disk_stats.call_args_list if args != ((expecteddir, reserved_space), {})],
                    (mock_get_disk_stats.call_args_list, expecteddir, reserved_space))

        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Total disk space: 5.00 GB", s)
        self.failUnlessIn("Disk space used: - 1.00 GB", s)
        self.failUnlessIn("Disk space free (root): 4.00 GB", s)
        self.failUnlessIn("Disk space free (non-root): 3.00 GB", s)
        self.failUnlessIn("Reserved space: - 1.00 GB", s)
        self.failUnlessIn("Space Available to Tahoe: 2.00 GB", s)
        self.failUnlessEqual(server.get_available_space(), 2*GB)

    def test_readonly(self):
        server = self.create("test_readonly", readonly=True)

        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Accepting new shares: No", s)

    def test_reserved(self):
        server = self.create("test_reserved", reserved_space=10e6)

        w = StorageStatus(server)
        html = w.renderSynchronously()
        self.failUnlessIn("<h1>Storage Server Status</h1>", html)
        s = remove_tags(html)
        self.failUnlessIn("Reserved space: - 10.00 MB (10000000)", s)

    def test_util(self):
        w = StorageStatus(None)
        self.failUnlessEqual(w.render_space(None, None), "?")
        self.failUnlessEqual(w.render_space(None, 10e6), "10000000")
        self.failUnlessEqual(w.render_abbrev_space(None, None), "?")
        self.failUnlessEqual(w.render_abbrev_space(None, 10e6), "10.00 MB")
        self.failUnlessEqual(remove_prefix("foo.bar", "foo."), "bar")
        self.failUnlessEqual(remove_prefix("foo.bar", "baz."), None)


class WebStatusWithMockCloudBackend(WithMockCloudBackend, WebRenderingMixin, unittest.TestCase):
    def test_status(self):
        server = self.create("test_status")

        w = StorageStatus(server, "nickname")
        d = self.render1(w)
        def _check_html(html):
            self.failUnlessIn("<h1>Storage Server Status</h1>", html)
            s = remove_tags(html)
            self.failUnlessIn("Server Nickname: nickname", s)
            self.failUnlessIn("Server Nodeid: %s"  % base32.b2a(server.get_serverid()), s)
            self.failUnlessIn("Accepting new shares: Yes", s)
        d.addCallback(_check_html)
        d.addCallback(lambda ign: self.render_json(w))
        def _check_json(json):
            data = simplejson.loads(json)
            s = data["stats"]
            self.failUnlessEqual(s["storage_server.accepting_immutable_shares"], 1)
            self.failUnlessIn("bucket-counter", data)
            self.failUnlessIn("lease-checker", data)
        d.addCallback(_check_json)
        return d
