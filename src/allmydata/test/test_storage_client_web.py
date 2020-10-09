"""
Tests for storage_client that involve web code.
"""

from json import (
    dumps,
    loads,
)

from testtools.matchers import (
    Equals,
)

from fixtures import (
    TempDir,
)
from hyperlink import (
    URL,
)

from twisted.internet.defer import inlineCallbacks
from twisted.python.filepath import (
    FilePath,
)

from .common_web import (
    do_http,
)
from .common import (
    AsyncTestCase,
    UseNode,
    UseTestPlugins,
    SameProcessStreamEndpointAssigner,
)
from allmydata.webish import (
    WebishServer,
)

SOME_FURL = b"pb://abcde@nowhere/fake"


class StoragePluginWebPresence(AsyncTestCase):
    """
    Tests for the web resources ``IFoolscapStorageServer`` plugins may expose.
    """
    @inlineCallbacks
    def setUp(self):
        super(StoragePluginWebPresence, self).setUp()

        self.useFixture(UseTestPlugins())

        self.port_assigner = SameProcessStreamEndpointAssigner()
        self.port_assigner.setUp()
        self.addCleanup(self.port_assigner.tearDown)
        self.storage_plugin = b"tahoe-lafs-dummy-v1"

        from twisted.internet import reactor
        _, port_endpoint = self.port_assigner.assign(reactor)

        tempdir = TempDir()
        self.useFixture(tempdir)
        self.basedir = FilePath(tempdir.path)
        self.basedir.child(u"private").makedirs()
        self.node_fixture = self.useFixture(UseNode(
            plugin_config={
                b"web": b"1",
            },
            node_config={
                b"tub.location": b"127.0.0.1:1",
                b"web.port": port_endpoint,
            },
            storage_plugin=self.storage_plugin,
            basedir=self.basedir,
            introducer_furl=SOME_FURL,
        ))
        self.node = yield self.node_fixture.create_node()
        self.webish = self.node.getServiceNamed(WebishServer.name)
        self.node.startService()
        self.addCleanup(self.node.stopService)
        self.port = self.webish.getPortnum()

    @inlineCallbacks
    def test_plugin_resource_path(self):
        """
        The plugin's resource is published at */storage-plugins/<plugin name>*.
        """
        url = u"http://127.0.0.1:{port}/storage-plugins/{plugin_name}".format(
            port=self.port,
            plugin_name=self.storage_plugin,
        ).encode("utf-8")
        result = yield do_http(b"get", url)
        self.assertThat(result, Equals(dumps({b"web": b"1"})))

    @inlineCallbacks
    def test_plugin_resource_persistent_across_requests(self):
        """
        The plugin's resource is loaded and then saved and re-used for future
        requests.
        """
        url = URL(
            scheme=u"http",
            host=u"127.0.0.1",
            port=self.port,
            path=(
                u"storage-plugins",
                self.storage_plugin.decode("utf-8"),
                u"counter",
            ),
        ).to_text().encode("utf-8")
        values = {
            loads((yield do_http(b"get", url)))[u"value"],
            loads((yield do_http(b"get", url)))[u"value"],
        }
        self.assertThat(
            values,
            # If the counter manages to go up then the state stuck around.
            Equals({1, 2}),
        )
