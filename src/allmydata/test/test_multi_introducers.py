"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from six import ensure_binary

import os

from twisted.python.filepath import FilePath
from twisted.trial import unittest
from twisted.internet import defer
from allmydata.util import yamlutil
from allmydata.client import create_client
from allmydata.scripts.create_node import write_node_config

INTRODUCERS_CFG_FURLS=['furl1', 'furl2']
INTRODUCERS_CFG_FURLS_COMMENTED="""introducers:
  'intro1': {furl: furl1}
# 'intro2': {furl: furl4}
        """

class MultiIntroTests(unittest.TestCase):

    async def setUp(self):
        # setup tahoe.cfg and basedir/private/introducers
        # create a custom tahoe.cfg
        self.basedir = os.path.dirname(self.mktemp())
        c = open(os.path.join(self.basedir, "tahoe.cfg"), "w")
        config = {'hide-ip':False, 'listen': 'tcp',
                  'port': None, 'location': None, 'hostname': 'example.net'}
        await write_node_config(c, config)
        c.write("[storage]\n")
        c.write("enabled = false\n")
        c.close()
        os.mkdir(os.path.join(self.basedir,"private"))
        self.yaml_path = FilePath(os.path.join(self.basedir, "private",
                                               "introducers.yaml"))

    @defer.inlineCallbacks
    def test_introducer_count(self):
        """
        If there are two introducers configured in ``introducers.yaml`` then
        ``Client`` creates two introducer clients.
        """
        connections = {
            'introducers': {
                u'intro1':{ 'furl': 'furl1' },
                u'intro2':{ 'furl': 'furl4' },
            },
        }
        self.yaml_path.setContent(ensure_binary(yamlutil.safe_dump(connections)))
        # get a client and count of introducer_clients
        myclient = yield create_client(self.basedir)
        ic_count = len(myclient.introducer_clients)

        # assertions
        self.failUnlessEqual(ic_count, len(connections["introducers"]))

    async def test_read_introducer_furl_from_tahoecfg(self):
        """
        The deprecated [client]introducer.furl item is still read and respected.
        """
        # create a custom tahoe.cfg
        c = open(os.path.join(self.basedir, "tahoe.cfg"), "w")
        config = {'hide-ip':False, 'listen': 'tcp',
                  'port': None, 'location': None, 'hostname': 'example.net'}
        await write_node_config(c, config)
        fake_furl = "furl1"
        c.write("[client]\n")
        c.write("introducer.furl = %s\n" % fake_furl)
        c.write("[storage]\n")
        c.write("enabled = false\n")
        c.close()

        # get a client and first introducer_furl
        myclient = yield create_client(self.basedir)
        tahoe_cfg_furl = myclient.introducer_clients[0].introducer_furl

        # assertions
        self.failUnlessEqual(fake_furl, str(tahoe_cfg_furl, "utf-8"))
        self.assertEqual(
            list(
                warning["message"]
                for warning
                in self.flushWarnings()
                if warning["category"] is DeprecationWarning
            ),
            ["tahoe.cfg [client]introducer.furl is deprecated; "
             "use private/introducers.yaml instead."],
        )

    @defer.inlineCallbacks
    def test_reject_default_in_yaml(self):
        """
        If an introducer is configured in tahoe.cfg with the deprecated
        [client]introducer.furl then a "default" introducer in
        introducers.yaml is rejected.
        """
        connections = {
            'introducers': {
                u'default': { 'furl': 'furl1' },
            },
        }
        self.yaml_path.setContent(ensure_binary(yamlutil.safe_dump(connections)))
        FilePath(self.basedir).child("tahoe.cfg").setContent(
            b"[client]\n"
            b"introducer.furl = furl1\n"
        )

        with self.assertRaises(ValueError) as ctx:
            yield create_client(self.basedir)

        self.assertEquals(
            str(ctx.exception),
            "'default' introducer furl cannot be specified in tahoe.cfg and introducers.yaml; "
            "please fix impossible configuration.",
        )

SIMPLE_YAML = b"""
introducers:
  one:
    furl: furl1
"""

# this format was recommended in docs/configuration.rst in 1.12.0, but it
# isn't correct (the "furl = furl1" line is recorded as the string value of
# the ["one"] key, instead of being parsed as a single-key dictionary).
EQUALS_YAML = b"""
introducers:
  one: furl = furl1
"""

class NoDefault(unittest.TestCase):
    async def setUp(self):
        # setup tahoe.cfg and basedir/private/introducers
        # create a custom tahoe.cfg
        self.basedir = os.path.dirname(self.mktemp())
        c = open(os.path.join(self.basedir, "tahoe.cfg"), "w")
        config = {'hide-ip':False, 'listen': 'tcp',
                  'port': None, 'location': None, 'hostname': 'example.net'}
        await write_node_config(c, config)
        c.write("[storage]\n")
        c.write("enabled = false\n")
        c.close()
        os.mkdir(os.path.join(self.basedir,"private"))
        self.yaml_path = FilePath(os.path.join(self.basedir, "private",
                                               "introducers.yaml"))

    @defer.inlineCallbacks
    def test_ok(self):
        connections = {'introducers': {
            u'one': { 'furl': 'furl1' },
            }}
        self.yaml_path.setContent(ensure_binary(yamlutil.safe_dump(connections)))
        myclient = yield create_client(self.basedir)
        tahoe_cfg_furl = myclient.introducer_clients[0].introducer_furl
        self.assertEquals(tahoe_cfg_furl, b'furl1')

    @defer.inlineCallbacks
    def test_real_yaml(self):
        self.yaml_path.setContent(SIMPLE_YAML)
        myclient = yield create_client(self.basedir)
        tahoe_cfg_furl = myclient.introducer_clients[0].introducer_furl
        self.assertEquals(tahoe_cfg_furl, b'furl1')

    @defer.inlineCallbacks
    def test_invalid_equals_yaml(self):
        self.yaml_path.setContent(EQUALS_YAML)
        with self.assertRaises(TypeError) as ctx:
            yield create_client(self.basedir)
        self.assertIsInstance(
            ctx.exception,
            TypeError,
        )

    @defer.inlineCallbacks
    def test_introducerless(self):
        connections = {'introducers': {} }
        self.yaml_path.setContent(ensure_binary(yamlutil.safe_dump(connections)))
        myclient = yield create_client(self.basedir)
        self.assertEquals(len(myclient.introducer_clients), 0)
