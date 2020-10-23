"""
Ported to Python 3.
"""
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from future.utils import PY2, bchr
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

# system-level upload+download roundtrip test, but using shares created from
# a previous run. This asserts that the current code is capable of decoding
# shares from a previous version.

import six
import os
from twisted.python.filepath import (
    FilePath,
)
from twisted.trial import unittest
from twisted.internet import defer, reactor
from allmydata import uri
from allmydata.storage.server import storage_index_to_dir
from allmydata.util import base32, fileutil, spans, log, hashutil
from allmydata.util.consumer import download_to_data, MemoryConsumer
from allmydata.immutable import upload, layout
from allmydata.test.no_network import GridTestMixin, NoNetworkServer
from allmydata.test.common import ShouldFailMixin
from allmydata.interfaces import NotEnoughSharesError, NoSharesError, \
     DownloadStopped
from allmydata.immutable.downloader.common import BadSegmentNumberError, \
     BadCiphertextHashError, COMPLETE, OVERDUE, DEAD
from allmydata.immutable.downloader.status import DownloadStatus
from allmydata.immutable.downloader.fetcher import SegmentFetcher
from allmydata.codec import CRSDecoder
from foolscap.eventual import eventually, fireEventually, flushEventualQueue

if six.PY3:
    long = int

plaintext = b"This is a moderate-sized file.\n" * 10
mutable_plaintext = b"This is a moderate-sized mutable file.\n" * 10

def load_share_data(root, name_template, placement):
    return {
        client_num: {
            share_num: root.child(
                name_template.format(
                    client_num,
                    share_num,
                ),
            ).getContent()
            for share_num
            in share_nums
        }
        for client_num, share_nums
        in placement.items()
    }

# The data for immutable_shares and mutable_shares were created by
# _Base.create_shares(), written to disk, lived in this source file for a
# while, then were moved into individual files.  These shares were created by
# 1.2.0-r3247, a version that's probably fairly close to 1.3.0 .
immutable_uri = b"URI:CHK:g4i6qkk7mlj4vkl5ncg6dwo73i:qcas2ebousfk3q5rkl2ncayeku52kpyse76v5yeel2t2eaa4f6ha:3:10:310"
immutable_shares = load_share_data(
    FilePath(__file__).sibling(u"data"),
    u"immutable_share.client={},share={}",
    {
        0: [0, 5],
        1: [2, 7],
        2: [1, 6],
        3: [4, 9],
        4: [3, 8],
    },
)

mutable_uri = b"URI:SSK:vfvcbdfbszyrsaxchgevhmmlii:euw4iw7bbnkrrwpzuburbhppuxhc3gwxv26f6imekhz7zyw2ojnq"
mutable_shares = load_share_data(
    FilePath(__file__).sibling(u"data"),
    u"mutable_share.client={},share={}",
    {
        0: [2, 7],
        1: [3, 8],
        2: [4, 9],
        3: [1, 6],
        4: [0, 5],
    },
)


class _Base(GridTestMixin, ShouldFailMixin):

    def create_shares(self, ignored=None):
        u = upload.Data(plaintext, None)
        d = self.c0.upload(u)
        f = open("stored_shares.py", "w")
        def _created_immutable(ur):
            # write the generated shares and URI to a file, which can then be
            # incorporated into this one next time.
            f.write('immutable_uri = b"%s"\n' % ur.get_uri())
            f.write('immutable_shares = {\n')
            si = uri.from_string(ur.get_uri()).get_storage_index()
            si_dir = storage_index_to_dir(si)
            for (i,ss,ssdir) in self.iterate_servers():
                sharedir = os.path.join(ssdir, "shares", si_dir)
                shares = {}
                for fn in os.listdir(sharedir):
                    shnum = int(fn)
                    sharedata = open(os.path.join(sharedir, fn), "rb").read()
                    shares[shnum] = sharedata
                fileutil.rm_dir(sharedir)
                if shares:
                    f.write(' %d: { # client[%d]\n' % (i, i))
                    for shnum in sorted(shares.keys()):
                        f.write('  %d: base32.a2b(b"%s"),\n' %
                                (shnum, base32.b2a(shares[shnum])))
                    f.write('    },\n')
            f.write('}\n')
            f.write('\n')

        d.addCallback(_created_immutable)

        d.addCallback(lambda ignored:
                      self.c0.create_mutable_file(mutable_plaintext))
        def _created_mutable(n):
            f.write('mutable_uri = b"%s"\n' % n.get_uri())
            f.write('mutable_shares = {\n')
            si = uri.from_string(n.get_uri()).get_storage_index()
            si_dir = storage_index_to_dir(si)
            for (i,ss,ssdir) in self.iterate_servers():
                sharedir = os.path.join(ssdir, "shares", si_dir)
                shares = {}
                for fn in os.listdir(sharedir):
                    shnum = int(fn)
                    sharedata = open(os.path.join(sharedir, fn), "rb").read()
                    shares[shnum] = sharedata
                fileutil.rm_dir(sharedir)
                if shares:
                    f.write(' %d: { # client[%d]\n' % (i, i))
                    for shnum in sorted(shares.keys()):
                        f.write('  %d: base32.a2b(b"%s"),\n' %
                                (shnum, base32.b2a(shares[shnum])))
                    f.write('    },\n')
            f.write('}\n')

            f.close()
        d.addCallback(_created_mutable)

        def _done(ignored):
            f.close()
        d.addCallback(_done)

        return d

    def load_shares(self, ignored=None):
        # this uses the data generated by create_shares() to populate the
        # storage servers with pre-generated shares
        si = uri.from_string(immutable_uri).get_storage_index()
        si_dir = storage_index_to_dir(si)
        for i in immutable_shares:
            shares = immutable_shares[i]
            for shnum in shares:
                dn = os.path.join(self.get_serverdir(i), "shares", si_dir)
                fileutil.make_dirs(dn)
                fn = os.path.join(dn, str(shnum))
                f = open(fn, "wb")
                f.write(shares[shnum])
                f.close()

        si = uri.from_string(mutable_uri).get_storage_index()
        si_dir = storage_index_to_dir(si)
        for i in mutable_shares:
            shares = mutable_shares[i]
            for shnum in shares:
                dn = os.path.join(self.get_serverdir(i), "shares", si_dir)
                fileutil.make_dirs(dn)
                fn = os.path.join(dn, str(shnum))
                f = open(fn, "wb")
                f.write(shares[shnum])
                f.close()

    def download_immutable(self, ignored=None):
        n = self.c0.create_node_from_uri(immutable_uri)
        d = download_to_data(n)
        def _got_data(data):
            self.failUnlessEqual(data, plaintext)
        d.addCallback(_got_data)
        # make sure we can use the same node twice
        d.addCallback(lambda ign: download_to_data(n))
        d.addCallback(_got_data)
        return d

    def download_mutable(self, ignored=None):
        n = self.c0.create_node_from_uri(mutable_uri)
        d = n.download_best_version()
        def _got_data(data):
            self.failUnlessEqual(data, mutable_plaintext)
        d.addCallback(_got_data)
        return d

class DownloadTest(_Base, unittest.TestCase):
    def test_download(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # do this to create the shares
        #return self.create_shares()

        self.load_shares()
        d = self.download_immutable()
        d.addCallback(self.download_mutable)
        return d

    def test_download_failover(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        self.load_shares()
        si = uri.from_string(immutable_uri).get_storage_index()
        si_dir = storage_index_to_dir(si)

        n = self.c0.create_node_from_uri(immutable_uri)
        d = download_to_data(n)
        def _got_data(data):
            self.failUnlessEqual(data, plaintext)
        d.addCallback(_got_data)

        def _clobber_some_shares(ign):
            # find the three shares that were used, and delete them. Then
            # download again, forcing the downloader to fail over to other
            # shares
            for s in n._cnode._node._shares:
                for clientnum in immutable_shares:
                    for shnum in immutable_shares[clientnum]:
                        if s._shnum == shnum:
                            fn = os.path.join(self.get_serverdir(clientnum),
                                              "shares", si_dir, str(shnum))
                            os.unlink(fn)
        d.addCallback(_clobber_some_shares)
        d.addCallback(lambda ign: download_to_data(n))
        d.addCallback(_got_data)

        def _clobber_most_shares(ign):
            # delete all but one of the shares that are still alive
            live_shares = [s for s in n._cnode._node._shares if s.is_alive()]
            save_me = live_shares[0]._shnum
            for clientnum in immutable_shares:
                for shnum in immutable_shares[clientnum]:
                    if shnum == save_me:
                        continue
                    fn = os.path.join(self.get_serverdir(clientnum),
                                      "shares", si_dir, str(shnum))
                    if os.path.exists(fn):
                        os.unlink(fn)
            # now the download should fail with NotEnoughSharesError
            return self.shouldFail(NotEnoughSharesError, "1shares", None,
                                   download_to_data, n)
        d.addCallback(_clobber_most_shares)

        def _clobber_all_shares(ign):
            # delete the last remaining share
            for clientnum in immutable_shares:
                for shnum in immutable_shares[clientnum]:
                    fn = os.path.join(self.get_serverdir(clientnum),
                                      "shares", si_dir, str(shnum))
                    if os.path.exists(fn):
                        os.unlink(fn)
            # now a new download should fail with NoSharesError. We want a
            # new ImmutableFileNode so it will forget about the old shares.
            # If we merely called create_node_from_uri() without first
            # dereferencing the original node, the NodeMaker's _node_cache
            # would give us back the old one.
            n = None
            n = self.c0.create_node_from_uri(immutable_uri)
            return self.shouldFail(NoSharesError, "0shares", None,
                                   download_to_data, n)
        d.addCallback(_clobber_all_shares)
        return d

    def test_lost_servers(self):
        # while downloading a file (after seg[0], before seg[1]), lose the
        # three servers that we were using. The download should switch over
        # to other servers.
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # upload a file with multiple segments, so we can catch the download
        # in the middle.
        u = upload.Data(plaintext, None)
        u.max_segment_size = 70 # 5 segs
        d = self.c0.upload(u)
        def _uploaded(ur):
            self.uri = ur.get_uri()
            self.n = self.c0.create_node_from_uri(self.uri)
            return download_to_data(self.n)
        d.addCallback(_uploaded)
        def _got_data(data):
            self.failUnlessEqual(data, plaintext)
        d.addCallback(_got_data)
        def _kill_some_shares():
            # find the shares that were used and delete them
            shares = self.n._cnode._node._shares
            self.killed_share_nums = sorted([s._shnum for s in shares])

            # break the RIBucketReader references
            # (we don't break the RIStorageServer references, because that
            # isn't needed to test the current downloader implementation)
            for s in shares:
                s._rref.broken = True
        def _download_again(ign):
            # download again, deleting some shares after the first write
            # to the consumer
            c = StallingConsumer(_kill_some_shares)
            return self.n.read(c)
        d.addCallback(_download_again)
        def _check_failover(c):
            self.failUnlessEqual(b"".join(c.chunks), plaintext)
            shares = self.n._cnode._node._shares
            shnums = sorted([s._shnum for s in shares])
            self.failIfEqual(shnums, self.killed_share_nums)
        d.addCallback(_check_failover)
        return d

    def test_long_offset(self):
        # bug #1154: mplayer doing a seek-to-end results in an offset of type
        # 'long', rather than 'int', and apparently __len__ is required to
        # return an int. Rewrote Spans/DataSpans to provide s.len() instead
        # of len(s) .
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)

        c = MemoryConsumer()
        d = n.read(c, int(0), int(10))
        d.addCallback(lambda c: len(b"".join(c.chunks)))
        d.addCallback(lambda size: self.failUnlessEqual(size, 10))
        return d

    def test_badguess(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)

        # Cause the downloader to guess a segsize that's too low, so it will
        # ask for a segment number that's too high (beyond the end of the
        # real list, causing BadSegmentNumberError), to exercise
        # Segmentation._retry_bad_segment
        n._cnode._maybe_create_download_node()
        n._cnode._node._build_guessed_tables(90)

        con1 = MemoryConsumer()
        # plaintext size of 310 bytes, wrong-segsize of 90 bytes, will make
        # us think that file[180:200] is in the third segment (segnum=2), but
        # really there's only one segment
        d = n.read(con1, 180, 20)
        def _done(res):
            self.failUnlessEqual(b"".join(con1.chunks), plaintext[180:200])
        d.addCallback(_done)
        return d

    def test_simultaneous_badguess(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # upload a file with multiple segments, and a non-default segsize, to
        # exercise the offset-guessing code. Because we don't tell the
        # downloader about the unusual segsize, it will guess wrong, and have
        # to do extra roundtrips to get the correct data.
        u = upload.Data(plaintext, None)
        u.max_segment_size = 70 # 5 segs, 8-wide hashtree
        con1 = MemoryConsumer()
        con2 = MemoryConsumer()
        d = self.c0.upload(u)
        def _uploaded(ur):
            n = self.c0.create_node_from_uri(ur.get_uri())
            d1 = n.read(con1, 70, 20)
            d2 = n.read(con2, 140, 20)
            return defer.gatherResults([d1,d2])
        d.addCallback(_uploaded)
        def _done(res):
            self.failUnlessEqual(b"".join(con1.chunks), plaintext[70:90])
            self.failUnlessEqual(b"".join(con2.chunks), plaintext[140:160])
        d.addCallback(_done)
        return d

    def test_simultaneous_goodguess(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # upload a file with multiple segments, and a non-default segsize, to
        # exercise the offset-guessing code. This time we *do* tell the
        # downloader about the unusual segsize, so it can guess right.
        u = upload.Data(plaintext, None)
        u.max_segment_size = 70 # 5 segs, 8-wide hashtree
        con1 = MemoryConsumer()
        con2 = MemoryConsumer()
        d = self.c0.upload(u)
        def _uploaded(ur):
            n = self.c0.create_node_from_uri(ur.get_uri())
            n._cnode._maybe_create_download_node()
            n._cnode._node._build_guessed_tables(u.max_segment_size)
            d1 = n.read(con1, 70, 20)
            d2 = n.read(con2, 140, 20)
            return defer.gatherResults([d1,d2])
        d.addCallback(_uploaded)
        def _done(res):
            self.failUnlessEqual(b"".join(con1.chunks), plaintext[70:90])
            self.failUnlessEqual(b"".join(con2.chunks), plaintext[140:160])
        d.addCallback(_done)
        return d

    def test_sequential_goodguess(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        data = (plaintext*100)[:30000] # multiple of k

        # upload a file with multiple segments, and a non-default segsize, to
        # exercise the offset-guessing code. This time we *do* tell the
        # downloader about the unusual segsize, so it can guess right.
        u = upload.Data(data, None)
        u.max_segment_size = 6000 # 5 segs, 8-wide hashtree
        con1 = MemoryConsumer()
        con2 = MemoryConsumer()
        d = self.c0.upload(u)
        def _uploaded(ur):
            n = self.c0.create_node_from_uri(ur.get_uri())
            n._cnode._maybe_create_download_node()
            n._cnode._node._build_guessed_tables(u.max_segment_size)
            d = n.read(con1, 12000, 20)
            def _read1(ign):
                self.failUnlessEqual(b"".join(con1.chunks), data[12000:12020])
                return n.read(con2, 24000, 20)
            d.addCallback(_read1)
            def _read2(ign):
                self.failUnlessEqual(b"".join(con2.chunks), data[24000:24020])
            d.addCallback(_read2)
            return d
        d.addCallback(_uploaded)
        return d


    def test_simultaneous_get_blocks(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        self.load_shares()
        stay_empty = []

        n = self.c0.create_node_from_uri(immutable_uri)
        d = download_to_data(n)
        def _use_shares(ign):
            shares = list(n._cnode._node._shares)
            s0 = shares[0]
            # make sure .cancel works too
            o0 = s0.get_block(0)
            o0.subscribe(lambda **kwargs: stay_empty.append(kwargs))
            o1 = s0.get_block(0)
            o2 = s0.get_block(0)
            o0.cancel()
            o3 = s0.get_block(1) # state=BADSEGNUM
            d1 = defer.Deferred()
            d2 = defer.Deferred()
            d3 = defer.Deferred()
            o1.subscribe(lambda **kwargs: d1.callback(kwargs))
            o2.subscribe(lambda **kwargs: d2.callback(kwargs))
            o3.subscribe(lambda **kwargs: d3.callback(kwargs))
            return defer.gatherResults([d1,d2,d3])
        d.addCallback(_use_shares)
        def _done(res):
            r1,r2,r3 = res
            self.failUnlessEqual(r1["state"], "COMPLETE")
            self.failUnlessEqual(r2["state"], "COMPLETE")
            self.failUnlessEqual(r3["state"], "BADSEGNUM")
            self.failUnless("block" in r1)
            self.failUnless("block" in r2)
            self.failIf(stay_empty)
        d.addCallback(_done)
        return d

    def test_simultaneous_onefails_onecancelled(self):
        # This exercises an mplayer behavior in ticket #1154. I believe that
        # mplayer made two simultaneous webapi GET requests: first one for an
        # index region at the end of the (mp3/video) file, then one for the
        # first block of the file (the order doesn't really matter). All GETs
        # failed (NoSharesError) because of the type(__len__)==long bug. Each
        # GET submitted a DownloadNode.get_segment() request, which was
        # queued by the DN (DN._segment_requests), so the second one was
        # blocked waiting on the first one. When the first one failed,
        # DN.fetch_failed() was invoked, which errbacks the first GET, but
        # left the other one hanging (the lost-progress bug mentioned in
        # #1154 comment 10)
        #
        # Then mplayer sees that the index region GET failed, so it cancels
        # the first-block GET (by closing the HTTP request), triggering
        # stopProducer. The second GET was waiting in the Deferred (between
        # n.get_segment() and self._request_retired), so its
        # _cancel_segment_request was active, so was invoked. However,
        # DN._active_segment was None since it was not working on any segment
        # at that time, hence the error in #1154.

        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # upload a file with multiple segments, so we can catch the download
        # in the middle. Tell the downloader, so it can guess correctly.
        u = upload.Data(plaintext, None)
        u.max_segment_size = 70 # 5 segs
        d = self.c0.upload(u)
        def _uploaded(ur):
            # corrupt all the shares so the download will fail
            def _corruptor(s, debug=False):
                which = 48 # first byte of block0
                return s[:which] + bchr(ord(s[which:which+1])^0x01) + s[which+1:]
            self.corrupt_all_shares(ur.get_uri(), _corruptor)
            n = self.c0.create_node_from_uri(ur.get_uri())
            n._cnode._maybe_create_download_node()
            n._cnode._node._build_guessed_tables(u.max_segment_size)
            con1 = MemoryConsumer()
            con2 = MemoryConsumer()
            d = n.read(con1, int(0), int(20))
            d2 = n.read(con2, int(140), int(20))
            # con2 will be cancelled, so d2 should fail with DownloadStopped
            def _con2_should_not_succeed(res):
                self.fail("the second read should not have succeeded")
            def _con2_failed(f):
                self.failUnless(f.check(DownloadStopped))
            d2.addCallbacks(_con2_should_not_succeed, _con2_failed)

            def _con1_should_not_succeed(res):
                self.fail("the first read should not have succeeded")
            def _con1_failed(f):
                self.failUnless(f.check(NoSharesError))
                con2.producer.stopProducing()
                return d2
            d.addCallbacks(_con1_should_not_succeed, _con1_failed)
            return d
        d.addCallback(_uploaded)
        return d

    def test_simultaneous_onefails(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # upload a file with multiple segments, so we can catch the download
        # in the middle. Tell the downloader, so it can guess correctly.
        u = upload.Data(plaintext, None)
        u.max_segment_size = 70 # 5 segs
        d = self.c0.upload(u)
        def _uploaded(ur):
            # corrupt all the shares so the download will fail
            def _corruptor(s, debug=False):
                which = 48 # first byte of block0
                return s[:which] + bchr(ord(s[which:which+1])^0x01) + s[which+1:]
            self.corrupt_all_shares(ur.get_uri(), _corruptor)
            n = self.c0.create_node_from_uri(ur.get_uri())
            n._cnode._maybe_create_download_node()
            n._cnode._node._build_guessed_tables(u.max_segment_size)
            con1 = MemoryConsumer()
            con2 = MemoryConsumer()
            d = n.read(con1, int(0), int(20))
            d2 = n.read(con2, int(140), int(20))
            # con2 should wait for con1 to fail and then con2 should succeed.
            # In particular, we should not lose progress. If this test fails,
            # it will fail with a timeout error.
            def _con2_should_succeed(res):
                # this should succeed because we only corrupted the first
                # segment of each share. The segment that holds [140:160] is
                # fine, as are the hash chains and UEB.
                self.failUnlessEqual(b"".join(con2.chunks), plaintext[140:160])
            d2.addCallback(_con2_should_succeed)

            def _con1_should_not_succeed(res):
                self.fail("the first read should not have succeeded")
            def _con1_failed(f):
                self.failUnless(f.check(NoSharesError))
                # we *don't* cancel the second one here: this exercises a
                # lost-progress bug from #1154. We just wait for it to
                # succeed.
                return d2
            d.addCallbacks(_con1_should_not_succeed, _con1_failed)
            return d
        d.addCallback(_uploaded)
        return d

    def test_download_no_overrun(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        self.load_shares()

        # tweak the client's copies of server-version data, so it believes
        # that they're old and can't handle reads that overrun the length of
        # the share. This exercises a different code path.
        for s in self.c0.storage_broker.get_connected_servers():
            v = s.get_version()
            v1 = v[b"http://allmydata.org/tahoe/protocols/storage/v1"]
            v1[b"tolerates-immutable-read-overrun"] = False

        n = self.c0.create_node_from_uri(immutable_uri)
        d = download_to_data(n)
        def _got_data(data):
            self.failUnlessEqual(data, plaintext)
        d.addCallback(_got_data)
        return d

    def test_download_segment(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)
        cn = n._cnode
        (d,c) = cn.get_segment(0)
        def _got_segment(offset_and_data_and_decodetime):
            (offset, data, decodetime) = offset_and_data_and_decodetime
            self.failUnlessEqual(offset, 0)
            self.failUnlessEqual(len(data), len(plaintext))
        d.addCallback(_got_segment)
        return d

    def test_download_segment_cancel(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)
        cn = n._cnode
        (d,c) = cn.get_segment(0)
        fired = []
        d.addCallback(fired.append)
        c.cancel()
        d = fireEventually()
        d.addCallback(flushEventualQueue)
        def _check(ign):
            self.failUnlessEqual(fired, [])
        d.addCallback(_check)
        return d

    def test_download_bad_segment(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)
        cn = n._cnode
        def _try_download():
            (d,c) = cn.get_segment(1)
            return d
        d = self.shouldFail(BadSegmentNumberError, "badseg",
                            "segnum=1, numsegs=1",
                            _try_download)
        return d

    def test_download_segment_terminate(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)
        cn = n._cnode
        (d,c) = cn.get_segment(0)
        fired = []
        d.addCallback(fired.append)
        self.c0.terminator.disownServiceParent()
        d = fireEventually()
        d.addCallback(flushEventualQueue)
        def _check(ign):
            self.failUnlessEqual(fired, [])
        d.addCallback(_check)
        return d

    def test_pause(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)
        c = PausingConsumer()
        d = n.read(c)
        def _downloaded(mc):
            newdata = b"".join(mc.chunks)
            self.failUnlessEqual(newdata, plaintext)
        d.addCallback(_downloaded)
        return d

    def test_pause_then_stop(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)
        c = PausingAndStoppingConsumer()
        d = self.shouldFail(DownloadStopped, "test_pause_then_stop",
                            "our Consumer called stopProducing()",
                            n.read, c)
        return d

    def test_stop(self):
        # use a download target that stops after the first segment (#473)
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)
        c = StoppingConsumer()
        d = self.shouldFail(DownloadStopped, "test_stop",
                            "our Consumer called stopProducing()",
                            n.read, c)
        return d

    def test_stop_immediately(self):
        # and a target that stops right after registerProducer (maybe #1154)
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)

        c = ImmediatelyStoppingConsumer() # stops after registerProducer
        d = self.shouldFail(DownloadStopped, "test_stop_immediately",
                            "our Consumer called stopProducing()",
                            n.read, c)
        return d

    def test_stop_immediately2(self):
        # and a target that stops right after registerProducer (maybe #1154)
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()
        n = self.c0.create_node_from_uri(immutable_uri)

        c = MemoryConsumer()
        d0 = n.read(c)
        c.producer.stopProducing()
        d = self.shouldFail(DownloadStopped, "test_stop_immediately",
                            "our Consumer called stopProducing()",
                            lambda: d0)
        return d

    def test_download_segment_bad_ciphertext_hash(self):
        # The crypttext_hash_tree asserts the integrity of the decoded
        # ciphertext, and exists to detect two sorts of problems. The first
        # is a bug in zfec decode. The second is the "two-sided t-shirt"
        # attack (found by Christian Grothoff), in which a malicious uploader
        # creates two sets of shares (one for file A, second for file B),
        # uploads a combination of them (shares 0-4 of A, 5-9 of B), and then
        # builds an otherwise normal UEB around those shares: their goal is
        # to give their victim a filecap which sometimes downloads the good A
        # contents, and sometimes the bad B contents, depending upon which
        # servers/shares they can get to. Having a hash of the ciphertext
        # forces them to commit to exactly one version. (Christian's prize
        # for finding this problem was a t-shirt with two sides: the shares
        # of file A on the front, B on the back).

        # creating a set of shares with this property is too hard, although
        # it'd be nice to do so and confirm our fix. (it requires a lot of
        # tampering with the uploader). So instead, we just damage the
        # decoder. The tail decoder is rebuilt each time, so we need to use a
        # file with multiple segments.
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        u = upload.Data(plaintext, None)
        u.max_segment_size = 60 # 6 segs
        d = self.c0.upload(u)
        def _uploaded(ur):
            n = self.c0.create_node_from_uri(ur.get_uri())
            n._cnode._maybe_create_download_node()
            n._cnode._node._build_guessed_tables(u.max_segment_size)

            d = download_to_data(n)
            def _break_codec(data):
                # the codec isn't created until the UEB is retrieved
                node = n._cnode._node
                vcap = node._verifycap
                k, N = vcap.needed_shares, vcap.total_shares
                bad_codec = BrokenDecoder()
                bad_codec.set_params(node.segment_size, k, N)
                node._codec = bad_codec
            d.addCallback(_break_codec)
            # now try to download it again. The broken codec will provide
            # ciphertext that fails the hash test.
            d.addCallback(lambda ign:
                          self.shouldFail(BadCiphertextHashError, "badhash",
                                          "hash failure in "
                                          "ciphertext_hash_tree: segnum=0",
                                          download_to_data, n))
            return d
        d.addCallback(_uploaded)
        return d

    def OFFtest_download_segment_XXX(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # upload a file with multiple segments, and a non-default segsize, to
        # exercise the offset-guessing code. This time we *do* tell the
        # downloader about the unusual segsize, so it can guess right.
        u = upload.Data(plaintext, None)
        u.max_segment_size = 70 # 5 segs, 8-wide hashtree
        con1 = MemoryConsumer()
        con2 = MemoryConsumer()
        d = self.c0.upload(u)
        def _uploaded(ur):
            n = self.c0.create_node_from_uri(ur.get_uri())
            n._cnode._maybe_create_download_node()
            n._cnode._node._build_guessed_tables(u.max_segment_size)
            d1 = n.read(con1, 70, 20)
            #d2 = n.read(con2, 140, 20)
            d2 = defer.succeed(None)
            return defer.gatherResults([d1,d2])
        d.addCallback(_uploaded)
        def _done(res):
            self.failUnlessEqual(b"".join(con1.chunks), plaintext[70:90])
            self.failUnlessEqual(b"".join(con2.chunks), plaintext[140:160])
        #d.addCallback(_done)
        return d

    def test_duplicate_shares(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        self.load_shares()
        # make sure everybody has a copy of sh0. The second server contacted
        # will report two shares, and the ShareFinder will handle the
        # duplicate by attaching both to the same CommonShare instance.
        si = uri.from_string(immutable_uri).get_storage_index()
        si_dir = storage_index_to_dir(si)
        sh0_file = [sharefile
                    for (shnum, serverid, sharefile)
                    in self.find_uri_shares(immutable_uri)
                    if shnum == 0][0]
        sh0_data = open(sh0_file, "rb").read()
        for clientnum in immutable_shares:
            if 0 in immutable_shares[clientnum]:
                continue
            cdir = self.get_serverdir(clientnum)
            target = os.path.join(cdir, "shares", si_dir, "0")
            outf = open(target, "wb")
            outf.write(sh0_data)
            outf.close()

        d = self.download_immutable()
        return d

    def test_verifycap(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]
        self.load_shares()

        n = self.c0.create_node_from_uri(immutable_uri)
        vcap = n.get_verify_cap().to_string()
        vn = self.c0.create_node_from_uri(vcap)
        d = download_to_data(vn)
        def _got_ciphertext(ciphertext):
            self.failUnlessEqual(len(ciphertext), len(plaintext))
            self.failIfEqual(ciphertext, plaintext)
        d.addCallback(_got_ciphertext)
        return d

class BrokenDecoder(CRSDecoder):
    def decode(self, shares, shareids):
        d = CRSDecoder.decode(self, shares, shareids)
        def _decoded(buffers):
            def _corruptor(s, which):
                return s[:which] + bchr(ord(s[which:which+1])^0x01) + s[which+1:]
            buffers[0] = _corruptor(buffers[0], 0) # flip lsb of first byte
            return buffers
        d.addCallback(_decoded)
        return d


class PausingConsumer(MemoryConsumer):
    def __init__(self):
        MemoryConsumer.__init__(self)
        self.size = 0
        self.writes = 0
    def write(self, data):
        self.size += len(data)
        self.writes += 1
        if self.writes <= 2:
            # we happen to use 4 segments, and want to avoid pausing on the
            # last one (since then the _unpause timer will still be running)
            self.producer.pauseProducing()
            reactor.callLater(0.1, self._unpause)
        return MemoryConsumer.write(self, data)
    def _unpause(self):
        self.producer.resumeProducing()

class PausingAndStoppingConsumer(PausingConsumer):
    debug_stopped = False
    def write(self, data):
        if self.debug_stopped:
            raise Exception("I'm stopped, don't write to me")
        self.producer.pauseProducing()
        eventually(self._stop)
    def _stop(self):
        self.debug_stopped = True
        self.producer.stopProducing()

class StoppingConsumer(PausingConsumer):
    def write(self, data):
        self.producer.stopProducing()

class ImmediatelyStoppingConsumer(MemoryConsumer):
    def registerProducer(self, p, streaming):
        MemoryConsumer.registerProducer(self, p, streaming)
        self.producer.stopProducing()

class StallingConsumer(MemoryConsumer):
    def __init__(self, halfway_cb):
        MemoryConsumer.__init__(self)
        self.halfway_cb = halfway_cb
        self.writes = 0
    def write(self, data):
        self.writes += 1
        if self.writes == 1:
            self.halfway_cb()
        return MemoryConsumer.write(self, data)

class Corruption(_Base, unittest.TestCase):

    def _corrupt_flip(self, ign, imm_uri, which):
        log.msg("corrupt %d" % which)
        def _corruptor(s, debug=False):
            return s[:which] + bchr(ord(s[which:which+1])^0x01) + s[which+1:]
        self.corrupt_shares_numbered(imm_uri, [2], _corruptor)

    def _corrupt_set(self, ign, imm_uri, which, newvalue):
        log.msg("corrupt %d" % which)
        def _corruptor(s, debug=False):
            return s[:which] + bchr(newvalue) + s[which+1:]
        self.corrupt_shares_numbered(imm_uri, [2], _corruptor)

    def test_each_byte(self):
        # Setting catalog_detection=True performs an exhaustive test of the
        # Downloader's response to corruption in the lsb of each byte of the
        # 2070-byte share, with two goals: make sure we tolerate all forms of
        # corruption (i.e. don't hang or return bad data), and make a list of
        # which bytes can be corrupted without influencing the download
        # (since we don't need every byte of the share). That takes 50s to
        # run on my laptop and doesn't have any actual asserts, so we don't
        # normally do that.
        self.catalog_detection = False

        self.basedir = "download/Corruption/each_byte"
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # to exercise the block-hash-tree code properly, we need to have
        # multiple segments. We don't tell the downloader about the different
        # segsize, so it guesses wrong and must do extra roundtrips.
        u = upload.Data(plaintext, None)
        u.max_segment_size = 120 # 3 segs, 4-wide hashtree

        if self.catalog_detection:
            undetected = spans.Spans()

        def _download(ign, imm_uri, which, expected):
            n = self.c0.create_node_from_uri(imm_uri)
            n._cnode._maybe_create_download_node()
            # for this test to work, we need to have a new Node each time.
            # Make sure the NodeMaker's weakcache hasn't interfered.
            assert not n._cnode._node._shares
            d = download_to_data(n)
            def _got_data(data):
                self.failUnlessEqual(data, plaintext)
                shnums = sorted([s._shnum for s in n._cnode._node._shares])
                no_sh2 = bool(2 not in shnums)
                sh2 = [s for s in n._cnode._node._shares if s._shnum == 2]
                sh2_had_corruption = False
                if sh2 and sh2[0].had_corruption:
                    sh2_had_corruption = True
                num_needed = len(n._cnode._node._shares)
                if self.catalog_detection:
                    detected = no_sh2 or sh2_had_corruption or (num_needed!=3)
                    if not detected:
                        undetected.add(which, 1)
                if expected == "no-sh2":
                    self.failIfIn(2, shnums)
                elif expected == "2bad-need-3":
                    self.failIf(no_sh2)
                    self.failUnless(sh2[0].had_corruption)
                    self.failUnlessEqual(num_needed, 3)
                elif expected == "need-4th":
                    # XXX check with warner; what relevance does this
                    # have for the "need-4th" stuff?
                    #self.failIf(no_sh2)
                    #self.failUnless(sh2[0].had_corruption)
                    self.failIfEqual(num_needed, 3)
            d.addCallback(_got_data)
            return d


        d = self.c0.upload(u)
        def _uploaded(ur):
            imm_uri = ur.get_uri()
            self.shares = self.copy_shares(imm_uri)
            d = defer.succeed(None)
            # 'victims' is a list of corruption tests to run. Each one flips
            # the low-order bit of the specified offset in the share file (so
            # offset=0 is the MSB of the container version, offset=15 is the
            # LSB of the share version, offset=24 is the MSB of the
            # data-block-offset, and offset=48 is the first byte of the first
            # data-block). Each one also specifies what sort of corruption
            # we're expecting to see.
            no_sh2_victims = [0,1,2,3] # container version
            need3_victims =  [ ] # none currently in this category
            # when the offsets are corrupted, the Share will be unable to
            # retrieve the data it wants (because it thinks that data lives
            # off in the weeds somewhere), and Share treats DataUnavailable
            # as abandon-this-share, so in general we'll be forced to look
            # for a 4th share.
            need_4th_victims = [12,13,14,15, # offset[data]
                                24,25,26,27, # offset[block_hashes]
                                ]
            need_4th_victims.append(36) # block data
            # when corrupting hash trees, we must corrupt a value that isn't
            # directly set from somewhere else. Since we download data from
            # seg2, corrupt something on its hash chain, like [2] (the
            # right-hand child of the root)
            need_4th_victims.append(600+2*32) # block_hashes[2]
            # Share.loop is pretty conservative: it abandons the share at the
            # first sign of corruption. It doesn't strictly need to be this
            # way: if the UEB were corrupt, we could still get good block
            # data from that share, as long as there was a good copy of the
            # UEB elsewhere. If this behavior is relaxed, then corruption in
            # the following fields (which are present in multiple shares)
            # should fall into the "need3_victims" case instead of the
            # "need_4th_victims" case.
            need_4th_victims.append(824) # share_hashes
            corrupt_me = ([(i,"no-sh2") for i in no_sh2_victims] +
                          [(i, "2bad-need-3") for i in need3_victims] +
                          [(i, "need-4th") for i in need_4th_victims])
            if self.catalog_detection:
                share_len = len(list(self.shares.values())[0])
                corrupt_me = [(i, "") for i in range(share_len)]
                # This is a work around for ticket #2024.
                corrupt_me = corrupt_me[0:8]+corrupt_me[12:]
            for i,expected in corrupt_me:
                # All these tests result in a successful download. What we're
                # measuring is how many shares the downloader had to use.
                d.addCallback(self._corrupt_flip, imm_uri, i)
                d.addCallback(_download, imm_uri, i, expected)
                d.addCallback(lambda ign: self.restore_all_shares(self.shares))
                d.addCallback(fireEventually)
            corrupt_values = [(3, 2, "no-sh2"),
                              (15, 2, "need-4th"), # share looks v2
                              ]
            for i,newvalue,expected in corrupt_values:
                d.addCallback(self._corrupt_set, imm_uri, i, newvalue)
                d.addCallback(_download, imm_uri, i, expected)
                d.addCallback(lambda ign: self.restore_all_shares(self.shares))
                d.addCallback(fireEventually)
            return d
        d.addCallback(_uploaded)
        def _show_results(ign):
            share_len = len(list(self.shares.values())[0])
            print()
            print("of [0:%d], corruption ignored in %s" %
                   (share_len, undetected.dump()))
        if self.catalog_detection:
            d.addCallback(_show_results)
            # of [0:2070], corruption ignored in len=1133:
            # [4-11],[16-23],[28-31],[152-439],[600-663],[1309-2069]
            #  [4-11]: container sizes
            #  [16-23]: share block/data sizes
            #  [152-375]: plaintext hash tree
            #  [376-408]: crypttext_hash_tree[0] (root)
            #  [408-439]: crypttext_hash_tree[1] (computed)
            #  [600-631]: block hash tree[0] (root)
            #  [632-663]: block hash tree[1] (computed)
            #  [1309-]: reserved+unused UEB space
        return d

    def test_failure(self):
        # this test corrupts all shares in the same way, and asserts that the
        # download fails.

        self.basedir = "download/Corruption/failure"
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # to exercise the block-hash-tree code properly, we need to have
        # multiple segments. We don't tell the downloader about the different
        # segsize, so it guesses wrong and must do extra roundtrips.
        u = upload.Data(plaintext, None)
        u.max_segment_size = 120 # 3 segs, 4-wide hashtree

        d = self.c0.upload(u)
        def _uploaded(ur):
            imm_uri = ur.get_uri()
            self.shares = self.copy_shares(imm_uri)

            corrupt_me = [(48, "block data", "Last failure: None"),
                          (600+2*32, "block_hashes[2]", "BadHashError"),
                          (376+2*32, "crypttext_hash_tree[2]", "BadHashError"),
                          (824, "share_hashes", "BadHashError"),
                          ]
            def _download(imm_uri):
                n = self.c0.create_node_from_uri(imm_uri)
                n._cnode._maybe_create_download_node()
                # for this test to work, we need to have a new Node each time.
                # Make sure the NodeMaker's weakcache hasn't interfered.
                assert not n._cnode._node._shares
                return download_to_data(n)

            d = defer.succeed(None)
            for i,which,substring in corrupt_me:
                # All these tests result in a failed download.
                d.addCallback(self._corrupt_flip_all, imm_uri, i)
                d.addCallback(lambda ign, which=which, substring=substring:
                              self.shouldFail(NoSharesError, which,
                                              substring,
                                              _download, imm_uri))
                d.addCallback(lambda ign: self.restore_all_shares(self.shares))
                d.addCallback(fireEventually)
            return d
        d.addCallback(_uploaded)

        return d

    def _corrupt_flip_all(self, ign, imm_uri, which):
        def _corruptor(s, debug=False):
            return s[:which] + bchr(ord(s[which:which+1])^0x01) + s[which+1:]
        self.corrupt_all_shares(imm_uri, _corruptor)

class DownloadV2(_Base, unittest.TestCase):
    # tests which exercise v2-share code. They first upload a file with
    # FORCE_V2 set.

    def setUp(self):
        d = defer.maybeDeferred(_Base.setUp, self)
        def _set_force_v2(ign):
            self.old_force_v2 = layout.FORCE_V2
            layout.FORCE_V2 = True
        d.addCallback(_set_force_v2)
        return d
    def tearDown(self):
        layout.FORCE_V2 = self.old_force_v2
        return _Base.tearDown(self)

    def test_download(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # upload a file
        u = upload.Data(plaintext, None)
        d = self.c0.upload(u)
        def _uploaded(ur):
            imm_uri = ur.get_uri()
            n = self.c0.create_node_from_uri(imm_uri)
            return download_to_data(n)
        d.addCallback(_uploaded)
        return d

    def test_download_no_overrun(self):
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        # tweak the client's copies of server-version data, so it believes
        # that they're old and can't handle reads that overrun the length of
        # the share. This exercises a different code path.
        for s in self.c0.storage_broker.get_connected_servers():
            v = s.get_version()
            v1 = v[b"http://allmydata.org/tahoe/protocols/storage/v1"]
            v1[b"tolerates-immutable-read-overrun"] = False

        # upload a file
        u = upload.Data(plaintext, None)
        d = self.c0.upload(u)
        def _uploaded(ur):
            imm_uri = ur.get_uri()
            n = self.c0.create_node_from_uri(imm_uri)
            return download_to_data(n)
        d.addCallback(_uploaded)
        return d

    def OFF_test_no_overrun_corrupt_shver(self): # unnecessary
        self.basedir = self.mktemp()
        self.set_up_grid()
        self.c0 = self.g.clients[0]

        for s in self.c0.storage_broker.get_connected_servers():
            v = s.get_version()
            v1 = v["http://allmydata.org/tahoe/protocols/storage/v1"]
            v1["tolerates-immutable-read-overrun"] = False

        # upload a file
        u = upload.Data(plaintext, None)
        d = self.c0.upload(u)
        def _uploaded(ur):
            imm_uri = ur.get_uri()
            def _do_corrupt(which, newvalue):
                def _corruptor(s, debug=False):
                    return s[:which] + chr(newvalue) + s[which+1:]
                self.corrupt_shares_numbered(imm_uri, [0], _corruptor)
            _do_corrupt(12+3, 0x00)
            n = self.c0.create_node_from_uri(imm_uri)
            d = download_to_data(n)
            def _got_data(data):
                self.failUnlessEqual(data, plaintext)
            d.addCallback(_got_data)
            return d
        d.addCallback(_uploaded)
        return d

class Status(unittest.TestCase):
    def test_status(self):
        now = 12345.1
        ds = DownloadStatus("si-1", 123)
        self.failUnlessEqual(ds.get_status(), "idle")
        ev0 = ds.add_segment_request(0, now)
        self.failUnlessEqual(ds.get_status(), "fetching segment 0")
        ev0.activate(now+0.5)
        ev0.deliver(now+1, 0, 1000, 2.0)
        self.failUnlessEqual(ds.get_status(), "idle")
        ev2 = ds.add_segment_request(2, now+2)
        del ev2 # hush pyflakes
        ev1 = ds.add_segment_request(1, now+2)
        self.failUnlessEqual(ds.get_status(), "fetching segments 1,2")
        ev1.error(now+3)
        self.failUnlessEqual(ds.get_status(),
                             "fetching segment 2; errors on segment 1")

    def test_progress(self):
        now = 12345.1
        ds = DownloadStatus("si-1", 123)
        self.failUnlessEqual(ds.get_progress(), 0.0)
        e = ds.add_read_event(0, 1000, now)
        self.failUnlessEqual(ds.get_progress(), 0.0)
        e.update(500, 2.0, 2.0)
        self.failUnlessEqual(ds.get_progress(), 0.5)
        e.finished(now+2)
        self.failUnlessEqual(ds.get_progress(), 1.0)

        e1 = ds.add_read_event(1000, 2000, now+3)
        e2 = ds.add_read_event(4000, 2000, now+3)
        self.failUnlessEqual(ds.get_progress(), 0.0)
        e1.update(1000, 2.0, 2.0)
        self.failUnlessEqual(ds.get_progress(), 0.25)
        e2.update(1000, 2.0, 2.0)
        self.failUnlessEqual(ds.get_progress(), 0.5)
        e1.update(1000, 2.0, 2.0)
        e1.finished(now+4)
        # now there is only one outstanding read, and it is 50% done
        self.failUnlessEqual(ds.get_progress(), 0.5)
        e2.update(1000, 2.0, 2.0)
        e2.finished(now+5)
        self.failUnlessEqual(ds.get_progress(), 1.0)

    def test_active(self):
        now = 12345.1
        ds = DownloadStatus("si-1", 123)
        self.failUnlessEqual(ds.get_active(), False)
        e1 = ds.add_read_event(0, 1000, now)
        self.failUnlessEqual(ds.get_active(), True)
        e2 = ds.add_read_event(1, 1000, now+1)
        self.failUnlessEqual(ds.get_active(), True)
        e1.finished(now+2)
        self.failUnlessEqual(ds.get_active(), True)
        e2.finished(now+3)
        self.failUnlessEqual(ds.get_active(), False)

def make_server(clientid):
    tubid = hashutil.tagged_hash(b"clientid", clientid)[:20]
    return NoNetworkServer(tubid, None)
def make_servers(clientids):
    servers = {}
    for clientid in clientids:
        servers[clientid] = make_server(clientid)
    return servers

class MyShare(object):
    def __init__(self, shnum, server, rtt):
        self._shnum = shnum
        self._server = server
        self._dyhb_rtt = rtt

    def __repr__(self):
        return "sh%d-on-%s" % (self._shnum, self._server.get_name())

class MySegmentFetcher(SegmentFetcher):
    def __init__(self, *args, **kwargs):
        SegmentFetcher.__init__(self, *args, **kwargs)
        self._test_start_shares = []
    def _start_share(self, share, shnum):
        self._test_start_shares.append(share)

class FakeNode(object):
    def __init__(self):
        self.want_more = 0
        self.failed = None
        self.processed = None
        self._si_prefix = "si_prefix"

    def want_more_shares(self):
        self.want_more += 1

    def fetch_failed(self, fetcher, f):
        self.failed = f

    def process_blocks(self, segnum, blocks):
        self.processed = (segnum, blocks)

    def get_num_segments(self):
        return 1, True


class Selection(unittest.TestCase):
    def test_failure(self):
        """If the fetch loop fails, it tell the Node the fetch failed."""
        node = FakeNode()
        # Simulate a failure:
        node.get_num_segments = lambda: 1/0
        sf = SegmentFetcher(node, 0, 3, None)
        sf.add_shares([])
        d = flushEventualQueue()
        def _check1(ign):
            [_] = self.flushLoggedErrors(ZeroDivisionError)
            self.failUnless(node.failed)
            self.failUnless(node.failed.check(ZeroDivisionError))
        d.addCallback(_check1)
        return d

    def test_no_shares(self):
        node = FakeNode()
        sf = SegmentFetcher(node, 0, 3, None)
        sf.add_shares([])
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 1)
            self.failUnlessEqual(node.failed, None)
            sf.no_more_shares()
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failUnless(node.failed)
            self.failUnless(node.failed.check(NoSharesError))
        d.addCallback(_check2)
        return d

    def test_only_one_share(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        serverA = make_server(b"peer-A")
        shares = [MyShare(0, serverA, 0.0)]
        sf.add_shares(shares)
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 1)
            self.failUnlessEqual(node.failed, None)
            sf.no_more_shares()
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failUnless(node.failed)
            self.failUnless(node.failed.check(NotEnoughSharesError))
            sname = serverA.get_name()
            self.failUnlessIn("complete= pending=sh0-on-%s overdue= unused="  % sname,
                              str(node.failed))
        d.addCallback(_check2)
        return d

    def test_good_diversity_early(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        shares = [MyShare(i, make_server(b"peer-%d" % i), i) for i in range(10)]
        sf.add_shares(shares)
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 0)
            self.failUnlessEqual(sf._test_start_shares, shares[:3])
            for sh in sf._test_start_shares:
                sf._block_request_activity(sh, sh._shnum, COMPLETE,
                                           "block-%d" % sh._shnum)
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failIfEqual(node.processed, None)
            self.failUnlessEqual(node.processed, (0, {0: "block-0",
                                                      1: "block-1",
                                                      2: "block-2"}) )
        d.addCallback(_check2)
        return d

    def test_good_diversity_late(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        shares = [MyShare(i, make_server(b"peer-%d" % i), i) for i in range(10)]
        sf.add_shares([])
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 1)
            sf.add_shares(shares)
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failUnlessEqual(sf._test_start_shares, shares[:3])
            for sh in sf._test_start_shares:
                sf._block_request_activity(sh, sh._shnum, COMPLETE,
                                           "block-%d" % sh._shnum)
            return flushEventualQueue()
        d.addCallback(_check2)
        def _check3(ign):
            self.failIfEqual(node.processed, None)
            self.failUnlessEqual(node.processed, (0, {0: "block-0",
                                                      1: "block-1",
                                                      2: "block-2"}) )
        d.addCallback(_check3)
        return d

    def test_avoid_bad_diversity_late(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        # we could satisfy the read entirely from the first server, but we'd
        # prefer not to. Instead, we expect to only pull one share from the
        # first server
        servers = make_servers([b"peer-A", b"peer-B", b"peer-C"])
        shares = [MyShare(0, servers[b"peer-A"], 0.0),
                  MyShare(1, servers[b"peer-A"], 0.0),
                  MyShare(2, servers[b"peer-A"], 0.0),
                  MyShare(3, servers[b"peer-B"], 1.0),
                  MyShare(4, servers[b"peer-C"], 2.0),
                  ]
        sf.add_shares([])
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 1)
            sf.add_shares(shares)
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failUnlessEqual(sf._test_start_shares,
                                 [shares[0], shares[3], shares[4]])
            for sh in sf._test_start_shares:
                sf._block_request_activity(sh, sh._shnum, COMPLETE,
                                           "block-%d" % sh._shnum)
            return flushEventualQueue()
        d.addCallback(_check2)
        def _check3(ign):
            self.failIfEqual(node.processed, None)
            self.failUnlessEqual(node.processed, (0, {0: "block-0",
                                                      3: "block-3",
                                                      4: "block-4"}) )
        d.addCallback(_check3)
        return d

    def test_suffer_bad_diversity_late(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        # we satisfy the read entirely from the first server because we don't
        # have any other choice.
        serverA = make_server(b"peer-A")
        shares = [MyShare(0, serverA, 0.0),
                  MyShare(1, serverA, 0.0),
                  MyShare(2, serverA, 0.0),
                  MyShare(3, serverA, 0.0),
                  MyShare(4, serverA, 0.0),
                  ]
        sf.add_shares([])
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 1)
            sf.add_shares(shares)
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failUnlessEqual(node.want_more, 3)
            self.failUnlessEqual(sf._test_start_shares,
                                 [shares[0], shares[1], shares[2]])
            for sh in sf._test_start_shares:
                sf._block_request_activity(sh, sh._shnum, COMPLETE,
                                           "block-%d" % sh._shnum)
            return flushEventualQueue()
        d.addCallback(_check2)
        def _check3(ign):
            self.failIfEqual(node.processed, None)
            self.failUnlessEqual(node.processed, (0, {0: "block-0",
                                                      1: "block-1",
                                                      2: "block-2"}) )
        d.addCallback(_check3)
        return d

    def test_suffer_bad_diversity_early(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        # we satisfy the read entirely from the first server because we don't
        # have any other choice.
        serverA = make_server(b"peer-A")
        shares = [MyShare(0, serverA, 0.0),
                  MyShare(1, serverA, 0.0),
                  MyShare(2, serverA, 0.0),
                  MyShare(3, serverA, 0.0),
                  MyShare(4, serverA, 0.0),
                  ]
        sf.add_shares(shares)
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 2)
            self.failUnlessEqual(sf._test_start_shares,
                                 [shares[0], shares[1], shares[2]])
            for sh in sf._test_start_shares:
                sf._block_request_activity(sh, sh._shnum, COMPLETE,
                                           "block-%d" % sh._shnum)
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failIfEqual(node.processed, None)
            self.failUnlessEqual(node.processed, (0, {0: "block-0",
                                                      1: "block-1",
                                                      2: "block-2"}) )
        d.addCallback(_check2)
        return d

    def test_overdue(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        shares = [MyShare(i, make_server(b"peer-%d" % i), i) for i in range(10)]
        sf.add_shares(shares)
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 0)
            self.failUnlessEqual(sf._test_start_shares, shares[:3])
            for sh in sf._test_start_shares:
                sf._block_request_activity(sh, sh._shnum, OVERDUE)
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failUnlessEqual(sf._test_start_shares, shares[:6])
            for sh in sf._test_start_shares[3:]:
                sf._block_request_activity(sh, sh._shnum, COMPLETE,
                                           "block-%d" % sh._shnum)
            return flushEventualQueue()
        d.addCallback(_check2)
        def _check3(ign):
            self.failIfEqual(node.processed, None)
            self.failUnlessEqual(node.processed, (0, {3: "block-3",
                                                      4: "block-4",
                                                      5: "block-5"}) )
        d.addCallback(_check3)
        return d

    def test_overdue_fails(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        servers = make_servers([b"peer-%d" % i for i in range(6)])
        shares = [MyShare(i, servers[b"peer-%d" % i], i) for i in range(6)]
        sf.add_shares(shares)
        sf.no_more_shares()
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 0)
            self.failUnlessEqual(sf._test_start_shares, shares[:3])
            for sh in sf._test_start_shares:
                sf._block_request_activity(sh, sh._shnum, OVERDUE)
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            self.failUnlessEqual(sf._test_start_shares, shares[:6])
            for sh in sf._test_start_shares[3:]:
                sf._block_request_activity(sh, sh._shnum, DEAD)
            return flushEventualQueue()
        d.addCallback(_check2)
        def _check3(ign):
            # we're still waiting
            self.failUnlessEqual(node.processed, None)
            self.failUnlessEqual(node.failed, None)
            # now complete one of the overdue ones, and kill one of the other
            # ones, leaving one hanging. This should trigger a failure, since
            # we cannot succeed.
            live = sf._test_start_shares[0]
            die = sf._test_start_shares[1]
            sf._block_request_activity(live, live._shnum, COMPLETE, "block")
            sf._block_request_activity(die, die._shnum, DEAD)
            return flushEventualQueue()
        d.addCallback(_check3)
        def _check4(ign):
            self.failUnless(node.failed)
            self.failUnless(node.failed.check(NotEnoughSharesError))
            sname = servers[b"peer-2"].get_name()
            self.failUnlessIn("complete=sh0 pending= overdue=sh2-on-%s unused=" % sname,
                              str(node.failed))
        d.addCallback(_check4)
        return d

    def test_avoid_redundancy(self):
        node = FakeNode()
        sf = MySegmentFetcher(node, 0, 3, None)
        # we could satisfy the read entirely from the first server, but we'd
        # prefer not to. Instead, we expect to only pull one share from the
        # first server
        servers = make_servers([b"peer-A", b"peer-B", b"peer-C", b"peer-D",
                                b"peer-E"])
        shares = [MyShare(0, servers[b"peer-A"],0.0),
                  MyShare(1, servers[b"peer-B"],1.0),
                  MyShare(0, servers[b"peer-C"],2.0), # this will be skipped
                  MyShare(1, servers[b"peer-D"],3.0),
                  MyShare(2, servers[b"peer-E"],4.0),
                  ]
        sf.add_shares(shares[:3])
        d = flushEventualQueue()
        def _check1(ign):
            self.failUnlessEqual(node.want_more, 1)
            self.failUnlessEqual(sf._test_start_shares,
                                 [shares[0], shares[1]])
            # allow sh1 to retire
            sf._block_request_activity(shares[1], 1, COMPLETE, "block-1")
            return flushEventualQueue()
        d.addCallback(_check1)
        def _check2(ign):
            # and then feed in the remaining shares
            sf.add_shares(shares[3:])
            sf.no_more_shares()
            return flushEventualQueue()
        d.addCallback(_check2)
        def _check3(ign):
            self.failUnlessEqual(sf._test_start_shares,
                                 [shares[0], shares[1], shares[4]])
            sf._block_request_activity(shares[0], 0, COMPLETE, "block-0")
            sf._block_request_activity(shares[4], 2, COMPLETE, "block-2")
            return flushEventualQueue()
        d.addCallback(_check3)
        def _check4(ign):
            self.failIfEqual(node.processed, None)
            self.failUnlessEqual(node.processed, (0, {0: "block-0",
                                                      1: "block-1",
                                                      2: "block-2"}) )
        d.addCallback(_check4)
        return d
