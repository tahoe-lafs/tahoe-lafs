# coding=utf-8

import os.path
from twisted.trial import unittest
from cStringIO import StringIO
import urllib
import re
import simplejson

from allmydata.util import fileutil, hashutil, base32
from allmydata import uri
from allmydata.immutable import upload

# Test that the scripts can be imported -- although the actual tests of their functionality are
# done by invoking them in a subprocess.
from allmydata.scripts import tahoe_ls, tahoe_get, tahoe_put, tahoe_rm, tahoe_cp
_hush_pyflakes = [tahoe_ls, tahoe_get, tahoe_put, tahoe_rm, tahoe_cp]

from allmydata.scripts import common
from allmydata.scripts.common import DEFAULT_ALIAS, get_aliases, get_alias, \
     DefaultAliasMarker

from allmydata.scripts import cli, debug, runner, backupdb
from allmydata.test.common_util import StallMixin
from allmydata.test.no_network import GridTestMixin
from twisted.internet import threads # CLI tests use deferToThread
from twisted.python import usage

class CLI(unittest.TestCase):
    # this test case only looks at argument-processing and simple stuff.
    def test_options(self):
        fileutil.rm_dir("cli/test_options")
        fileutil.make_dirs("cli/test_options")
        fileutil.make_dirs("cli/test_options/private")
        open("cli/test_options/node.url","w").write("http://localhost:8080/\n")
        filenode_uri = uri.WriteableSSKFileURI(writekey="\x00"*16,
                                               fingerprint="\x00"*32)
        private_uri = uri.NewDirectoryURI(filenode_uri).to_string()
        open("cli/test_options/private/root_dir.cap", "w").write(private_uri + "\n")
        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], private_uri)
        self.failUnlessEqual(o.where, "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--node-url", "http://example.org:8111/"])
        self.failUnlessEqual(o['node-url'], "http://example.org:8111/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], private_uri)
        self.failUnlessEqual(o.where, "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-cap", "root"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], "root")
        self.failUnlessEqual(o.where, "")

        o = cli.ListOptions()
        other_filenode_uri = uri.WriteableSSKFileURI(writekey="\x11"*16,
                                                     fingerprint="\x11"*32)
        other_uri = uri.NewDirectoryURI(other_filenode_uri).to_string()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-cap", other_uri])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], other_uri)
        self.failUnlessEqual(o.where, "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-cap", other_uri, "subdir"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], other_uri)
        self.failUnlessEqual(o.where, "subdir")

        o = cli.ListOptions()
        self.failUnlessRaises(usage.UsageError,
                              o.parseOptions,
                              ["--node-directory", "cli/test_options",
                               "--node-url", "NOT-A-URL"])

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--node-url", "http://localhost:8080"])
        self.failUnlessEqual(o["node-url"], "http://localhost:8080/")

    def _dump_cap(self, *args):
        config = debug.DumpCapOptions()
        config.stdout,config.stderr = StringIO(), StringIO()
        config.parseOptions(args)
        debug.dump_cap(config)
        self.failIf(config.stderr.getvalue())
        output = config.stdout.getvalue()
        return output

    def test_dump_cap_chk(self):
        key = "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        storage_index = hashutil.storage_index_hash(key)
        uri_extension_hash = hashutil.uri_extension_hash("stuff")
        needed_shares = 25
        total_shares = 100
        size = 1234
        u = uri.CHKFileURI(key=key,
                           uri_extension_hash=uri_extension_hash,
                           needed_shares=needed_shares,
                           total_shares=total_shares,
                           size=size)
        output = self._dump_cap(u.to_string())
        self.failUnless("CHK File:" in output, output)
        self.failUnless("key: aaaqeayeaudaocajbifqydiob4" in output, output)
        self.failUnless("UEB hash: nf3nimquen7aeqm36ekgxomalstenpkvsdmf6fplj7swdatbv5oa" in output, output)
        self.failUnless("size: 1234" in output, output)
        self.failUnless("k/N: 25/100" in output, output)
        self.failUnless("storage index: hdis5iaveku6lnlaiccydyid7q" in output, output)

        output = self._dump_cap("--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("client renewal secret: znxmki5zdibb5qlt46xbdvk2t55j7hibejq3i5ijyurkr6m6jkhq" in output, output)

        output = self._dump_cap(u.get_verify_cap().to_string())
        self.failIf("key: " in output, output)
        self.failUnless("UEB hash: nf3nimquen7aeqm36ekgxomalstenpkvsdmf6fplj7swdatbv5oa" in output, output)
        self.failUnless("size: 1234" in output, output)
        self.failUnless("k/N: 25/100" in output, output)
        self.failUnless("storage index: hdis5iaveku6lnlaiccydyid7q" in output, output)

        prefixed_u = "http://127.0.0.1/uri/%s" % urllib.quote(u.to_string())
        output = self._dump_cap(prefixed_u)
        self.failUnless("CHK File:" in output, output)
        self.failUnless("key: aaaqeayeaudaocajbifqydiob4" in output, output)
        self.failUnless("UEB hash: nf3nimquen7aeqm36ekgxomalstenpkvsdmf6fplj7swdatbv5oa" in output, output)
        self.failUnless("size: 1234" in output, output)
        self.failUnless("k/N: 25/100" in output, output)
        self.failUnless("storage index: hdis5iaveku6lnlaiccydyid7q" in output, output)

    def test_dump_cap_lit(self):
        u = uri.LiteralFileURI("this is some data")
        output = self._dump_cap(u.to_string())
        self.failUnless("Literal File URI:" in output, output)
        self.failUnless("data: this is some data" in output, output)

    def test_dump_cap_ssk(self):
        writekey = "\x01" * 16
        fingerprint = "\xfe" * 32
        u = uri.WriteableSSKFileURI(writekey, fingerprint)

        output = self._dump_cap(u.to_string())
        self.failUnless("SSK Writeable URI:" in output, output)
        self.failUnless("writekey: aeaqcaibaeaqcaibaeaqcaibae" in output, output)
        self.failUnless("readkey: nvgh5vj2ekzzkim5fgtb4gey5y" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        output = self._dump_cap("--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("file renewal secret: arpszxzc2t6kb4okkg7sp765xgkni5z7caavj7lta73vmtymjlxq" in output, output)

        fileutil.make_dirs("cli/test_dump_cap/private")
        f = open("cli/test_dump_cap/private/secret", "w")
        f.write("5s33nk3qpvnj2fw3z4mnm2y6fa\n")
        f.close()
        output = self._dump_cap("--client-dir", "cli/test_dump_cap",
                                u.to_string())
        self.failUnless("file renewal secret: arpszxzc2t6kb4okkg7sp765xgkni5z7caavj7lta73vmtymjlxq" in output, output)

        output = self._dump_cap("--client-dir", "cli/test_dump_cap_BOGUS",
                                u.to_string())
        self.failIf("file renewal secret:" in output, output)

        output = self._dump_cap("--nodeid", "tqc35esocrvejvg4mablt6aowg6tl43j",
                                u.to_string())
        self.failUnless("write_enabler: mgcavriox2wlb5eer26unwy5cw56elh3sjweffckkmivvsxtaknq" in output, output)
        self.failIf("file renewal secret:" in output, output)

        output = self._dump_cap("--nodeid", "tqc35esocrvejvg4mablt6aowg6tl43j",
                                "--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("write_enabler: mgcavriox2wlb5eer26unwy5cw56elh3sjweffckkmivvsxtaknq" in output, output)
        self.failUnless("file renewal secret: arpszxzc2t6kb4okkg7sp765xgkni5z7caavj7lta73vmtymjlxq" in output, output)
        self.failUnless("lease renewal secret: 7pjtaumrb7znzkkbvekkmuwpqfjyfyamznfz4bwwvmh4nw33lorq" in output, output)

        u = u.get_readonly()
        output = self._dump_cap(u.to_string())
        self.failUnless("SSK Read-only URI:" in output, output)
        self.failUnless("readkey: nvgh5vj2ekzzkim5fgtb4gey5y" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        u = u.get_verify_cap()
        output = self._dump_cap(u.to_string())
        self.failUnless("SSK Verifier URI:" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

    def test_dump_cap_directory(self):
        writekey = "\x01" * 16
        fingerprint = "\xfe" * 32
        u1 = uri.WriteableSSKFileURI(writekey, fingerprint)
        u = uri.NewDirectoryURI(u1)

        output = self._dump_cap(u.to_string())
        self.failUnless("Directory Writeable URI:" in output, output)
        self.failUnless("writekey: aeaqcaibaeaqcaibaeaqcaibae" in output,
                        output)
        self.failUnless("readkey: nvgh5vj2ekzzkim5fgtb4gey5y" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output,
                        output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        output = self._dump_cap("--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("file renewal secret: arpszxzc2t6kb4okkg7sp765xgkni5z7caavj7lta73vmtymjlxq" in output, output)

        output = self._dump_cap("--nodeid", "tqc35esocrvejvg4mablt6aowg6tl43j",
                                u.to_string())
        self.failUnless("write_enabler: mgcavriox2wlb5eer26unwy5cw56elh3sjweffckkmivvsxtaknq" in output, output)
        self.failIf("file renewal secret:" in output, output)

        output = self._dump_cap("--nodeid", "tqc35esocrvejvg4mablt6aowg6tl43j",
                                "--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("write_enabler: mgcavriox2wlb5eer26unwy5cw56elh3sjweffckkmivvsxtaknq" in output, output)
        self.failUnless("file renewal secret: arpszxzc2t6kb4okkg7sp765xgkni5z7caavj7lta73vmtymjlxq" in output, output)
        self.failUnless("lease renewal secret: 7pjtaumrb7znzkkbvekkmuwpqfjyfyamznfz4bwwvmh4nw33lorq" in output, output)

        u = u.get_readonly()
        output = self._dump_cap(u.to_string())
        self.failUnless("Directory Read-only URI:" in output, output)
        self.failUnless("readkey: nvgh5vj2ekzzkim5fgtb4gey5y" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        u = u.get_verify_cap()
        output = self._dump_cap(u.to_string())
        self.failUnless("Directory Verifier URI:" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

    def _catalog_shares(self, *basedirs):
        o = debug.CatalogSharesOptions()
        o.stdout,o.stderr = StringIO(), StringIO()
        args = list(basedirs)
        o.parseOptions(args)
        debug.catalog_shares(o)
        out = o.stdout.getvalue()
        err = o.stderr.getvalue()
        return out, err

    def test_catalog_shares_error(self):
        nodedir1 = "cli/test_catalog_shares/node1"
        sharedir = os.path.join(nodedir1, "storage", "shares", "mq", "mqfblse6m5a6dh45isu2cg7oji")
        fileutil.make_dirs(sharedir)
        f = open(os.path.join(sharedir, "8"), "wb")
        open("cli/test_catalog_shares/node1/storage/shares/mq/not-a-dir", "wb").close()
        # write a bogus share that looks a little bit like CHK
        f.write("\x00\x00\x00\x01" + "\xff" * 200) # this triggers an assert
        f.close()

        nodedir2 = "cli/test_catalog_shares/node2"
        fileutil.make_dirs(nodedir2)
        open("cli/test_catalog_shares/node1/storage/shares/not-a-dir", "wb").close()

        # now make sure that the 'catalog-shares' commands survives the error
        out, err = self._catalog_shares(nodedir1, nodedir2)
        self.failUnlessEqual(out, "", out)
        self.failUnless("Error processing " in err,
                        "didn't see 'error processing' in '%s'" % err)
        #self.failUnless(nodedir1 in err,
        #                "didn't see '%s' in '%s'" % (nodedir1, err))
        # windows mangles the path, and os.path.join isn't enough to make
        # up for it, so just look for individual strings
        self.failUnless("node1" in err,
                        "didn't see 'node1' in '%s'" % err)
        self.failUnless("mqfblse6m5a6dh45isu2cg7oji" in err,
                        "didn't see 'mqfblse6m5a6dh45isu2cg7oji' in '%s'" % err)

    def test_alias(self):
        aliases = {"tahoe": "TA",
                   "work": "WA",
                   "c": "CA"}
        def ga1(path):
            return get_alias(aliases, path, "tahoe")
        uses_lettercolon = common.platform_uses_lettercolon_drivename()
        self.failUnlessEqual(ga1("bare"), ("TA", "bare"))
        self.failUnlessEqual(ga1("baredir/file"), ("TA", "baredir/file"))
        self.failUnlessEqual(ga1("baredir/file:7"), ("TA", "baredir/file:7"))
        self.failUnlessEqual(ga1("tahoe:"), ("TA", ""))
        self.failUnlessEqual(ga1("tahoe:file"), ("TA", "file"))
        self.failUnlessEqual(ga1("tahoe:dir/file"), ("TA", "dir/file"))
        self.failUnlessEqual(ga1("work:"), ("WA", ""))
        self.failUnlessEqual(ga1("work:file"), ("WA", "file"))
        self.failUnlessEqual(ga1("work:dir/file"), ("WA", "dir/file"))
        # default != None means we really expect a tahoe path, regardless of
        # whether we're on windows or not. This is what 'tahoe get' uses.
        self.failUnlessEqual(ga1("c:"), ("CA", ""))
        self.failUnlessEqual(ga1("c:file"), ("CA", "file"))
        self.failUnlessEqual(ga1("c:dir/file"), ("CA", "dir/file"))
        self.failUnlessEqual(ga1("URI:stuff"), ("URI:stuff", ""))
        self.failUnlessEqual(ga1("URI:stuff:./file"), ("URI:stuff", "file"))
        self.failUnlessEqual(ga1("URI:stuff:./dir/file"),
                             ("URI:stuff", "dir/file"))
        self.failUnlessRaises(common.UnknownAliasError, ga1, "missing:")
        self.failUnlessRaises(common.UnknownAliasError, ga1, "missing:dir")
        self.failUnlessRaises(common.UnknownAliasError, ga1, "missing:dir/file")

        def ga2(path):
            return get_alias(aliases, path, None)
        self.failUnlessEqual(ga2("bare"), (DefaultAliasMarker, "bare"))
        self.failUnlessEqual(ga2("baredir/file"),
                             (DefaultAliasMarker, "baredir/file"))
        self.failUnlessEqual(ga2("baredir/file:7"),
                             (DefaultAliasMarker, "baredir/file:7"))
        self.failUnlessEqual(ga2("baredir/sub:1/file:7"),
                             (DefaultAliasMarker, "baredir/sub:1/file:7"))
        self.failUnlessEqual(ga2("tahoe:"), ("TA", ""))
        self.failUnlessEqual(ga2("tahoe:file"), ("TA", "file"))
        self.failUnlessEqual(ga2("tahoe:dir/file"), ("TA", "dir/file"))
        # on windows, we really want c:foo to indicate a local file.
        # default==None is what 'tahoe cp' uses.
        if uses_lettercolon:
            self.failUnlessEqual(ga2("c:"), (DefaultAliasMarker, "c:"))
            self.failUnlessEqual(ga2("c:file"), (DefaultAliasMarker, "c:file"))
            self.failUnlessEqual(ga2("c:dir/file"),
                                 (DefaultAliasMarker, "c:dir/file"))
        else:
            self.failUnlessEqual(ga2("c:"), ("CA", ""))
            self.failUnlessEqual(ga2("c:file"), ("CA", "file"))
            self.failUnlessEqual(ga2("c:dir/file"), ("CA", "dir/file"))
        self.failUnlessEqual(ga2("work:"), ("WA", ""))
        self.failUnlessEqual(ga2("work:file"), ("WA", "file"))
        self.failUnlessEqual(ga2("work:dir/file"), ("WA", "dir/file"))
        self.failUnlessEqual(ga2("URI:stuff"), ("URI:stuff", ""))
        self.failUnlessEqual(ga2("URI:stuff:./file"), ("URI:stuff", "file"))
        self.failUnlessEqual(ga2("URI:stuff:./dir/file"), ("URI:stuff", "dir/file"))
        self.failUnlessRaises(common.UnknownAliasError, ga2, "missing:")
        self.failUnlessRaises(common.UnknownAliasError, ga2, "missing:dir")
        self.failUnlessRaises(common.UnknownAliasError, ga2, "missing:dir/file")

        def ga3(path):
            old = common.pretend_platform_uses_lettercolon
            try:
                common.pretend_platform_uses_lettercolon = True
                retval = get_alias(aliases, path, None)
            finally:
                common.pretend_platform_uses_lettercolon = old
            return retval
        self.failUnlessEqual(ga3("bare"), (DefaultAliasMarker, "bare"))
        self.failUnlessEqual(ga3("baredir/file"),
                             (DefaultAliasMarker, "baredir/file"))
        self.failUnlessEqual(ga3("baredir/file:7"),
                             (DefaultAliasMarker, "baredir/file:7"))
        self.failUnlessEqual(ga3("baredir/sub:1/file:7"),
                             (DefaultAliasMarker, "baredir/sub:1/file:7"))
        self.failUnlessEqual(ga3("tahoe:"), ("TA", ""))
        self.failUnlessEqual(ga3("tahoe:file"), ("TA", "file"))
        self.failUnlessEqual(ga3("tahoe:dir/file"), ("TA", "dir/file"))
        self.failUnlessEqual(ga3("c:"), (DefaultAliasMarker, "c:"))
        self.failUnlessEqual(ga3("c:file"), (DefaultAliasMarker, "c:file"))
        self.failUnlessEqual(ga3("c:dir/file"),
                             (DefaultAliasMarker, "c:dir/file"))
        self.failUnlessEqual(ga3("work:"), ("WA", ""))
        self.failUnlessEqual(ga3("work:file"), ("WA", "file"))
        self.failUnlessEqual(ga3("work:dir/file"), ("WA", "dir/file"))
        self.failUnlessEqual(ga3("URI:stuff"), ("URI:stuff", ""))
        self.failUnlessEqual(ga3("URI:stuff:./file"), ("URI:stuff", "file"))
        self.failUnlessEqual(ga3("URI:stuff:./dir/file"), ("URI:stuff", "dir/file"))
        self.failUnlessRaises(common.UnknownAliasError, ga3, "missing:")
        self.failUnlessRaises(common.UnknownAliasError, ga3, "missing:dir")
        self.failUnlessRaises(common.UnknownAliasError, ga3, "missing:dir/file")


class Help(unittest.TestCase):

    def test_get(self):
        help = str(cli.GetOptions())
        self.failUnless("get VDRIVE_FILE LOCAL_FILE" in help, help)
        self.failUnless("% tahoe get FOO |less" in help, help)

    def test_put(self):
        help = str(cli.PutOptions())
        self.failUnless("put LOCAL_FILE VDRIVE_FILE" in help, help)
        self.failUnless("% cat FILE | tahoe put" in help, help)

    def test_rm(self):
        help = str(cli.RmOptions())
        self.failUnless("rm VDRIVE_FILE" in help, help)

    def test_mv(self):
        help = str(cli.MvOptions())
        self.failUnless("mv FROM TO" in help, help)

    def test_ln(self):
        help = str(cli.LnOptions())
        self.failUnless("ln FROM TO" in help, help)

    def test_backup(self):
        help = str(cli.BackupOptions())
        self.failUnless("backup FROM ALIAS:TO" in help, help)

    def test_webopen(self):
        help = str(cli.WebopenOptions())
        self.failUnless("webopen [ALIAS:PATH]" in help, help)

    def test_manifest(self):
        help = str(cli.ManifestOptions())
        self.failUnless("manifest [ALIAS:PATH]" in help, help)

    def test_stats(self):
        help = str(cli.StatsOptions())
        self.failUnless("stats [ALIAS:PATH]" in help, help)

    def test_check(self):
        help = str(cli.CheckOptions())
        self.failUnless("check [ALIAS:PATH]" in help, help)

    def test_deep_check(self):
        help = str(cli.DeepCheckOptions())
        self.failUnless("deep-check [ALIAS:PATH]" in help, help)

    def test_create_alias(self):
        help = str(cli.CreateAliasOptions())
        self.failUnless("create-alias ALIAS" in help, help)

    def test_add_aliases(self):
        help = str(cli.AddAliasOptions())
        self.failUnless("add-alias ALIAS DIRCAP" in help, help)

class CLITestMixin:
    def do_cli(self, verb, *args, **kwargs):
        nodeargs = [
            "--node-directory", self.get_clientdir(),
            ]
        argv = [verb] + nodeargs + list(args)
        stdin = kwargs.get("stdin", "")
        stdout, stderr = StringIO(), StringIO()
        d = threads.deferToThread(runner.runner, argv, run_by_human=False,
                                  stdin=StringIO(stdin),
                                  stdout=stdout, stderr=stderr)
        def _done(rc):
            return rc, stdout.getvalue(), stderr.getvalue()
        d.addCallback(_done)
        return d

class CreateAlias(GridTestMixin, CLITestMixin, unittest.TestCase):

    def _test_webopen(self, args, expected_url):
        woo = cli.WebopenOptions()
        all_args = ["--node-directory", self.get_clientdir()] + list(args)
        woo.parseOptions(all_args)
        urls = []
        rc = cli.webopen(woo, urls.append)
        self.failUnlessEqual(rc, 0)
        self.failUnlessEqual(len(urls), 1)
        self.failUnlessEqual(urls[0], expected_url)

    def test_create(self):
        self.basedir = "cli/CreateAlias/create"
        self.set_up_grid()

        d = self.do_cli("create-alias", "tahoe")
        def _done((rc,stdout,stderr)):
            self.failUnless("Alias 'tahoe' created" in stdout)
            self.failIf(stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnless("tahoe" in aliases)
            self.failUnless(aliases["tahoe"].startswith("URI:DIR2:"))
        d.addCallback(_done)
        d.addCallback(lambda res: self.do_cli("create-alias", "two"))

        def _stash_urls(res):
            aliases = get_aliases(self.get_clientdir())
            node_url_file = os.path.join(self.get_clientdir(), "node.url")
            nodeurl = open(node_url_file, "r").read().strip()
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
            self.failUnlessEqual(aliases["two"], self.two_uri)
        d.addCallback(_check_create_duplicate)

        d.addCallback(lambda res: self.do_cli("add-alias", "added", self.two_uri))
        def _check_add((rc,stdout,stderr)):
            self.failUnlessEqual(rc, 0)
            self.failUnless("Alias 'added' added" in stdout)
        d.addCallback(_check_add)

        # check add-alias with a duplicate
        d.addCallback(lambda res: self.do_cli("add-alias", "two", self.two_uri))
        def _check_add_duplicate((rc,stdout,stderr)):
            self.failIfEqual(rc, 0)
            self.failUnless("Alias 'two' already exists!" in stderr)
            aliases = get_aliases(self.get_clientdir())
            self.failUnlessEqual(aliases["two"], self.two_uri)
        d.addCallback(_check_add_duplicate)

        def _test_urls(junk):
            self._test_webopen([], self.tahoe_url)
            self._test_webopen(["/"], self.tahoe_url)
            self._test_webopen(["tahoe:"], self.tahoe_url)
            self._test_webopen(["tahoe:/"], self.tahoe_url)
            self._test_webopen(["tahoe:subdir"], self.tahoe_subdir_url)
            self._test_webopen(["tahoe:subdir/"], self.tahoe_subdir_url + '/')
            self._test_webopen(["tahoe:subdir/file"], self.tahoe_subdir_url + '/file')
            # if "file" is indeed a file, then the url produced by webopen in
            # this case is disallowed by the webui. but by design, webopen
            # passes through the mistake from the user to the resultant
            # webopened url
            self._test_webopen(["tahoe:subdir/file/"], self.tahoe_subdir_url + '/file/')
            self._test_webopen(["two:"], self.two_url)
        d.addCallback(_test_urls)

        return d

class Put(GridTestMixin, CLITestMixin, unittest.TestCase):

    def test_unlinked_immutable_stdin(self):
        # tahoe get `echo DATA | tahoe put`
        # tahoe get `echo DATA | tahoe put -`
        self.basedir = "cli/Put/unlinked_immutable_stdin"
        DATA = "data" * 100
        self.set_up_grid()
        d = self.do_cli("put", stdin=DATA)
        def _uploaded(res):
            (rc, stdout, stderr) = res
            self.failUnless("waiting for file data on stdin.." in stderr)
            self.failUnless("200 OK" in stderr, stderr)
            self.readcap = stdout
            self.failUnless(self.readcap.startswith("URI:CHK:"))
        d.addCallback(_uploaded)
        d.addCallback(lambda res: self.do_cli("get", self.readcap))
        def _downloaded(res):
            (rc, stdout, stderr) = res
            self.failUnlessEqual(stderr, "")
            self.failUnlessEqual(stdout, DATA)
        d.addCallback(_downloaded)
        d.addCallback(lambda res: self.do_cli("put", "-", stdin=DATA))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, self.readcap))
        return d

    def test_unlinked_immutable_from_file(self):
        # tahoe put file.txt
        # tahoe put ./file.txt
        # tahoe put /tmp/file.txt
        # tahoe put ~/file.txt
        self.basedir = "cli/Put/unlinked_immutable_from_file"
        self.set_up_grid()

        rel_fn = os.path.join(self.basedir, "DATAFILE")
        abs_fn = os.path.abspath(rel_fn)
        # we make the file small enough to fit in a LIT file, for speed
        f = open(rel_fn, "w")
        f.write("short file")
        f.close()
        d = self.do_cli("put", rel_fn)
        def _uploaded((rc,stdout,stderr)):
            readcap = stdout
            self.failUnless(readcap.startswith("URI:LIT:"))
            self.readcap = readcap
        d.addCallback(_uploaded)
        d.addCallback(lambda res: self.do_cli("put", "./" + rel_fn))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, self.readcap))
        d.addCallback(lambda res: self.do_cli("put", abs_fn))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, self.readcap))
        # we just have to assume that ~ is handled properly
        return d

    def test_immutable_from_file(self):
        # tahoe put file.txt uploaded.txt
        # tahoe - uploaded.txt
        # tahoe put file.txt subdir/uploaded.txt
        # tahoe put file.txt tahoe:uploaded.txt
        # tahoe put file.txt tahoe:subdir/uploaded.txt
        # tahoe put file.txt DIRCAP:./uploaded.txt
        # tahoe put file.txt DIRCAP:./subdir/uploaded.txt
        self.basedir = "cli/Put/immutable_from_file"
        self.set_up_grid()

        rel_fn = os.path.join(self.basedir, "DATAFILE")
        abs_fn = os.path.abspath(rel_fn)
        # we make the file small enough to fit in a LIT file, for speed
        DATA = "short file"
        DATA2 = "short file two"
        f = open(rel_fn, "w")
        f.write(DATA)
        f.close()

        d = self.do_cli("create-alias", "tahoe")

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "uploaded.txt"))
        def _uploaded((rc,stdout,stderr)):
            readcap = stdout.strip()
            self.failUnless(readcap.startswith("URI:LIT:"))
            self.failUnless("201 Created" in stderr, stderr)
            self.readcap = readcap
        d.addCallback(_uploaded)
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:uploaded.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", "-", "uploaded.txt", stdin=DATA2))
        def _replaced((rc,stdout,stderr)):
            readcap = stdout.strip()
            self.failUnless(readcap.startswith("URI:LIT:"))
            self.failUnless("200 OK" in stderr, stderr)
        d.addCallback(_replaced)

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "subdir/uploaded2.txt"))
        d.addCallback(lambda res: self.do_cli("get", "subdir/uploaded2.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "tahoe:uploaded3.txt"))
        d.addCallback(lambda res: self.do_cli("get", "tahoe:uploaded3.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn, "tahoe:subdir/uploaded4.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:subdir/uploaded4.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, DATA))

        def _get_dircap(res):
            self.dircap = get_aliases(self.get_clientdir())["tahoe"]
        d.addCallback(_get_dircap)

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn,
                                  self.dircap+":./uploaded5.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:uploaded5.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, DATA))

        d.addCallback(lambda res:
                      self.do_cli("put", rel_fn,
                                  self.dircap+":./subdir/uploaded6.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:subdir/uploaded6.txt"))
        d.addCallback(lambda (rc,stdout,stderr):
                      self.failUnlessEqual(stdout, DATA))

        return d

    def test_mutable_unlinked(self):
        # FILECAP = `echo DATA | tahoe put --mutable`
        # tahoe get FILECAP, compare against DATA
        # echo DATA2 | tahoe put - FILECAP
        # tahoe get FILECAP, compare against DATA2
        # tahoe put file.txt FILECAP
        self.basedir = "cli/Put/mutable_unlinked"
        self.set_up_grid()

        DATA = "data" * 100
        DATA2 = "two" * 100
        rel_fn = os.path.join(self.basedir, "DATAFILE")
        abs_fn = os.path.abspath(rel_fn)
        DATA3 = "three" * 100
        f = open(rel_fn, "w")
        f.write(DATA3)
        f.close()

        d = self.do_cli("put", "--mutable", stdin=DATA)
        def _created(res):
            (rc, stdout, stderr) = res
            self.failUnless("waiting for file data on stdin.." in stderr)
            self.failUnless("200 OK" in stderr)
            self.filecap = stdout
            self.failUnless(self.filecap.startswith("URI:SSK:"))
        d.addCallback(_created)
        d.addCallback(lambda res: self.do_cli("get", self.filecap))
        d.addCallback(lambda (rc,out,err): self.failUnlessEqual(out, DATA))

        d.addCallback(lambda res: self.do_cli("put", "-", self.filecap, stdin=DATA2))
        def _replaced(res):
            (rc, stdout, stderr) = res
            self.failUnless("waiting for file data on stdin.." in stderr)
            self.failUnless("200 OK" in stderr)
            self.failUnlessEqual(self.filecap, stdout)
        d.addCallback(_replaced)
        d.addCallback(lambda res: self.do_cli("get", self.filecap))
        d.addCallback(lambda (rc,out,err): self.failUnlessEqual(out, DATA2))

        d.addCallback(lambda res: self.do_cli("put", rel_fn, self.filecap))
        def _replaced2(res):
            (rc, stdout, stderr) = res
            self.failUnless("200 OK" in stderr)
            self.failUnlessEqual(self.filecap, stdout)
        d.addCallback(_replaced2)
        d.addCallback(lambda res: self.do_cli("get", self.filecap))
        d.addCallback(lambda (rc,out,err): self.failUnlessEqual(out, DATA3))

        return d

    def test_mutable(self):
        # echo DATA1 | tahoe put --mutable - uploaded.txt
        # echo DATA2 | tahoe put - uploaded.txt # should modify-in-place
        # tahoe get uploaded.txt, compare against DATA2

        self.basedir = "cli/Put/mutable"
        self.set_up_grid()

        DATA1 = "data" * 100
        fn1 = os.path.join(self.basedir, "DATA1")
        f = open(fn1, "w")
        f.write(DATA1)
        f.close()
        DATA2 = "two" * 100
        fn2 = os.path.join(self.basedir, "DATA2")
        f = open(fn2, "w")
        f.write(DATA2)
        f.close()

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res:
                      self.do_cli("put", "--mutable", fn1, "tahoe:uploaded.txt"))
        d.addCallback(lambda res:
                      self.do_cli("put", fn2, "tahoe:uploaded.txt"))
        d.addCallback(lambda res:
                      self.do_cli("get", "tahoe:uploaded.txt"))
        d.addCallback(lambda (rc,out,err): self.failUnlessEqual(out, DATA2))
        return d

class List(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_list(self):
        self.basedir = "cli/List/list"
        self.set_up_grid()
        c0 = self.g.clients[0]
        d = c0.create_empty_dirnode()
        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.rooturi = n.get_uri()
            return n.add_file(u"good", upload.Data("small", convergence=""))
        d.addCallback(_stash_root_and_create_file)
        d.addCallback(lambda ign:
                      self.rootnode.create_empty_directory(u"1share"))
        d.addCallback(lambda n:
                      self.delete_shares_numbered(n.get_uri(), range(1,10)))
        d.addCallback(lambda ign:
                      self.rootnode.create_empty_directory(u"0share"))
        d.addCallback(lambda n:
                      self.delete_shares_numbered(n.get_uri(), range(0,10)))
        d.addCallback(lambda ign:
                      self.do_cli("add-alias", "tahoe", self.rooturi))
        d.addCallback(lambda ign: self.do_cli("ls"))
        def _check1((rc,out,err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out.splitlines(), ["0share", "1share", "good"])
        d.addCallback(_check1)
        d.addCallback(lambda ign: self.do_cli("ls", "missing"))
        def _check2((rc,out,err)):
            self.failIfEqual(rc, 0)
            self.failUnlessEqual(err.strip(), "No such file or directory")
            self.failUnlessEqual(out, "")
        d.addCallback(_check2)
        d.addCallback(lambda ign: self.do_cli("ls", "1share"))
        def _check3((rc,out,err)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("Error during GET: 410 Gone ", err)
            self.failUnlessIn("UnrecoverableFileError:", err)
            self.failUnlessIn("could not be retrieved, because there were "
                              "insufficient good shares.", err)
            self.failUnlessEqual(out, "")
        d.addCallback(_check3)
        d.addCallback(lambda ign: self.do_cli("ls", "0share"))
        d.addCallback(_check3)
        return d

class Cp(GridTestMixin, CLITestMixin, unittest.TestCase):

    def test_not_enough_args(self):
        o = cli.CpOptions()
        self.failUnlessRaises(usage.UsageError,
                              o.parseOptions, ["onearg"])

    def test_unicode_filename(self):
        self.basedir = "cli/Cp/unicode_filename"
        self.set_up_grid()

        fn1 = os.path.join(self.basedir, "Ärtonwall")
        DATA1 = "unicode file content"
        open(fn1, "wb").write(DATA1)

        fn2 = os.path.join(self.basedir, "Metallica")
        DATA2 = "non-unicode file content"
        open(fn2, "wb").write(DATA2)

        # Bug #534
        # Assure that uploading a file whose name contains unicode character doesn't
        # prevent further uploads in the same directory
        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("cp", fn1, "tahoe:"))
        d.addCallback(lambda res: self.do_cli("cp", fn2, "tahoe:"))

        d.addCallback(lambda res: self.do_cli("get", "tahoe:Ärtonwall"))
        d.addCallback(lambda (rc,out,err): self.failUnlessEqual(out, DATA1))

        d.addCallback(lambda res: self.do_cli("get", "tahoe:Metallica"))
        d.addCallback(lambda (rc,out,err): self.failUnlessEqual(out, DATA2))

        return d
    test_unicode_filename.todo = "This behavior is not yet supported, although it does happen to work (for reasons that are ill-understood) on many platforms.  See issue ticket #534."

    def test_dangling_symlink_vs_recursion(self):
        if not hasattr(os, 'symlink'):
            raise unittest.SkipTest("There is no symlink on this platform.")
        # cp -r on a directory containing a dangling symlink shouldn't assert
        self.basedir = "cli/Cp/dangling_symlink_vs_recursion"
        self.set_up_grid()
        dn = os.path.join(self.basedir, "dir")
        os.mkdir(dn)
        fn = os.path.join(dn, "Fakebandica")
        ln = os.path.join(dn, "link")
        os.symlink(fn, ln)

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("cp", "--recursive",
                                              dn, "tahoe:"))
        return d

class Backup(GridTestMixin, CLITestMixin, StallMixin, unittest.TestCase):

    def writeto(self, path, data):
        d = os.path.dirname(os.path.join(self.basedir, "home", path))
        fileutil.make_dirs(d)
        f = open(os.path.join(self.basedir, "home", path), "w")
        f.write(data)
        f.close()

    def count_output(self, out):
        mo = re.search(r"(\d)+ files uploaded \((\d+) reused\), (\d+) directories created \((\d+) reused\)", out)
        return [int(s) for s in mo.groups()]

    def count_output2(self, out):
        mo = re.search(r"(\d)+ files checked, (\d+) directories checked, (\d+) directories read", out)
        return [int(s) for s in mo.groups()]

    def test_backup(self):
        self.basedir = "cli/Backup/backup"
        self.set_up_grid()

        # is the backupdb available? If so, we test that a second backup does
        # not create new directories.
        hush = StringIO()
        have_bdb = backupdb.get_backupdb(os.path.join(self.basedir, "dbtest"),
                                         hush)

        # create a small local directory with a couple of files
        source = os.path.join(self.basedir, "home")
        fileutil.make_dirs(os.path.join(source, "empty"))
        self.writeto("parent/subdir/foo.txt", "foo")
        self.writeto("parent/subdir/bar.txt", "bar\n" * 1000)
        self.writeto("parent/blah.txt", "blah")

        def do_backup(verbose=False):
            cmd = ["backup"]
            if verbose:
                cmd.append("--verbose")
            cmd.append(source)
            cmd.append("tahoe:backups")
            return self.do_cli(*cmd)

        d = self.do_cli("create-alias", "tahoe")

        if not have_bdb:
            d.addCallback(lambda res: self.do_cli("backup", source, "tahoe:backups"))
            def _should_complain((rc, out, err)):
                self.failUnless("I was unable to import a python sqlite library" in err, err)
            d.addCallback(_should_complain)
            d.addCallback(self.stall, 1.1) # make sure the backups get distinct timestamps

        d.addCallback(lambda res: do_backup())
        def _check0((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            fu, fr, dc, dr = self.count_output(out)
            # foo.txt, bar.txt, blah.txt
            self.failUnlessEqual(fu, 3)
            self.failUnlessEqual(fr, 0)
            # empty, home, home/parent, home/parent/subdir
            self.failUnlessEqual(dc, 4)
            self.failUnlessEqual(dr, 0)
        d.addCallback(_check0)

        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups"))
        def _check1((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(sorted(out.split()), ["Archives", "Latest"])
        d.addCallback(_check1)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Latest"))
        def _check2((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(sorted(out.split()), ["empty", "parent"])
        d.addCallback(_check2)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Latest/empty"))
        def _check2a((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out.strip(), "")
        d.addCallback(_check2a)
        d.addCallback(lambda res: self.do_cli("get", "tahoe:backups/Latest/parent/subdir/foo.txt"))
        def _check3((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out, "foo")
        d.addCallback(_check3)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Archives"))
        def _check4((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.old_archives = out.split()
            self.failUnlessEqual(len(self.old_archives), 1)
        d.addCallback(_check4)


        d.addCallback(self.stall, 1.1)
        d.addCallback(lambda res: do_backup())
        def _check4a((rc, out, err)):
            # second backup should reuse everything, if the backupdb is
            # available
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            if have_bdb:
                fu, fr, dc, dr = self.count_output(out)
                # foo.txt, bar.txt, blah.txt
                self.failUnlessEqual(fu, 0)
                self.failUnlessEqual(fr, 3)
                # empty, home, home/parent, home/parent/subdir
                self.failUnlessEqual(dc, 0)
                self.failUnlessEqual(dr, 4)
        d.addCallback(_check4a)

        if have_bdb:
            # sneak into the backupdb, crank back the "last checked"
            # timestamp to force a check on all files
            def _reset_last_checked(res):
                dbfile = os.path.join(self.get_clientdir(),
                                      "private", "backupdb.sqlite")
                self.failUnless(os.path.exists(dbfile), dbfile)
                bdb = backupdb.get_backupdb(dbfile)
                bdb.cursor.execute("UPDATE last_upload SET last_checked=0")
                bdb.connection.commit()

            d.addCallback(_reset_last_checked)

            d.addCallback(self.stall, 1.1)
            d.addCallback(lambda res: do_backup(verbose=True))
            def _check4b((rc, out, err)):
                # we should check all files, and re-use all of them. None of
                # the directories should have been changed.
                self.failUnlessEqual(err, "")
                self.failUnlessEqual(rc, 0)
                fu, fr, dc, dr = self.count_output(out)
                fchecked, dchecked, dread = self.count_output2(out)
                self.failUnlessEqual(fchecked, 3)
                self.failUnlessEqual(fu, 0)
                self.failUnlessEqual(fr, 3)
                # TODO: backupdb doesn't do dirs yet; when it does, this will
                # change to dchecked=4, and maybe dread=0
                self.failUnlessEqual(dchecked, 0)
                self.failUnlessEqual(dread, 4)
                self.failUnlessEqual(dc, 0)
                self.failUnlessEqual(dr, 4)
            d.addCallback(_check4b)

        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Archives"))
        def _check5((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.new_archives = out.split()
            expected_new = 2
            if have_bdb:
                expected_new += 1
            self.failUnlessEqual(len(self.new_archives), expected_new, out)
            # the original backup should still be the oldest (i.e. sorts
            # alphabetically towards the beginning)
            self.failUnlessEqual(sorted(self.new_archives)[0],
                                 self.old_archives[0])
        d.addCallback(_check5)

        d.addCallback(self.stall, 1.1)
        def _modify(res):
            self.writeto("parent/subdir/foo.txt", "FOOF!")
            # and turn a file into a directory
            os.unlink(os.path.join(source, "parent/blah.txt"))
            os.mkdir(os.path.join(source, "parent/blah.txt"))
            self.writeto("parent/blah.txt/surprise file", "surprise")
            self.writeto("parent/blah.txt/surprisedir/subfile", "surprise")
            # turn a directory into a file
            os.rmdir(os.path.join(source, "empty"))
            self.writeto("empty", "imagine nothing being here")
            return do_backup()
        d.addCallback(_modify)
        def _check5a((rc, out, err)):
            # second backup should reuse bar.txt (if backupdb is available),
            # and upload the rest. None of the directories can be reused.
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            if have_bdb:
                fu, fr, dc, dr = self.count_output(out)
                # new foo.txt, surprise file, subfile, empty
                self.failUnlessEqual(fu, 4)
                # old bar.txt
                self.failUnlessEqual(fr, 1)
                # home, parent, subdir, blah.txt, surprisedir
                self.failUnlessEqual(dc, 5)
                self.failUnlessEqual(dr, 0)
        d.addCallback(_check5a)
        d.addCallback(lambda res: self.do_cli("ls", "tahoe:backups/Archives"))
        def _check6((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.new_archives = out.split()
            expected_new = 3
            if have_bdb:
                expected_new += 1
            self.failUnlessEqual(len(self.new_archives), expected_new)
            self.failUnlessEqual(sorted(self.new_archives)[0],
                                 self.old_archives[0])
        d.addCallback(_check6)
        d.addCallback(lambda res: self.do_cli("get", "tahoe:backups/Latest/parent/subdir/foo.txt"))
        def _check7((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out, "FOOF!")
            # the old snapshot should not be modified
            return self.do_cli("get", "tahoe:backups/Archives/%s/parent/subdir/foo.txt" % self.old_archives[0])
        d.addCallback(_check7)
        def _check8((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(out, "foo")
        d.addCallback(_check8)

        return d

    # on our old dapper buildslave, this test takes a long time (usually
    # 130s), so we have to bump up the default 120s timeout. The create-alias
    # and initial backup alone take 60s, probably because of the handful of
    # dirnodes being created (RSA key generation). The backup between check4
    # and check4a takes 6s, as does the backup before check4b.
    test_backup.timeout = 3000

    def test_exclude_options(self):
        root_listdir = ('lib.a', '_darcs', 'subdir', 'nice_doc.lyx')
        subdir_listdir = ('another_doc.lyx', 'run_snake_run.py', 'CVS', '.svn', '_darcs')
        basedir = "cli/Backup/exclude_options"
        fileutil.make_dirs(basedir)
        nodeurl_path = os.path.join(basedir, 'node.url')
        nodeurl = file(nodeurl_path, 'w')
        nodeurl.write('http://example.net:2357/')
        nodeurl.close()

        def _check_filtering(filtered, all, included, excluded):
            filtered = set(filtered)
            all = set(all)
            included = set(included)
            excluded = set(excluded)
            self.failUnlessEqual(filtered, included)
            self.failUnlessEqual(all.difference(filtered), excluded)

        # test simple exclude
        backup_options = cli.BackupOptions()
        backup_options.parseOptions(['--exclude', '*lyx', '--node-directory',
                                     basedir, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(root_listdir))
        _check_filtering(filtered, root_listdir, ('lib.a', '_darcs', 'subdir'),
                         ('nice_doc.lyx',))
        # multiple exclude
        backup_options = cli.BackupOptions()
        backup_options.parseOptions(['--exclude', '*lyx', '--exclude', 'lib.?', '--node-directory',
                                     basedir, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(root_listdir))
        _check_filtering(filtered, root_listdir, ('_darcs', 'subdir'),
                         ('nice_doc.lyx', 'lib.a'))
        # vcs metadata exclusion
        backup_options = cli.BackupOptions()
        backup_options.parseOptions(['--exclude-vcs', '--node-directory',
                                     basedir, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(subdir_listdir))
        _check_filtering(filtered, subdir_listdir, ('another_doc.lyx', 'run_snake_run.py',),
                         ('CVS', '.svn', '_darcs'))
        # read exclude patterns from file
        exclusion_string = "_darcs\n*py\n.svn"
        excl_filepath = os.path.join(basedir, 'exclusion')
        excl_file = file(excl_filepath, 'w')
        excl_file.write(exclusion_string)
        excl_file.close()
        backup_options = cli.BackupOptions()
        backup_options.parseOptions(['--exclude-from', excl_filepath, '--node-directory',
                                     basedir, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(subdir_listdir))
        _check_filtering(filtered, subdir_listdir, ('another_doc.lyx', 'CVS'),
                         ('.svn', '_darcs', 'run_snake_run.py'))
        # text BackupConfigurationError
        self.failUnlessRaises(cli.BackupConfigurationError,
                              backup_options.parseOptions,
                              ['--exclude-from', excl_filepath + '.no', '--node-directory',
                               basedir, 'from', 'to'])

        # test that an iterator works too
        backup_options = cli.BackupOptions()
        backup_options.parseOptions(['--exclude', '*lyx', '--node-directory',
                                     basedir, 'from', 'to'])
        filtered = list(backup_options.filter_listdir(iter(root_listdir)))
        _check_filtering(filtered, root_listdir, ('lib.a', '_darcs', 'subdir'),
                         ('nice_doc.lyx',))

class Check(GridTestMixin, CLITestMixin, unittest.TestCase):

    def test_check(self):
        self.basedir = "cli/Check/check"
        self.set_up_grid()
        c0 = self.g.clients[0]
        DATA = "data" * 100
        d = c0.create_mutable_file(DATA)
        def _stash_uri(n):
            self.uri = n.get_uri()
        d.addCallback(_stash_uri)

        d.addCallback(lambda ign: self.do_cli("check", self.uri))
        def _check1((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("Summary: Healthy" in lines, out)
            self.failUnless(" good-shares: 10 (encoding is 3-of-10)" in lines, out)
        d.addCallback(_check1)

        d.addCallback(lambda ign: self.do_cli("check", "--raw", self.uri))
        def _check2((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            data = simplejson.loads(out)
            self.failUnlessEqual(data["summary"], "Healthy")
        d.addCallback(_check2)

        def _clobber_shares(ignored):
            # delete one, corrupt a second
            shares = self.find_shares(self.uri)
            self.failUnlessEqual(len(shares), 10)
            os.unlink(shares[0][2])
            cso = debug.CorruptShareOptions()
            cso.stdout = StringIO()
            cso.parseOptions([shares[1][2]])
            storage_index = uri.from_string(self.uri).get_storage_index()
            self._corrupt_share_line = "  server %s, SI %s, shnum %d" % \
                                       (base32.b2a(shares[1][1]),
                                        base32.b2a(storage_index),
                                        shares[1][0])
            debug.corrupt_share(cso)
        d.addCallback(_clobber_shares)

        d.addCallback(lambda ign: self.do_cli("check", "--verify", self.uri))
        def _check3((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            summary = [l for l in lines if l.startswith("Summary")][0]
            self.failUnless("Summary: Unhealthy: 8 shares (enc 3-of-10)"
                            in summary, summary)
            self.failUnless(" good-shares: 8 (encoding is 3-of-10)" in lines, out)
            self.failUnless(" corrupt shares:" in lines, out)
            self.failUnless(self._corrupt_share_line in lines, out)
        d.addCallback(_check3)

        d.addCallback(lambda ign:
                      self.do_cli("check", "--verify", "--repair", self.uri))
        def _check4((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("Summary: not healthy" in lines, out)
            self.failUnless(" good-shares: 8 (encoding is 3-of-10)" in lines, out)
            self.failUnless(" corrupt shares:" in lines, out)
            self.failUnless(self._corrupt_share_line in lines, out)
            self.failUnless(" repair successful" in lines, out)
        d.addCallback(_check4)

        d.addCallback(lambda ign:
                      self.do_cli("check", "--verify", "--repair", self.uri))
        def _check5((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("Summary: healthy" in lines, out)
            self.failUnless(" good-shares: 10 (encoding is 3-of-10)" in lines, out)
            self.failIf(" corrupt shares:" in lines, out)
        d.addCallback(_check5)

        return d

    def test_deep_check(self):
        self.basedir = "cli/Check/deep_check"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.uris = {}
        self.fileurls = {}
        DATA = "data" * 100
        d = c0.create_empty_dirnode()
        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.rooturi = n.get_uri()
            return n.add_file(u"good", upload.Data(DATA, convergence=""))
        d.addCallback(_stash_root_and_create_file)
        def _stash_uri(fn, which):
            self.uris[which] = fn.get_uri()
            return fn
        d.addCallback(_stash_uri, "good")
        d.addCallback(lambda ign:
                      self.rootnode.add_file(u"small",
                                             upload.Data("literal",
                                                        convergence="")))
        d.addCallback(_stash_uri, "small")
        d.addCallback(lambda ign: c0.create_mutable_file(DATA+"1"))
        d.addCallback(lambda fn: self.rootnode.set_node(u"mutable", fn))
        d.addCallback(_stash_uri, "mutable")

        d.addCallback(lambda ign: self.do_cli("deep-check", self.rooturi))
        def _check1((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("done: 4 objects checked, 4 healthy, 0 unhealthy"
                            in lines, out)
        d.addCallback(_check1)

        # root
        # root/good
        # root/small
        # root/mutable

        d.addCallback(lambda ign: self.do_cli("deep-check", "--verbose",
                                              self.rooturi))
        def _check2((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("<root>: Healthy" in lines, out)
            self.failUnless("small: Healthy (LIT)" in lines, out)
            self.failUnless("good: Healthy" in lines, out)
            self.failUnless("mutable: Healthy" in lines, out)
            self.failUnless("done: 4 objects checked, 4 healthy, 0 unhealthy"
                            in lines, out)
        d.addCallback(_check2)

        def _clobber_shares(ignored):
            shares = self.find_shares(self.uris["good"])
            self.failUnlessEqual(len(shares), 10)
            os.unlink(shares[0][2])

            shares = self.find_shares(self.uris["mutable"])
            cso = debug.CorruptShareOptions()
            cso.stdout = StringIO()
            cso.parseOptions([shares[1][2]])
            storage_index = uri.from_string(self.uris["mutable"]).get_storage_index()
            self._corrupt_share_line = " corrupt: server %s, SI %s, shnum %d" % \
                                       (base32.b2a(shares[1][1]),
                                        base32.b2a(storage_index),
                                        shares[1][0])
            debug.corrupt_share(cso)
        d.addCallback(_clobber_shares)

        # root
        # root/good  [9 shares]
        # root/small
        # root/mutable [1 corrupt share]

        d.addCallback(lambda ign:
                      self.do_cli("deep-check", "--verbose", self.rooturi))
        def _check3((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("<root>: Healthy" in lines, out)
            self.failUnless("small: Healthy (LIT)" in lines, out)
            self.failUnless("mutable: Healthy" in lines, out) # needs verifier
            self.failUnless("good: Not Healthy: 9 shares (enc 3-of-10)"
                            in lines, out)
            self.failIf(self._corrupt_share_line in lines, out)
            self.failUnless("done: 4 objects checked, 3 healthy, 1 unhealthy"
                            in lines, out)
        d.addCallback(_check3)

        d.addCallback(lambda ign:
                      self.do_cli("deep-check", "--verbose", "--verify",
                                  self.rooturi))
        def _check4((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("<root>: Healthy" in lines, out)
            self.failUnless("small: Healthy (LIT)" in lines, out)
            mutable = [l for l in lines if l.startswith("mutable")][0]
            self.failUnless(mutable.startswith("mutable: Unhealthy: 9 shares (enc 3-of-10)"),
                            mutable)
            self.failUnless(self._corrupt_share_line in lines, out)
            self.failUnless("good: Not Healthy: 9 shares (enc 3-of-10)"
                            in lines, out)
            self.failUnless("done: 4 objects checked, 2 healthy, 2 unhealthy"
                            in lines, out)
        d.addCallback(_check4)

        d.addCallback(lambda ign:
                      self.do_cli("deep-check", "--raw",
                                  self.rooturi))
        def _check5((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            units = [simplejson.loads(line) for line in lines]
            # root, small, good, mutable,  stats
            self.failUnlessEqual(len(units), 4+1)
        d.addCallback(_check5)

        d.addCallback(lambda ign:
                      self.do_cli("deep-check",
                                  "--verbose", "--verify", "--repair",
                                  self.rooturi))
        def _check6((rc, out, err)):
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            lines = out.splitlines()
            self.failUnless("<root>: healthy" in lines, out)
            self.failUnless("small: healthy" in lines, out)
            self.failUnless("mutable: not healthy" in lines, out)
            self.failUnless(self._corrupt_share_line in lines, out)
            self.failUnless("good: not healthy" in lines, out)
            self.failUnless("done: 4 objects checked" in lines, out)
            self.failUnless(" pre-repair: 2 healthy, 2 unhealthy" in lines, out)
            self.failUnless(" 2 repairs attempted, 2 successful, 0 failed"
                            in lines, out)
            self.failUnless(" post-repair: 4 healthy, 0 unhealthy" in lines,out)
        d.addCallback(_check6)

        # now add a subdir, and a file below that, then make the subdir
        # unrecoverable

        d.addCallback(lambda ign:
                      self.rootnode.create_empty_directory(u"subdir"))
        d.addCallback(_stash_uri, "subdir")
        d.addCallback(lambda fn:
                      fn.add_file(u"subfile", upload.Data(DATA+"2", "")))
        d.addCallback(lambda ign:
                      self.delete_shares_numbered(self.uris["subdir"],
                                                  range(10)))

        # root
        # root/good
        # root/small
        # root/mutable
        # root/subdir [unrecoverable: 0 shares]
        # root/subfile

        d.addCallback(lambda ign: self.do_cli("manifest", self.rooturi))
        def _manifest_failed((rc, out, err)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("ERROR: UnrecoverableFileError", err)
            # the fatal directory should still show up, as the last line
            self.failUnlessIn(" subdir\n", out)
        d.addCallback(_manifest_failed)

        d.addCallback(lambda ign: self.do_cli("deep-check", self.rooturi))
        def _deep_check_failed((rc, out, err)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("ERROR: UnrecoverableFileError", err)
            # we want to make sure that the error indication is the last
            # thing that gets emitted
            self.failIf("done:" in out, out)
        d.addCallback(_deep_check_failed)

        # this test is disabled until the deep-repair response to an
        # unrepairable directory is fixed. The failure-to-repair should not
        # throw an exception, but the failure-to-traverse that follows
        # should throw UnrecoverableFileError.

        #d.addCallback(lambda ign:
        #              self.do_cli("deep-check", "--repair", self.rooturi))
        #def _deep_check_repair_failed((rc, out, err)):
        #    self.failIfEqual(rc, 0)
        #    print err
        #    self.failUnlessIn("ERROR: UnrecoverableFileError", err)
        #    self.failIf("done:" in out, out)
        #d.addCallback(_deep_check_repair_failed)

        return d

class Errors(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_check(self):
        self.basedir = "cli/Check/check"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.fileurls = {}
        DATA = "data" * 100
        d = c0.upload(upload.Data(DATA, convergence=""))
        def _stash_bad(ur):
            self.uri_1share = ur.uri
            self.delete_shares_numbered(ur.uri, range(1,10))
        d.addCallback(_stash_bad)

        d.addCallback(lambda ign: self.do_cli("get", self.uri_1share))
        def _check1((rc, out, err)):
            self.failIfEqual(rc, 0)
            self.failUnless("410 Gone" in err, err)
            self.failUnless("NotEnoughSharesError: 1 share found, but we need 3" in err,
                            err)
        d.addCallback(_check1)

        return d
