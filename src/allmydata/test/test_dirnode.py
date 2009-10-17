
import time
from twisted.trial import unittest
from twisted.internet import defer
from allmydata import uri, dirnode
from allmydata.client import Client
from allmydata.immutable import upload
from allmydata.interfaces import IFileNode, \
     ExistingChildError, NoSuchChildError, \
     IDeepCheckResults, IDeepCheckAndRepairResults, CannotPackUnknownNodeError
from allmydata.mutable.filenode import MutableFileNode
from allmydata.mutable.common import UncoordinatedWriteError
from allmydata.util import hashutil, base32
from allmydata.monitor import Monitor
from allmydata.test.common import make_chk_file_uri, make_mutable_file_uri, \
     ErrorMixin
from allmydata.test.no_network import GridTestMixin
from allmydata.unknown import UnknownNode
from allmydata.nodemaker import NodeMaker
from base64 import b32decode
import common_util as testutil

class Dirnode(GridTestMixin, unittest.TestCase,
              testutil.ShouldFailMixin, testutil.StallMixin, ErrorMixin):
    timeout = 240 # It takes longer than 120 seconds on Francois's arm box.

    def test_basic(self):
        self.basedir = "dirnode/Dirnode/test_basic"
        self.set_up_grid()
        c = self.g.clients[0]
        d = c.create_dirnode()
        def _done(res):
            self.failUnless(isinstance(res, dirnode.DirectoryNode))
            rep = str(res)
            self.failUnless("RW" in rep)
        d.addCallback(_done)
        return d

    def test_check(self):
        self.basedir = "dirnode/Dirnode/test_check"
        self.set_up_grid()
        c = self.g.clients[0]
        d = c.create_dirnode()
        d.addCallback(lambda dn: dn.check(Monitor()))
        def _done(res):
            self.failUnless(res.is_healthy())
        d.addCallback(_done)
        return d

    def _test_deepcheck_create(self):
        # create a small tree with a loop, and some non-directories
        #  root/
        #  root/subdir/
        #  root/subdir/file1
        #  root/subdir/link -> root
        #  root/rodir
        c = self.g.clients[0]
        d = c.create_dirnode()
        def _created_root(rootnode):
            self._rootnode = rootnode
            return rootnode.create_subdirectory(u"subdir")
        d.addCallback(_created_root)
        def _created_subdir(subdir):
            self._subdir = subdir
            d = subdir.add_file(u"file1", upload.Data("data"*100, None))
            d.addCallback(lambda res: subdir.set_node(u"link", self._rootnode))
            d.addCallback(lambda res: c.create_dirnode())
            d.addCallback(lambda dn:
                          self._rootnode.set_uri(u"rodir",
                                                 dn.get_uri(),
                                                 dn.get_readonly_uri()))
            return d
        d.addCallback(_created_subdir)
        def _done(res):
            return self._rootnode
        d.addCallback(_done)
        return d

    def test_deepcheck(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck"
        self.set_up_grid()
        d = self._test_deepcheck_create()
        d.addCallback(lambda rootnode: rootnode.start_deep_check().when_done())
        def _check_results(r):
            self.failUnless(IDeepCheckResults.providedBy(r))
            c = r.get_counters()
            self.failUnlessEqual(c,
                                 {"count-objects-checked": 4,
                                  "count-objects-healthy": 4,
                                  "count-objects-unhealthy": 0,
                                  "count-objects-unrecoverable": 0,
                                  "count-corrupt-shares": 0,
                                  })
            self.failIf(r.get_corrupt_shares())
            self.failUnlessEqual(len(r.get_all_results()), 4)
        d.addCallback(_check_results)
        return d

    def test_deepcheck_and_repair(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck_and_repair"
        self.set_up_grid()
        d = self._test_deepcheck_create()
        d.addCallback(lambda rootnode:
                      rootnode.start_deep_check_and_repair().when_done())
        def _check_results(r):
            self.failUnless(IDeepCheckAndRepairResults.providedBy(r))
            c = r.get_counters()
            self.failUnlessEqual(c,
                                 {"count-objects-checked": 4,
                                  "count-objects-healthy-pre-repair": 4,
                                  "count-objects-unhealthy-pre-repair": 0,
                                  "count-objects-unrecoverable-pre-repair": 0,
                                  "count-corrupt-shares-pre-repair": 0,
                                  "count-objects-healthy-post-repair": 4,
                                  "count-objects-unhealthy-post-repair": 0,
                                  "count-objects-unrecoverable-post-repair": 0,
                                  "count-corrupt-shares-post-repair": 0,
                                  "count-repairs-attempted": 0,
                                  "count-repairs-successful": 0,
                                  "count-repairs-unsuccessful": 0,
                                  })
            self.failIf(r.get_corrupt_shares())
            self.failIf(r.get_remaining_corrupt_shares())
            self.failUnlessEqual(len(r.get_all_results()), 4)
        d.addCallback(_check_results)
        return d

    def _mark_file_bad(self, rootnode):
        si = rootnode.get_storage_index()
        self.delete_shares_numbered(rootnode.get_uri(), [0])
        return rootnode

    def test_deepcheck_problems(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck_problems"
        self.set_up_grid()
        d = self._test_deepcheck_create()
        d.addCallback(lambda rootnode: self._mark_file_bad(rootnode))
        d.addCallback(lambda rootnode: rootnode.start_deep_check().when_done())
        def _check_results(r):
            c = r.get_counters()
            self.failUnlessEqual(c,
                                 {"count-objects-checked": 4,
                                  "count-objects-healthy": 3,
                                  "count-objects-unhealthy": 1,
                                  "count-objects-unrecoverable": 0,
                                  "count-corrupt-shares": 0,
                                  })
            #self.failUnlessEqual(len(r.get_problems()), 1) # TODO
        d.addCallback(_check_results)
        return d

    def test_readonly(self):
        self.basedir = "dirnode/Dirnode/test_readonly"
        self.set_up_grid()
        c = self.g.clients[0]
        nm = c.nodemaker
        filecap = make_chk_file_uri(1234)
        filenode = nm.create_from_cap(filecap)
        uploadable = upload.Data("some data", convergence="some convergence string")

        d = c.create_dirnode()
        def _created(rw_dn):
            d2 = rw_dn.set_uri(u"child", filecap, filecap)
            d2.addCallback(lambda res: rw_dn)
            return d2
        d.addCallback(_created)

        def _ready(rw_dn):
            ro_uri = rw_dn.get_readonly_uri()
            ro_dn = c.create_node_from_uri(ro_uri)
            self.failUnless(ro_dn.is_readonly())
            self.failUnless(ro_dn.is_mutable())

            self.shouldFail(dirnode.NotMutableError, "set_uri ro", None,
                            ro_dn.set_uri, u"newchild", filecap, filecap)
            self.shouldFail(dirnode.NotMutableError, "set_uri ro", None,
                            ro_dn.set_node, u"newchild", filenode)
            self.shouldFail(dirnode.NotMutableError, "set_nodes ro", None,
                            ro_dn.set_nodes, { u"newchild": (filenode, None) })
            self.shouldFail(dirnode.NotMutableError, "set_uri ro", None,
                            ro_dn.add_file, u"newchild", uploadable)
            self.shouldFail(dirnode.NotMutableError, "set_uri ro", None,
                            ro_dn.delete, u"child")
            self.shouldFail(dirnode.NotMutableError, "set_uri ro", None,
                            ro_dn.create_subdirectory, u"newchild")
            self.shouldFail(dirnode.NotMutableError, "set_metadata_for ro", None,
                            ro_dn.set_metadata_for, u"child", {})
            self.shouldFail(dirnode.NotMutableError, "set_uri ro", None,
                            ro_dn.move_child_to, u"child", rw_dn)
            self.shouldFail(dirnode.NotMutableError, "set_uri ro", None,
                            rw_dn.move_child_to, u"child", ro_dn)
            return ro_dn.list()
        d.addCallback(_ready)
        def _listed(children):
            self.failUnless(u"child" in children)
        d.addCallback(_listed)
        return d

    def failUnlessGreaterThan(self, a, b):
        self.failUnless(a > b, "%r should be > %r" % (a, b))

    def failUnlessGreaterOrEqualThan(self, a, b):
        self.failUnless(a >= b, "%r should be >= %r" % (a, b))

    def test_create(self):
        self.basedir = "dirnode/Dirnode/test_create"
        self.set_up_grid()
        c = self.g.clients[0]

        self.expected_manifest = []
        self.expected_verifycaps = set()
        self.expected_storage_indexes = set()

        d = c.create_dirnode()
        def _then(n):
            # /
            self.rootnode = n
            self.failUnless(n.is_mutable())
            u = n.get_uri()
            self.failUnless(u)
            self.failUnless(u.startswith("URI:DIR2:"), u)
            u_ro = n.get_readonly_uri()
            self.failUnless(u_ro.startswith("URI:DIR2-RO:"), u_ro)
            u_v = n.get_verify_cap().to_string()
            self.failUnless(u_v.startswith("URI:DIR2-Verifier:"), u_v)
            u_r = n.get_repair_cap().to_string()
            self.failUnlessEqual(u_r, u)
            self.expected_manifest.append( ((), u) )
            self.expected_verifycaps.add(u_v)
            si = n.get_storage_index()
            self.expected_storage_indexes.add(base32.b2a(si))
            expected_si = n._uri._filenode_uri.storage_index
            self.failUnlessEqual(si, expected_si)

            d = n.list()
            d.addCallback(lambda res: self.failUnlessEqual(res, {}))
            d.addCallback(lambda res: n.has_child(u"missing"))
            d.addCallback(lambda res: self.failIf(res))

            fake_file_uri = make_mutable_file_uri()
            other_file_uri = make_mutable_file_uri()
            m = c.nodemaker.create_from_cap(fake_file_uri)
            ffu_v = m.get_verify_cap().to_string()
            self.expected_manifest.append( ((u"child",) , m.get_uri()) )
            self.expected_verifycaps.add(ffu_v)
            self.expected_storage_indexes.add(base32.b2a(m.get_storage_index()))
            d.addCallback(lambda res: n.set_uri(u"child",
                                                fake_file_uri, fake_file_uri))
            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "set_uri-no",
                                          "child 'child' already exists",
                                          n.set_uri, u"child",
                                          other_file_uri, other_file_uri,
                                          overwrite=False))
            # /
            # /child = mutable

            d.addCallback(lambda res: n.create_subdirectory(u"subdir"))

            # /
            # /child = mutable
            # /subdir = directory
            def _created(subdir):
                self.failUnless(isinstance(subdir, dirnode.DirectoryNode))
                self.subdir = subdir
                new_v = subdir.get_verify_cap().to_string()
                assert isinstance(new_v, str)
                self.expected_manifest.append( ((u"subdir",), subdir.get_uri()) )
                self.expected_verifycaps.add(new_v)
                si = subdir.get_storage_index()
                self.expected_storage_indexes.add(base32.b2a(si))
            d.addCallback(_created)

            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "mkdir-no",
                                          "child 'subdir' already exists",
                                          n.create_subdirectory, u"subdir",
                                          overwrite=False))

            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted([u"child", u"subdir"])))

            d.addCallback(lambda res: n.start_deep_stats().when_done())
            def _check_deepstats(stats):
                self.failUnless(isinstance(stats, dict))
                expected = {"count-immutable-files": 0,
                            "count-mutable-files": 1,
                            "count-literal-files": 0,
                            "count-files": 1,
                            "count-directories": 2,
                            "size-immutable-files": 0,
                            "size-literal-files": 0,
                            #"size-directories": 616, # varies
                            #"largest-directory": 616,
                            "largest-directory-children": 2,
                            "largest-immutable-file": 0,
                            }
                for k,v in expected.iteritems():
                    self.failUnlessEqual(stats[k], v,
                                         "stats[%s] was %s, not %s" %
                                         (k, stats[k], v))
                self.failUnless(stats["size-directories"] > 500,
                                stats["size-directories"])
                self.failUnless(stats["largest-directory"] > 500,
                                stats["largest-directory"])
                self.failUnlessEqual(stats["size-files-histogram"], [])
            d.addCallback(_check_deepstats)

            d.addCallback(lambda res: n.build_manifest().when_done())
            def _check_manifest(res):
                manifest = res["manifest"]
                self.failUnlessEqual(sorted(manifest),
                                     sorted(self.expected_manifest))
                stats = res["stats"]
                _check_deepstats(stats)
                self.failUnlessEqual(self.expected_verifycaps,
                                     res["verifycaps"])
                self.failUnlessEqual(self.expected_storage_indexes,
                                     res["storage-index"])
            d.addCallback(_check_manifest)

            def _add_subsubdir(res):
                return self.subdir.create_subdirectory(u"subsubdir")
            d.addCallback(_add_subsubdir)
            # /
            # /child = mutable
            # /subdir = directory
            # /subdir/subsubdir = directory
            d.addCallback(lambda res: n.get_child_at_path(u"subdir/subsubdir"))
            d.addCallback(lambda subsubdir:
                          self.failUnless(isinstance(subsubdir,
                                                     dirnode.DirectoryNode)))
            d.addCallback(lambda res: n.get_child_at_path(u""))
            d.addCallback(lambda res: self.failUnlessEqual(res.get_uri(),
                                                           n.get_uri()))

            d.addCallback(lambda res: n.get_metadata_for(u"child"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()),
                                               set(["tahoe", "ctime", "mtime"])))

            d.addCallback(lambda res:
                          self.shouldFail(NoSuchChildError, "gcamap-no",
                                          "nope",
                                          n.get_child_and_metadata_at_path,
                                          u"subdir/nope"))
            d.addCallback(lambda res:
                          n.get_child_and_metadata_at_path(u""))
            def _check_child_and_metadata1(res):
                child, metadata = res
                self.failUnless(isinstance(child, dirnode.DirectoryNode))
                # edge-metadata needs at least one path segment
                self.failUnlessEqual(sorted(metadata.keys()), [])
            d.addCallback(_check_child_and_metadata1)
            d.addCallback(lambda res:
                          n.get_child_and_metadata_at_path(u"child"))

            def _check_child_and_metadata2(res):
                child, metadata = res
                self.failUnlessEqual(child.get_uri(),
                                     fake_file_uri)
                self.failUnlessEqual(set(metadata.keys()),
                                     set(["tahoe", "ctime", "mtime"]))
            d.addCallback(_check_child_and_metadata2)

            d.addCallback(lambda res:
                          n.get_child_and_metadata_at_path(u"subdir/subsubdir"))
            def _check_child_and_metadata3(res):
                child, metadata = res
                self.failUnless(isinstance(child, dirnode.DirectoryNode))
                self.failUnlessEqual(set(metadata.keys()),
                                     set(["tahoe", "ctime", "mtime"]))
            d.addCallback(_check_child_and_metadata3)

            # set_uri + metadata
            # it should be possible to add a child without any metadata
            d.addCallback(lambda res: n.set_uri(u"c2",
                                                fake_file_uri, fake_file_uri,
                                                {}))
            d.addCallback(lambda res: n.get_metadata_for(u"c2"))
            d.addCallback(lambda metadata: self.failUnlessEqual(metadata.keys(), ['tahoe']))

            # You can't override the link timestamps.
            d.addCallback(lambda res: n.set_uri(u"c2",
                                                fake_file_uri, fake_file_uri,
                                                { 'tahoe': {'linkcrtime': "bogus"}}))
            d.addCallback(lambda res: n.get_metadata_for(u"c2"))
            def _has_good_linkcrtime(metadata):
                self.failUnless(metadata.has_key('tahoe'))
                self.failUnless(metadata['tahoe'].has_key('linkcrtime'))
                self.failIfEqual(metadata['tahoe']['linkcrtime'], 'bogus')
            d.addCallback(_has_good_linkcrtime)

            # if we don't set any defaults, the child should get timestamps
            d.addCallback(lambda res: n.set_uri(u"c3",
                                                fake_file_uri, fake_file_uri))
            d.addCallback(lambda res: n.get_metadata_for(u"c3"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()),
                                               set(["tahoe", "ctime", "mtime"])))

            # or we can add specific metadata at set_uri() time, which
            # overrides the timestamps
            d.addCallback(lambda res: n.set_uri(u"c4",
                                                fake_file_uri, fake_file_uri,
                                                {"key": "value"}))
            d.addCallback(lambda res: n.get_metadata_for(u"c4"))
            d.addCallback(lambda metadata:
                              self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                              (metadata['key'] == "value"), metadata))

            d.addCallback(lambda res: n.delete(u"c2"))
            d.addCallback(lambda res: n.delete(u"c3"))
            d.addCallback(lambda res: n.delete(u"c4"))

            # set_node + metadata
            # it should be possible to add a child without any metadata
            d.addCallback(lambda res: n.set_node(u"d2", n, {}))
            d.addCallback(lambda res: c.create_dirnode())
            d.addCallback(lambda n2:
                          self.shouldFail(ExistingChildError, "set_node-no",
                                          "child 'd2' already exists",
                                          n.set_node, u"d2", n2,
                                          overwrite=False))
            d.addCallback(lambda res: n.get_metadata_for(u"d2"))
            d.addCallback(lambda metadata: self.failUnlessEqual(metadata.keys(), ['tahoe']))

            # if we don't set any defaults, the child should get timestamps
            d.addCallback(lambda res: n.set_node(u"d3", n))
            d.addCallback(lambda res: n.get_metadata_for(u"d3"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()),
                                               set(["tahoe", "ctime", "mtime"])))

            # or we can add specific metadata at set_node() time, which
            # overrides the timestamps
            d.addCallback(lambda res: n.set_node(u"d4", n,
                                                {"key": "value"}))
            d.addCallback(lambda res: n.get_metadata_for(u"d4"))
            d.addCallback(lambda metadata:
                          self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                          (metadata['key'] == "value"), metadata))

            d.addCallback(lambda res: n.delete(u"d2"))
            d.addCallback(lambda res: n.delete(u"d3"))
            d.addCallback(lambda res: n.delete(u"d4"))

            # metadata through set_children()
            d.addCallback(lambda res:
                          n.set_children({
                              u"e1": (fake_file_uri, fake_file_uri),
                              u"e2": (fake_file_uri, fake_file_uri, {}),
                              u"e3": (fake_file_uri, fake_file_uri,
                                      {"key": "value"}),
                              }))
            d.addCallback(lambda n2: self.failUnlessIdentical(n2, n))
            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "set_children-no",
                                          "child 'e1' already exists",
                                          n.set_children,
                                          { u"e1": (other_file_uri,
                                                    other_file_uri),
                                            u"new": (other_file_uri,
                                                     other_file_uri),
                                            },
                                          overwrite=False))
            # and 'new' should not have been created
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children: self.failIf(u"new" in children))
            d.addCallback(lambda res: n.get_metadata_for(u"e1"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()),
                                               set(["tahoe", "ctime", "mtime"])))
            d.addCallback(lambda res: n.get_metadata_for(u"e2"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(['tahoe'])))
            d.addCallback(lambda res: n.get_metadata_for(u"e3"))
            d.addCallback(lambda metadata:
                              self.failUnless((set(metadata.keys()) == set(["key", "tahoe"]))
                                              and (metadata['key'] == "value"), metadata))

            d.addCallback(lambda res: n.delete(u"e1"))
            d.addCallback(lambda res: n.delete(u"e2"))
            d.addCallback(lambda res: n.delete(u"e3"))

            # metadata through set_nodes()
            d.addCallback(lambda res:
                          n.set_nodes({ u"f1": (n, None),
                                        u"f2": (n, {}),
                                        u"f3": (n, {"key": "value"}),
                                        }))
            d.addCallback(lambda n2: self.failUnlessIdentical(n2, n))
            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "set_nodes-no",
                                          "child 'f1' already exists",
                                          n.set_nodes, { u"f1": (n, None),
                                                         u"new": (n, None), },
                                          overwrite=False))
            # and 'new' should not have been created
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children: self.failIf(u"new" in children))
            d.addCallback(lambda res: n.get_metadata_for(u"f1"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()),
                                               set(["tahoe", "ctime", "mtime"])))
            d.addCallback(lambda res: n.get_metadata_for(u"f2"))
            d.addCallback(
                lambda metadata: self.failUnlessEqual(set(metadata.keys()), set(['tahoe'])))
            d.addCallback(lambda res: n.get_metadata_for(u"f3"))
            d.addCallback(lambda metadata:
                              self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                              (metadata['key'] == "value"), metadata))

            d.addCallback(lambda res: n.delete(u"f1"))
            d.addCallback(lambda res: n.delete(u"f2"))
            d.addCallback(lambda res: n.delete(u"f3"))


            d.addCallback(lambda res:
                          n.set_metadata_for(u"child",
                                             {"tags": ["web2.0-compatible"]}))
            d.addCallback(lambda n1: n1.get_metadata_for(u"child"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(metadata,
                                               {"tags": ["web2.0-compatible"]}))

            def _start(res):
                self._start_timestamp = time.time()
            d.addCallback(_start)
            # simplejson-1.7.1 (as shipped on Ubuntu 'gutsy') rounds all
            # floats to hundredeths (it uses str(num) instead of repr(num)).
            # simplejson-1.7.3 does not have this bug. To prevent this bug
            # from causing the test to fail, stall for more than a few
            # hundrededths of a second.
            d.addCallback(self.stall, 0.1)
            d.addCallback(lambda res: n.add_file(u"timestamps",
                                                 upload.Data("stamp me", convergence="some convergence string")))
            d.addCallback(self.stall, 0.1)
            def _stop(res):
                self._stop_timestamp = time.time()
            d.addCallback(_stop)

            d.addCallback(lambda res: n.get_metadata_for(u"timestamps"))
            def _check_timestamp1(metadata):
                self.failUnless("ctime" in metadata)
                self.failUnless("mtime" in metadata)
                self.failUnlessGreaterOrEqualThan(metadata["ctime"],
                                                  self._start_timestamp)
                self.failUnlessGreaterOrEqualThan(self._stop_timestamp,
                                                  metadata["ctime"])
                self.failUnlessGreaterOrEqualThan(metadata["mtime"],
                                                  self._start_timestamp)
                self.failUnlessGreaterOrEqualThan(self._stop_timestamp,
                                                  metadata["mtime"])
                # Our current timestamp rules say that replacing an existing
                # child should preserve the 'ctime' but update the mtime
                self._old_ctime = metadata["ctime"]
                self._old_mtime = metadata["mtime"]
            d.addCallback(_check_timestamp1)
            d.addCallback(self.stall, 2.0) # accomodate low-res timestamps
            d.addCallback(lambda res: n.set_node(u"timestamps", n))
            d.addCallback(lambda res: n.get_metadata_for(u"timestamps"))
            def _check_timestamp2(metadata):
                self.failUnlessEqual(metadata["ctime"], self._old_ctime,
                                     "%s != %s" % (metadata["ctime"],
                                                   self._old_ctime))
                self.failUnlessGreaterThan(metadata["mtime"], self._old_mtime)
                return n.delete(u"timestamps")
            d.addCallback(_check_timestamp2)

            # also make sure we can add/update timestamps on a
            # previously-existing child that didn't have any, since there are
            # a lot of 0.7.0-generated edges around out there
            d.addCallback(lambda res: n.set_node(u"no_timestamps", n, {}))
            d.addCallback(lambda res: n.set_node(u"no_timestamps", n))
            d.addCallback(lambda res: n.get_metadata_for(u"no_timestamps"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()),
                                               set(["tahoe", "ctime", "mtime"])))
            d.addCallback(lambda res: n.delete(u"no_timestamps"))

            d.addCallback(lambda res: n.delete(u"subdir"))
            d.addCallback(lambda old_child:
                          self.failUnlessEqual(old_child.get_uri(),
                                               self.subdir.get_uri()))

            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted([u"child"])))

            uploadable1 = upload.Data("some data", convergence="converge")
            d.addCallback(lambda res: n.add_file(u"newfile", uploadable1))
            d.addCallback(lambda newnode:
                          self.failUnless(IFileNode.providedBy(newnode)))
            uploadable2 = upload.Data("some data", convergence="stuff")
            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "add_file-no",
                                          "child 'newfile' already exists",
                                          n.add_file, u"newfile",
                                          uploadable2,
                                          overwrite=False))
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted([u"child", u"newfile"])))
            d.addCallback(lambda res: n.get_metadata_for(u"newfile"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()),
                                               set(["tahoe", "ctime", "mtime"])))

            uploadable3 = upload.Data("some data", convergence="converge")
            d.addCallback(lambda res: n.add_file(u"newfile-metadata",
                                                 uploadable3,
                                                 {"key": "value"}))
            d.addCallback(lambda newnode:
                          self.failUnless(IFileNode.providedBy(newnode)))
            d.addCallback(lambda res: n.get_metadata_for(u"newfile-metadata"))
            d.addCallback(lambda metadata:
                              self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                              (metadata['key'] == "value"), metadata))
            d.addCallback(lambda res: n.delete(u"newfile-metadata"))

            d.addCallback(lambda res: n.create_subdirectory(u"subdir2"))
            def _created2(subdir2):
                self.subdir2 = subdir2
                # put something in the way, to make sure it gets overwritten
                return subdir2.add_file(u"child", upload.Data("overwrite me",
                                                              "converge"))
            d.addCallback(_created2)

            d.addCallback(lambda res:
                          n.move_child_to(u"child", self.subdir2))
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted([u"newfile", u"subdir2"])))
            d.addCallback(lambda res: self.subdir2.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted([u"child"])))
            d.addCallback(lambda res: self.subdir2.get(u"child"))
            d.addCallback(lambda child:
                          self.failUnlessEqual(child.get_uri(),
                                               fake_file_uri))

            # move it back, using new_child_name=
            d.addCallback(lambda res:
                          self.subdir2.move_child_to(u"child", n, u"newchild"))
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()),
                                               sorted([u"newchild", u"newfile",
                                                       u"subdir2"])))
            d.addCallback(lambda res: self.subdir2.list())
            d.addCallback(lambda children:
                          self.failUnlessEqual(sorted(children.keys()), []))

            # now make sure that we honor overwrite=False
            d.addCallback(lambda res:
                          self.subdir2.set_uri(u"newchild",
                                               other_file_uri, other_file_uri))

            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "move_child_to-no",
                                          "child 'newchild' already exists",
                                          n.move_child_to, u"newchild",
                                          self.subdir2,
                                          overwrite=False))
            d.addCallback(lambda res: self.subdir2.get(u"newchild"))
            d.addCallback(lambda child:
                          self.failUnlessEqual(child.get_uri(),
                                               other_file_uri))

            return d

        d.addCallback(_then)

        d.addErrback(self.explain_error)
        return d

    def test_create_subdirectory(self):
        self.basedir = "dirnode/Dirnode/test_create_subdirectory"
        self.set_up_grid()
        c = self.g.clients[0]
        nm = c.nodemaker

        d = c.create_dirnode()
        def _then(n):
            # /
            self.rootnode = n
            fake_file_uri = make_mutable_file_uri()
            other_file_uri = make_mutable_file_uri()
            md = {"metakey": "metavalue"}
            kids = {u"kid1": (nm.create_from_cap(fake_file_uri), None),
                    u"kid2": (nm.create_from_cap(other_file_uri), md),
                    }
            d = n.create_subdirectory(u"subdir", kids)
            def _check(sub):
                d = n.get_child_at_path(u"subdir")
                d.addCallback(lambda sub2: self.failUnlessEqual(sub2.get_uri(),
                                                                sub.get_uri()))
                d.addCallback(lambda ign: sub.list())
                return d
            d.addCallback(_check)
            def _check_kids(kids2):
                self.failUnlessEqual(sorted(kids.keys()), sorted(kids2.keys()))
                self.failUnlessEqual(kids2[u"kid2"][1]["metakey"], "metavalue")
            d.addCallback(_check_kids)
            return d
        d.addCallback(_then)
        return d

class Packing(unittest.TestCase):
    # This is a base32-encoded representation of the directory tree
    # root/file1
    # root/file2
    # root/file3
    # as represented after being fed to _pack_contents.
    # We have it here so we can decode it, feed it to
    # _unpack_contents, and verify that _unpack_contents
    # works correctly.

    known_tree = "GM4TOORVHJTGS3DFGEWDSNJ2KVJESOSDJBFTU33MPB2GS3LZNVYG6N3GGI3WU5TIORTXC3DOMJ2G4NB2MVWXUZDONBVTE5LNGRZWK2LYN55GY23XGNYXQMTOMZUWU5TENN4DG23ZG5UTO2L2NQ2DO6LFMRWDMZJWGRQTUMZ2GEYDUMJQFQYTIMZ22XZKZORX5XS7CAQCSK3URR6QOHISHRCMGER5LRFSZRNAS5ZSALCS6TWFQAE754IVOIKJVK73WZPP3VUUEDTX3WHTBBZ5YX3CEKHCPG3ZWQLYA4QM6LDRCF7TJQYWLIZHKGN5ROA3AUZPXESBNLQQ6JTC2DBJU2D47IZJTLR3PKZ4RVF57XLPWY7FX7SZV3T6IJ3ORFW37FXUPGOE3ROPFNUX5DCGMAQJ3PGGULBRGM3TU6ZCMN2GS3LFEI5CAMJSGQ3DMNRTHA4TOLRUGI3TKNRWGEWCAITUMFUG6ZJCHIQHWITMNFXGW3LPORUW2ZJCHIQDCMRUGY3DMMZYHE3S4NBSG42TMNRRFQQCE3DJNZVWG4TUNFWWKIR2EAYTENBWGY3DGOBZG4XDIMRXGU3DMML5FQQCE3LUNFWWKIR2EAYTENBWGY3DGOBZG4XDIMRXGU3DMML5FQWDGOJRHI2TUZTJNRSTELBZGQ5FKUSJHJBUQSZ2MFYGKZ3SOBSWQ43IO52WO23CNAZWU3DUGVSWSNTIOE5DK33POVTW4ZLNMNWDK6DHPA2GS2THNF2W25DEN5VGY2LQNFRGG5DKNNRHO5TZPFTWI6LNMRYGQ2LCGJTHM4J2GM5DCMB2GQWDCNBSHKVVQBGRYMACKJ27CVQ6O6B4QPR72RFVTGOZUI76XUSWAX73JRV5PYRHMIFYZIA25MXDPGUGML6M2NMRSG4YD4W4K37ZDYSXHMJ3IUVT4F64YTQQVBJFFFOUC7J7LAB2VFCL5UKKGMR2D3F4EPOYC7UYWQZNR5KXHBSNXLCNBX2SNF22DCXJIHSMEKWEWOG5XCJEVVZ7UW5IB6I64XXQSJ34B5CAYZGZIIMR6LBRGMZTU6ZCMN2GS3LFEI5CAMJSGQ3DMNRTHA4TOLRUGMYDEMJYFQQCE5DBNBXWKIR2EB5SE3DJNZVW233UNFWWKIR2EAYTENBWGY3DGOBZG4XDIMZQGIYTQLBAEJWGS3TLMNZHI2LNMURDUIBRGI2DMNRWGM4DSNZOGQZTAMRRHB6SYIBCNV2GS3LFEI5CAMJSGQ3DMNRTHA4TOLRUGMYDEMJYPUWCYMZZGU5DKOTGNFWGKMZMHE2DUVKSJE5EGSCLHJRW25DDPBYTO2DXPB3GM6DBNYZTI6LJMV3DM2LWNB4TU4LWMNSWW3LKORXWK5DEMN3TI23NNE3WEM3SORRGY5THPA3TKNBUMNZG453BOF2GSZLXMVWWI3DJOFZW623RHIZTUMJQHI2SYMJUGI5BOSHWDPG3WKPAVXCF3XMKA7QVIWPRMWJHDTQHD27AHDCPJWDQENQ5H5ZZILTXQNIXXCIW4LKQABU2GCFRG5FHQN7CHD7HF4EKNRZFIV2ZYQIBM7IQU7F4RGB3XCX3FREPBKQ7UCICHVWPCYFGA6OLH3J45LXQ6GWWICJ3PGWJNLZ7PCRNLAPNYUGU6BENS7OXMBEOOFRIZV3PF2FFWZ5WHDPKXERYP7GNHKRMGEZTOOT3EJRXI2LNMURDUIBRGI2DMNRWGM4DSNZOGQZTGNRSGY4SYIBCORQWQ33FEI5CA6ZCNRUW423NN52GS3LFEI5CAMJSGQ3DMNRTHA4TOLRUGMZTMMRWHEWCAITMNFXGWY3SORUW2ZJCHIQDCMRUGY3DMMZYHE3S4NBTGM3DENRZPUWCAITNORUW2ZJCHIQDCMRUGY3DMMZYHE3S4NBTGM3DENRZPUWCY==="

    def test_unpack_and_pack_behavior(self):
        known_tree = b32decode(self.known_tree)
        nodemaker = NodeMaker(None, None, None,
                              None, None, None,
                              {"k": 3, "n": 10}, None)
        writecap = "URI:SSK-RO:e3mdrzfwhoq42hy5ubcz6rp3o4:ybyibhnp3vvwuq2vaw2ckjmesgkklfs6ghxleztqidihjyofgw7q"
        filenode = nodemaker.create_from_cap(writecap)
        node = dirnode.DirectoryNode(filenode, nodemaker, None)
        children = node._unpack_contents(known_tree)
        self._check_children(children)

        packed_children = node._pack_contents(children)
        children = node._unpack_contents(packed_children)
        self._check_children(children)

    def _check_children(self, children):
        # Are all the expected child nodes there?
        self.failUnless(children.has_key(u'file1'))
        self.failUnless(children.has_key(u'file2'))
        self.failUnless(children.has_key(u'file3'))

        # Are the metadata for child 3 right?
        file3_rocap = "URI:CHK:cmtcxq7hwxvfxan34yiev6ivhy:qvcekmjtoetdcw4kmi7b3rtblvgx7544crnwaqtiewemdliqsokq:3:10:5"
        file3_rwcap = "URI:CHK:cmtcxq7hwxvfxan34yiev6ivhy:qvcekmjtoetdcw4kmi7b3rtblvgx7544crnwaqtiewemdliqsokq:3:10:5"
        file3_metadata = {'ctime': 1246663897.4336269, 'tahoe': {'linkmotime': 1246663897.4336269, 'linkcrtime': 1246663897.4336269}, 'mtime': 1246663897.4336269}
        self.failUnlessEqual(file3_metadata, children[u'file3'][1])
        self.failUnlessEqual(file3_rocap,
                             children[u'file3'][0].get_readonly_uri())
        self.failUnlessEqual(file3_rwcap,
                             children[u'file3'][0].get_uri())

        # Are the metadata for child 2 right?
        file2_rocap = "URI:CHK:apegrpehshwugkbh3jlt5ei6hq:5oougnemcl5xgx4ijgiumtdojlipibctjkbwvyygdymdphib2fvq:3:10:4"
        file2_rwcap = "URI:CHK:apegrpehshwugkbh3jlt5ei6hq:5oougnemcl5xgx4ijgiumtdojlipibctjkbwvyygdymdphib2fvq:3:10:4"
        file2_metadata = {'ctime': 1246663897.430218, 'tahoe': {'linkmotime': 1246663897.430218, 'linkcrtime': 1246663897.430218}, 'mtime': 1246663897.430218}
        self.failUnlessEqual(file2_metadata, children[u'file2'][1])
        self.failUnlessEqual(file2_rocap,
                             children[u'file2'][0].get_readonly_uri())
        self.failUnlessEqual(file2_rwcap,
                             children[u'file2'][0].get_uri())

        # Are the metadata for child 1 right?
        file1_rocap = "URI:CHK:olxtimympo7f27jvhtgqlnbtn4:emzdnhk2um4seixozlkw3qx2nfijvdkx3ky7i7izl47yedl6e64a:3:10:10"
        file1_rwcap = "URI:CHK:olxtimympo7f27jvhtgqlnbtn4:emzdnhk2um4seixozlkw3qx2nfijvdkx3ky7i7izl47yedl6e64a:3:10:10"
        file1_metadata = {'ctime': 1246663897.4275661, 'tahoe': {'linkmotime': 1246663897.4275661, 'linkcrtime': 1246663897.4275661}, 'mtime': 1246663897.4275661}
        self.failUnlessEqual(file1_metadata, children[u'file1'][1])
        self.failUnlessEqual(file1_rocap,
                             children[u'file1'][0].get_readonly_uri())
        self.failUnlessEqual(file1_rwcap,
                             children[u'file1'][0].get_uri())

class FakeMutableFile:
    counter = 0
    def __init__(self, initial_contents=""):
        self.data = initial_contents
        counter = FakeMutableFile.counter
        FakeMutableFile.counter += 1
        writekey = hashutil.ssk_writekey_hash(str(counter))
        fingerprint = hashutil.ssk_pubkey_fingerprint_hash(str(counter))
        self.uri = uri.WriteableSSKFileURI(writekey, fingerprint)
    def get_uri(self):
        return self.uri.to_string()
    def download_best_version(self):
        return defer.succeed(self.data)
    def get_writekey(self):
        return "writekey"
    def is_readonly(self):
        return False
    def is_mutable(self):
        return True
    def modify(self, modifier):
        self.data = modifier(self.data, None, True)
        return defer.succeed(None)

class FakeNodeMaker(NodeMaker):
    def create_mutable_file(self, contents="", keysize=None):
        return defer.succeed(FakeMutableFile(contents))

class FakeClient2(Client):
    def __init__(self):
        self.nodemaker = FakeNodeMaker(None, None, None,
                                       None, None, None,
                                       {"k":3,"n":10}, None)
    def create_node_from_uri(self, rwcap, rocap):
        return self.nodemaker.create_from_cap(rwcap, rocap)

class Dirnode2(unittest.TestCase, testutil.ShouldFailMixin):
    def setUp(self):
        self.client = FakeClient2()
        self.nodemaker = self.client.nodemaker

    def test_from_future(self):
        # create a dirnode that contains unknown URI types, and make sure we
        # tolerate them properly. Since dirnodes aren't allowed to add
        # unknown node types, we have to be tricky.
        d = self.nodemaker.create_new_mutable_directory()
        future_writecap = "x-tahoe-crazy://I_am_from_the_future."
        future_readcap = "x-tahoe-crazy-readonly://I_am_from_the_future."
        future_node = UnknownNode(future_writecap, future_readcap)
        def _then(n):
            self._node = n
            return n.set_node(u"future", future_node)
        d.addCallback(_then)

        # we should be prohibited from adding an unknown URI to a directory,
        # since we don't know how to diminish the cap to a readcap (for the
        # dirnode's rocap slot), and we don't want to accidentally grant
        # write access to a holder of the dirnode's readcap.
        d.addCallback(lambda ign:
             self.shouldFail(CannotPackUnknownNodeError,
                             "copy unknown",
                             "cannot pack unknown node as child add",
                             self._node.set_uri, u"add",
                             future_writecap, future_readcap))
        d.addCallback(lambda ign: self._node.list())
        def _check(children):
            self.failUnlessEqual(len(children), 1)
            (fn, metadata) = children[u"future"]
            self.failUnless(isinstance(fn, UnknownNode), fn)
            self.failUnlessEqual(fn.get_uri(), future_writecap)
            self.failUnlessEqual(fn.get_readonly_uri(), future_readcap)
            # but we *should* be allowed to copy this node, because the
            # UnknownNode contains all the information that was in the
            # original directory (readcap and writecap), so we're preserving
            # everything.
            return self._node.set_node(u"copy", fn)
        d.addCallback(_check)
        d.addCallback(lambda ign: self._node.list())
        def _check2(children):
            self.failUnlessEqual(len(children), 2)
            (fn, metadata) = children[u"copy"]
            self.failUnless(isinstance(fn, UnknownNode), fn)
            self.failUnlessEqual(fn.get_uri(), future_writecap)
            self.failUnlessEqual(fn.get_readonly_uri(), future_readcap)
        return d

class DeepStats(unittest.TestCase):
    timeout = 240 # It takes longer than 120 seconds on Francois's arm box.
    def test_stats(self):
        ds = dirnode.DeepStats(None)
        ds.add("count-files")
        ds.add("size-immutable-files", 123)
        ds.histogram("size-files-histogram", 123)
        ds.max("largest-directory", 444)

        s = ds.get_results()
        self.failUnlessEqual(s["count-files"], 1)
        self.failUnlessEqual(s["size-immutable-files"], 123)
        self.failUnlessEqual(s["largest-directory"], 444)
        self.failUnlessEqual(s["count-literal-files"], 0)

        ds.add("count-files")
        ds.add("size-immutable-files", 321)
        ds.histogram("size-files-histogram", 321)
        ds.max("largest-directory", 2)

        s = ds.get_results()
        self.failUnlessEqual(s["count-files"], 2)
        self.failUnlessEqual(s["size-immutable-files"], 444)
        self.failUnlessEqual(s["largest-directory"], 444)
        self.failUnlessEqual(s["count-literal-files"], 0)
        self.failUnlessEqual(s["size-files-histogram"],
                             [ (101, 316, 1), (317, 1000, 1) ])

        ds = dirnode.DeepStats(None)
        for i in range(1, 1100):
            ds.histogram("size-files-histogram", i)
        ds.histogram("size-files-histogram", 4*1000*1000*1000*1000) # 4TB
        s = ds.get_results()
        self.failUnlessEqual(s["size-files-histogram"],
                             [ (1, 3, 3),
                               (4, 10, 7),
                               (11, 31, 21),
                               (32, 100, 69),
                               (101, 316, 216),
                               (317, 1000, 684),
                               (1001, 3162, 99),
                               (3162277660169L, 10000000000000L, 1),
                               ])

class UCWEingMutableFileNode(MutableFileNode):
    please_ucwe_after_next_upload = False

    def _upload(self, new_contents, servermap):
        d = MutableFileNode._upload(self, new_contents, servermap)
        def _ucwe(res):
            if self.please_ucwe_after_next_upload:
                self.please_ucwe_after_next_upload = False
                raise UncoordinatedWriteError()
            return res
        d.addCallback(_ucwe)
        return d

class UCWEingNodeMaker(NodeMaker):
    def _create_mutable(self, cap):
        n = UCWEingMutableFileNode(self.storage_broker, self.secret_holder,
                                   self.default_encoding_parameters,
                                   self.history)
        return n.init_from_uri(cap)


class Deleter(GridTestMixin, unittest.TestCase):
    timeout = 3600 # It takes longer than 433 seconds on Zandr's ARM box.
    def test_retry(self):
        # ticket #550, a dirnode.delete which experiences an
        # UncoordinatedWriteError will fail with an incorrect "you're
        # deleting something which isn't there" NoSuchChildError exception.

        # to trigger this, we start by creating a directory with a single
        # file in it. Then we create a special dirnode that uses a modified
        # MutableFileNode which will raise UncoordinatedWriteError once on
        # demand. We then call dirnode.delete, which ought to retry and
        # succeed.

        self.basedir = self.mktemp()
        self.set_up_grid()
        c0 = self.g.clients[0]
        d = c0.create_dirnode()
        small = upload.Data("Small enough for a LIT", None)
        def _created_dir(dn):
            self.root = dn
            self.root_uri = dn.get_uri()
            return dn.add_file(u"file", small)
        d.addCallback(_created_dir)
        def _do_delete(ignored):
            nm = UCWEingNodeMaker(c0.storage_broker, c0._secret_holder,
                                  c0.get_history(), c0.getServiceNamed("uploader"),
                                  c0.downloader,
                                  c0.download_cache_dirman,
                                  c0.get_encoding_parameters(),
                                  c0._key_generator)
            n = nm.create_from_cap(self.root_uri)
            assert n._node.please_ucwe_after_next_upload == False
            n._node.please_ucwe_after_next_upload = True
            # This should succeed, not raise an exception
            return n.delete(u"file")
        d.addCallback(_do_delete)

        return d

class Adder(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin):

    def test_overwrite(self):
        # note: This functionality could be tested without actually creating
        # several RSA keys. It would be faster without the GridTestMixin: use
        # dn.set_node(nodemaker.create_from_cap(make_chk_file_uri())) instead
        # of dn.add_file, and use a special NodeMaker that creates fake
        # mutable files.
        self.basedir = "dirnode/Adder/test_overwrite"
        self.set_up_grid()
        c = self.g.clients[0]
        fileuri = make_chk_file_uri(1234)
        filenode = c.nodemaker.create_from_cap(fileuri)
        d = c.create_dirnode()

        def _create_directory_tree(root_node):
            # Build
            # root/file1
            # root/file2
            # root/dir1
            d = root_node.add_file(u'file1', upload.Data("Important Things",
                None))
            d.addCallback(lambda res:
                root_node.add_file(u'file2', upload.Data("Sekrit Codes", None)))
            d.addCallback(lambda res:
                root_node.create_subdirectory(u"dir1"))
            d.addCallback(lambda res: root_node)
            return d

        d.addCallback(_create_directory_tree)

        def _test_adder(root_node):
            d = root_node.set_node(u'file1', filenode)
            # We've overwritten file1. Let's try it with a directory
            d.addCallback(lambda res:
                root_node.create_subdirectory(u'dir2'))
            d.addCallback(lambda res:
                root_node.set_node(u'dir2', filenode))
            # We try overwriting a file with a child while also specifying
            # overwrite=False. We should receive an ExistingChildError
            # when we do this.
            d.addCallback(lambda res:
                self.shouldFail(ExistingChildError, "set_node",
                                "child 'file1' already exists",
                               root_node.set_node, u"file1",
                               filenode, overwrite=False))
            # If we try with a directory, we should see the same thing
            d.addCallback(lambda res:
                self.shouldFail(ExistingChildError, "set_node",
                                "child 'dir1' already exists",
                                root_node.set_node, u'dir1', filenode,
                                overwrite=False))
            d.addCallback(lambda res:
                 root_node.set_node(u'file1', filenode,
                                    overwrite="only-files"))
            d.addCallback(lambda res:
                 self.shouldFail(ExistingChildError, "set_node",
                                "child 'dir1' already exists",
                                root_node.set_node, u'dir1', filenode,
                                overwrite="only-files"))
            return d

        d.addCallback(_test_adder)
        return d
