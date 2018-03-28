import json
from mock import patch

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks

from allmydata.util.encodingutil import unicode_to_argv
from allmydata.scripts.common import get_aliases
from allmydata.test.no_network import GridTestMixin
from .common import CLITestMixin
from ..common_util import skip_if_cannot_represent_argv

# see also test_create_alias

class ListAlias(GridTestMixin, CLITestMixin, unittest.TestCase):

    @inlineCallbacks
    def test_list(self):
        self.basedir = "cli/ListAlias/test_list"
        self.set_up_grid(oneshare=True)

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
    def test_list_unicode_mismatch_json(self):
        """
        pretty hack-y test, but we want to cover the 'except' on Unicode
        errors paths and I can't come up with a nicer way to trigger
        this
        """
        self.basedir = "cli/ListAlias/test_list_unicode_mismatch_json"
        skip_if_cannot_represent_argv(u"tahoe\u263A")
        self.set_up_grid(oneshare=True)

        rc, stdout, stderr = yield self.do_cli(
            "create-alias",
            unicode_to_argv(u"tahoe\u263A"),
        )

        self.failUnless(unicode_to_argv(u"Alias 'tahoe\u263A' created") in stdout)
        self.failIf(stderr)

        booms = []

        def boom(out, indent=4):
            if not len(booms):
                booms.append(out)
                raise UnicodeEncodeError("foo", u"foo", 3, 5, "foo")
            return str(out)

        with patch("allmydata.scripts.tahoe_add_alias.json.dumps", boom):
            aliases = get_aliases(self.get_clientdir())
            self.failUnless(u"tahoe\u263A" in aliases)
            self.failUnless(aliases[u"tahoe\u263A"].startswith("URI:DIR2:"))

            rc, stdout, stderr = yield self.do_cli("list-aliases", "--json")

            self.assertEqual(1, rc)
            self.assertIn("could not be converted", stderr)

    @inlineCallbacks
    def test_list_unicode_mismatch(self):
        self.basedir = "cli/ListAlias/test_list_unicode_mismatch"
        skip_if_cannot_represent_argv(u"tahoe\u263A")
        self.set_up_grid(oneshare=True)

        rc, stdout, stderr = yield self.do_cli(
            "create-alias",
            unicode_to_argv(u"tahoe\u263A"),
        )

        def boom(out):
            print("boom {}".format(out))
            return out
            raise UnicodeEncodeError("foo", u"foo", 3, 5, "foo")

        with patch("allmydata.scripts.tahoe_add_alias.unicode_to_output", boom):
            self.failUnless(unicode_to_argv(u"Alias 'tahoe\u263A' created") in stdout)
            self.failIf(stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnless(u"tahoe\u263A" in aliases)
            self.failUnless(aliases[u"tahoe\u263A"].startswith("URI:DIR2:"))

            rc, stdout, stderr = yield self.do_cli("list-aliases")

            self.assertEqual(1, rc)
            self.assertIn("could not be converted", stderr)
