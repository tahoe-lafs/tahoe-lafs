from __future__ import print_function

__all__ = [
    "SyncTestCase",
    "AsyncTestCase",
    "AsyncBrokenTestCase",
    "TrialTestCase",

    "flush_logged_errors",
    "skip",
    "skipIf",
]

import os, random, struct
import six
import tempfile
from tempfile import mktemp
from functools import partial
from unittest import case as _case
from socket import (
    AF_INET,
    SOCK_STREAM,
    SOMAXCONN,
    socket,
    error as socket_error,
)
from errno import (
    EADDRINUSE,
)

import attr

import treq

from zope.interface import implementer

from testtools import (
    TestCase,
    skip,
    skipIf,
)
from testtools.twistedsupport import (
    SynchronousDeferredRunTest,
    AsynchronousDeferredRunTest,
    AsynchronousDeferredRunTestForBrokenTwisted,
    flush_logged_errors,
)

from twisted.application import service
from twisted.plugin import IPlugin
from twisted.internet import defer
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.interfaces import IPullProducer
from twisted.python import failure
from twisted.python.filepath import FilePath
from twisted.web.error import Error as WebError
from twisted.internet.interfaces import (
    IStreamServerEndpointStringParser,
    IReactorSocket,
)
from twisted.internet.endpoints import AdoptedStreamServerEndpoint
from twisted.trial.unittest import TestCase as _TrialTestCase

from allmydata import uri
from allmydata.interfaces import IMutableFileNode, IImmutableFileNode,\
                                 NotEnoughSharesError, ICheckable, \
                                 IMutableUploadable, SDMF_VERSION, \
                                 MDMF_VERSION
from allmydata.check_results import CheckResults, CheckAndRepairResults, \
     DeepCheckResults, DeepCheckAndRepairResults
from allmydata.storage_client import StubServer
from allmydata.mutable.layout import unpack_header
from allmydata.mutable.publish import MutableData
from allmydata.storage.mutable import MutableShareFile
from allmydata.util import hashutil, log, iputil
from allmydata.util.assertutil import precondition
from allmydata.util.consumer import download_to_data
import allmydata.test.common_util as testutil
from allmydata.immutable.upload import Uploader
from allmydata.client import (
    config_from_string,
    create_client_from_config,
)

from ..crypto import (
    ed25519,
)
from .eliotutil import (
    EliotLoggedRunTest,
)
from .common_util import ShouldFailMixin  # noqa: F401


TEST_RSA_KEY_SIZE = 522

EMPTY_CLIENT_CONFIG = config_from_string(
    "/dev/null",
    "tub.port",
    ""
)


@attr.s
class MemoryIntroducerClient(object):
    """
    A model-only (no behavior) stand-in for ``IntroducerClient``.
    """
    tub = attr.ib()
    introducer_furl = attr.ib()
    nickname = attr.ib()
    my_version = attr.ib()
    oldest_supported = attr.ib()
    app_versions = attr.ib()
    sequencer = attr.ib()
    cache_filepath = attr.ib()

    subscribed_to = attr.ib(default=attr.Factory(list))
    published_announcements = attr.ib(default=attr.Factory(list))


    def setServiceParent(self, parent):
        pass


    def subscribe_to(self, service_name, cb, *args, **kwargs):
        self.subscribed_to.append(Subscription(service_name, cb, args, kwargs))


    def publish(self, service_name, ann, signing_key):
        self.published_announcements.append(Announcement(
            service_name,
            ann,
            ed25519.string_from_signing_key(signing_key),
        ))


@attr.s
class Subscription(object):
    """
    A model of an introducer subscription.
    """
    service_name = attr.ib()
    cb = attr.ib()
    args = attr.ib()
    kwargs = attr.ib()


@attr.s
class Announcement(object):
    """
    A model of an introducer announcement.
    """
    service_name = attr.ib()
    ann = attr.ib()
    signing_key_bytes = attr.ib(type=bytes)

    @property
    def signing_key(self):
        return ed25519.signing_keypair_from_string(self.signing_key_bytes)[0]


def get_published_announcements(client):
    """
    Get a flattened list of all announcements sent using all introducer
    clients.
    """
    return list(
        announcement
        for introducer_client
        in client.introducer_clients
        for announcement
        in introducer_client.published_announcements
    )


class UseTestPlugins(object):
    """
    A fixture which enables loading Twisted plugins from the Tahoe-LAFS test
    suite.
    """
    def setUp(self):
        """
        Add the testing package ``plugins`` directory to the ``twisted.plugins``
        aggregate package.
        """
        import twisted.plugins
        testplugins = FilePath(__file__).sibling("plugins")
        twisted.plugins.__path__.insert(0, testplugins.path)

    def cleanUp(self):
        """
        Remove the testing package ``plugins`` directory from the
        ``twisted.plugins`` aggregate package.
        """
        import twisted.plugins
        testplugins = FilePath(__file__).sibling("plugins")
        twisted.plugins.__path__.remove(testplugins.path)

    def getDetails(self):
        return {}


@attr.s
class UseNode(object):
    """
    A fixture which creates a client node.

    :ivar dict[bytes, bytes] plugin_config: Configuration items to put in the
        node's configuration.

    :ivar bytes storage_plugin: The name of a storage plugin to enable.

    :ivar FilePath basedir: The base directory of the node.

    :ivar bytes introducer_furl: The introducer furl with which to
        configure the client.

    :ivar dict[bytes, bytes] node_config: Configuration items for the *node*
        section of the configuration.

    :ivar _Config config: The complete resulting configuration.
    """
    plugin_config = attr.ib()
    storage_plugin = attr.ib()
    basedir = attr.ib()
    introducer_furl = attr.ib()
    node_config = attr.ib(default=attr.Factory(dict))

    config = attr.ib(default=None)

    def setUp(self):
        def format_config_items(config):
            return b"\n".join(
                b" = ".join((key, value))
                for (key, value)
                in config.items()
            )

        if self.plugin_config is None:
            plugin_config_section = b""
        else:
            plugin_config_section = b"""
[storageclient.plugins.{storage_plugin}]
{config}
""".format(
    storage_plugin=self.storage_plugin,
    config=format_config_items(self.plugin_config),
)

        self.config = config_from_string(
            self.basedir.asTextMode().path,
            "tub.port",
"""
[node]
{node_config}

[client]
introducer.furl = {furl}
storage.plugins = {storage_plugin}
{plugin_config_section}
""".format(
    furl=self.introducer_furl,
    storage_plugin=self.storage_plugin,
    node_config=format_config_items(self.node_config),
    plugin_config_section=plugin_config_section,
)
        )

    def create_node(self):
        return create_client_from_config(
            self.config,
            _introducer_factory=MemoryIntroducerClient,
        )

    def cleanUp(self):
        pass


    def getDetails(self):
        return {}



@implementer(IPlugin, IStreamServerEndpointStringParser)
class AdoptedServerPort(object):
    """
    Parse an ``adopt-socket:<fd>`` endpoint description by adopting ``fd`` as
    a listening TCP port.
    """
    prefix = "adopt-socket"

    def parseStreamServer(self, reactor, fd):
        log.msg("Adopting {}".format(fd))
        # AdoptedStreamServerEndpoint wants to own the file descriptor.  It
        # will duplicate it and then close the one we pass in.  This means it
        # is really only possible to adopt a particular file descriptor once.
        #
        # This wouldn't matter except one of the tests wants to stop one of
        # the nodes and start it up again.  This results in exactly an attempt
        # to adopt a particular file descriptor twice.
        #
        # So we'll dup it ourselves.  AdoptedStreamServerEndpoint can do
        # whatever it wants to the result - the original will still be valid
        # and reusable.
        return AdoptedStreamServerEndpoint(reactor, os.dup(int(fd)), AF_INET)


def really_bind(s, addr):
    # Arbitrarily decide we'll try 100 times.  We don't want to try forever in
    # case this is a persistent problem.  Trying is cheap, though, so we may
    # as well try a lot.  Hopefully the OS isn't so bad at allocating a port
    # for us that it takes more than 2 iterations.
    for i in range(100):
        try:
            s.bind(addr)
        except socket_error as e:
            if e.errno == EADDRINUSE:
                continue
            raise
        else:
            return
    raise Exception("Many bind attempts failed with EADDRINUSE")


class SameProcessStreamEndpointAssigner(object):
    """
    A fixture which can assign streaming server endpoints for use *in this
    process only*.

    An effort is made to avoid address collisions for this port but the logic
    for doing so is platform-dependent (sorry, Windows).

    This is more reliable than trying to listen on a hard-coded non-zero port
    number.  It is at least as reliable as trying to listen on port number
    zero on Windows and more reliable than doing that on other platforms.
    """
    def setUp(self):
        self._cleanups = []
        # Make sure the `adopt-socket` endpoint is recognized.  We do this
        # instead of providing a dropin because we don't want to make this
        # endpoint available to random other applications.
        f = UseTestPlugins()
        f.setUp()
        self._cleanups.append(f.cleanUp)

    def tearDown(self):
        for c in self._cleanups:
            c()

    def assign(self, reactor):
        """
        Make a new streaming server endpoint and return its string description.

        This is intended to help write config files that will then be read and
        used in this process.

        :param reactor: The reactor which will be used to listen with the
            resulting endpoint.  If it provides ``IReactorSocket`` then
            resulting reliability will be extremely high.  If it doesn't,
            resulting reliability will be pretty alright.

        :return: A two-tuple of (location hint, port endpoint description) as
            strings.
        """
        if IReactorSocket.providedBy(reactor):
            # On this platform, we can reliable pre-allocate a listening port.
            # Once it is bound we know it will not fail later with EADDRINUSE.
            s = socket(AF_INET, SOCK_STREAM)
            # We need to keep ``s`` alive as long as the file descriptor we put in
            # this string might still be used.  We could dup() the descriptor
            # instead but then we've only inverted the cleanup problem: gone from
            # don't-close-too-soon to close-just-late-enough.  So we'll leave
            # ``s`` alive and use it as the cleanup mechanism.
            self._cleanups.append(s.close)
            s.setblocking(False)
            really_bind(s, ("127.0.0.1", 0))
            s.listen(SOMAXCONN)
            host, port = s.getsockname()
            location_hint = "tcp:%s:%d" % (host, port)
            port_endpoint = "adopt-socket:fd=%d" % (s.fileno(),)
        else:
            # On other platforms, we blindly guess and hope we get lucky.
            portnum = iputil.allocate_tcp_port()
            location_hint = "tcp:127.0.0.1:%d" % (portnum,)
            port_endpoint = "tcp:%d:interface=127.0.0.1" % (portnum,)

        return location_hint, port_endpoint

@implementer(IPullProducer)
class DummyProducer(object):
    def resumeProducing(self):
        pass

@implementer(IImmutableFileNode)
class FakeCHKFileNode(object):
    """I provide IImmutableFileNode, but all of my data is stored in a
    class-level dictionary."""

    def __init__(self, filecap, all_contents):
        precondition(isinstance(filecap, (uri.CHKFileURI, uri.LiteralFileURI)), filecap)
        self.all_contents = all_contents
        self.my_uri = filecap
        self.storage_index = self.my_uri.get_storage_index()

    def get_uri(self):
        return self.my_uri.to_string()
    def get_write_uri(self):
        return None
    def get_readonly_uri(self):
        return self.my_uri.to_string()
    def get_cap(self):
        return self.my_uri
    def get_verify_cap(self):
        return self.my_uri.get_verify_cap()
    def get_repair_cap(self):
        return self.my_uri.get_verify_cap()
    def get_storage_index(self):
        return self.storage_index

    def check(self, monitor, verify=False, add_lease=False):
        s = StubServer("\x00"*20)
        r = CheckResults(self.my_uri, self.storage_index,
                         healthy=True, recoverable=True,
                         count_happiness=10,
                         count_shares_needed=3,
                         count_shares_expected=10,
                         count_shares_good=10,
                         count_good_share_hosts=10,
                         count_recoverable_versions=1,
                         count_unrecoverable_versions=0,
                         servers_responding=[s],
                         sharemap={1: [s]},
                         count_wrong_shares=0,
                         list_corrupt_shares=[],
                         count_corrupt_shares=0,
                         list_incompatible_shares=[],
                         count_incompatible_shares=0,
                         summary="",
                         report=[],
                         share_problems=[],
                         servermap=None)
        return defer.succeed(r)
    def check_and_repair(self, monitor, verify=False, add_lease=False):
        d = self.check(verify)
        def _got(cr):
            r = CheckAndRepairResults(self.storage_index)
            r.pre_repair_results = r.post_repair_results = cr
            return r
        d.addCallback(_got)
        return d

    def is_mutable(self):
        return False
    def is_readonly(self):
        return True
    def is_unknown(self):
        return False
    def is_allowed_in_immutable_directory(self):
        return True
    def raise_error(self):
        pass

    def get_size(self):
        if isinstance(self.my_uri, uri.LiteralFileURI):
            return self.my_uri.get_size()
        try:
            data = self.all_contents[self.my_uri.to_string()]
        except KeyError as le:
            raise NotEnoughSharesError(le, 0, 3)
        return len(data)
    def get_current_size(self):
        return defer.succeed(self.get_size())

    def read(self, consumer, offset=0, size=None):
        # we don't bother to call registerProducer/unregisterProducer,
        # because it's a hassle to write a dummy Producer that does the right
        # thing (we have to make sure that DummyProducer.resumeProducing
        # writes the data into the consumer immediately, otherwise it will
        # loop forever).

        d = defer.succeed(None)
        d.addCallback(self._read, consumer, offset, size)
        return d

    def _read(self, ignored, consumer, offset, size):
        if isinstance(self.my_uri, uri.LiteralFileURI):
            data = self.my_uri.data
        else:
            if self.my_uri.to_string() not in self.all_contents:
                raise NotEnoughSharesError(None, 0, 3)
            data = self.all_contents[self.my_uri.to_string()]
        start = offset
        if size is not None:
            end = offset + size
        else:
            end = len(data)
        consumer.write(data[start:end])
        return consumer


    def get_best_readable_version(self):
        return defer.succeed(self)


    def download_to_data(self, progress=None):
        return download_to_data(self, progress=progress)


    download_best_version = download_to_data


    def get_size_of_best_version(self):
        return defer.succeed(self.get_size)


def make_chk_file_cap(size):
    return uri.CHKFileURI(key=os.urandom(16),
                          uri_extension_hash=os.urandom(32),
                          needed_shares=3,
                          total_shares=10,
                          size=size)
def make_chk_file_uri(size):
    return make_chk_file_cap(size).to_string()

def create_chk_filenode(contents, all_contents):
    filecap = make_chk_file_cap(len(contents))
    n = FakeCHKFileNode(filecap, all_contents)
    all_contents[filecap.to_string()] = contents
    return n


@implementer(IMutableFileNode, ICheckable)
class FakeMutableFileNode(object):
    """I provide IMutableFileNode, but all of my data is stored in a
    class-level dictionary."""

    MUTABLE_SIZELIMIT = 10000

    def __init__(self, storage_broker, secret_holder,
                 default_encoding_parameters, history, all_contents):
        self.all_contents = all_contents
        self.file_types = {} # storage index => MDMF_VERSION or SDMF_VERSION
        self.init_from_cap(make_mutable_file_cap())
        self._k = default_encoding_parameters['k']
        self._segsize = default_encoding_parameters['max_segment_size']
    def create(self, contents, key_generator=None, keysize=None,
               version=SDMF_VERSION):
        if version == MDMF_VERSION and \
            isinstance(self.my_uri, (uri.ReadonlySSKFileURI,
                                 uri.WriteableSSKFileURI)):
            self.init_from_cap(make_mdmf_mutable_file_cap())
        self.file_types[self.storage_index] = version
        initial_contents = self._get_initial_contents(contents)
        data = initial_contents.read(initial_contents.get_size())
        data = "".join(data)
        self.all_contents[self.storage_index] = data
        return defer.succeed(self)
    def _get_initial_contents(self, contents):
        if contents is None:
            return MutableData("")

        if IMutableUploadable.providedBy(contents):
            return contents

        assert callable(contents), "%s should be callable, not %s" % \
               (contents, type(contents))
        return contents(self)
    def init_from_cap(self, filecap):
        assert isinstance(filecap, (uri.WriteableSSKFileURI,
                                    uri.ReadonlySSKFileURI,
                                    uri.WriteableMDMFFileURI,
                                    uri.ReadonlyMDMFFileURI))
        self.my_uri = filecap
        self.storage_index = self.my_uri.get_storage_index()
        if isinstance(filecap, (uri.WriteableMDMFFileURI,
                                uri.ReadonlyMDMFFileURI)):
            self.file_types[self.storage_index] = MDMF_VERSION

        else:
            self.file_types[self.storage_index] = SDMF_VERSION

        return self
    def get_cap(self):
        return self.my_uri
    def get_readcap(self):
        return self.my_uri.get_readonly()
    def get_uri(self):
        return self.my_uri.to_string()
    def get_write_uri(self):
        if self.is_readonly():
            return None
        return self.my_uri.to_string()
    def get_readonly(self):
        return self.my_uri.get_readonly()
    def get_readonly_uri(self):
        return self.my_uri.get_readonly().to_string()
    def get_verify_cap(self):
        return self.my_uri.get_verify_cap()
    def get_repair_cap(self):
        if self.my_uri.is_readonly():
            return None
        return self.my_uri
    def is_readonly(self):
        return self.my_uri.is_readonly()
    def is_mutable(self):
        return self.my_uri.is_mutable()
    def is_unknown(self):
        return False
    def is_allowed_in_immutable_directory(self):
        return not self.my_uri.is_mutable()
    def raise_error(self):
        pass
    def get_writekey(self):
        return "\x00"*16
    def get_size(self):
        return len(self.all_contents[self.storage_index])
    def get_current_size(self):
        return self.get_size_of_best_version()
    def get_size_of_best_version(self):
        return defer.succeed(len(self.all_contents[self.storage_index]))

    def get_storage_index(self):
        return self.storage_index

    def get_servermap(self, mode):
        return defer.succeed(None)

    def get_version(self):
        assert self.storage_index in self.file_types
        return self.file_types[self.storage_index]

    def check(self, monitor, verify=False, add_lease=False):
        s = StubServer("\x00"*20)
        r = CheckResults(self.my_uri, self.storage_index,
                         healthy=True, recoverable=True,
                         count_happiness=10,
                         count_shares_needed=3,
                         count_shares_expected=10,
                         count_shares_good=10,
                         count_good_share_hosts=10,
                         count_recoverable_versions=1,
                         count_unrecoverable_versions=0,
                         servers_responding=[s],
                         sharemap={"seq1-abcd-sh0": [s]},
                         count_wrong_shares=0,
                         list_corrupt_shares=[],
                         count_corrupt_shares=0,
                         list_incompatible_shares=[],
                         count_incompatible_shares=0,
                         summary="",
                         report=[],
                         share_problems=[],
                         servermap=None)
        return defer.succeed(r)

    def check_and_repair(self, monitor, verify=False, add_lease=False):
        d = self.check(verify)
        def _got(cr):
            r = CheckAndRepairResults(self.storage_index)
            r.pre_repair_results = r.post_repair_results = cr
            return r
        d.addCallback(_got)
        return d

    def deep_check(self, verify=False, add_lease=False):
        d = self.check(verify)
        def _done(r):
            dr = DeepCheckResults(self.storage_index)
            dr.add_check(r, [])
            return dr
        d.addCallback(_done)
        return d

    def deep_check_and_repair(self, verify=False, add_lease=False):
        d = self.check_and_repair(verify)
        def _done(r):
            dr = DeepCheckAndRepairResults(self.storage_index)
            dr.add_check(r, [])
            return dr
        d.addCallback(_done)
        return d

    def download_best_version(self, progress=None):
        return defer.succeed(self._download_best_version(progress=progress))


    def _download_best_version(self, ignored=None, progress=None):
        if isinstance(self.my_uri, uri.LiteralFileURI):
            return self.my_uri.data
        if self.storage_index not in self.all_contents:
            raise NotEnoughSharesError(None, 0, 3)
        return self.all_contents[self.storage_index]


    def overwrite(self, new_contents):
        assert not self.is_readonly()
        new_data = new_contents.read(new_contents.get_size())
        new_data = "".join(new_data)
        self.all_contents[self.storage_index] = new_data
        return defer.succeed(None)
    def modify(self, modifier):
        # this does not implement FileTooLargeError, but the real one does
        return defer.maybeDeferred(self._modify, modifier)
    def _modify(self, modifier):
        assert not self.is_readonly()
        old_contents = self.all_contents[self.storage_index]
        new_data = modifier(old_contents, None, True)
        self.all_contents[self.storage_index] = new_data
        return None

    # As actually implemented, MutableFilenode and MutableFileVersion
    # are distinct. However, nothing in the webapi uses (yet) that
    # distinction -- it just uses the unified download interface
    # provided by get_best_readable_version and read. When we start
    # doing cooler things like LDMF, we will want to revise this code to
    # be less simplistic.
    def get_best_readable_version(self):
        return defer.succeed(self)


    def get_best_mutable_version(self):
        return defer.succeed(self)

    # Ditto for this, which is an implementation of IWriteable.
    # XXX: Declare that the same is implemented.
    def update(self, data, offset):
        assert not self.is_readonly()
        def modifier(old, servermap, first_time):
            new = old[:offset] + "".join(data.read(data.get_size()))
            new += old[len(new):]
            return new
        return self.modify(modifier)


    def read(self, consumer, offset=0, size=None):
        data = self._download_best_version()
        if size:
            data = data[offset:offset+size]
        consumer.write(data)
        return defer.succeed(consumer)


def make_mutable_file_cap():
    return uri.WriteableSSKFileURI(writekey=os.urandom(16),
                                   fingerprint=os.urandom(32))

def make_mdmf_mutable_file_cap():
    return uri.WriteableMDMFFileURI(writekey=os.urandom(16),
                                   fingerprint=os.urandom(32))

def make_mutable_file_uri(mdmf=False):
    if mdmf:
        uri = make_mdmf_mutable_file_cap()
    else:
        uri = make_mutable_file_cap()

    return uri.to_string()

def make_verifier_uri():
    return uri.SSKVerifierURI(storage_index=os.urandom(16),
                              fingerprint=os.urandom(32)).to_string()

def create_mutable_filenode(contents, mdmf=False, all_contents=None):
    # XXX: All of these arguments are kind of stupid.
    if mdmf:
        cap = make_mdmf_mutable_file_cap()
    else:
        cap = make_mutable_file_cap()

    encoding_params = {}
    encoding_params['k'] = 3
    encoding_params['max_segment_size'] = 128*1024

    filenode = FakeMutableFileNode(None, None, encoding_params, None,
                                   all_contents)
    filenode.init_from_cap(cap)
    if mdmf:
        filenode.create(MutableData(contents), version=MDMF_VERSION)
    else:
        filenode.create(MutableData(contents), version=SDMF_VERSION)
    return filenode


class LoggingServiceParent(service.MultiService):
    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)


TEST_DATA=b"\x02"*(Uploader.URI_LIT_SIZE_THRESHOLD+1)


class WebErrorMixin(object):
    def explain_web_error(self, f):
        # an error on the server side causes the client-side getPage() to
        # return a failure(t.web.error.Error), and its str() doesn't show the
        # response body, which is where the useful information lives. Attach
        # this method as an errback handler, and it will reveal the hidden
        # message.
        f.trap(WebError)
        print("Web Error:", f.value, ":", f.value.response)
        return f

    def _shouldHTTPError(self, res, which, validator):
        if isinstance(res, failure.Failure):
            res.trap(WebError)
            return validator(res)
        else:
            self.fail("%s was supposed to Error, not get '%s'" % (which, res))

    def shouldHTTPError(self, which,
                        code=None, substring=None, response_substring=None,
                        callable=None, *args, **kwargs):
        # returns a Deferred with the response body
        assert substring is None or isinstance(substring, str)
        assert callable
        def _validate(f):
            if code is not None:
                self.failUnlessEqual(f.value.status, str(code), which)
            if substring:
                code_string = str(f)
                self.failUnless(substring in code_string,
                                "%s: substring '%s' not in '%s'"
                                % (which, substring, code_string))
            response_body = f.value.response
            if response_substring:
                self.failUnless(response_substring in response_body,
                                "%s: response substring '%s' not in '%s'"
                                % (which, response_substring, response_body))
            return response_body
        d = defer.maybeDeferred(callable, *args, **kwargs)
        d.addBoth(self._shouldHTTPError, which, _validate)
        return d

    @inlineCallbacks
    def assertHTTPError(self, url, code, response_substring,
                        method="get", persistent=False,
                        **args):
        response = yield treq.request(method, url, persistent=persistent,
                                      **args)
        body = yield response.content()
        self.assertEquals(response.code, code)
        if response_substring is not None:
            self.assertIn(response_substring, body)
        returnValue(body)

class ErrorMixin(WebErrorMixin):
    def explain_error(self, f):
        if f.check(defer.FirstError):
            print("First Error:", f.value.subFailure)
        return f

def corrupt_field(data, offset, size, debug=False):
    if random.random() < 0.5:
        newdata = testutil.flip_one_bit(data, offset, size)
        if debug:
            log.msg("testing: corrupting offset %d, size %d flipping one bit orig: %r, newdata: %r" % (offset, size, data[offset:offset+size], newdata[offset:offset+size]))
        return newdata
    else:
        newval = testutil.insecurerandstr(size)
        if debug:
            log.msg("testing: corrupting offset %d, size %d randomizing field, orig: %r, newval: %r" % (offset, size, data[offset:offset+size], newval))
        return data[:offset]+newval+data[offset+size:]

def _corrupt_nothing(data, debug=False):
    """Leave the data pristine. """
    return data

def _corrupt_file_version_number(data, debug=False):
    """Scramble the file data -- the share file version number have one bit
    flipped or else will be changed to a random value."""
    return corrupt_field(data, 0x00, 4)

def _corrupt_size_of_file_data(data, debug=False):
    """Scramble the file data -- the field showing the size of the share data
    within the file will be set to one smaller."""
    return corrupt_field(data, 0x04, 4)

def _corrupt_sharedata_version_number(data, debug=False):
    """Scramble the file data -- the share data version number will have one
    bit flipped or else will be changed to a random value, but not 1 or 2."""
    return corrupt_field(data, 0x0c, 4)
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    newsharevernum = sharevernum
    while newsharevernum in (1, 2):
        newsharevernum = random.randrange(0, 2**32)
    newsharevernumbytes = struct.pack(">L", newsharevernum)
    return data[:0x0c] + newsharevernumbytes + data[0x0c+4:]

def _corrupt_sharedata_version_number_to_plausible_version(data, debug=False):
    """Scramble the file data -- the share data version number will be
    changed to 2 if it is 1 or else to 1 if it is 2."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        newsharevernum = 2
    else:
        newsharevernum = 1
    newsharevernumbytes = struct.pack(">L", newsharevernum)
    return data[:0x0c] + newsharevernumbytes + data[0x0c+4:]

def _corrupt_segment_size(data, debug=False):
    """Scramble the file data -- the field showing the size of the segment
    will have one bit flipped or else be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x04, 4, debug=False)
    else:
        return corrupt_field(data, 0x0c+0x04, 8, debug=False)

def _corrupt_size_of_sharedata(data, debug=False):
    """Scramble the file data -- the field showing the size of the data
    within the share data will have one bit flipped or else will be changed
    to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x08, 4)
    else:
        return corrupt_field(data, 0x0c+0x0c, 8)

def _corrupt_offset_of_sharedata(data, debug=False):
    """Scramble the file data -- the field showing the offset of the data
    within the share data will have one bit flipped or else be changed to a
    random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x0c, 4)
    else:
        return corrupt_field(data, 0x0c+0x14, 8)

def _corrupt_offset_of_ciphertext_hash_tree(data, debug=False):
    """Scramble the file data -- the field showing the offset of the
    ciphertext hash tree within the share data will have one bit flipped or
    else be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x14, 4, debug=False)
    else:
        return corrupt_field(data, 0x0c+0x24, 8, debug=False)

def _corrupt_offset_of_block_hashes(data, debug=False):
    """Scramble the file data -- the field showing the offset of the block
    hash tree within the share data will have one bit flipped or else will be
    changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x18, 4)
    else:
        return corrupt_field(data, 0x0c+0x2c, 8)

def _corrupt_offset_of_block_hashes_to_truncate_crypttext_hashes(data, debug=False):
    """Scramble the file data -- the field showing the offset of the block
    hash tree within the share data will have a multiple of hash size
    subtracted from it, thus causing the downloader to download an incomplete
    crypttext hash tree."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        curval = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
        newval = random.randrange(0, max(1, (curval//hashutil.CRYPTO_VAL_SIZE)//2))*hashutil.CRYPTO_VAL_SIZE
        newvalstr = struct.pack(">L", newval)
        return data[:0x0c+0x18]+newvalstr+data[0x0c+0x18+4:]
    else:
        curval = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]
        newval = random.randrange(0, max(1, (curval//hashutil.CRYPTO_VAL_SIZE)//2))*hashutil.CRYPTO_VAL_SIZE
        newvalstr = struct.pack(">Q", newval)
        return data[:0x0c+0x2c]+newvalstr+data[0x0c+0x2c+8:]

def _corrupt_offset_of_share_hashes(data, debug=False):
    """Scramble the file data -- the field showing the offset of the share
    hash tree within the share data will have one bit flipped or else will be
    changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x1c, 4)
    else:
        return corrupt_field(data, 0x0c+0x34, 8)

def _corrupt_offset_of_uri_extension(data, debug=False):
    """Scramble the file data -- the field showing the offset of the uri
    extension will have one bit flipped or else will be changed to a random
    value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        return corrupt_field(data, 0x0c+0x20, 4)
    else:
        return corrupt_field(data, 0x0c+0x3c, 8)

def _corrupt_offset_of_uri_extension_to_force_short_read(data, debug=False):
    """Scramble the file data -- the field showing the offset of the uri
    extension will be set to the size of the file minus 3. This means when
    the client tries to read the length field from that location it will get
    a short read -- the result string will be only 3 bytes long, not the 4 or
    8 bytes necessary to do a successful struct.unpack."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    # The "-0x0c" in here is to skip the server-side header in the share
    # file, which the client doesn't see when seeking and reading.
    if sharevernum == 1:
        if debug:
            log.msg("testing: corrupting offset %d, size %d, changing %d to %d (len(data) == %d)" % (0x2c, 4, struct.unpack(">L", data[0x2c:0x2c+4])[0], len(data)-0x0c-3, len(data)))
        return data[:0x2c] + struct.pack(">L", len(data)-0x0c-3) + data[0x2c+4:]
    else:
        if debug:
            log.msg("testing: corrupting offset %d, size %d, changing %d to %d (len(data) == %d)" % (0x48, 8, struct.unpack(">Q", data[0x48:0x48+8])[0], len(data)-0x0c-3, len(data)))
        return data[:0x48] + struct.pack(">Q", len(data)-0x0c-3) + data[0x48+8:]

def _corrupt_mutable_share_data(data, debug=False):
    prefix = data[:32]
    assert prefix == MutableShareFile.MAGIC, "This function is designed to corrupt mutable shares of v1, and the magic number doesn't look right: %r vs %r" % (prefix, MutableShareFile.MAGIC)
    data_offset = MutableShareFile.DATA_OFFSET
    sharetype = data[data_offset:data_offset+1]
    assert sharetype == "\x00", "non-SDMF mutable shares not supported"
    (version, ig_seqnum, ig_roothash, ig_IV, ig_k, ig_N, ig_segsize,
     ig_datalen, offsets) = unpack_header(data[data_offset:])
    assert version == 0, "this function only handles v0 SDMF files"
    start = data_offset + offsets["share_data"]
    length = data_offset + offsets["enc_privkey"] - start
    return corrupt_field(data, start, length)

def _corrupt_share_data(data, debug=False):
    """Scramble the file data -- the field containing the share data itself
    will have one bit flipped or else will be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways, not v%d." % sharevernum
    if sharevernum == 1:
        sharedatasize = struct.unpack(">L", data[0x0c+0x08:0x0c+0x08+4])[0]

        return corrupt_field(data, 0x0c+0x24, sharedatasize)
    else:
        sharedatasize = struct.unpack(">Q", data[0x0c+0x08:0x0c+0x0c+8])[0]

        return corrupt_field(data, 0x0c+0x44, sharedatasize)

def _corrupt_share_data_last_byte(data, debug=False):
    """Scramble the file data -- flip all bits of the last byte."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways, not v%d." % sharevernum
    if sharevernum == 1:
        sharedatasize = struct.unpack(">L", data[0x0c+0x08:0x0c+0x08+4])[0]
        offset = 0x0c+0x24+sharedatasize-1
    else:
        sharedatasize = struct.unpack(">Q", data[0x0c+0x08:0x0c+0x0c+8])[0]
        offset = 0x0c+0x44+sharedatasize-1

    newdata = data[:offset] + chr(ord(data[offset])^0xFF) + data[offset+1:]
    if debug:
        log.msg("testing: flipping all bits of byte at offset %d: %r, newdata: %r" % (offset, data[offset], newdata[offset]))
    return newdata

def _corrupt_crypttext_hash_tree(data, debug=False):
    """Scramble the file data -- the field containing the crypttext hash tree
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        crypttexthashtreeoffset = struct.unpack(">L", data[0x0c+0x14:0x0c+0x14+4])[0]
        blockhashesoffset = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
    else:
        crypttexthashtreeoffset = struct.unpack(">Q", data[0x0c+0x24:0x0c+0x24+8])[0]
        blockhashesoffset = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]

    return corrupt_field(data, 0x0c+crypttexthashtreeoffset, blockhashesoffset-crypttexthashtreeoffset, debug=debug)

def _corrupt_crypttext_hash_tree_byte_x221(data, debug=False):
    """Scramble the file data -- the byte at offset 0x221 will have its 7th
    (b1) bit flipped.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if debug:
        log.msg("original data: %r" % (data,))
    return data[:0x0c+0x221] + chr(ord(data[0x0c+0x221])^0x02) + data[0x0c+0x2210+1:]

def _corrupt_block_hashes(data, debug=False):
    """Scramble the file data -- the field containing the block hash tree
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        blockhashesoffset = struct.unpack(">L", data[0x0c+0x18:0x0c+0x18+4])[0]
        sharehashesoffset = struct.unpack(">L", data[0x0c+0x1c:0x0c+0x1c+4])[0]
    else:
        blockhashesoffset = struct.unpack(">Q", data[0x0c+0x2c:0x0c+0x2c+8])[0]
        sharehashesoffset = struct.unpack(">Q", data[0x0c+0x34:0x0c+0x34+8])[0]

    return corrupt_field(data, 0x0c+blockhashesoffset, sharehashesoffset-blockhashesoffset)

def _corrupt_share_hashes(data, debug=False):
    """Scramble the file data -- the field containing the share hash chain
    will have one bit flipped or else will be changed to a random value.
    """
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        sharehashesoffset = struct.unpack(">L", data[0x0c+0x1c:0x0c+0x1c+4])[0]
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
    else:
        sharehashesoffset = struct.unpack(">Q", data[0x0c+0x34:0x0c+0x34+8])[0]
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]

    return corrupt_field(data, 0x0c+sharehashesoffset, uriextoffset-sharehashesoffset)

def _corrupt_length_of_uri_extension(data, debug=False):
    """Scramble the file data -- the field showing the length of the uri
    extension will have one bit flipped or else will be changed to a random
    value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
        return corrupt_field(data, uriextoffset, 4)
    else:
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]
        return corrupt_field(data, 0x0c+uriextoffset, 8)

def _corrupt_uri_extension(data, debug=False):
    """Scramble the file data -- the field containing the uri extension will
    have one bit flipped or else will be changed to a random value."""
    sharevernum = struct.unpack(">L", data[0x0c:0x0c+4])[0]
    assert sharevernum in (1, 2), "This test is designed to corrupt immutable shares of v1 or v2 in specific ways."
    if sharevernum == 1:
        uriextoffset = struct.unpack(">L", data[0x0c+0x20:0x0c+0x20+4])[0]
        uriextlen = struct.unpack(">L", data[0x0c+uriextoffset:0x0c+uriextoffset+4])[0]
    else:
        uriextoffset = struct.unpack(">Q", data[0x0c+0x3c:0x0c+0x3c+8])[0]
        uriextlen = struct.unpack(">Q", data[0x0c+uriextoffset:0x0c+uriextoffset+8])[0]

    return corrupt_field(data, 0x0c+uriextoffset, uriextlen)


class _TestCaseMixin(object):
    """
    A mixin for ``TestCase`` which collects helpful behaviors for subclasses.

    Those behaviors are:

    * All of the features of testtools TestCase.
    * Each test method will be run in a unique Eliot action context which
      identifies the test and collects all Eliot log messages emitted by that
      test (including setUp and tearDown messages).
    * trial-compatible mktemp method
    * unittest2-compatible assertRaises helper
    * Automatic cleanup of tempfile.tempdir mutation (pervasive through the
      Tahoe-LAFS test suite).
    """
    def setUp(self):
        # Restore the original temporary directory.  Node ``init_tempdir``
        # mangles it and many tests manage to get that method called.
        self.addCleanup(
            partial(setattr, tempfile, "tempdir", tempfile.tempdir),
        )
        return super(_TestCaseMixin, self).setUp()

    class _DummyCase(_case.TestCase):
        def dummy(self):
            pass
    _dummyCase = _DummyCase("dummy")

    def mktemp(self):
        return mktemp()

    def assertRaises(self, *a, **kw):
        return self._dummyCase.assertRaises(*a, **kw)


class SyncTestCase(_TestCaseMixin, TestCase):
    """
    A ``TestCase`` which can run tests that may return an already-fired
    ``Deferred``.
    """
    run_tests_with = EliotLoggedRunTest.make_factory(
        SynchronousDeferredRunTest,
    )


class AsyncTestCase(_TestCaseMixin, TestCase):
    """
    A ``TestCase`` which can run tests that may return a Deferred that will
    only fire if the global reactor is running.
    """
    run_tests_with = EliotLoggedRunTest.make_factory(
        AsynchronousDeferredRunTest.make_factory(timeout=60.0),
    )


class AsyncBrokenTestCase(_TestCaseMixin, TestCase):
    """
    A ``TestCase`` like ``AsyncTestCase`` but which spins the reactor a little
    longer than apparently necessary to clean out lingering unaccounted for
    event sources.

    Tests which require this behavior are broken and should be fixed so they
    pass with ``AsyncTestCase``.
    """
    run_tests_with = EliotLoggedRunTest.make_factory(
        AsynchronousDeferredRunTestForBrokenTwisted.make_factory(timeout=60.0),
    )


class TrialTestCase(_TrialTestCase):
    """
    A twisted.trial.unittest.TestCaes with Tahoe required fixes
    applied. Currently these are:

      - ensure that .fail() passes a bytes msg on Python2
    """

    def fail(self, msg):
        """
        Ensure our msg is a native string on Python2. If it was Unicode,
        we encode it as utf8 and hope for the best. On Python3 we take
        no action.

        This is necessary because Twisted passes the 'msg' argument
        along to the constructor of an exception; on Python2,
        Exception will accept a `unicode` instance but will fail if
        you try to turn that Exception instance into a string.
        """

        if six.PY2:
            if isinstance(msg, six.text_type):
                return super(TrialTestCase, self).fail(msg.encode("utf8"))
        return super(TrialTestCase, self).fail(msg)
