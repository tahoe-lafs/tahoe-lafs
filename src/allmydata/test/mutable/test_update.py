import re
from twisted.trial import unittest
from twisted.internet import defer
from allmydata.interfaces import MDMF_VERSION
from allmydata.mutable.filenode import MutableFileNode
from allmydata.mutable.publish import MutableData, DEFAULT_MAX_SEGMENT_SIZE
from ..no_network import GridTestMixin
from .. import common_util as testutil

# We should really force a smaller segsize for the duration of the tests, to
# let them run faster, but Many of them tests depend upon a specific segment
# size. Factor out this expectation here, to start the process of cleaning
# this up.
SEGSIZE = 128*1024

class Update(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin):
    timeout = 400 # these tests are too big, 120s is not enough on slow
                  # platforms
    def setUp(self):
        GridTestMixin.setUp(self)
        self.basedir = self.mktemp()
        self.set_up_grid(num_servers=13)
        self.c = self.g.clients[0]
        self.nm = self.c.nodemaker
        # self.data should be at least three segments long.
        td = "testdata "
        self.data = td*(int(3*SEGSIZE/len(td))+10) # currently about 400kB
        assert len(self.data) > 3*SEGSIZE
        self.small_data = "test data" * 10 # 90 B; SDMF


    def do_upload_sdmf(self):
        d = self.nm.create_mutable_file(MutableData(self.small_data))
        def _then(n):
            assert isinstance(n, MutableFileNode)
            self.sdmf_node = n
        d.addCallback(_then)
        return d

    def do_upload_mdmf(self):
        d = self.nm.create_mutable_file(MutableData(self.data),
                                        version=MDMF_VERSION)
        def _then(n):
            assert isinstance(n, MutableFileNode)
            self.mdmf_node = n
        d.addCallback(_then)
        return d

    def _test_replace(self, offset, new_data):
        expected = self.data[:offset]+new_data+self.data[offset+len(new_data):]
        d0 = self.do_upload_mdmf()
        def _run(ign):
            d = defer.succeed(None)
            d.addCallback(lambda ign: self.mdmf_node.get_best_mutable_version())
            d.addCallback(lambda mv: mv.update(MutableData(new_data), offset))
            d.addCallback(lambda ign: self.mdmf_node.download_best_version())
            def _check(results):
                if results != expected:
                    print
                    print "got: %s ... %s" % (results[:20], results[-20:])
                    print "exp: %s ... %s" % (expected[:20], expected[-20:])
                    self.fail("results != expected")
            d.addCallback(_check)
            return d
        d0.addCallback(_run)
        return d0

    def test_append(self):
        # We should be able to append data to a mutable file and get
        # what we expect.
        return self._test_replace(len(self.data), "appended")

    def test_replace_middle(self):
        # We should be able to replace data in the middle of a mutable
        # file and get what we expect back.
        return self._test_replace(100, "replaced")

    def test_replace_beginning(self):
        # We should be able to replace data at the beginning of the file
        # without truncating the file
        return self._test_replace(0, "beginning")

    def test_replace_segstart1(self):
        return self._test_replace(128*1024+1, "NNNN")

    def test_replace_zero_length_beginning(self):
        return self._test_replace(0, "")

    def test_replace_zero_length_middle(self):
        return self._test_replace(50, "")

    def test_replace_zero_length_segstart1(self):
        return self._test_replace(128*1024+1, "")

    def test_replace_and_extend(self):
        # We should be able to replace data in the middle of a mutable
        # file and extend that mutable file and get what we expect.
        return self._test_replace(100, "modified " * 100000)


    def _check_differences(self, got, expected):
        # displaying arbitrary file corruption is tricky for a
        # 1MB file of repeating data,, so look for likely places
        # with problems and display them separately
        gotmods = [mo.span() for mo in re.finditer('([A-Z]+)', got)]
        expmods = [mo.span() for mo in re.finditer('([A-Z]+)', expected)]
        gotspans = ["%d:%d=%s" % (start,end,got[start:end])
                    for (start,end) in gotmods]
        expspans = ["%d:%d=%s" % (start,end,expected[start:end])
                    for (start,end) in expmods]
        #print "expecting: %s" % expspans

        if got != expected:
            print "differences:"
            for segnum in range(len(expected)//SEGSIZE):
                start = segnum * SEGSIZE
                end = (segnum+1) * SEGSIZE
                got_ends = "%s .. %s" % (got[start:start+20], got[end-20:end])
                exp_ends = "%s .. %s" % (expected[start:start+20], expected[end-20:end])
                if got_ends != exp_ends:
                    print "expected[%d]: %s" % (start, exp_ends)
                    print "got     [%d]: %s" % (start, got_ends)
            if expspans != gotspans:
                print "expected: %s" % expspans
                print "got     : %s" % gotspans
            open("EXPECTED","wb").write(expected)
            open("GOT","wb").write(got)
            print "wrote data to EXPECTED and GOT"
            self.fail("didn't get expected data")


    def test_replace_locations(self):
        # exercise fencepost conditions
        suspects = range(SEGSIZE-3, SEGSIZE+1)+range(2*SEGSIZE-3, 2*SEGSIZE+1)
        letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        d0 = self.do_upload_mdmf()
        def _run(ign):
            expected = self.data
            d = defer.succeed(None)
            for offset in suspects:
                new_data = letters.next()*2 # "AA", then "BB", etc
                expected = expected[:offset]+new_data+expected[offset+2:]
                d.addCallback(lambda ign:
                              self.mdmf_node.get_best_mutable_version())
                def _modify(mv, offset=offset, new_data=new_data):
                    # close over 'offset','new_data'
                    md = MutableData(new_data)
                    return mv.update(md, offset)
                d.addCallback(_modify)
                d.addCallback(lambda ignored:
                              self.mdmf_node.download_best_version())
                d.addCallback(self._check_differences, expected)
            return d
        d0.addCallback(_run)
        return d0


    def test_append_power_of_two(self):
        # If we attempt to extend a mutable file so that its segment
        # count crosses a power-of-two boundary, the update operation
        # should know how to reencode the file.

        # Note that the data populating self.mdmf_node is about 900 KiB
        # long -- this is 7 segments in the default segment size. So we
        # need to add 2 segments worth of data to push it over a
        # power-of-two boundary.
        segment = "a" * DEFAULT_MAX_SEGMENT_SIZE
        new_data = self.data + (segment * 2)
        d0 = self.do_upload_mdmf()
        def _run(ign):
            d = defer.succeed(None)
            d.addCallback(lambda ign: self.mdmf_node.get_best_mutable_version())
            d.addCallback(lambda mv: mv.update(MutableData(segment * 2),
                                               len(self.data)))
            d.addCallback(lambda ign: self.mdmf_node.download_best_version())
            d.addCallback(lambda results:
                          self.failUnlessEqual(results, new_data))
            return d
        d0.addCallback(_run)
        return d0

    def test_update_sdmf(self):
        # Running update on a single-segment file should still work.
        new_data = self.small_data + "appended"
        d0 = self.do_upload_sdmf()
        def _run(ign):
            d = defer.succeed(None)
            d.addCallback(lambda ign: self.sdmf_node.get_best_mutable_version())
            d.addCallback(lambda mv: mv.update(MutableData("appended"),
                                               len(self.small_data)))
            d.addCallback(lambda ign: self.sdmf_node.download_best_version())
            d.addCallback(lambda results:
                          self.failUnlessEqual(results, new_data))
            return d
        d0.addCallback(_run)
        return d0

    def test_replace_in_last_segment(self):
        # The wrapper should know how to handle the tail segment
        # appropriately.
        replace_offset = len(self.data) - 100
        new_data = self.data[:replace_offset] + "replaced"
        rest_offset = replace_offset + len("replaced")
        new_data += self.data[rest_offset:]
        d0 = self.do_upload_mdmf()
        def _run(ign):
            d = defer.succeed(None)
            d.addCallback(lambda ign: self.mdmf_node.get_best_mutable_version())
            d.addCallback(lambda mv: mv.update(MutableData("replaced"),
                                               replace_offset))
            d.addCallback(lambda ign: self.mdmf_node.download_best_version())
            d.addCallback(lambda results:
                          self.failUnlessEqual(results, new_data))
            return d
        d0.addCallback(_run)
        return d0

    def test_multiple_segment_replace(self):
        replace_offset = 2 * DEFAULT_MAX_SEGMENT_SIZE
        new_data = self.data[:replace_offset]
        new_segment = "a" * DEFAULT_MAX_SEGMENT_SIZE
        new_data += 2 * new_segment
        new_data += "replaced"
        rest_offset = len(new_data)
        new_data += self.data[rest_offset:]
        d0 = self.do_upload_mdmf()
        def _run(ign):
            d = defer.succeed(None)
            d.addCallback(lambda ign: self.mdmf_node.get_best_mutable_version())
            d.addCallback(lambda mv: mv.update(MutableData((2 * new_segment) + "replaced"),
                                               replace_offset))
            d.addCallback(lambda ignored: self.mdmf_node.download_best_version())
            d.addCallback(lambda results:
                          self.failUnlessEqual(results, new_data))
            return d
        d0.addCallback(_run)
        return d0
