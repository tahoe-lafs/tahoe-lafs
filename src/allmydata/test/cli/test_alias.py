import json
from os.path import join

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks

from allmydata.util.encodingutil import unicode_to_argv
from allmydata.util import encodingutil
from allmydata.scripts.common import get_aliases
from allmydata.test.no_network import GridTestMixin
from .common import CLITestMixin


# see also test_create_alias


class ListAlias(GridTestMixin, CLITestMixin, unittest.TestCase):

    @inlineCallbacks
    def test_list(self):
        self.basedir = "cli/ListAlias/test_list"
        self.set_up_grid(oneshare=True)
        aliasfile = join(self.get_clientdir(), "private", "aliases")

        rc, stdout, stderr = yield self.do_cli(
            "create-alias",
            unicode_to_argv(u"tahoe"),
        )

        self.failUnless(unicode_to_argv(u"Alias 'tahoe' created") in stdout)
        self.failIf(stderr)
        aliases = get_aliases(self.get_clientdir())
        self.failUnless(u"tahoe" in aliases)
        self.failUnless(aliases[u"tahoe"].startswith("URI:DIR2:"))

        rc, stdout, stderr = yield self.do_cli("list-aliases", "--json")

        self.assertEqual(0, rc)
        data = json.loads(stdout)
        self.assertIn(u"tahoe", data)
        data = data[u"tahoe"]
        self.assertIn("readwrite", data)
        self.assertIn("readonly", data)

    @inlineCallbacks
    def test_list_unicode_mismatch(self):
        self.basedir = "cli/ListAlias/test_list_unicode_mismatch"
        self.set_up_grid(oneshare=True)
        aliasfile = join(self.get_clientdir(), "private", "aliases")

        rc, stdout, stderr = yield self.do_cli(
            "create-alias",
            unicode_to_argv(u"tahoe\u263A"),
        )

        self.failUnless(unicode_to_argv(u"Alias 'tahoe\u263A' created") in stdout)
        self.failIf(stderr)
        aliases = get_aliases(self.get_clientdir())
        self.failUnless(u"tahoe\u263A" in aliases)
        self.failUnless(aliases[u"tahoe\u263A"].startswith("URI:DIR2:"))

        rc, stdout, stderr = yield self.do_cli("list-aliases", "--json")

        self.assertEqual(0, rc)
        data = json.loads(stdout)
        self.assertIn(u"tahoe\u263A", data)
        data = data[u"tahoe\u263A"]
        self.assertIn("readwrite", data)
        self.assertIn("readonly", data)
