from twisted.trial import unittest
from twisted.internet import defer

from allmydata.immutable import upload
from allmydata.interfaces import MDMF_VERSION, SDMF_VERSION
from allmydata.mutable.publish import MutableData
from allmydata.test.no_network import GridTestMixin
from allmydata.util.encodingutil import quote_output, get_io_encoding
from .test_cli import CLITestMixin

timeout = 480 # deep_check takes 360s on Zandr's linksys box, others take > 240s

class List(GridTestMixin, CLITestMixin, unittest.TestCase):
    def test_list(self):
        self.basedir = "cli/List/list"
        self.set_up_grid()
        c0 = self.g.clients[0]
        small = "small"

        # u"g\u00F6\u00F6d" might not be representable in the argv and/or output encodings.
        # It is initially included in the directory in any case.
        try:
            good_arg = u"g\u00F6\u00F6d".encode(get_io_encoding())
        except UnicodeEncodeError:
            good_arg = None

        try:
            good_out = u"g\u00F6\u00F6d".encode(get_io_encoding())
        except UnicodeEncodeError:
            good_out = None

        d = c0.create_dirnode()
        def _stash_root_and_create_file(n):
            self.rootnode = n
            self.rooturi = n.get_uri()
            return n.add_file(u"g\u00F6\u00F6d", upload.Data(small, convergence=""))
        d.addCallback(_stash_root_and_create_file)
        def _stash_goodcap(n):
            self.goodcap = n.get_uri()
        d.addCallback(_stash_goodcap)
        d.addCallback(lambda ign: self.rootnode.create_subdirectory(u"1share"))
        d.addCallback(lambda n:
                      self.delete_shares_numbered(n.get_uri(), range(1,10)))
        d.addCallback(lambda ign: self.rootnode.create_subdirectory(u"0share"))
        d.addCallback(lambda n:
                      self.delete_shares_numbered(n.get_uri(), range(0,10)))
        d.addCallback(lambda ign:
                      self.do_cli("add-alias", "tahoe", self.rooturi))
        d.addCallback(lambda ign: self.do_cli("ls"))
        def _check1((rc,out,err)):
            if good_out is None:
                self.failUnlessReallyEqual(rc, 1)
                self.failUnlessIn("files whose names could not be converted", err)
                self.failUnlessIn(quote_output(u"g\u00F6\u00F6d"), err)
                self.failUnlessReallyEqual(sorted(out.splitlines()), sorted(["0share", "1share"]))
            else:
                self.failUnlessReallyEqual(rc, 0)
                self.failUnlessReallyEqual(err, "")
                self.failUnlessReallyEqual(sorted(out.splitlines()), sorted(["0share", "1share", good_out]))
        d.addCallback(_check1)
        d.addCallback(lambda ign: self.do_cli("ls", "missing"))
        def _check2((rc,out,err)):
            self.failIfEqual(rc, 0)
            self.failUnlessReallyEqual(err.strip(), "No such file or directory")
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check2)
        d.addCallback(lambda ign: self.do_cli("ls", "1share"))
        def _check3((rc,out,err)):
            self.failIfEqual(rc, 0)
            self.failUnlessIn("Error during GET: 410 Gone", err)
            self.failUnlessIn("UnrecoverableFileError:", err)
            self.failUnlessIn("could not be retrieved, because there were "
                              "insufficient good shares.", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check3)
        d.addCallback(lambda ign: self.do_cli("ls", "0share"))
        d.addCallback(_check3)
        def _check4((rc, out, err)):
            if good_out is None:
                self.failUnlessReallyEqual(rc, 1)
                self.failUnlessIn("files whose names could not be converted", err)
                self.failUnlessIn(quote_output(u"g\u00F6\u00F6d"), err)
                self.failUnlessReallyEqual(out, "")
            else:
                # listing a file (as dir/filename) should have the edge metadata,
                # including the filename
                self.failUnlessReallyEqual(rc, 0)
                self.failUnlessIn(good_out, out)
                self.failIfIn("-r-- %d -" % len(small), out,
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

        def _check5((rc, out, err)):
            # listing a raw filecap should not explode, but it will have no
            # metadata, just the size
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual("-r-- %d -" % len(small), out.strip())
        d.addCallback(lambda ign: self.do_cli("ls", "-l", self.goodcap))
        d.addCallback(_check5)

        # Now rename 'g\u00F6\u00F6d' to 'good' and repeat the tests that might have been skipped due
        # to encoding problems.
        d.addCallback(lambda ign: self.rootnode.move_child_to(u"g\u00F6\u00F6d", self.rootnode, u"good"))

        d.addCallback(lambda ign: self.do_cli("ls"))
        def _check1_ascii((rc,out,err)):
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessReallyEqual(err, "")
            self.failUnlessReallyEqual(sorted(out.splitlines()), sorted(["0share", "1share", "good"]))
        d.addCallback(_check1_ascii)
        def _check4_ascii((rc, out, err)):
            # listing a file (as dir/filename) should have the edge metadata,
            # including the filename
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

        unknown_immcap = "imm.URI:unknown"
        def _create_unknown(ign):
            nm = c0.nodemaker
            kids = {u"unknownchild-imm": (nm.create_from_cap(unknown_immcap), {})}
            return self.rootnode.create_subdirectory(u"unknown", initial_children=kids,
                                                     mutable=False)
        d.addCallback(_create_unknown)
        def _check6((rc, out, err)):
            # listing a directory referencing an unknown object should print
            # an extra message to stderr
            self.failUnlessReallyEqual(rc, 0)
            self.failUnlessIn("?r-- ? - unknownchild-imm\n", out)
            self.failUnlessIn("included unknown objects", err)
        d.addCallback(lambda ign: self.do_cli("ls", "-l", "unknown"))
        d.addCallback(_check6)
        def _check7((rc, out, err)):
            # listing an unknown cap directly should print an extra message
            # to stderr (currently this only works if the URI starts with 'URI:'
            # after any 'ro.' or 'imm.' prefix, otherwise it will be confused
            # with an alias).
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
        self.set_up_grid()
        d = self.do_cli("ls")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d

    def test_list_with_nonexistent_alias(self):
        # doing 'tahoe ls' while specifying an alias that doesn't already
        # exist should fail with an informative error message
        self.basedir = "cli/List/list_with_nonexistent_alias"
        self.set_up_grid()
        d = self.do_cli("ls", "nonexistent:")
        def _check((rc, out, err)):
            self.failUnlessReallyEqual(rc, 1)
            self.failUnlessIn("error:", err)
            self.failUnlessIn("nonexistent", err)
            self.failUnlessReallyEqual(out, "")
        d.addCallback(_check)
        return d

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
            mutable_data = MutableData("data" * 100000)
            mutable_data2 = MutableData("data" * 100000)
            # Add both kinds of mutable node.
            d1 = nm.create_mutable_file(mutable_data,
                                        version=MDMF_VERSION)
            d2 = nm.create_mutable_file(mutable_data2,
                                        version=SDMF_VERSION)
            # Add an immutable node. We do this through the directory,
            # with add_file.
            immutable_data = upload.Data("immutable data" * 100000,
                                         convergence="")
            d3 = n.add_file(u"immutable", immutable_data)
            ds = [d1, d2, d3]
            dl = defer.DeferredList(ds)
            def _made_files((r1, r2, r3)):
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
        self.set_up_grid()
        d = self._create_directory_structure()
        d.addCallback(lambda ignored:
            self.do_cli("ls", self._dircap))
        def _got_ls((rc, out, err)):
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
        self.set_up_grid()
        d = self._create_directory_structure()
        d.addCallback(lambda ignored:
            self.do_cli("ls", "--json", self._dircap))
        def _got_json((rc, out, err)):
            self.failUnlessEqual(rc, 0)
            self.failUnlessEqual(err, "")
            self.failUnlessIn(self._mdmf_uri, out)
            self.failUnlessIn(self._mdmf_readonly_uri, out)
            self.failUnlessIn(self._sdmf_uri, out)
            self.failUnlessIn(self._sdmf_readonly_uri, out)
            self.failUnlessIn(self._imm_uri, out)
            self.failUnlessIn('"format": "SDMF"', out)
            self.failUnlessIn('"format": "MDMF"', out)
        d.addCallback(_got_json)
        return d
