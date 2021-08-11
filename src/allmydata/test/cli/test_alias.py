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
    def _check_create_alias(self, alias, encoding):
        """
        Verify that ``tahoe create-alias`` can be used to create an alias named
        ``alias`` when argv is encoded using ``encoding``.

        :param unicode alias: The alias to try to create.

        :param NoneType|str encoding: The name of an encoding to force the
            ``create-alias`` implementation to use.  This simulates the
            effects of setting LANG and doing other locale-foolishness without
            actually having to mess with this process's global locale state.
            If this is ``None`` then the encoding used will be ascii but the
            stdio objects given to the code under test will not declare any
            encoding (this is like Python 2 when stdio is not a tty).

        :return Deferred: A Deferred that fires with success if the alias can
            be created and that creation is reported on stdout appropriately
            encoded or with failure if something goes wrong.
        """
        self.basedir = self.mktemp()
        self.set_up_grid(oneshare=True)

        # We can pass an encoding into the test utilities to invoke the code
        # under test but we can't pass such a parameter directly to the code
        # under test.  Instead, that code looks at io_encoding.  So,
        # monkey-patch that value to our desired value here.  This is the code
        # that most directly takes the place of messing with LANG or the
        # locale module.
        self.patch(encodingutil, "io_encoding", encoding or "ascii")

        rc, stdout, stderr = yield self.do_cli_unicode(
            u"create-alias",
            [alias],
            encoding=encoding,
        )

        # Make sure the result of the create-alias command is as we want it to
        # be.
        self.assertEqual(u"Alias '{}' created\n".format(alias), stdout)
        self.assertEqual("", stderr)
        self.assertEqual(0, rc)

        # Make sure it had the intended side-effect, too - an alias created in
        # the node filesystem state.
        aliases = get_aliases(self.get_clientdir())
        self.assertIn(alias, aliases)
        self.assertTrue(aliases[alias].startswith(b"URI:DIR2:"))

        # And inspect the state via the user interface list-aliases command
        # too.
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


    def test_list_none(self):
        """
        An alias composed of all ASCII-encodeable code points can be created when
        stdio aren't clearly marked with an encoding.
        """
        return self._check_create_alias(
            u"tahoe",
            encoding=None,
        )


    def test_list_ascii(self):
        """
        An alias composed of all ASCII-encodeable code points can be created when
        the active encoding is ASCII.
        """
        return self._check_create_alias(
            u"tahoe",
            encoding="ascii",
        )


    def test_list_utf_8(self):
        """
        An alias composed of all UTF-8-encodeable code points can be created when
        the active encoding is UTF-8.
        """
        return self._check_create_alias(
            u"tahoe\N{SNOWMAN}",
            encoding="utf-8",
        )
