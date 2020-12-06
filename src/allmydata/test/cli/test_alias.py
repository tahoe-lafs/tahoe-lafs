import json

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks

from allmydata.util.encodingutil import unicode_to_argv
from allmydata.scripts.common import get_aliases
from allmydata.test.no_network import GridTestMixin
from .common import CLITestMixin

# see also test_create_alias

class ListAlias(GridTestMixin, CLITestMixin, unittest.TestCase):

    @inlineCallbacks
    def _test_list(self, alias):
        self.basedir = "cli/ListAlias/test_list"
        self.set_up_grid(oneshare=True)

        rc, stdout, stderr = yield self.do_cli(
            "create-alias",
            unicode_to_argv(alias),
        )

        self.assertIn(
            unicode_to_argv(u"Alias '{}' created".format(alias)),
            stdout,
        )
        self.assertEqual("", stderr)
        aliases = get_aliases(self.get_clientdir())
        self.assertIn(alias, aliases)
        self.assertTrue(aliases[alias].startswith("URI:DIR2:"))

        rc, stdout, stderr = yield self.do_cli("list-aliases", "--json")

        self.assertEqual(0, rc)
        data = json.loads(stdout)
        self.assertIn(alias, data)
        data = data[alias]
        self.assertIn("readwrite", data)
        self.assertIn("readonly", data)


    def test_list(self):
        return self._test_list(u"tahoe")


    def test_list_unicode(self):
        return self._test_list(u"tahoe\{SNOWMAN}")
