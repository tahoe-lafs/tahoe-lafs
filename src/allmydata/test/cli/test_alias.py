import json

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks

from allmydata.scripts.common import get_aliases
from allmydata.test.no_network import GridTestMixin
from .common import CLITestMixin
from allmydata.util.encodingutil import quote_output

# see also test_create_alias

class ListAlias(GridTestMixin, CLITestMixin, unittest.TestCase):

    @inlineCallbacks
    def _test_list(self, alias, encoding):
        self.basedir = self.mktemp()
        self.set_up_grid(oneshare=True)

        rc, stdout, stderr = yield self.do_cli_ex(
            u"create-alias",
            [alias],
            encoding=encoding,
        )

        self.assertIn(
            b"Alias {} created".format(quote_output(alias, encoding=encoding)),
            stdout.encode(encoding),
        )
        self.assertEqual("", stderr)
        aliases = get_aliases(self.get_clientdir())
        self.assertIn(alias, aliases)
        self.assertTrue(aliases[alias].startswith(u"URI:DIR2:"))

        rc, stdout, stderr = yield self.do_cli_ex(
            u"list-aliases",
            [u"--json"],
            encoding=encoding,
        )

        self.assertEqual(0, rc)
        data = json.loads(stdout)
        self.assertIn(alias, data)
        data = data[alias]
        self.assertIn(u"readwrite", data)
        self.assertIn(u"readonly", data)


    def test_list_ascii(self):
        return self._test_list(u"tahoe", encoding="ascii")


    def test_list_nonascii_ascii(self):
        return self._test_list(u"tahoe\N{SNOWMAN}", encoding="ascii")


    def test_list_utf_8(self):
        return self._test_list(u"tahoe", encoding="utf-8")


    def test_list_nonascii_utf_8(self):
        return self._test_list(u"tahoe\N{SNOWMAN}", encoding="utf-8")
