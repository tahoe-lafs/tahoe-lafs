
import os.path
from cStringIO import StringIO
import urllib, sys

from twisted.trial import unittest
from twisted.python.monkey import MonkeyPatcher

import allmydata
from allmydata.util import fileutil, hashutil, base32, keyutil
from allmydata.util.namespace import Namespace
from allmydata import uri
from allmydata.immutable import upload
from allmydata.dirnode import normalize
from allmydata.scripts.common_http import socket_error
import allmydata.scripts.common_http
from pycryptopp.publickey import ed25519

# Test that the scripts can be imported.
from allmydata.scripts import create_node, debug, keygen, startstop_node, \
    tahoe_add_alias, tahoe_backup, tahoe_check, tahoe_cp, tahoe_get, tahoe_ls, \
    tahoe_manifest, tahoe_mkdir, tahoe_mv, tahoe_put, tahoe_unlink, tahoe_webopen
_hush_pyflakes = [create_node, debug, keygen, startstop_node,
    tahoe_add_alias, tahoe_backup, tahoe_check, tahoe_cp, tahoe_get, tahoe_ls,
    tahoe_manifest, tahoe_mkdir, tahoe_mv, tahoe_put, tahoe_unlink, tahoe_webopen]

from allmydata.scripts import common
from allmydata.scripts.common import DEFAULT_ALIAS, get_aliases, get_alias, \
     DefaultAliasMarker

from allmydata.scripts import cli, debug, runner
from allmydata.test.common_util import ReallyEqualMixin
from allmydata.test.no_network import GridTestMixin
from twisted.internet import threads # CLI tests use deferToThread
from twisted.python import usage

from allmydata.util.assertutil import precondition
from allmydata.util.encodingutil import listdir_unicode, unicode_platform, \
    get_io_encoding, get_filesystem_encoding

timeout = 480 # deep_check takes 360s on Zandr's linksys box, others take > 240s

def parse_options(basedir, command, args):
    o = runner.Options()
    o.parseOptions(["--node-directory", basedir, command] + args)
    while hasattr(o, "subOptions"):
        o = o.subOptions
    return o

class CLITestMixin(ReallyEqualMixin):
    def do_cli(self, verb, *args, **kwargs):
        nodeargs = [
            "--node-directory", self.get_clientdir(),
            ]
        argv = nodeargs + [verb] + list(args)
        stdin = kwargs.get("stdin", "")
        stdout, stderr = StringIO(), StringIO()
        d = threads.deferToThread(runner.runner, argv, run_by_human=False,
                                  stdin=StringIO(stdin),
                                  stdout=stdout, stderr=stderr)
        def _done(rc):
            return rc, stdout.getvalue(), stderr.getvalue()
        d.addCallback(_done)
        return d

    def skip_if_cannot_represent_filename(self, u):
        precondition(isinstance(u, unicode))

        enc = get_filesystem_encoding()
        if not unicode_platform():
            try:
                u.encode(enc)
            except UnicodeEncodeError:
                raise unittest.SkipTest("A non-ASCII filename could not be encoded on this platform.")


class CLI(CLITestMixin, unittest.TestCase):
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
        self.failUnless("data: 'this is some data'" in output, output)

    def test_dump_cap_sdmf(self):
        writekey = "\x01" * 16
        fingerprint = "\xfe" * 32
        u = uri.WriteableSSKFileURI(writekey, fingerprint)

        output = self._dump_cap(u.to_string())
        self.failUnless("SDMF Writeable URI:" in output, output)
        self.failUnless("writekey: aeaqcaibaeaqcaibaeaqcaibae" in output, output)
        self.failUnless("readkey: nvgh5vj2ekzzkim5fgtb4gey5y" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        output = self._dump_cap("--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("file renewal secret: arpszxzc2t6kb4okkg7sp765xgkni5z7caavj7lta73vmtymjlxq" in output, output)

        fileutil.make_dirs("cli/test_dump_cap/private")
        fileutil.write("cli/test_dump_cap/private/secret", "5s33nk3qpvnj2fw3z4mnm2y6fa\n")
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
        self.failUnless("SDMF Read-only URI:" in output, output)
        self.failUnless("readkey: nvgh5vj2ekzzkim5fgtb4gey5y" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        u = u.get_verify_cap()
        output = self._dump_cap(u.to_string())
        self.failUnless("SDMF Verifier URI:" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

    def test_dump_cap_mdmf(self):
        writekey = "\x01" * 16
        fingerprint = "\xfe" * 32
        u = uri.WriteableMDMFFileURI(writekey, fingerprint)

        output = self._dump_cap(u.to_string())
        self.failUnless("MDMF Writeable URI:" in output, output)
        self.failUnless("writekey: aeaqcaibaeaqcaibaeaqcaibae" in output, output)
        self.failUnless("readkey: nvgh5vj2ekzzkim5fgtb4gey5y" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        output = self._dump_cap("--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("file renewal secret: arpszxzc2t6kb4okkg7sp765xgkni5z7caavj7lta73vmtymjlxq" in output, output)

        fileutil.make_dirs("cli/test_dump_cap/private")
        fileutil.write("cli/test_dump_cap/private/secret", "5s33nk3qpvnj2fw3z4mnm2y6fa\n")
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
        self.failUnless("MDMF Read-only URI:" in output, output)
        self.failUnless("readkey: nvgh5vj2ekzzkim5fgtb4gey5y" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        u = u.get_verify_cap()
        output = self._dump_cap(u.to_string())
        self.failUnless("MDMF Verifier URI:" in output, output)
        self.failUnless("storage index: nt4fwemuw7flestsezvo2eveke" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)


    def test_dump_cap_chk_directory(self):
        key = "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        uri_extension_hash = hashutil.uri_extension_hash("stuff")
        needed_shares = 25
        total_shares = 100
        size = 1234
        u1 = uri.CHKFileURI(key=key,
                            uri_extension_hash=uri_extension_hash,
                            needed_shares=needed_shares,
                            total_shares=total_shares,
                            size=size)
        u = uri.ImmutableDirectoryURI(u1)

        output = self._dump_cap(u.to_string())
        self.failUnless("CHK Directory URI:" in output, output)
        self.failUnless("key: aaaqeayeaudaocajbifqydiob4" in output, output)
        self.failUnless("UEB hash: nf3nimquen7aeqm36ekgxomalstenpkvsdmf6fplj7swdatbv5oa" in output, output)
        self.failUnless("size: 1234" in output, output)
        self.failUnless("k/N: 25/100" in output, output)
        self.failUnless("storage index: hdis5iaveku6lnlaiccydyid7q" in output, output)

        output = self._dump_cap("--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("file renewal secret: csrvkjgomkyyyil5yo4yk5np37p6oa2ve2hg6xmk2dy7kaxsu6xq" in output, output)

        u = u.get_verify_cap()
        output = self._dump_cap(u.to_string())
        self.failUnless("CHK Directory Verifier URI:" in output, output)
        self.failIf("key: " in output, output)
        self.failUnless("UEB hash: nf3nimquen7aeqm36ekgxomalstenpkvsdmf6fplj7swdatbv5oa" in output, output)
        self.failUnless("size: 1234" in output, output)
        self.failUnless("k/N: 25/100" in output, output)
        self.failUnless("storage index: hdis5iaveku6lnlaiccydyid7q" in output, output)

    def test_dump_cap_sdmf_directory(self):
        writekey = "\x01" * 16
        fingerprint = "\xfe" * 32
        u1 = uri.WriteableSSKFileURI(writekey, fingerprint)
        u = uri.DirectoryURI(u1)

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

    def test_dump_cap_mdmf_directory(self):
        writekey = "\x01" * 16
        fingerprint = "\xfe" * 32
        u1 = uri.WriteableMDMFFileURI(writekey, fingerprint)
        u = uri.MDMFDirectoryURI(u1)

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
        fileutil.write("cli/test_catalog_shares/node1/storage/shares/mq/not-a-dir", "")
        # write a bogus share that looks a little bit like CHK
        fileutil.write(os.path.join(sharedir, "8"),
                       "\x00\x00\x00\x01" + "\xff" * 200) # this triggers an assert

        nodedir2 = "cli/test_catalog_shares/node2"
        fileutil.make_dirs(nodedir2)
        fileutil.write("cli/test_catalog_shares/node1/storage/shares/not-a-dir", "")

        # now make sure that the 'catalog-shares' commands survives the error
        out, err = self._catalog_shares(nodedir1, nodedir2)
        self.failUnlessReallyEqual(out, "", out)
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
        def s128(c): return base32.b2a(c*(128/8))
        def s256(c): return base32.b2a(c*(256/8))
        TA = "URI:DIR2:%s:%s" % (s128("T"), s256("T"))
        WA = "URI:DIR2:%s:%s" % (s128("W"), s256("W"))
        CA = "URI:DIR2:%s:%s" % (s128("C"), s256("C"))
        aliases = {"tahoe": TA,
                   "work": WA,
                   "c": CA}
        def ga1(path):
            return get_alias(aliases, path, u"tahoe")
        uses_lettercolon = common.platform_uses_lettercolon_drivename()
        self.failUnlessReallyEqual(ga1(u"bare"), (TA, "bare"))
        self.failUnlessReallyEqual(ga1(u"baredir/file"), (TA, "baredir/file"))
        self.failUnlessReallyEqual(ga1(u"baredir/file:7"), (TA, "baredir/file:7"))
        self.failUnlessReallyEqual(ga1(u"tahoe:"), (TA, ""))
        self.failUnlessReallyEqual(ga1(u"tahoe:file"), (TA, "file"))
        self.failUnlessReallyEqual(ga1(u"tahoe:dir/file"), (TA, "dir/file"))
        self.failUnlessReallyEqual(ga1(u"work:"), (WA, ""))
        self.failUnlessReallyEqual(ga1(u"work:file"), (WA, "file"))
        self.failUnlessReallyEqual(ga1(u"work:dir/file"), (WA, "dir/file"))
        # default != None means we really expect a tahoe path, regardless of
        # whether we're on windows or not. This is what 'tahoe get' uses.
        self.failUnlessReallyEqual(ga1(u"c:"), (CA, ""))
        self.failUnlessReallyEqual(ga1(u"c:file"), (CA, "file"))
        self.failUnlessReallyEqual(ga1(u"c:dir/file"), (CA, "dir/file"))
        self.failUnlessReallyEqual(ga1(u"URI:stuff"), ("URI:stuff", ""))
        self.failUnlessReallyEqual(ga1(u"URI:stuff/file"), ("URI:stuff", "file"))
        self.failUnlessReallyEqual(ga1(u"URI:stuff:./file"), ("URI:stuff", "file"))
        self.failUnlessReallyEqual(ga1(u"URI:stuff/dir/file"), ("URI:stuff", "dir/file"))
        self.failUnlessReallyEqual(ga1(u"URI:stuff:./dir/file"), ("URI:stuff", "dir/file"))
        self.failUnlessRaises(common.UnknownAliasError, ga1, u"missing:")
        self.failUnlessRaises(common.UnknownAliasError, ga1, u"missing:dir")
        self.failUnlessRaises(common.UnknownAliasError, ga1, u"missing:dir/file")

        def ga2(path):
            return get_alias(aliases, path, None)
        self.failUnlessReallyEqual(ga2(u"bare"), (DefaultAliasMarker, "bare"))
        self.failUnlessReallyEqual(ga2(u"baredir/file"),
                             (DefaultAliasMarker, "baredir/file"))
        self.failUnlessReallyEqual(ga2(u"baredir/file:7"),
                             (DefaultAliasMarker, "baredir/file:7"))
        self.failUnlessReallyEqual(ga2(u"baredir/sub:1/file:7"),
                             (DefaultAliasMarker, "baredir/sub:1/file:7"))
        self.failUnlessReallyEqual(ga2(u"tahoe:"), (TA, ""))
        self.failUnlessReallyEqual(ga2(u"tahoe:file"), (TA, "file"))
        self.failUnlessReallyEqual(ga2(u"tahoe:dir/file"), (TA, "dir/file"))
        # on windows, we really want c:foo to indicate a local file.
        # default==None is what 'tahoe cp' uses.
        if uses_lettercolon:
            self.failUnlessReallyEqual(ga2(u"c:"), (DefaultAliasMarker, "c:"))
            self.failUnlessReallyEqual(ga2(u"c:file"), (DefaultAliasMarker, "c:file"))
            self.failUnlessReallyEqual(ga2(u"c:dir/file"),
                                 (DefaultAliasMarker, "c:dir/file"))
        else:
            self.failUnlessReallyEqual(ga2(u"c:"), (CA, ""))
            self.failUnlessReallyEqual(ga2(u"c:file"), (CA, "file"))
            self.failUnlessReallyEqual(ga2(u"c:dir/file"), (CA, "dir/file"))
        self.failUnlessReallyEqual(ga2(u"work:"), (WA, ""))
        self.failUnlessReallyEqual(ga2(u"work:file"), (WA, "file"))
        self.failUnlessReallyEqual(ga2(u"work:dir/file"), (WA, "dir/file"))
        self.failUnlessReallyEqual(ga2(u"URI:stuff"), ("URI:stuff", ""))
        self.failUnlessReallyEqual(ga2(u"URI:stuff/file"), ("URI:stuff", "file"))
        self.failUnlessReallyEqual(ga2(u"URI:stuff:./file"), ("URI:stuff", "file"))
        self.failUnlessReallyEqual(ga2(u"URI:stuff/dir/file"), ("URI:stuff", "dir/file"))
        self.failUnlessReallyEqual(ga2(u"URI:stuff:./dir/file"), ("URI:stuff", "dir/file"))
        self.failUnlessRaises(common.UnknownAliasError, ga2, u"missing:")
        self.failUnlessRaises(common.UnknownAliasError, ga2, u"missing:dir")
        self.failUnlessRaises(common.UnknownAliasError, ga2, u"missing:dir/file")

        def ga3(path):
            old = common.pretend_platform_uses_lettercolon
            try:
                common.pretend_platform_uses_lettercolon = True
                retval = get_alias(aliases, path, None)
            finally:
                common.pretend_platform_uses_lettercolon = old
            return retval
        self.failUnlessReallyEqual(ga3(u"bare"), (DefaultAliasMarker, "bare"))
        self.failUnlessReallyEqual(ga3(u"baredir/file"),
                             (DefaultAliasMarker, "baredir/file"))
        self.failUnlessReallyEqual(ga3(u"baredir/file:7"),
                             (DefaultAliasMarker, "baredir/file:7"))
        self.failUnlessReallyEqual(ga3(u"baredir/sub:1/file:7"),
                             (DefaultAliasMarker, "baredir/sub:1/file:7"))
        self.failUnlessReallyEqual(ga3(u"tahoe:"), (TA, ""))
        self.failUnlessReallyEqual(ga3(u"tahoe:file"), (TA, "file"))
        self.failUnlessReallyEqual(ga3(u"tahoe:dir/file"), (TA, "dir/file"))
        self.failUnlessReallyEqual(ga3(u"c:"), (DefaultAliasMarker, "c:"))
        self.failUnlessReallyEqual(ga3(u"c:file"), (DefaultAliasMarker, "c:file"))
        self.failUnlessReallyEqual(ga3(u"c:dir/file"),
                             (DefaultAliasMarker, "c:dir/file"))
        self.failUnlessReallyEqual(ga3(u"work:"), (WA, ""))
        self.failUnlessReallyEqual(ga3(u"work:file"), (WA, "file"))
        self.failUnlessReallyEqual(ga3(u"work:dir/file"), (WA, "dir/file"))
        self.failUnlessReallyEqual(ga3(u"URI:stuff"), ("URI:stuff", ""))
        self.failUnlessReallyEqual(ga3(u"URI:stuff:./file"), ("URI:stuff", "file"))
        self.failUnlessReallyEqual(ga3(u"URI:stuff:./dir/file"), ("URI:stuff", "dir/file"))
        self.failUnlessRaises(common.UnknownAliasError, ga3, u"missing:")
        self.failUnlessRaises(common.UnknownAliasError, ga3, u"missing:dir")
        self.failUnlessRaises(common.UnknownAliasError, ga3, u"missing:dir/file")
        # calling get_alias with a path that doesn't include an alias and
        # default set to something that isn't in the aliases argument should
        # raise an UnknownAliasError.
        def ga4(path):
            return get_alias(aliases, path, u"badddefault:")
        self.failUnlessRaises(common.UnknownAliasError, ga4, u"afile")
        self.failUnlessRaises(common.UnknownAliasError, ga4, u"a/dir/path/")

        def ga5(path):
            old = common.pretend_platform_uses_lettercolon
            try:
                common.pretend_platform_uses_lettercolon = True
                retval = get_alias(aliases, path, u"baddefault:")
            finally:
                common.pretend_platform_uses_lettercolon = old
            return retval
        self.failUnlessRaises(common.UnknownAliasError, ga5, u"C:\\Windows")

    def test_alias_tolerance(self):
        def s128(c): return base32.b2a(c*(128/8))
        def s256(c): return base32.b2a(c*(256/8))
        TA = "URI:DIR2:%s:%s" % (s128("T"), s256("T"))
        aliases = {"present": TA,
                   "future": "URI-FROM-FUTURE:ooh:aah"}
        def ga1(path):
            return get_alias(aliases, path, u"tahoe")
        self.failUnlessReallyEqual(ga1(u"present:file"), (TA, "file"))
        # this throws, via assert IDirnodeURI.providedBy(), since get_alias()
        # wants a dirnode, and the future cap gives us UnknownURI instead.
        self.failUnlessRaises(AssertionError, ga1, u"future:stuff")

    def test_listdir_unicode_good(self):
        filenames = [u'L\u00F4zane', u'Bern', u'Gen\u00E8ve']  # must be NFC

        for name in filenames:
            self.skip_if_cannot_represent_filename(name)

        basedir = "cli/common/listdir_unicode_good"
        fileutil.make_dirs(basedir)

        for name in filenames:
            open(os.path.join(unicode(basedir), name), "wb").close()

        for file in listdir_unicode(unicode(basedir)):
            self.failUnlessIn(normalize(file), filenames)

    def test_exception_catcher(self):
        self.basedir = "cli/exception_catcher"

        stderr = StringIO()
        exc = Exception("canary")
        ns = Namespace()

        ns.runner_called = False
        def call_runner(args, install_node_control=True):
            ns.runner_called = True
            self.failUnlessEqual(install_node_control, True)
            raise exc

        ns.sys_exit_called = False
        def call_sys_exit(exitcode):
            ns.sys_exit_called = True
            self.failUnlessEqual(exitcode, 1)

        patcher = MonkeyPatcher((runner, 'runner', call_runner),
                                (sys, 'argv', ["tahoe"]),
                                (sys, 'exit', call_sys_exit),
                                (sys, 'stderr', stderr))
        patcher.runWithPatches(runner.run)

        self.failUnless(ns.runner_called)
        self.failUnless(ns.sys_exit_called)
        self.failUnlessIn(str(exc), stderr.getvalue())


class Help(unittest.TestCase):
    def failUnlessInNormalized(self, x, y):
        # helper function to deal with the --help output being wrapped to
        # various widths, depending on the $COLUMNS environment variable
        self.failUnlessIn(x.replace("\n", " "), y.replace("\n", " "))

    def test_get(self):
        help = str(cli.GetOptions())
        self.failUnlessIn("[options] REMOTE_FILE LOCAL_FILE", help)
        self.failUnlessIn("% tahoe get FOO |less", help)

    def test_put(self):
        help = str(cli.PutOptions())
        self.failUnlessIn("[options] LOCAL_FILE REMOTE_FILE", help)
        self.failUnlessIn("% cat FILE | tahoe put", help)

    def test_ls(self):
        help = str(cli.ListOptions())
        self.failUnlessIn("[options] [PATH]", help)

    def test_unlink(self):
        help = str(cli.UnlinkOptions())
        self.failUnlessIn("[options] REMOTE_FILE", help)

    def test_rm(self):
        help = str(cli.RmOptions())
        self.failUnlessIn("[options] REMOTE_FILE", help)

    def test_mv(self):
        help = str(cli.MvOptions())
        self.failUnlessIn("[options] FROM TO", help)
        self.failUnlessInNormalized("Use 'tahoe mv' to move files", help)

    def test_cp(self):
        help = str(cli.CpOptions())
        self.failUnlessIn("[options] FROM.. TO", help)
        self.failUnlessInNormalized("Use 'tahoe cp' to copy files", help)

    def test_ln(self):
        help = str(cli.LnOptions())
        self.failUnlessIn("[options] FROM_LINK TO_LINK", help)
        self.failUnlessInNormalized("Use 'tahoe ln' to duplicate a link", help)

    def test_mkdir(self):
        help = str(cli.MakeDirectoryOptions())
        self.failUnlessIn("[options] [REMOTE_DIR]", help)
        self.failUnlessInNormalized("Create a new directory", help)

    def test_backup(self):
        help = str(cli.BackupOptions())
        self.failUnlessIn("[options] FROM ALIAS:TO", help)

    def test_webopen(self):
        help = str(cli.WebopenOptions())
        self.failUnlessIn("[options] [ALIAS:PATH]", help)

    def test_manifest(self):
        help = str(cli.ManifestOptions())
        self.failUnlessIn("[options] [ALIAS:PATH]", help)

    def test_stats(self):
        help = str(cli.StatsOptions())
        self.failUnlessIn("[options] [ALIAS:PATH]", help)

    def test_check(self):
        help = str(cli.CheckOptions())
        self.failUnlessIn("[options] [ALIAS:PATH]", help)

    def test_deep_check(self):
        help = str(cli.DeepCheckOptions())
        self.failUnlessIn("[options] [ALIAS:PATH]", help)

    def test_create_alias(self):
        help = str(cli.CreateAliasOptions())
        self.failUnlessIn("[options] ALIAS[:]", help)

    def test_add_alias(self):
        help = str(cli.AddAliasOptions())
        self.failUnlessIn("[options] ALIAS[:] DIRCAP", help)

    def test_list_aliases(self):
        help = str(cli.ListAliasesOptions())
        self.failUnlessIn("[options]", help)

    def test_start(self):
        help = str(startstop_node.StartOptions())
        self.failUnlessIn("[options] [NODEDIR [twistd-options]]", help)

    def test_stop(self):
        help = str(startstop_node.StopOptions())
        self.failUnlessIn("[options] [NODEDIR]", help)

    def test_restart(self):
        help = str(startstop_node.RestartOptions())
        self.failUnlessIn("[options] [NODEDIR [twistd-options]]", help)

    def test_run(self):
        help = str(startstop_node.RunOptions())
        self.failUnlessIn("[options] [NODEDIR [twistd-options]]", help)

    def test_create_client(self):
        help = str(create_node.CreateClientOptions())
        self.failUnlessIn("[options] [NODEDIR]", help)

    def test_create_node(self):
        help = str(create_node.CreateNodeOptions())
        self.failUnlessIn("[options] [NODEDIR]", help)

    def test_create_introducer(self):
        help = str(create_node.CreateIntroducerOptions())
        self.failUnlessIn("[options] NODEDIR", help)

    def test_debug_trial(self):
        help = str(debug.TrialOptions())
        self.failUnlessIn(" [global-options] debug trial [options] [[file|package|module|TestCase|testmethod]...]", help)
        self.failUnlessInNormalized("The 'tahoe debug trial' command uses the correct imports", help)

    def test_debug_flogtool(self):
        options = debug.FlogtoolOptions()
        help = str(options)
        self.failUnlessIn(" [global-options] debug flogtool ", help)
        self.failUnlessInNormalized("The 'tahoe debug flogtool' command uses the correct imports", help)

        for (option, shortcut, oClass, desc) in options.subCommands:
            subhelp = str(oClass())
            self.failUnlessIn(" [global-options] debug flogtool %s " % (option,), subhelp)


class Ln(GridTestMixin, CLITestMixin, unittest.TestCase):
    def _create_test_file(self):
        data = "puppies" * 1000
        path = os.path.join(self.basedir, "datafile")
        fileutil.write(path, data)
        self.datafile = path

    def test_ln_without_alias(self):
        # if invoked without an alias when the 'tahoe' alias doesn't
        # exist, 'tahoe ln' should output a useful error message and not
        # a stack trace
        self.basedir = "cli/Ln/ln_without_alias"
        self.set_up_grid()
        d = self.do_cli("ln", "from", "to")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        # Make sure that validation extends to the "to" parameter
        d.addCallback(lambda ign: self.do_cli("create-alias", "havasu"))
        d.addCallback(lambda ign: self._create_test_file())
        d.addCallback(lambda ign: self.do_cli("put", self.datafile,
                                              "havasu:from"))
        d.addCallback(lambda ign: self.do_cli("ln", "havasu:from", "to"))
        d.addCallback(_check)
        return d

    def test_ln_with_nonexistent_alias(self):
        # If invoked with aliases that don't exist, 'tahoe ln' should
        # output a useful error message and not a stack trace.
        self.basedir = "cli/Ln/ln_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("ln", "havasu:from", "havasu:to")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
        d.addCallback(_check)
        # Make sure that validation occurs on the to parameter if the
        # from parameter passes.
        d.addCallback(lambda ign: self.do_cli("create-alias", "havasu"))
        d.addCallback(lambda ign: self._create_test_file())
        d.addCallback(lambda ign: self.do_cli("put", self.datafile,
                                              "havasu:from"))
        d.addCallback(lambda ign: self.do_cli("ln", "havasu:from", "huron:to"))
        d.addCallback(_check)
        return d


class Admin(unittest.TestCase):
    def do_cli(self, *args, **kwargs):
        argv = list(args)
        stdin = kwargs.get("stdin", "")
        stdout, stderr = StringIO(), StringIO()
        d = threads.deferToThread(runner.runner, argv, run_by_human=False,
                                  stdin=StringIO(stdin),
                                  stdout=stdout, stderr=stderr)
        def _done(res):
            return stdout.getvalue(), stderr.getvalue()
        d.addCallback(_done)
        return d

    def test_generate_keypair(self):
        d = self.do_cli("admin", "generate-keypair")
        def _done( (stdout, stderr) ):
            lines = [line.strip() for line in stdout.splitlines()]
            privkey_bits = lines[0].split()
            pubkey_bits = lines[1].split()
            sk_header = "private:"
            vk_header = "public:"
            self.failUnlessEqual(privkey_bits[0], sk_header, lines[0])
            self.failUnlessEqual(pubkey_bits[0], vk_header, lines[1])
            self.failUnless(privkey_bits[1].startswith("priv-v0-"), lines[0])
            self.failUnless(pubkey_bits[1].startswith("pub-v0-"), lines[1])
            sk_bytes = base32.a2b(keyutil.remove_prefix(privkey_bits[1], "priv-v0-"))
            sk = ed25519.SigningKey(sk_bytes)
            vk_bytes = base32.a2b(keyutil.remove_prefix(pubkey_bits[1], "pub-v0-"))
            self.failUnlessEqual(sk.get_verifying_key_bytes(), vk_bytes)
        d.addCallback(_done)
        return d

    def test_derive_pubkey(self):
        priv1,pub1 = keyutil.make_keypair()
        d = self.do_cli("admin", "derive-pubkey", priv1)
        def _done( (stdout, stderr) ):
            lines = stdout.split("\n")
            privkey_line = lines[0].strip()
            pubkey_line = lines[1].strip()
            sk_header = "private: priv-v0-"
            vk_header = "public: pub-v0-"
            self.failUnless(privkey_line.startswith(sk_header), privkey_line)
            self.failUnless(pubkey_line.startswith(vk_header), pubkey_line)
            pub2 = pubkey_line[len(vk_header):]
            self.failUnlessEqual("pub-v0-"+pub2, pub1)
        d.addCallback(_done)
        return d


class Errors(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_get(self):
        self.basedir = "cli/Errors/get"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.fileurls = {}
        DATA = "data" * 100
        d = c0.upload(upload.Data(DATA, convergence=""))
        def _stash_bad(ur):
            self.uri_1share = ur.get_uri()
            self.delete_shares_numbered(ur.get_uri(), range(1,10))
        d.addCallback(_stash_bad)

        # the download is abandoned as soon as it's clear that we won't get
        # enough shares. The one remaining share might be in either the
        # COMPLETE or the PENDING state.
        in_complete_msg = "ran out of shares: complete=sh0 pending= overdue= unused= need 3"
        in_pending_msg = "ran out of shares: complete= pending=Share(sh0-on-fob7vqgd) overdue= unused= need 3"

        d.addCallback(lambda ign: self.do_cli("get", self.uri_1share))
        def _check1((rc, out, err)):
            self.failIfEqual(rc, 0)
            self.failUnless("410 Gone" in err, err)
            self.failUnlessIn("NotEnoughSharesError: ", err)
            self.failUnless(in_complete_msg in err or in_pending_msg in err,
                            err)
        d.addCallback(_check1)

        targetf = os.path.join(self.basedir, "output")
        d.addCallback(lambda ign: self.do_cli("get", self.uri_1share, targetf))
        def _check2((rc, out, err)):
            self.failIfEqual(rc, 0)
            self.failUnless("410 Gone" in err, err)
            self.failUnlessIn("NotEnoughSharesError: ", err)
            self.failUnless(in_complete_msg in err or in_pending_msg in err,
                            err)
            self.failIf(os.path.exists(targetf))
        d.addCallback(_check2)

        return d

    def test_broken_socket(self):
        # When the http connection breaks (such as when node.url is overwritten
        # by a confused user), a user friendly error message should be printed.
        self.basedir = "cli/Errors/test_broken_socket"
        self.set_up_grid()

        # Simulate a connection error
        def _socket_error(*args, **kwargs):
            raise socket_error('test error')
        self.patch(allmydata.scripts.common_http.httplib.HTTPConnection,
                   "endheaders", _socket_error)

        d = self.do_cli("mkdir")
        def _check_invalid((rc,stdout,stderr)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("Error trying to connect to http://127.0.0.1", stderr)
        d.addCallback(_check_invalid)
        return d


class Get(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_get_without_alias(self):
        # 'tahoe get' should output a useful error message when invoked
        # without an explicit alias and when the default 'tahoe' alias
        # hasn't been created yet.
        self.basedir = "cli/Get/get_without_alias"
        self.set_up_grid()
        d = self.do_cli('get', 'file')
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d

    def test_get_with_nonexistent_alias(self):
        # 'tahoe get' should output a useful error message when invoked with
        # an explicit alias that doesn't exist.
        self.basedir = "cli/Get/get_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("get", "nonexistent:file")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d


class Manifest(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_manifest_without_alias(self):
        # 'tahoe manifest' should output a useful error message when invoked
        # without an explicit alias when the default 'tahoe' alias is
        # missing.
        self.basedir = "cli/Manifest/manifest_without_alias"
        self.set_up_grid()
        d = self.do_cli("manifest")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d

    def test_manifest_with_nonexistent_alias(self):
        # 'tahoe manifest' should output a useful error message when invoked
        # with an explicit alias that doesn't exist.
        self.basedir = "cli/Manifest/manifest_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("manifest", "nonexistent:")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d


class Mkdir(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_mkdir(self):
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid()

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("mkdir", "test"))
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessIn("URI:", out)
        d.addCallback(_check)

        return d

    def test_mkdir_mutable_type(self):
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid()
        d = self.do_cli("create-alias", "tahoe")
        def _check((rc, out, err), st):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessIn(st, out)
            return out
        def _mkdir(ign, mutable_type, uri_prefix, dirname):
            d2 = self.do_cli("mkdir", "--format="+mutable_type, dirname)
            d2.addCallback(_check, uri_prefix)
            def _stash_filecap(cap):
                u = uri.from_string(cap)
                fn_uri = u.get_filenode_cap()
                self._filecap = fn_uri.to_string()
            d2.addCallback(_stash_filecap)
            d2.addCallback(lambda ign: self.do_cli("ls", "--json", dirname))
            d2.addCallback(_check, uri_prefix)
            d2.addCallback(lambda ign: self.do_cli("ls", "--json", self._filecap))
            d2.addCallback(_check, '"format": "%s"' % (mutable_type.upper(),))
            return d2

        d.addCallback(_mkdir, "sdmf", "URI:DIR2", "tahoe:foo")
        d.addCallback(_mkdir, "SDMF", "URI:DIR2", "tahoe:foo2")
        d.addCallback(_mkdir, "mdmf", "URI:DIR2-MDMF", "tahoe:bar")
        d.addCallback(_mkdir, "MDMF", "URI:DIR2-MDMF", "tahoe:bar2")
        return d

    def test_mkdir_mutable_type_unlinked(self):
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid()
        d = self.do_cli("mkdir", "--format=SDMF")
        def _check((rc, out, err), st):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessIn(st, out)
            return out
        d.addCallback(_check, "URI:DIR2")
        def _stash_dircap(cap):
            self._dircap = cap
            # Now we're going to feed the cap into uri.from_string...
            u = uri.from_string(cap)
            # ...grab the underlying filenode uri.
            fn_uri = u.get_filenode_cap()
            # ...and stash that.
            self._filecap = fn_uri.to_string()
        d.addCallback(_stash_dircap)
        d.addCallback(lambda res: self.do_cli("ls", "--json",
                                              self._filecap))
        d.addCallback(_check, '"format": "SDMF"')
        d.addCallback(lambda res: self.do_cli("mkdir", "--format=MDMF"))
        d.addCallback(_check, "URI:DIR2-MDMF")
        d.addCallback(_stash_dircap)
        d.addCallback(lambda res: self.do_cli("ls", "--json",
                                              self._filecap))
        d.addCallback(_check, '"format": "MDMF"')
        return d

    def test_mkdir_bad_mutable_type(self):
        o = cli.MakeDirectoryOptions()
        self.failUnlessRaises(usage.UsageError,
                              o.parseOptions,
                              ["--format=LDMF"])

    def test_mkdir_unicode(self):
        self.basedir = os.path.dirname(self.mktemp())
        self.set_up_grid()

        try:
            motorhead_arg = u"tahoe:Mot\u00F6rhead".encode(get_io_encoding())
        except UnicodeEncodeError:
            raise unittest.SkipTest("A non-ASCII command argument could not be encoded on this platform.")

        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda res: self.do_cli("mkdir", motorhead_arg))
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessIn("URI:", out)
        d.addCallback(_check)

        return d

    def test_mkdir_with_nonexistent_alias(self):
        # when invoked with an alias that doesn't exist, 'tahoe mkdir' should
        # output a sensible error message rather than a stack trace.
        self.basedir = "cli/Mkdir/mkdir_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("mkdir", "havasu:")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d


class Unlink(GridTestMixin, CLITestMixin, unittest.TestCase):
    command = "unlink"

    def _create_test_file(self):
        data = "puppies" * 1000
        path = os.path.join(self.basedir, "datafile")
        fileutil.write(path, data)
        self.datafile = path

    def test_unlink_without_alias(self):
        # 'tahoe unlink' should behave sensibly when invoked without an explicit
        # alias before the default 'tahoe' alias has been created.
        self.basedir = "cli/Unlink/%s_without_alias" % (self.command,)
        self.set_up_grid()
        d = self.do_cli(self.command, "afile")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)

        d.addCallback(lambda ign: self.do_cli(self.command, "afile"))
        d.addCallback(_check)
        return d

    def test_unlink_with_nonexistent_alias(self):
        # 'tahoe unlink' should behave sensibly when invoked with an explicit
        # alias that doesn't exist.
        self.basedir = "cli/Unlink/%s_with_nonexistent_alias" % (self.command,)
        self.set_up_grid()
        d = self.do_cli(self.command, "nonexistent:afile")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)

        d.addCallback(lambda ign: self.do_cli(self.command, "nonexistent:afile"))
        d.addCallback(_check)
        return d

    def test_unlink_without_path(self):
        # 'tahoe unlink' should give a sensible error message when invoked without a path.
        self.basedir = "cli/Unlink/%s_without_path" % (self.command,)
        self.set_up_grid()
        self._create_test_file()
        d = self.do_cli("create-alias", "tahoe")
        d.addCallback(lambda ign: self.do_cli("put", self.datafile, "tahoe:test"))
        def _do_unlink((rc, out, err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnless(out.startswith("URI:"), out)
            return self.do_cli(self.command, out.strip('\n'))
        d.addCallback(_do_unlink)

        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("'tahoe %s'" % (self.command,), err)
            self.failUnlessIn("path must be given", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d


class Rm(Unlink):
    """Test that 'tahoe rm' behaves in the same way as 'tahoe unlink'."""
    command = "rm"


class Stats(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_empty_directory(self):
        self.basedir = "cli/Stats/empty_directory"
        self.set_up_grid()
        c0 = self.g.clients[0]
        self.fileurls = {}
        d = c0.create_dirnode()
        def _stash_root(n):
            self.rootnode = n
            self.rooturi = n.get_uri()
        d.addCallback(_stash_root)

        # make sure we can get stats on an empty directory too
        d.addCallback(lambda ign: self.do_cli("stats", self.rooturi))
        def _check_stats((rc, out, err)):
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(rc, 0)
            lines = out.splitlines()
            self.failUnlessIn(" count-immutable-files: 0", lines)
            self.failUnlessIn("   count-mutable-files: 0", lines)
            self.failUnlessIn("   count-literal-files: 0", lines)
            self.failUnlessIn("     count-directories: 1", lines)
            self.failUnlessIn("  size-immutable-files: 0", lines)
            self.failIfIn("Size Histogram:", lines)
        d.addCallback(_check_stats)

        return d

    def test_stats_without_alias(self):
        # when invoked with no explicit alias and before the default 'tahoe'
        # alias is created, 'tahoe stats' should output an informative error
        # message, not a stack trace.
        self.basedir = "cli/Stats/stats_without_alias"
        self.set_up_grid()
        d = self.do_cli("stats")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d

    def test_stats_with_nonexistent_alias(self):
        # when invoked with an explicit alias that doesn't exist,
        # 'tahoe stats' should output a useful error message.
        self.basedir = "cli/Stats/stats_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("stats", "havasu:")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d


class Webopen(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_webopen_with_nonexistent_alias(self):
        # when invoked with an alias that doesn't exist, 'tahoe webopen'
        # should output an informative error message instead of a stack
        # trace.
        self.basedir = "cli/Webopen/webopen_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("webopen", "fake:")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d

    def test_webopen(self):
        # TODO: replace with @patch that supports Deferreds.
        import webbrowser
        def call_webbrowser_open(url):
            self.failUnlessIn(self.alias_uri.replace(':', '%3A'), url)
            self.webbrowser_open_called = True
        def _cleanup(res):
            webbrowser.open = self.old_webbrowser_open
            return res

        self.old_webbrowser_open = webbrowser.open
        try:
            webbrowser.open = call_webbrowser_open

            self.basedir = "cli/Webopen/webopen"
            self.set_up_grid()
            d = self.do_cli("create-alias", "alias:")
            def _check_alias((rc, out, err)):
                self.failUnlessReallyEqual(rc, 0, repr((rc, out, err)))
                self.failUnlessIn("Alias 'alias' created", out)
                self.failUnlessReallyEqual(err, "")
                self.alias_uri = get_aliases(self.get_clientdir())["alias"]
            d.addCallback(_check_alias)
            d.addCallback(lambda res: self.do_cli("webopen", "alias:"))
            def _check_webopen((rc, out, err)):
                self.failUnlessReallyEqual(rc, 0, repr((rc, out, err)))
                self.failUnlessReallyEqual(out, "")
                self.failUnlessReallyEqual(err, "")
                self.failUnless(self.webbrowser_open_called)
            d.addCallback(_check_webopen)
            d.addBoth(_cleanup)
        except:
            _cleanup(None)
            raise
        return d

class Options(ReallyEqualMixin, unittest.TestCase):
    # this test case only looks at argument-processing and simple stuff.

    def parse(self, args, stdout=None):
        o = runner.Options()
        if stdout is not None:
            o.stdout = stdout
        o.parseOptions(args)
        while hasattr(o, "subOptions"):
            o = o.subOptions
        return o

    def test_list(self):
        fileutil.rm_dir("cli/test_options")
        fileutil.make_dirs("cli/test_options")
        fileutil.make_dirs("cli/test_options/private")
        fileutil.write("cli/test_options/node.url", "http://localhost:8080/\n")
        filenode_uri = uri.WriteableSSKFileURI(writekey="\x00"*16,
                                               fingerprint="\x00"*32)
        private_uri = uri.DirectoryURI(filenode_uri).to_string()
        fileutil.write("cli/test_options/private/root_dir.cap", private_uri + "\n")
        def parse2(args): return parse_options("cli/test_options", "ls", args)
        o = parse2([])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], private_uri)
        self.failUnlessEqual(o.where, u"")

        o = parse2(["--node-url", "http://example.org:8111/"])
        self.failUnlessEqual(o['node-url'], "http://example.org:8111/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], private_uri)
        self.failUnlessEqual(o.where, u"")

        o = parse2(["--dir-cap", "root"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], "root")
        self.failUnlessEqual(o.where, u"")

        other_filenode_uri = uri.WriteableSSKFileURI(writekey="\x11"*16,
                                                     fingerprint="\x11"*32)
        other_uri = uri.DirectoryURI(other_filenode_uri).to_string()
        o = parse2(["--dir-cap", other_uri])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], other_uri)
        self.failUnlessEqual(o.where, u"")

        o = parse2(["--dir-cap", other_uri, "subdir"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o.aliases[DEFAULT_ALIAS], other_uri)
        self.failUnlessEqual(o.where, u"subdir")

        self.failUnlessRaises(usage.UsageError, parse2,
                              ["--node-url", "NOT-A-URL"])

        o = parse2(["--node-url", "http://localhost:8080"])
        self.failUnlessEqual(o["node-url"], "http://localhost:8080/")

        o = parse2(["--node-url", "https://localhost/"])
        self.failUnlessEqual(o["node-url"], "https://localhost/")

    def test_version(self):
        # "tahoe --version" dumps text to stdout and exits
        stdout = StringIO()
        self.failUnlessRaises(SystemExit, self.parse, ["--version"], stdout)
        self.failUnlessIn(allmydata.__appname__ + ":", stdout.getvalue())
        # but "tahoe SUBCOMMAND --version" should be rejected
        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["start", "--version"])
        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["start", "--version-and-path"])

    def test_quiet(self):
        # accepted as an overall option, but not on subcommands
        o = self.parse(["--quiet", "start"])
        self.failUnless(o.parent["quiet"])
        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["start", "--quiet"])

    def test_basedir(self):
        # accept a --node-directory option before the verb, or a --basedir
        # option after, or a basedir argument after, but none in the wrong
        # place, and not more than one of the three.
        o = self.parse(["start"])
        self.failUnlessReallyEqual(o["basedir"], os.path.join(fileutil.abspath_expanduser_unicode(u"~"),
                                                              u".tahoe"))
        o = self.parse(["start", "here"])
        self.failUnlessReallyEqual(o["basedir"], fileutil.abspath_expanduser_unicode(u"here"))
        o = self.parse(["start", "--basedir", "there"])
        self.failUnlessReallyEqual(o["basedir"], fileutil.abspath_expanduser_unicode(u"there"))
        o = self.parse(["--node-directory", "there", "start"])
        self.failUnlessReallyEqual(o["basedir"], fileutil.abspath_expanduser_unicode(u"there"))

        o = self.parse(["start", "here", "--nodaemon"])
        self.failUnlessReallyEqual(o["basedir"], fileutil.abspath_expanduser_unicode(u"here"))

        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["--basedir", "there", "start"])
        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["start", "--node-directory", "there"])

        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["--node-directory=there",
                               "start", "--basedir=here"])
        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["start", "--basedir=here", "anywhere"])
        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["--node-directory=there",
                               "start", "anywhere"])
        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["--node-directory=there",
                               "start", "--basedir=here", "anywhere"])

        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["--node-directory=there", "start", "--nodaemon"])
        self.failUnlessRaises(usage.UsageError, self.parse,
                              ["start", "--basedir=here", "--nodaemon"])
