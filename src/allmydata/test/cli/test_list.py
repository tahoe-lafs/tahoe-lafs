"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2, PY3
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from six import ensure_str

from twisted.trial import unittest
from twisted.internet import defer

from allmydata.immutable import upload
from allmydata.interfaces import MDMF_VERSION, SDMF_VERSION
from allmydata.mutable.publish import MutableData
from ..no_network import GridTestMixin
from allmydata.util.encodingutil import quote_output, get_io_encoding
from .common import CLITestMixin


class List(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_list(self):
        self.basedir = "cli/List/list"
        self.set_up_grid()
        c0 = self.g.clients[0]
        small = b"small"

        good_arg = u"g\u00F6\u00F6d"
        good_out = u"g\u00F6\u00F6d"

        # On Python 2 we get bytes, so we need encoded version. On Python 3
        # stdio is unicode so can leave unchanged.
        good_out_encoded = good_out if PY3 else good_out.encode(get_io_encoding())

        d = c0.create_dirnode()
        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.rooturi = str(n.get_uri(), "utf-8")
            return n.add_file(u"g\u00F6\u00F6d", upload.Data(small, convergence=b""))
        d.addCallback(_stash_root_and_create_file)
        def _stash_goodcap(n):
            self.goodcap = n.get_uri()
        d.addCallback(_stash_goodcap)
        d.addCallback(lambda ign: self.rootnode.create_subdirectory(u"1share"))
        d.addCallback(lambda n:
                      self.delete_shares_numbered(n.get_uri(), list(range(1,10))))
        d.addCallback(lambda ign: self.rootnode.create_subdirectory(u"0share"))
        d.addCallback(lambda n:
                      self.delete_shares_numbered(n.get_uri(), list(range(0,10))))
        d.addCallback(lambda ign:
                      self.do_cli("add-alias", "tahoe", self.rooturi))
        d.addCallback(lambda ign: self.do_cli("ls"))
        def _check1(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 0)
            self.assertEqual(len(err), 0, err)
            expected = sorted([ensure_str("0share"), ensure_str("1share"), good_out_encoded])
            self.assertEqual(sorted(out.splitlines()), expected)
        d.addCallback(_check1)
        d.addCallback(lambda ign: self.do_cli("ls", "missing"))
        def _check2(args):
            (rc, out, err) = args
            self.failIfEqual(rc, 0)
            self.assertEqual(err.strip(), "No such file or directory")
            self.assertEqual(len(out), 0, out)
        d.addCallback(_check2)
        d.addCallback(lambda ign: self.do_cli("ls", "1share"))
        def _check3(args):
            (rc, out, err) = args
            self.failIfEqual(rc, 0)
            self.failUnlessIn("Error during GET: 410 Gone", err)
            self.failUnlessIn("UnrecoverableFileError:", err)
            self.failUnlessIn("could not be retrieved, because there were "
                              "insufficient good shares.", err)
            self.assertEqual(len(out), 0, out)
        d.addCallback(_check3)
        d.addCallback(lambda ign: self.do_cli("ls", "0share"))
        d.addCallback(_check3)
        def _check4(args):
            (rc, out, err) = args
            if good_out is None:
                self.failUnlessReallyEqual(rc, 1)
                self.failUnlessIn("files whose names could not be converted", err)
                self.failUnlessIn(quote_output(u"g\u00F6\u00F6d"), err)
                self.assertEqual(len(out), 0, out)
            else:
                # listing a file (as dir/filename) should have the edge metadata,
                # including the filename
                self.failUnlessReallyEqual(rc, 0)
                self.failUnlessIn(good_out_encoded, out)
                self.failIfIn(ensure_str("-r-- %d -" % len(small)), out,
                              "trailing hyphen means unknown date")

        if good_arg is not None:
            d.addCallback(lambda ign: self.do_cli("ls", "-l", good_arg))
            d.addCallback(_check4)
            # listing a file as $DIRCAP/filename should work just like dir/filename
            d.addCallback(lambda ign: self.do_cli("ls", "-l", self.rooturi + "/" + good_arg))
            d.addCallback(_check4)
            # and similarly for $DIRCAP:./filename
            d.addCallback(lambda ign: self.do_cli("ls", "-l", self.rooturi + ":./" + good_arg))
            d.addCallback(_check4)

        def _check5(args):
            # listing a raw filecap should not explode, but it will have no
            # metadata, just the size
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 0)
            self.assertEqual("-r-- %d -" % len(small), out.strip())
        d.addCallback(lambda ign: self.do_cli("ls", "-l", self.goodcap))
        d.addCallback(_check5)

        # Now rename 'g\u00F6\u00F6d' to 'good' and repeat the tests that might have been skipped due
        # to encoding problems.
        d.addCallback(lambda ign: self.rootnode.move_child_to(u"g\u00F6\u00F6d", self.rootnode, u"good"))

        d.addCallback(lambda ign: self.do_cli("ls"))
        def _check1_ascii(args):
            (rc,out,err) = args
            self.failUnlessReallyEqual(rc, 0)
            self.assertEqual(len(err), 0, err)
            self.failUnlessReallyEqual(sorted(out.splitlines()), sorted(["0share", "1share", "good"]))
        d.addCallback(_check1_ascii)
        def _check4_ascii(args):
            # listing a file (as dir/filename) should have the edge metadata,
            # including the filename
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessIn("good", out)
            self.failIfIn("-r-- %d -" % len(small), out,
                          "trailing hyphen means unknown date")

        d.addCallback(lambda ign: self.do_cli("ls", "-l", "good"))
        d.addCallback(_check4_ascii)
        # listing a file as $DIRCAP/filename should work just like dir/filename
        d.addCallback(lambda ign: self.do_cli("ls", "-l", self.rooturi + "/good"))
        d.addCallback(_check4_ascii)
        # and similarly for $DIRCAP:./filename
        d.addCallback(lambda ign: self.do_cli("ls", "-l", self.rooturi + ":./good"))
        d.addCallback(_check4_ascii)

        unknown_immcap = b"imm.URI:unknown"
        def _create_unknown(ign):
            nm = c0.nodemaker
            kids = {u"unknownchild-imm": (nm.create_from_cap(unknown_immcap), {})}
            return self.rootnode.create_subdirectory(u"unknown", initial_children=kids,
                                                     mutable=False)
        d.addCallback(_create_unknown)
        def _check6(args):
            # listing a directory referencing an unknown object should print
            # an extra message to stderr
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessIn("?r-- ? - unknownchild-imm\n", out)
            self.failUnlessIn("included unknown objects", err)
        d.addCallback(lambda ign: self.do_cli("ls", "-l", "unknown"))
        d.addCallback(_check6)
        def _check7(args):
            # listing an unknown cap directly should print an extra message
            # to stderr (currently this only works if the URI starts with 'URI:'
            # after any 'ro.' or 'imm.' prefix, otherwise it will be confused
            # with an alias).
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessIn("?r-- ? -\n", out)
            self.failUnlessIn("included unknown objects", err)
        d.addCallback(lambda ign: self.do_cli("ls", "-l", unknown_immcap))
        d.addCallback(_check7)
        return d

    def test_list_without_alias(self):
        # doing just 'tahoe ls' without specifying an alias or first
        # doing 'tahoe create-alias tahoe' should fail gracefully.
        self.basedir = "cli/List/list_without_alias"
        self.set_up_grid(oneshare=True)
        d = self.do_cli("ls")
        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.assertEqual(len(out), 0, out)
        d.addCallback(_check)
        return d

    def test_list_with_nonexistent_alias(self):
        # doing 'tahoe ls' while specifying an alias that doesn't already
        # exist should fail with an informative error message
        self.basedir = "cli/List/list_with_nonexistent_alias"
        self.set_up_grid(oneshare=True)
        d = self.do_cli("ls", "nonexistent:")
        def _check(args):
            (rc, out, err) = args
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
            self.assertEqual(len(out), 0, out)
        d.addCallback(_check)
        return d

    @defer.inlineCallbacks
    def test_list_readonly(self):
        self.basedir = "cli/List/list_readonly"
        yield self.set_up_grid(oneshare=True)
        c0 = self.g.clients[0]

        root = yield c0.create_dirnode()
        rooturi = root.get_uri()
        rc, out, err = yield self.do_cli("add-alias", "tahoe", rooturi)
        self.assertEqual(0, rc)
        rc, out, err = yield self.do_cli("list-aliases", "--readonly-uri")
        self.assertTrue('URI:DIR2-RO' in out)


    def _create_directory_structure(self):
        # Create a simple directory structure that we can use for MDMF,
        # SDMF, and immutable testing.
        assert self.g

        client = self.g.clients[0]
        # Create a dirnode
        d = client.create_dirnode()
        def _got_rootnode(n):
            # Add a few nodes.
            self._dircap = n.get_uri()
            nm = n._nodemaker
            # The uploaders may run at the same time, so we need two
            # MutableData instances or they'll fight over offsets &c and
            # break.
            mutable_data = MutableData(b"data" * 100000)
            mutable_data2 = MutableData(b"data" * 100000)
            # Add both kinds of mutable node.
            d1 = nm.create_mutable_file(mutable_data,
                                        version=MDMF_VERSION)
            d2 = nm.create_mutable_file(mutable_data2,
                                        version=SDMF_VERSION)
            # Add an immutable node. We do this through the directory,
            # with add_file.
            immutable_data = upload.Data(b"immutable data" * 100000,
                                         convergence=b"")
            d3 = n.add_file(u"immutable", immutable_data)
            ds = [d1, d2, d3]
            dl = defer.DeferredList(ds)
            def _made_files(args):
                (r1, r2, r3) = args
                self.failUnless(r1[0])
                self.failUnless(r2[0])
                self.failUnless(r3[0])

                # r1, r2, and r3 contain nodes.
                mdmf_node = r1[1]
                sdmf_node = r2[1]
                imm_node = r3[1]

                self._mdmf_uri = mdmf_node.get_uri()
                self._mdmf_readonly_uri = mdmf_node.get_readonly_uri()
                self._sdmf_uri = mdmf_node.get_uri()
                self._sdmf_readonly_uri = sdmf_node.get_readonly_uri()
                self._imm_uri = imm_node.get_uri()

                d1 = n.set_node(u"mdmf", mdmf_node)
                d2 = n.set_node(u"sdmf", sdmf_node)
                return defer.DeferredList([d1, d2])
            # We can now list the directory by listing self._dircap.
            dl.addCallback(_made_files)
            return dl
        d.addCallback(_got_rootnode)
        return d

    def test_list_mdmf(self):
        # 'tahoe ls' should include MDMF files.
        self.basedir = "cli/List/list_mdmf"
        self.set_up_grid(oneshare=True)
        d = self._create_directory_structure()
        d.addCallback(lambda ignored:
            self.do_cli("ls", self._dircap))
        def _got_ls(args):
            (rc, out, err) = args
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(err, "")
            self.failUnlessIn("immutable", out)
            self.failUnlessIn("mdmf", out)
            self.failUnlessIn("sdmf", out)
        d.addCallback(_got_ls)
        return d

    def test_list_mdmf_json(self):
        # 'tahoe ls' should include MDMF caps when invoked with MDMF
        # caps.
        self.basedir = "cli/List/list_mdmf_json"
        self.set_up_grid(oneshare=True)
        d = self._create_directory_structure()
        d.addCallback(lambda ignored:
            self.do_cli("ls", "--json", self._dircap))
        def _got_json(args):
            (rc, out, err) = args
            self.failUnlessEqual(rc, 0)
            self.assertEqual(len(err), 0, err)
            self.failUnlessIn(str(self._mdmf_uri, "ascii"), out)
            self.failUnlessIn(str(self._mdmf_readonly_uri, "ascii"), out)
            self.failUnlessIn(str(self._sdmf_uri, "ascii"), out)
            self.failUnlessIn(str(self._sdmf_readonly_uri, "ascii"), out)
            self.failUnlessIn(str(self._imm_uri, "ascii"), out)
            self.failUnlessIn('"format": "SDMF"', out)
            self.failUnlessIn('"format": "MDMF"', out)
        d.addCallback(_got_json)
        return d
