import json

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks

from allmydata.scripts.common import get_aliases
from allmydata.test.no_network import GridTestMixin
from .common import CLITestMixin
from allmydata.util import encodingutil

# see also test_create_alias

class ListAlias(GridTestMixin, CLITestMixin, unittest.TestCase):

    @inlineCallbacks
    def _test_list(self, alias, encoding):
        """
        Assert that ``tahoe create-alias`` can be used to create an alias named
        ``alias`` when argv is encoded using ``encoding``.

        :param unicode alias: The alias to try to create.

        :param str encoding: The name of an encoding to force the
            ``create-alias`` implementation to use.  This simulates the
            effects of setting LANG and doing other locale-foolishness without
            actually having to mess with this process's global locale state.

        :return Deferred: A Deferred that fires with success if the alias can
            be created and that creation is reported on stdout appropriately
            encoded or with failure if something goes wrong.
        """
        self.basedir = self.mktemp()
        self.set_up_grid(oneshare=True)

        self.patch(encodingutil, "io_encoding", encoding)

        rc, stdout, stderr = yield self.do_cli_unicode(
            u"create-alias",
            [alias],
            encoding=encoding,
        )

        self.assertEqual(
            u"Alias '{}' created\n".format(alias),
            stdout,
        )
        self.assertEqual("", stderr)
        aliases = get_aliases(self.get_clientdir())
        self.assertIn(alias, aliases)
        self.assertTrue(aliases[alias].startswith(u"URI:DIR2:"))

        rc, stdout, stderr = yield self.do_cli_unicode(
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
        """
        An alias composed of all ASCII-encodeable code points can be created when
        the active encoding is ASCII.
        """
        return self._test_list(
            u"tahoe",
            encoding="ascii",
        )


    def test_list_latin_1(self):
        """
        An alias composed of all Latin-1-encodeable code points can be created
        when the active encoding is Latin-1.

        This is very similar to ``test_list_utf_8`` but the assumption of
        UTF-8 is nearly ubiquitous and explicitly exercising the codepaths
        with a UTF-8-incompatible encoding helps flush out unintentional UTF-8
        assumptions.
        """
        return self._test_list(
            u"taho\N{LATIN SMALL LETTER E WITH ACUTE}",
            encoding="latin-1",
        )


    def test_list_utf_8(self):
        """
        An alias composed of all UTF-8-encodeable code points can be created when
        the active encoding is UTF-8.
        """
        return self._test_list(
            u"tahoe\N{SNOWMAN}",
            encoding="utf-8",
        )
