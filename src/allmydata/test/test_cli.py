
from twisted.trial import unittest
from cStringIO import StringIO
import urllib

from allmydata.util import fileutil, hashutil
from allmydata import uri

# at least import the CLI scripts, even if we don't have any real tests for
# them yet.
from allmydata.scripts import tahoe_ls, tahoe_get, tahoe_put, tahoe_rm
_hush_pyflakes = [tahoe_ls, tahoe_get, tahoe_put, tahoe_rm]

from allmydata.scripts import cli, debug


class CLI(unittest.TestCase):
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
        self.failUnlessEqual(o['dir-cap'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--node-url", "http://example.org:8111/"])
        self.failUnlessEqual(o['node-url'], "http://example.org:8111/")
        self.failUnlessEqual(o['dir-cap'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-cap", "root"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['dir-cap'], private_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        other_filenode_uri = uri.WriteableSSKFileURI(writekey="\x11"*16,
                                                     fingerprint="\x11"*32)
        other_uri = uri.NewDirectoryURI(other_filenode_uri).to_string()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-cap", other_uri])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['dir-cap'], other_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "")

        o = cli.ListOptions()
        o.parseOptions(["--node-directory", "cli/test_options",
                        "--dir-cap", other_uri, "subdir"])
        self.failUnlessEqual(o['node-url'], "http://localhost:8080/")
        self.failUnlessEqual(o['dir-cap'], other_uri)
        self.failUnlessEqual(o['vdrive_pathname'], "subdir")

    def _dump_cap(self, *args):
        out,err = StringIO(), StringIO()
        config = debug.DumpCapOptions()
        config.parseOptions(args)
        debug.dump_cap(config, out, err)
        self.failIf(err.getvalue())
        output = out.getvalue()
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
        self.failUnless("CHK File:" in output)
        self.failUnless("key: aaaqeayeaudaocajbifqydiob4" in output, output)
        self.failUnless("UEB hash: 4d5euev6djvynq6vrg34mpy5xi3vl5x7oumdthqky6ovcy4wbvtq" in output, output)
        self.failUnless("size: 1234" in output)
        self.failUnless("k/N: 25/100" in output)
        self.failUnless("storage index: kmkbjguwmkxej3wejdcvu74zki" in output, output)

        output = self._dump_cap("--client-secret", "5s33nk3qpvnj2fw3z4mnm2y6fa",
                                u.to_string())
        self.failUnless("client renewal secret: jltcy6cppghq6ha3uzcawqr2lvwpzmw4teeqj2if6jd2vfpit6hq" in output, output)

        output = self._dump_cap(u.get_verifier().to_string())
        self.failIf("key: " in output)
        self.failUnless("UEB hash: 4d5euev6djvynq6vrg34mpy5xi3vl5x7oumdthqky6ovcy4wbvtq" in output, output)
        self.failUnless("size: 1234" in output)
        self.failUnless("k/N: 25/100" in output)
        self.failUnless("storage index: kmkbjguwmkxej3wejdcvu74zki" in output, output)

        prefixed_u = "http://127.0.0.1/uri/%s" % urllib.quote(u.to_string())
        output = self._dump_cap(prefixed_u)
        self.failUnless("CHK File:" in output)
        self.failUnless("key: aaaqeayeaudaocajbifqydiob4" in output, output)
        self.failUnless("UEB hash: 4d5euev6djvynq6vrg34mpy5xi3vl5x7oumdthqky6ovcy4wbvtq" in output, output)
        self.failUnless("size: 1234" in output)
        self.failUnless("k/N: 25/100" in output)
        self.failUnless("storage index: kmkbjguwmkxej3wejdcvu74zki" in output, output)

    def test_dump_cap_lit(self):
        u = uri.LiteralFileURI("this is some data")
        output = self._dump_cap(u.to_string())
        self.failUnless("Literal File URI:" in output)
        self.failUnless("data: this is some data" in output)

    def test_dump_cap_ssk(self):
        writekey = "\x01" * 16
        fingerprint = "\xfe" * 32
        u = uri.WriteableSSKFileURI(writekey, fingerprint)

        output = self._dump_cap(u.to_string())
        self.failUnless("SSK Writeable URI:" in output)
        self.failUnless("writekey: aeaqcaibaeaqcaibaeaqcaibae" in output, output)
        self.failUnless("readkey: x4gowaektauqze4y2sbsd5peye" in output, output)
        self.failUnless("storage index: rqx7xnpexjxuqprto6pezagdxi" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        output = self._dump_cap("--client-secret", "tylkpgr364eav3ipsnq57yyafu",
                                u.to_string())
        self.failUnless("file renewal secret: cs54qwurfjmeduruapo46kqwexpcvav5oemczblonglj6xmoyvkq" in output, output)

        fileutil.make_dirs("cli/test_dump_cap/private")
        f = open("cli/test_dump_cap/private/secret", "w")
        f.write("y6c7q34mjbt5kkf6hb3utuoj7u\n")
        f.close()
        output = self._dump_cap("--client-dir", "cli/test_dump_cap",
                                u.to_string())
        self.failUnless("file renewal secret: 4jkip4ie2zgmbhcni6g4vmsivwuakpbw7hwnmdancsc6fkrv27kq" in output, output)

        output = self._dump_cap("--client-dir", "cli/test_dump_cap_BOGUS",
                                u.to_string())
        self.failIf("file renewal secret:" in output)

        output = self._dump_cap("--nodeid", "tqc35esocrvejvg4mablt6aowg6tl43j",
                                u.to_string())
        self.failUnless("write_enabler: eok7o6u26dvl3abw4ok7kqrka4omdolnsx627hpcvtx3kkjwsu5q" in output, output)
        self.failIf("file renewal secret:" in output)

        output = self._dump_cap("--nodeid", "tqc35esocrvejvg4mablt6aowg6tl43j",
                                "--client-secret", "6orzlv22ggdhphjpmsixcbwufq",
                                u.to_string())
        self.failUnless("write_enabler: eok7o6u26dvl3abw4ok7kqrka4omdolnsx627hpcvtx3kkjwsu5q" in output, output)
        self.failUnless("file renewal secret: aabhsp6kfsxb57jzdan4dnyzcd3m2prx34jd4z5nj5t5a7guf5fq" in output, output)
        self.failUnless("lease renewal secret: bajcslergse474ga775msalmxxapgwr27lngeja4u7ef5j7yh4bq" in output, output)

        u = u.get_readonly()
        output = self._dump_cap(u.to_string())
        self.failUnless("SSK Read-only URI:" in output)
        self.failUnless("readkey: x4gowaektauqze4y2sbsd5peye" in output, output)
        self.failUnless("storage index: rqx7xnpexjxuqprto6pezagdxi" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output)

        u = u.get_verifier()
        output = self._dump_cap(u.to_string())
        self.failUnless("SSK Verifier URI:" in output)
        self.failUnless("storage index: rqx7xnpexjxuqprto6pezagdxi" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output)

    def test_dump_cap_directory(self):
        writekey = "\x01" * 16
        fingerprint = "\xfe" * 32
        u1 = uri.WriteableSSKFileURI(writekey, fingerprint)
        u = uri.NewDirectoryURI(u1)

        output = self._dump_cap(u.to_string())
        self.failUnless("Directory Writeable URI:" in output)
        self.failUnless("writekey: aeaqcaibaeaqcaibaeaqcaibae" in output, output)
        self.failUnless("readkey: x4gowaektauqze4y2sbsd5peye" in output, output)
        self.failUnless("storage index: rqx7xnpexjxuqprto6pezagdxi" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

        output = self._dump_cap("--client-secret", "a3nyfbnkorp377jhguslgc2dqi",
                                u.to_string())
        self.failUnless("file renewal secret: zwmq2azrd7lfcmhkrhpgjsxeb2vfpixgvrczbo2asqzdfbmiemwq" in output, output)

        output = self._dump_cap("--nodeid", "tqc35esocrvejvg4mablt6aowg6tl43j",
                                u.to_string())
        self.failUnless("write_enabler: eok7o6u26dvl3abw4ok7kqrka4omdolnsx627hpcvtx3kkjwsu5q" in output, output)
        self.failIf("file renewal secret:" in output)

        output = self._dump_cap("--nodeid", "tqc35esocrvejvg4mablt6aowg6tl43j",
                                "--client-secret", "rzaq5to2xm6e5otctpdvzw6bfa",
                                u.to_string())
        self.failUnless("write_enabler: eok7o6u26dvl3abw4ok7kqrka4omdolnsx627hpcvtx3kkjwsu5q" in output, output)
        self.failUnless("file renewal secret: wdmu6rwefvmp2venbb4xz5u3273oybmuu553mi7uic37gfu6bacq" in output, output)
        self.failUnless("lease renewal secret: tlvwfudyfeqyss5kybt6ya72foedqxdovumlbt6ok7u5pyrf2mfq" in output, output)

        u = u.get_readonly()
        output = self._dump_cap(u.to_string())
        self.failUnless("Directory Read-only URI:" in output)
        self.failUnless("readkey: x4gowaektauqze4y2sbsd5peye" in output, output)
        self.failUnless("storage index: rqx7xnpexjxuqprto6pezagdxi" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output)

        u = u.get_verifier()
        output = self._dump_cap(u.to_string())
        self.failUnless("Directory Verifier URI:" in output)
        self.failUnless("storage index: rqx7xnpexjxuqprto6pezagdxi" in output, output)
        self.failUnless("fingerprint: 737p57x6737p57x6737p57x6737p57x6737p57x6737p57x6737a" in output, output)

