import os.path
from twisted.trial import unittest
import urllib
from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from allmydata.scripts import cli, runner
from allmydata.test.no_network import GridTestMixin
from allmydata.util.encodingutil import quote_output, get_io_encoding
from .test_cli import CLITestMixin

timeout = 480 # deep_check takes 360s on Zandr's linksys box, others take > 240s

class CreateAlias(GridTestMixin, CLITestMixin, unittest.TestCase):

    def _test_webopen(self, args, expected_url):
        o = runner.Options()
        o.parseOptions(["--node-directory", self.get_clientdir(), "webopen"]
                       + list(args))
        urls = []
        rc = cli.webopen(o, urls.append)
        self.failUnlessReallyEqual(rc, 0)
        self.failUnlessReallyEqual(len(urls), 1)
        self.failUnlessReallyEqual(urls[0], expected_url)

    def test_create(self):
        self.basedir = "cli/CreateAlias/create"
        self.set_up_grid()
        aliasfile = os.path.join(self.get_clientdir(), "private", "aliases")

        d = self.do_cli("create-alias", "tahoe")
        def _done((rc,stdout,stderr)):
            self.failUnless("Alias 'tahoe' created" in stdout)
            self.failIf(stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnless("tahoe" in aliases)
            self.failUnless(aliases["tahoe"].startswith("URI:DIR2:"))
        d.addCallback(_done)
        d.addCallback(lambda res: self.do_cli("create-alias", "two:"))

        def _stash_urls(res):
            aliases = get_aliases(self.get_clientdir())
            node_url_file = os.path.join(self.get_clientdir(), "node.url")
            nodeurl = fileutil.read(node_url_file).strip()
            self.welcome_url = nodeurl
            uribase = nodeurl + "uri/"
            self.tahoe_url = uribase + urllib.quote(aliases["tahoe"])
            self.tahoe_subdir_url = self.tahoe_url + "/subdir"
            self.two_url = uribase + urllib.quote(aliases["two"])
            self.two_uri = aliases["two"]
        d.addCallback(_stash_urls)

        d.addCallback(lambda res: self.do_cli("create-alias", "two")) # dup
        def _check_create_duplicate((rc,stdout,stderr)):
            self.failIfEqual(rc, 0)
            self.failUnless("Alias 'two' already exists!" in stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnlessReallyEqual(aliases["two"], self.two_uri)
        d.addCallback(_check_create_duplicate)

        d.addCallback(lambda res: self.do_cli("add-alias", "added", self.two_uri))
        def _check_add((rc,stdout,stderr)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnless("Alias 'added' added" in stdout)
        d.addCallback(_check_add)

        # check add-alias with a duplicate
        d.addCallback(lambda res: self.do_cli("add-alias", "two", self.two_uri))
        def _check_add_duplicate((rc,stdout,stderr)):
            self.failIfEqual(rc, 0)
            self.failUnless("Alias 'two' already exists!" in stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnlessReallyEqual(aliases["two"], self.two_uri)
        d.addCallback(_check_add_duplicate)

        # check create-alias and add-alias with invalid aliases
        def _check_invalid((rc,stdout,stderr)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("cannot contain", stderr)

        for invalid in ['foo:bar', 'foo bar', 'foobar::']:
            d.addCallback(lambda res, invalid=invalid: self.do_cli("create-alias", invalid))
            d.addCallback(_check_invalid)
            d.addCallback(lambda res, invalid=invalid: self.do_cli("add-alias", invalid, self.two_uri))
            d.addCallback(_check_invalid)

        def _test_urls(junk):
            self._test_webopen([], self.welcome_url)
            self._test_webopen(["/"], self.tahoe_url)
            self._test_webopen(["tahoe:"], self.tahoe_url)
            self._test_webopen(["tahoe:/"], self.tahoe_url)
            self._test_webopen(["tahoe:subdir"], self.tahoe_subdir_url)
            self._test_webopen(["-i", "tahoe:subdir"],
                               self.tahoe_subdir_url+"?t=info")
            self._test_webopen(["tahoe:subdir/"], self.tahoe_subdir_url + '/')
            self._test_webopen(["tahoe:subdir/file"],
                               self.tahoe_subdir_url + '/file')
            self._test_webopen(["--info", "tahoe:subdir/file"],
                               self.tahoe_subdir_url + '/file?t=info')
            # if "file" is indeed a file, then the url produced by webopen in
            # this case is disallowed by the webui. but by design, webopen
            # passes through the mistake from the user to the resultant
            # webopened url
            self._test_webopen(["tahoe:subdir/file/"], self.tahoe_subdir_url + '/file/')
            self._test_webopen(["two:"], self.two_url)
        d.addCallback(_test_urls)

        def _remove_trailing_newline_and_create_alias(ign):
            # ticket #741 is about a manually-edited alias file (which
            # doesn't end in a newline) being corrupted by a subsequent
            # "tahoe create-alias"
            old = fileutil.read(aliasfile)
            fileutil.write(aliasfile, old.rstrip())
            return self.do_cli("create-alias", "un-corrupted1")
        d.addCallback(_remove_trailing_newline_and_create_alias)
        def _check_not_corrupted1((rc,stdout,stderr)):
            self.failUnless("Alias 'un-corrupted1' created" in stdout, stdout)
            self.failIf(stderr)
            # the old behavior was to simply append the new record, causing a
            # line that looked like "NAME1: CAP1NAME2: CAP2". This won't look
            # like a valid dircap, so get_aliases() will raise an exception.
            aliases = get_aliases(self.get_clientdir())
            self.failUnless("added" in aliases)
            self.failUnless(aliases["added"].startswith("URI:DIR2:"))
            # to be safe, let's confirm that we don't see "NAME2:" in CAP1.
            # No chance of a false-negative, because the hyphen in
            # "un-corrupted1" is not a valid base32 character.
            self.failIfIn("un-corrupted1:", aliases["added"])
            self.failUnless("un-corrupted1" in aliases)
            self.failUnless(aliases["un-corrupted1"].startswith("URI:DIR2:"))
        d.addCallback(_check_not_corrupted1)

        def _remove_trailing_newline_and_add_alias(ign):
            # same thing, but for "tahoe add-alias"
            old = fileutil.read(aliasfile)
            fileutil.write(aliasfile, old.rstrip())
            return self.do_cli("add-alias", "un-corrupted2", self.two_uri)
        d.addCallback(_remove_trailing_newline_and_add_alias)
        def _check_not_corrupted((rc,stdout,stderr)):
            self.failUnless("Alias 'un-corrupted2' added" in stdout, stdout)
            self.failIf(stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnless("un-corrupted1" in aliases)
            self.failUnless(aliases["un-corrupted1"].startswith("URI:DIR2:"))
            self.failIfIn("un-corrupted2:", aliases["un-corrupted1"])
            self.failUnless("un-corrupted2" in aliases)
            self.failUnless(aliases["un-corrupted2"].startswith("URI:DIR2:"))
        d.addCallback(_check_not_corrupted)

    def test_create_unicode(self):
        self.basedir = "cli/CreateAlias/create_unicode"
        self.set_up_grid()

        try:
            etudes_arg = u"\u00E9tudes".encode(get_io_encoding())
            lumiere_arg = u"lumi\u00E8re.txt".encode(get_io_encoding())
        except UnicodeEncodeError:
            raise unittest.SkipTest("A non-ASCII command argument could not be encoded on this platform.")

        d = self.do_cli("create-alias", etudes_arg)
        def _check_create_unicode((rc, out, err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessIn("Alias %s created" % quote_output(u"\u00E9tudes"), out)

            aliases = get_aliases(self.get_clientdir())
            etudes_alias = aliases[u"\u00E9tudes"]
            self.failUnless(etudes_alias.startswith("URI:DIR2-MDMF:"), etudes_alias)
        d.addCallback(_check_create_unicode)

        d.addCallback(lambda res: self.do_cli("ls", etudes_arg + ":"))
        def _check_ls1((rc, out, err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check_ls1)

        d.addCallback(lambda res: self.do_cli("put", "-", etudes_arg + ":uploaded.txt",
                                              stdin="Blah blah blah"))

        d.addCallback(lambda res: self.do_cli("ls", etudes_arg + ":"))
        def _check_ls2((rc, out, err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(out, "uploaded.txt\n")
        d.addCallback(_check_ls2)

        d.addCallback(lambda res: self.do_cli("get", etudes_arg + ":uploaded.txt"))
        def _check_get((rc, out, err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(out, "Blah blah blah")
        d.addCallback(_check_get)

        # Ensure that an Unicode filename in an Unicode alias works as expected
        d.addCallback(lambda res: self.do_cli("put", "-", etudes_arg + ":" + lumiere_arg,
                                              stdin="Let the sunshine In!"))

        d.addCallback(lambda res: self.do_cli("get",
                                              get_aliases(self.get_clientdir())[u"\u00E9tudes"] + "/" + lumiere_arg))
        def _check_get2((rc, out, err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(out, "Let the sunshine In!")
        d.addCallback(_check_get2)

        return d

    # TODO: test list-aliases, including Unicode


