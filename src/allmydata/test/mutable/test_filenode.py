"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from six.moves import cStringIO as StringIO
from twisted.internet import defer, reactor
from ..common import AsyncBrokenTestCase
from testtools.matchers import (
    Equals,
    Contains,
    HasLength,
    Is,
    IsInstance,
)
from allmydata import uri, client
from allmydata.util.consumer import MemoryConsumer
from allmydata.interfaces import SDMF_VERSION, MDMF_VERSION, DownloadStopped
from allmydata.mutable.filenode import MutableFileNode, BackoffAgent
from allmydata.mutable.common import MODE_ANYTHING, MODE_WRITE, MODE_READ, UncoordinatedWriteError

from allmydata.mutable.publish import MutableData
from ..test_download import PausingConsumer, PausingAndStoppingConsumer, \
     StoppingConsumer, ImmediatelyStoppingConsumer
from .. import common_util as testutil
from ...crypto.rsa import create_signing_keypair
from .util import (
    FakeStorage,
    make_nodemaker_with_peers,
    make_peer,
)

class Filenode(AsyncBrokenTestCase, testutil.ShouldFailMixin):
    # this used to be in Publish, but we removed the limit. Some of
    # these tests test whether the new code correctly allows files
    # larger than the limit.
    OLD_MAX_SEGMENT_SIZE = 3500000
    def setUp(self):
        super(Filenode, self).setUp()
        self._storage = FakeStorage()
        self._peers = list(
            make_peer(self._storage, n)
            for n
            # 10 is the default for N.  We're trying to make enough servers
            # here so that each only gets one share.
            in range(10)
        )
        self.nodemaker = make_nodemaker_with_peers(self._peers)

    def test_create(self):
        d = self.nodemaker.create_mutable_file()
        def _created(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            self.assertThat(n.get_storage_index(), Equals(n._storage_index))
            sb = self.nodemaker.storage_broker
            peer0 = sorted(sb.get_all_serverids())[0]
            shnums = self._storage._peers[peer0].keys()
            self.assertThat(shnums, HasLength(1))
        d.addCallback(_created)
        return d

    async def test_create_with_keypair(self):
        """
        An SDMF can be created using a given keypair.
        """
        (priv, pub) = create_signing_keypair(2048)
        node = await self.nodemaker.create_mutable_file(keypair=(pub, priv))
        self.assertThat(
            (node.get_privkey(), node.get_pubkey()),
            Equals((priv, pub)),
        )

    def test_create_mdmf(self):
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            self.assertThat(n.get_storage_index(), Equals(n._storage_index))
            sb = self.nodemaker.storage_broker
            peer0 = sorted(sb.get_all_serverids())[0]
            shnums = self._storage._peers[peer0].keys()
            self.assertThat(shnums, HasLength(1))
        d.addCallback(_created)
        return d

    def test_single_share(self):
        # Make sure that we tolerate publishing a single share.
        self.nodemaker.default_encoding_parameters['k'] = 1
        self.nodemaker.default_encoding_parameters['happy'] = 1
        self.nodemaker.default_encoding_parameters['n'] = 1
        d = defer.succeed(None)
        for v in (SDMF_VERSION, MDMF_VERSION):
            d.addCallback(lambda ignored, v=v:
                self.nodemaker.create_mutable_file(version=v))
            def _created(n):
                self.assertThat(n, IsInstance(MutableFileNode))
                self._node = n
                return n
            d.addCallback(_created)
            d.addCallback(lambda n:
                n.overwrite(MutableData(b"Contents" * 50000)))
            d.addCallback(lambda ignored:
                self._node.download_best_version())
            d.addCallback(lambda contents:
                self.assertThat(contents, Equals(b"Contents" * 50000)))
        return d

    def test_max_shares(self):
        self.nodemaker.default_encoding_parameters['n'] = 255
        d = self.nodemaker.create_mutable_file(version=SDMF_VERSION)
        def _created(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            self.assertThat(n.get_storage_index(), Equals(n._storage_index))
            sb = self.nodemaker.storage_broker
            num_shares = sum([len(self._storage._peers[x].keys()) for x \
                              in sb.get_all_serverids()])
            self.assertThat(num_shares, Equals(255))
            self._node = n
            return n
        d.addCallback(_created)
        # Now we upload some contents
        d.addCallback(lambda n:
            n.overwrite(MutableData(b"contents" * 50000)))
        # ...then download contents
        d.addCallback(lambda ignored:
            self._node.download_best_version())
        # ...and check to make sure everything went okay.
        d.addCallback(lambda contents:
            self.assertThat(b"contents" * 50000, Equals(contents)))
        return d

    def test_max_shares_mdmf(self):
        # Test how files behave when there are 255 shares.
        self.nodemaker.default_encoding_parameters['n'] = 255
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            self.assertThat(n.get_storage_index(), Equals(n._storage_index))
            sb = self.nodemaker.storage_broker
            num_shares = sum([len(self._storage._peers[x].keys()) for x \
                              in sb.get_all_serverids()])
            self.assertThat(num_shares, Equals(255))
            self._node = n
            return n
        d.addCallback(_created)
        d.addCallback(lambda n:
            n.overwrite(MutableData(b"contents" * 50000)))
        d.addCallback(lambda ignored:
            self._node.download_best_version())
        d.addCallback(lambda contents:
            self.assertThat(contents, Equals(b"contents" * 50000)))
        return d

    def test_mdmf_filenode_cap(self):
        # Test that an MDMF filenode, once created, returns an MDMF URI.
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            cap = n.get_cap()
            self.assertThat(cap, IsInstance(uri.WriteableMDMFFileURI))
            rcap = n.get_readcap()
            self.assertThat(rcap, IsInstance(uri.ReadonlyMDMFFileURI))
            vcap = n.get_verify_cap()
            self.assertThat(vcap, IsInstance(uri.MDMFVerifierURI))
        d.addCallback(_created)
        return d


    def test_create_from_mdmf_writecap(self):
        # Test that the nodemaker is capable of creating an MDMF
        # filenode given an MDMF cap.
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            s = n.get_uri()
            self.assertTrue(s.startswith(b"URI:MDMF"))
            n2 = self.nodemaker.create_from_cap(s)
            self.assertThat(n2, IsInstance(MutableFileNode))
            self.assertThat(n.get_storage_index(), Equals(n2.get_storage_index()))
            self.assertThat(n.get_uri(), Equals(n2.get_uri()))
        d.addCallback(_created)
        return d


    def test_create_from_mdmf_readcap(self):
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            s = n.get_readonly_uri()
            n2 = self.nodemaker.create_from_cap(s)
            self.assertThat(n2, IsInstance(MutableFileNode))

            # Check that it's a readonly node
            self.assertTrue(n2.is_readonly())
        d.addCallback(_created)
        return d


    def test_internal_version_from_cap(self):
        # MutableFileNodes and MutableFileVersions have an internal
        # switch that tells them whether they're dealing with an SDMF or
        # MDMF mutable file when they start doing stuff. We want to make
        # sure that this is set appropriately given an MDMF cap.
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            self.uri = n.get_uri()
            self.assertThat(n._protocol_version, Equals(MDMF_VERSION))

            n2 = self.nodemaker.create_from_cap(self.uri)
            self.assertThat(n2._protocol_version, Equals(MDMF_VERSION))
        d.addCallback(_created)
        return d


    def test_serialize(self):
        n = MutableFileNode(None, None, {"k": 3, "n": 10}, None)
        calls = []
        def _callback(*args, **kwargs):
            self.assertThat(args, Equals((4,)))
            self.assertThat(kwargs, Equals({"foo": 5}))
            calls.append(1)
            return 6
        d = n._do_serialized(_callback, 4, foo=5)
        def _check_callback(res):
            self.assertThat(res, Equals(6))
            self.assertThat(calls, Equals([1]))
        d.addCallback(_check_callback)

        def _errback():
            raise ValueError("heya")
        d.addCallback(lambda res:
                      self.shouldFail(ValueError, "_check_errback", "heya",
                                      n._do_serialized, _errback))
        return d

    def test_upload_and_download(self):
        d = self.nodemaker.create_mutable_file()
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            d.addCallback(lambda smap: smap.dump(StringIO()))
            d.addCallback(lambda sio:
                          self.assertTrue("3-of-10" in sio.getvalue()))
            d.addCallback(lambda res: n.overwrite(MutableData(b"contents 1")))
            d.addCallback(lambda res: self.assertThat(res, Is(None)))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 1")))
            d.addCallback(lambda res: n.get_size_of_best_version())
            d.addCallback(lambda size:
                          self.assertThat(size, Equals(len(b"contents 1"))))
            d.addCallback(lambda res: n.overwrite(MutableData(b"contents 2")))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 2")))
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            d.addCallback(lambda smap: n.upload(MutableData(b"contents 3"), smap))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 3")))
            d.addCallback(lambda res: n.get_servermap(MODE_ANYTHING))
            d.addCallback(lambda smap:
                          n.download_version(smap,
                                             smap.best_recoverable_version()))
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 3")))
            # test a file that is large enough to overcome the
            # mapupdate-to-retrieve data caching (i.e. make the shares larger
            # than the default readsize, which is 2000 bytes). A 15kB file
            # will have 5kB shares.
            d.addCallback(lambda res: n.overwrite(MutableData(b"large size file" * 1000)))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res:
                          self.assertThat(res, Equals(b"large size file" * 1000)))
            return d
        d.addCallback(_created)
        return d


    def test_upload_and_download_mdmf(self):
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda ignored:
                n.get_servermap(MODE_READ))
            def _then(servermap):
                dumped = servermap.dump(StringIO())
                self.assertThat(dumped.getvalue(), Contains("3-of-10"))
            d.addCallback(_then)
            # Now overwrite the contents with some new contents. We want
            # to make them big enough to force the file to be uploaded
            # in more than one segment.
            big_contents = b"contents1" * 100000 # about 900 KiB
            big_contents_uploadable = MutableData(big_contents)
            d.addCallback(lambda ignored:
                n.overwrite(big_contents_uploadable))
            d.addCallback(lambda ignored:
                n.download_best_version())
            d.addCallback(lambda data:
                self.assertThat(data, Equals(big_contents)))
            # Overwrite the contents again with some new contents. As
            # before, they need to be big enough to force multiple
            # segments, so that we make the downloader deal with
            # multiple segments.
            bigger_contents = b"contents2" * 1000000 # about 9MiB
            bigger_contents_uploadable = MutableData(bigger_contents)
            d.addCallback(lambda ignored:
                n.overwrite(bigger_contents_uploadable))
            d.addCallback(lambda ignored:
                n.download_best_version())
            d.addCallback(lambda data:
                self.assertThat(data, Equals(bigger_contents)))
            return d
        d.addCallback(_created)
        return d


    def test_retrieve_producer_mdmf(self):
        # We should make sure that the retriever is able to pause and stop
        # correctly.
        data = b"contents1" * 100000
        d = self.nodemaker.create_mutable_file(MutableData(data),
                                               version=MDMF_VERSION)
        d.addCallback(lambda node: node.get_best_mutable_version())
        d.addCallback(self._test_retrieve_producer, "MDMF", data)
        return d

    # note: SDMF has only one big segment, so we can't use the usual
    # after-the-first-write() trick to pause or stop the download.
    # Disabled until we find a better approach.
    def OFF_test_retrieve_producer_sdmf(self):
        data = b"contents1" * 100000
        d = self.nodemaker.create_mutable_file(MutableData(data),
                                               version=SDMF_VERSION)
        d.addCallback(lambda node: node.get_best_mutable_version())
        d.addCallback(self._test_retrieve_producer, "SDMF", data)
        return d

    def _test_retrieve_producer(self, version, kind, data):
        # Now we'll retrieve it into a pausing consumer.
        c = PausingConsumer()
        d = version.read(c)
        d.addCallback(lambda ign: self.assertThat(c.size, Equals(len(data))))

        c2 = PausingAndStoppingConsumer()
        d.addCallback(lambda ign:
                      self.shouldFail(DownloadStopped, kind+"_pause_stop",
                                      "our Consumer called stopProducing()",
                                      version.read, c2))

        c3 = StoppingConsumer()
        d.addCallback(lambda ign:
                      self.shouldFail(DownloadStopped, kind+"_stop",
                                      "our Consumer called stopProducing()",
                                      version.read, c3))

        c4 = ImmediatelyStoppingConsumer()
        d.addCallback(lambda ign:
                      self.shouldFail(DownloadStopped, kind+"_stop_imm",
                                      "our Consumer called stopProducing()",
                                      version.read, c4))

        def _then(ign):
            c5 = MemoryConsumer()
            d1 = version.read(c5)
            c5.producer.stopProducing()
            return self.shouldFail(DownloadStopped, kind+"_stop_imm2",
                                   "our Consumer called stopProducing()",
                                   lambda: d1)
        d.addCallback(_then)
        return d

    def test_download_from_mdmf_cap(self):
        # We should be able to download an MDMF file given its cap
        d = self.nodemaker.create_mutable_file(version=MDMF_VERSION)
        def _created(node):
            self.uri = node.get_uri()
            # also confirm that the cap has no extension fields
            pieces = self.uri.split(b":")
            self.assertThat(pieces, HasLength(4))

            return node.overwrite(MutableData(b"contents1" * 100000))
        def _then(ignored):
            node = self.nodemaker.create_from_cap(self.uri)
            return node.download_best_version()
        def _downloaded(data):
            self.assertThat(data, Equals(b"contents1" * 100000))
        d.addCallback(_created)
        d.addCallback(_then)
        d.addCallback(_downloaded)
        return d


    def test_mdmf_write_count(self):
        """
        Publishing an MDMF file causes exactly one write for each share that is to
        be published. Otherwise, we introduce undesirable semantics that are a
        regression from SDMF.
        """
        upload = MutableData(b"MDMF" * 100000) # about 400 KiB
        d = self.nodemaker.create_mutable_file(upload,
                                               version=MDMF_VERSION)
        def _check_server_write_counts(ignored):
            for peer in self._peers:
                # There were enough servers for each to only get a single
                # share.
                self.assertEqual(peer.storage_server.queries, 1)
        d.addCallback(_check_server_write_counts)
        return d


    def test_create_with_initial_contents(self):
        upload1 = MutableData(b"contents 1")
        d = self.nodemaker.create_mutable_file(upload1)
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 1")))
            upload2 = MutableData(b"contents 2")
            d.addCallback(lambda res: n.overwrite(upload2))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 2")))
            return d
        d.addCallback(_created)
        return d


    def test_create_mdmf_with_initial_contents(self):
        initial_contents = b"foobarbaz" * 131072 # 900KiB
        initial_contents_uploadable = MutableData(initial_contents)
        d = self.nodemaker.create_mutable_file(initial_contents_uploadable,
                                               version=MDMF_VERSION)
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda data:
                self.assertThat(data, Equals(initial_contents)))
            uploadable2 = MutableData(initial_contents + b"foobarbaz")
            d.addCallback(lambda ignored:
                n.overwrite(uploadable2))
            d.addCallback(lambda ignored:
                n.download_best_version())
            d.addCallback(lambda data:
                self.assertThat(data, Equals(initial_contents +
                                           b"foobarbaz")))
            return d
        d.addCallback(_created)
        return d

    def test_create_with_initial_contents_function(self):
        data = b"initial contents"
        def _make_contents(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            key = n.get_writekey()
            self.assertTrue(isinstance(key, bytes), key)
            self.assertThat(key, HasLength(16)) # AES key size
            return MutableData(data)
        d = self.nodemaker.create_mutable_file(_make_contents)
        def _created(n):
            return n.download_best_version()
        d.addCallback(_created)
        d.addCallback(lambda data2: self.assertThat(data2, Equals(data)))
        return d


    def test_create_mdmf_with_initial_contents_function(self):
        data = b"initial contents" * 100000
        def _make_contents(n):
            self.assertThat(n, IsInstance(MutableFileNode))
            key = n.get_writekey()
            self.assertTrue(isinstance(key, bytes), key)
            self.assertThat(key, HasLength(16))
            return MutableData(data)
        d = self.nodemaker.create_mutable_file(_make_contents,
                                               version=MDMF_VERSION)
        d.addCallback(lambda n:
            n.download_best_version())
        d.addCallback(lambda data2:
            self.assertThat(data2, Equals(data)))
        return d


    def test_create_with_too_large_contents(self):
        BIG = b"a" * (self.OLD_MAX_SEGMENT_SIZE + 1)
        BIG_uploadable = MutableData(BIG)
        d = self.nodemaker.create_mutable_file(BIG_uploadable)
        def _created(n):
            other_BIG_uploadable = MutableData(BIG)
            d = n.overwrite(other_BIG_uploadable)
            return d
        d.addCallback(_created)
        return d

    def failUnlessCurrentSeqnumIs(self, n, expected_seqnum, which):
        d = n.get_servermap(MODE_READ)
        d.addCallback(lambda servermap: servermap.best_recoverable_version())
        d.addCallback(lambda verinfo:
                      self.assertThat(verinfo[0], Equals(expected_seqnum), which))
        return d

    def test_modify(self):
        def _modifier(old_contents, servermap, first_time):
            new_contents = old_contents + b"line2"
            return new_contents
        def _non_modifier(old_contents, servermap, first_time):
            return old_contents
        def _none_modifier(old_contents, servermap, first_time):
            return None
        def _error_modifier(old_contents, servermap, first_time):
            raise ValueError("oops")
        def _toobig_modifier(old_contents, servermap, first_time):
            new_content = b"b" * (self.OLD_MAX_SEGMENT_SIZE + 1)
            return new_content
        calls = []
        def _ucw_error_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            new_contents = old_contents + b"line3"
            return new_contents
        def _ucw_error_non_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once, and don't actually
            # modify the contents on subsequent invocations
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents

        initial_contents = b"line1"
        d = self.nodemaker.create_mutable_file(MutableData(initial_contents))
        def _created(n):
            d = n.modify(_modifier)
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"line1line2")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "m"))

            d.addCallback(lambda res: n.modify(_non_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"line1line2")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "non"))

            d.addCallback(lambda res: n.modify(_none_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"line1line2")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "none"))

            d.addCallback(lambda res:
                          self.shouldFail(ValueError, "error_modifier", None,
                                          n.modify, _error_modifier))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"line1line2")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "err"))


            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"line1line2")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "big"))

            d.addCallback(lambda res: n.modify(_ucw_error_modifier))
            d.addCallback(lambda res: self.assertThat(calls, HasLength(2)))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res,
                                                           Equals(b"line1line2line3")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "ucw"))

            def _reset_ucw_error_modifier(res):
                calls[:] = []
                return res
            d.addCallback(_reset_ucw_error_modifier)

            # in practice, this n.modify call should publish twice: the first
            # one gets a UCWE, the second does not. But our test jig (in
            # which the modifier raises the UCWE) skips over the first one,
            # so in this test there will be only one publish, and the seqnum
            # will only be one larger than the previous test, not two (i.e. 4
            # instead of 5).
            d.addCallback(lambda res: n.modify(_ucw_error_non_modifier))
            d.addCallback(lambda res: self.assertThat(calls, HasLength(2)))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res,
                                                           Equals(b"line1line2line3")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 4, "ucw"))
            d.addCallback(lambda res: n.modify(_toobig_modifier))
            return d
        d.addCallback(_created)
        return d


    def test_modify_backoffer(self):
        def _modifier(old_contents, servermap, first_time):
            return old_contents + b"line2"
        calls = []
        def _ucw_error_modifier(old_contents, servermap, first_time):
            # simulate an UncoordinatedWriteError once
            calls.append(1)
            if len(calls) <= 1:
                raise UncoordinatedWriteError("simulated")
            return old_contents + b"line3"
        def _always_ucw_error_modifier(old_contents, servermap, first_time):
            raise UncoordinatedWriteError("simulated")
        def _backoff_stopper(node, f):
            return f
        def _backoff_pauser(node, f):
            d = defer.Deferred()
            reactor.callLater(0.5, d.callback, None)
            return d

        # the give-up-er will hit its maximum retry count quickly
        giveuper = BackoffAgent()
        giveuper._delay = 0.1
        giveuper.factor = 1

        d = self.nodemaker.create_mutable_file(MutableData(b"line1"))
        def _created(n):
            d = n.modify(_modifier)
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"line1line2")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "m"))

            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "_backoff_stopper", None,
                                          n.modify, _ucw_error_modifier,
                                          _backoff_stopper))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"line1line2")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 2, "stop"))

            def _reset_ucw_error_modifier(res):
                calls[:] = []
                return res
            d.addCallback(_reset_ucw_error_modifier)
            d.addCallback(lambda res: n.modify(_ucw_error_modifier,
                                               _backoff_pauser))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res,
                                                           Equals(b"line1line2line3")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "pause"))

            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "giveuper", None,
                                          n.modify, _always_ucw_error_modifier,
                                          giveuper.delay))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res,
                                                          Equals(b"line1line2line3")))
            d.addCallback(lambda res: self.failUnlessCurrentSeqnumIs(n, 3, "giveup"))

            return d
        d.addCallback(_created)
        return d

    def test_upload_and_download_full_size_keys(self):
        self.nodemaker.key_generator = client.KeyGenerator()
        d = self.nodemaker.create_mutable_file()
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            d.addCallback(lambda smap: smap.dump(StringIO()))
            d.addCallback(lambda sio:
                          self.assertTrue("3-of-10" in sio.getvalue()))
            d.addCallback(lambda res: n.overwrite(MutableData(b"contents 1")))
            d.addCallback(lambda res: self.assertThat(res, Is(None)))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 1")))
            d.addCallback(lambda res: n.overwrite(MutableData(b"contents 2")))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 2")))
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            d.addCallback(lambda smap: n.upload(MutableData(b"contents 3"), smap))
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 3")))
            d.addCallback(lambda res: n.get_servermap(MODE_ANYTHING))
            d.addCallback(lambda smap:
                          n.download_version(smap,
                                             smap.best_recoverable_version()))
            d.addCallback(lambda res: self.assertThat(res, Equals(b"contents 3")))
            return d
        d.addCallback(_created)
        return d


    def test_size_after_servermap_update(self):
        # a mutable file node should have something to say about how big
        # it is after a servermap update is performed, since this tells
        # us how large the best version of that mutable file is.
        d = self.nodemaker.create_mutable_file()
        def _created(n):
            self.n = n
            return n.get_servermap(MODE_READ)
        d.addCallback(_created)
        d.addCallback(lambda ignored:
            self.assertThat(self.n.get_size(), Equals(0)))
        d.addCallback(lambda ignored:
            self.n.overwrite(MutableData(b"foobarbaz")))
        d.addCallback(lambda ignored:
            self.assertThat(self.n.get_size(), Equals(9)))
        d.addCallback(lambda ignored:
            self.nodemaker.create_mutable_file(MutableData(b"foobarbaz")))
        d.addCallback(_created)
        d.addCallback(lambda ignored:
            self.assertThat(self.n.get_size(), Equals(9)))
        return d
