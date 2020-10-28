import json
from os.path import join

from bs4 import BeautifulSoup

from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet import defer

from foolscap.api import (
    fireEventually,
    flushEventualQueue,
    Tub,
)

import allmydata
from allmydata.introducer import (
    create_introducer,
)
from allmydata.introducer.server import (
    _IntroducerNode,
)
from allmydata.web.introweb import (
    IntroducerRoot,
)

from allmydata import node
from .common import (
    assert_soup_has_favicon,
    assert_soup_has_text,
    assert_soup_has_tag_with_attributes,
)
from ..common import (
    SameProcessStreamEndpointAssigner,
)
from ..common_util import (
    FakeCanary,
)
from ..common_web import (
    do_http,
    render,
)


@defer.inlineCallbacks
def create_introducer_webish(reactor, port_assigner, basedir):
    """
    Create and start an introducer node and return it and its ``WebishServer``
    service.

    :param reactor: The reactor to use to allow the introducer node to use to
        listen for connections.

    :param SameProcessStreamEndpointAssigner port_assigner: The assigner to
        use to assign a listening port for the introducer node.

    :param bytes basedir: A non-existant path where the introducer node will
        be created.

    :return Deferred[(_IntroducerNode, WebishServer)]: A Deferred that fires
        with the node and its webish service.
    """
    node.create_node_dir(basedir, "testing")
    _, port_endpoint = port_assigner.assign(reactor)
    with open(join(basedir, "tahoe.cfg"), "w") as f:
        f.write(
            "[node]\n"
            "tub.location = 127.0.0.1:1\n" +
            "web.port = {}\n".format(port_endpoint)
        )

    intro_node = yield create_introducer(basedir)
    ws = intro_node.getServiceNamed("webish")

    yield fireEventually(None)
    intro_node.startService()

    defer.returnValue((intro_node, ws))


class IntroducerWeb(unittest.TestCase):
    """
    Tests for web-facing functionality of an introducer node.
    """
    def setUp(self):
        self.node = None
        self.port_assigner = SameProcessStreamEndpointAssigner()
        self.port_assigner.setUp()
        self.addCleanup(self.port_assigner.tearDown)
        # Anything using Foolscap leaves some timer trash in the reactor that
        # we have to arrange to have cleaned up.
        self.addCleanup(lambda: flushEventualQueue(None))

    @defer.inlineCallbacks
    def test_welcome(self):
        node, ws = yield create_introducer_webish(
            reactor,
            self.port_assigner,
            self.mktemp(),
        )
        self.addCleanup(node.stopService)

        url = "http://localhost:%d/" % (ws.getPortnum(),)
        res = yield do_http("get", url)
        soup = BeautifulSoup(res, 'html5lib')
        assert_soup_has_text(self, soup, u'Welcome to the Tahoe-LAFS Introducer')
        assert_soup_has_favicon(self, soup)
        assert_soup_has_text(self, soup, u'Page rendered at')
        assert_soup_has_text(self, soup, u'Tahoe-LAFS code imported from:')

    @defer.inlineCallbacks
    def test_basic_information(self):
        """
        The introducer web page includes the software version and several other
        simple pieces of information.
        """
        node, ws = yield create_introducer_webish(
            reactor,
            self.port_assigner,
            self.mktemp(),
        )
        self.addCleanup(node.stopService)

        url = "http://localhost:%d/" % (ws.getPortnum(),)
        res = yield do_http("get", url)
        soup = BeautifulSoup(res, 'html5lib')
        assert_soup_has_text(
            self,
            soup,
            u"%s: %s" % (allmydata.__appname__, allmydata.__version__),
        )
        assert_soup_has_text(self, soup, u"no peers!")
        assert_soup_has_text(self, soup, u"subscribers!")
        assert_soup_has_tag_with_attributes(
            self,
            soup,
            "link",
            {"href": "/tahoe.css"},
        )

    @defer.inlineCallbacks
    def test_tahoe_css(self):
        """
        The introducer serves the css.
        """
        node, ws = yield create_introducer_webish(
            reactor,
            self.port_assigner,
            self.mktemp(),
        )
        self.addCleanup(node.stopService)

        url = "http://localhost:%d/tahoe.css" % (ws.getPortnum(),)

        # Just don't return an error.  If it does, do_http will raise
        # something.
        yield do_http("get", url)

    @defer.inlineCallbacks
    def test_json_front_page(self):
        """
        The front page can be served as json.
        """
        node, ws = yield create_introducer_webish(
            reactor,
            self.port_assigner,
            self.mktemp(),
        )
        self.addCleanup(node.stopService)

        url = "http://localhost:%d/?t=json" % (ws.getPortnum(),)
        res = yield do_http("get", url)
        data = json.loads(res)
        self.assertEqual(data["subscription_summary"], {})
        self.assertEqual(data["announcement_summary"], {})


class IntroducerRootTests(unittest.TestCase):
    """
    Tests for ``IntroducerRoot``.
    """
    def test_json(self):
        """
        The JSON response includes totals for the number of subscriptions and
        announcements of each service type.
        """
        config = node.config_from_string(self.mktemp(), "", "")
        config.get_private_path = lambda ignored: self.mktemp()
        main_tub = Tub()
        main_tub.listenOn(b"tcp:0")
        main_tub.setLocation(b"tcp:127.0.0.1:1")
        introducer_node = _IntroducerNode(config, main_tub, None, None, None)

        introducer_service = introducer_node.getServiceNamed("introducer")
        for n in range(2):
            introducer_service.add_subscriber(
                FakeCanary(),
                "arbitrary",
                {"info": "info"},
            )

        # It would be nice to use the publish method but then we have to
        # generate a correctly signed message which I don't feel like doing.
        ann_t = ("msg", "sig", "key")
        ann = {"service-name": "arbitrary"}
        introducer_service._announcements[("arbitrary", "key")] = (
            ann_t,
            FakeCanary(),
            ann,
            0,
        )

        resource = IntroducerRoot(introducer_node)
        response = json.loads(
            self.successResultOf(
                render(resource, {"t": [b"json"]}),
            ),
        )
        self.assertEqual(
            response,
            {
                u"subscription_summary": {"arbitrary": 2},
                u"announcement_summary": {"arbitrary": 1},
            },
        )
