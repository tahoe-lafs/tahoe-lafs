"""
Ported to Python 3.
"""
from __future__ import annotations

import os
from struct import (
    pack,
)
from functools import (
    partial,
)

import attr

from twisted.internet import defer
from twisted.trial import unittest
from twisted.application import service

from foolscap.api import Tub, fireEventually, flushEventualQueue

from eliot.twisted import (
    inline_callbacks,
)

from allmydata.introducer.client import IntroducerClient
from allmydata.crypto import aes
from allmydata.storage.server import (
    si_b2a,
    StorageServer,
    FoolscapStorageServer,
)
from allmydata.storage_client import StorageFarmBroker
from allmydata.immutable.layout import (
    make_write_bucket_proxy,
)
from allmydata.immutable import offloaded, upload
from allmydata import uri, client
from allmydata.util import hashutil, fileutil, mathutil, dictutil

from .no_network import (
    NoNetworkServer,
    LocalWrapper,
    fireNow,
)
from .common import (
    EMPTY_CLIENT_CONFIG,
    SyncTestCase,
)

from testtools.matchers import (
    Equals,
    MatchesListwise,
    IsInstance,
)
from testtools.twistedsupport import (
    succeeded,
)

MiB = 1024*1024

DATA = b"I need help\n" * 1000

class CHKUploadHelper_fake(offloaded.CHKUploadHelper):
    def start_encrypted(self, eu):
        d = eu.get_size()
        def _got_size(size):
            d2 = eu.get_all_encoding_parameters()
            def _got_parms(parms):
                # just pretend we did the upload
                needed_shares, happy, total_shares, segsize = parms
                ueb_data = {"needed_shares": needed_shares,
                            "total_shares": total_shares,
                            "segment_size": segsize,
                            "size": size,
                            }
                ueb_hash = b"fake"
                v = uri.CHKFileVerifierURI(self._storage_index, b"x"*32,
                                           needed_shares, total_shares, size)
                _UR = upload.UploadResults
                ur = _UR(file_size=size,
                         ciphertext_fetched=0,
                         preexisting_shares=0,
                         pushed_shares=total_shares,
                         sharemap={},
                         servermap={},
                         timings={},
                         uri_extension_data=ueb_data,
                         uri_extension_hash=ueb_hash,
                         verifycapstr=v.to_string())
                self._upload_status.set_results(ur)
                return ur
            d2.addCallback(_got_parms)
            return d2
        d.addCallback(_got_size)
        return d

@attr.s
class FakeCHKCheckerAndUEBFetcher(object):
    """
    A fake of ``CHKCheckerAndUEBFetcher`` which hard-codes some check result.
    """
    peer_getter = attr.ib()
    storage_index = attr.ib()
    logparent = attr.ib()

    _sharemap = attr.ib()
    _ueb_data = attr.ib()

    @property
    def _ueb_hash(self):
        return hashutil.uri_extension_hash(
            uri.pack_extension(self._ueb_data),
        )

    def check(self):
        return defer.succeed((
            self._sharemap,
            self._ueb_data,
            self._ueb_hash,
        ))

class FakeClient(service.MultiService):
    introducer_clients : list[IntroducerClient] = []
    DEFAULT_ENCODING_PARAMETERS = {"k":25,
                                   "happy": 75,
                                   "n": 100,
                                   "max_segment_size": 1*MiB,
                                   }

    def get_encoding_parameters(self):
        return self.DEFAULT_ENCODING_PARAMETERS
    def get_storage_broker(self):
        return self.storage_broker

def flush_but_dont_ignore(res):
    d = flushEventualQueue()
    def _done(ignored):
        return res
    d.addCallback(_done)
    return d

def wait_a_few_turns(ignored=None):
    d = fireEventually()
    d.addCallback(fireEventually)
    d.addCallback(fireEventually)
    d.addCallback(fireEventually)
    d.addCallback(fireEventually)
    d.addCallback(fireEventually)
    return d

def upload_data(uploader, data, convergence):
    u = upload.Data(data, convergence=convergence)
    return uploader.upload(u)


def make_uploader(helper_furl, parent, override_name=None):
    """
    Make an ``upload.Uploader`` service pointed at the given helper and with
    the given service parent.

    :param bytes helper_furl: The Foolscap URL of the upload helper.

    :param IServiceCollection parent: A parent to assign to the new uploader.

    :param str override_name: If not ``None``, a new name for the uploader
        service.  Multiple services cannot coexist with the same name.
    """
    u = upload.Uploader(helper_furl)
    if override_name is not None:
        u.name = override_name
    u.setServiceParent(parent)
    return u


class AssistedUpload(unittest.TestCase):
    def setUp(self):
        self.tub = t = Tub()
        t.setOption("expose-remote-exception-types", False)
        self.s = FakeClient()
        self.s.storage_broker = StorageFarmBroker(
            True,
            lambda h: self.tub,
            EMPTY_CLIENT_CONFIG,
        )
        self.s.secret_holder = client.SecretHolder(b"lease secret", b"converge")
        self.s.startService()

        t.setServiceParent(self.s)
        self.s.tub = t
        # we never actually use this for network traffic, so it can use a
        # bogus host/port
        t.setLocation(b"bogus:1234")

    def setUpHelper(self, basedir, chk_upload=CHKUploadHelper_fake, chk_checker=None):
        fileutil.make_dirs(basedir)
        self.helper = offloaded.Helper(
            basedir,
            self.s.storage_broker,
            self.s.secret_holder,
            None,
            None,
        )
        if chk_upload is not None:
            self.helper.chk_upload = chk_upload
        if chk_checker is not None:
            self.helper.chk_checker = chk_checker
        self.helper_furl = self.tub.registerReference(self.helper)

    def tearDown(self):
        d = self.s.stopService()
        d.addCallback(fireEventually)
        d.addBoth(flush_but_dont_ignore)
        return d

    def test_one(self):
        """
        Some data that has never been uploaded before can be uploaded in CHK
        format using the ``RIHelper`` provider and ``Uploader.upload``.
        """
        self.basedir = "helper/AssistedUpload/test_one"
        self.setUpHelper(self.basedir)
        u = make_uploader(self.helper_furl, self.s)

        d = wait_a_few_turns()

        def _ready(res):
            self.assertTrue(
                u._helper,
                "Expected uploader to have a helper reference, had {} instead.".format(
                    u._helper,
                ),
            )
            return upload_data(u, DATA, convergence=b"some convergence string")
        d.addCallback(_ready)

        def _uploaded(results):
            the_uri = results.get_uri()
            self.assertIn(b"CHK", the_uri)
            self.assertNotEqual(
                results.get_pushed_shares(),
                0,
            )
        d.addCallback(_uploaded)

        def _check_empty(res):
            # Make sure the intermediate artifacts aren't left lying around.
            files = os.listdir(os.path.join(self.basedir, "CHK_encoding"))
            self.assertEqual(files, [])
            files = os.listdir(os.path.join(self.basedir, "CHK_incoming"))
            self.assertEqual(files, [])
        d.addCallback(_check_empty)

        return d

    @inline_callbacks
    def test_concurrent(self):
        """
        The same data can be uploaded by more than one ``Uploader`` at a time.
        """
        self.basedir = "helper/AssistedUpload/test_concurrent"
        self.setUpHelper(self.basedir)
        u1 = make_uploader(self.helper_furl, self.s, "u1")
        u2 = make_uploader(self.helper_furl, self.s, "u2")

        yield wait_a_few_turns()

        for u in [u1, u2]:
            self.assertTrue(
                u._helper,
                "Expected uploader to have a helper reference, had {} instead.".format(
                    u._helper,
                ),
            )

        uploads = list(
            upload_data(u, DATA, convergence=b"some convergence string")
            for u
            in [u1, u2]
        )

        result1, result2 = yield defer.gatherResults(uploads)

        self.assertEqual(
            result1.get_uri(),
            result2.get_uri(),
        )
        # It would be really cool to assert that result1.get_pushed_shares() +
        # result2.get_pushed_shares() == total_shares here.  However, we're
        # faking too much for that to be meaningful here.  Also it doesn't
        # hold because we don't actually push _anything_, we just lie about
        # having pushed stuff.

    def test_previous_upload_failed(self):
        self.basedir = "helper/AssistedUpload/test_previous_upload_failed"
        self.setUpHelper(self.basedir)

        # we want to make sure that an upload which fails (leaving the
        # ciphertext in the CHK_encoding/ directory) does not prevent a later
        # attempt to upload that file from working. We simulate this by
        # populating the directory manually. The hardest part is guessing the
        # storage index.

        k = FakeClient.DEFAULT_ENCODING_PARAMETERS["k"]
        n = FakeClient.DEFAULT_ENCODING_PARAMETERS["n"]
        max_segsize = FakeClient.DEFAULT_ENCODING_PARAMETERS["max_segment_size"]
        segsize = min(max_segsize, len(DATA))
        # this must be a multiple of 'required_shares'==k
        segsize = mathutil.next_multiple(segsize, k)

        key = hashutil.convergence_hash(k, n, segsize, DATA, b"test convergence string")
        assert len(key) == 16
        encryptor = aes.create_encryptor(key)
        SI = hashutil.storage_index_hash(key)
        SI_s = str(si_b2a(SI), "utf-8")
        encfile = os.path.join(self.basedir, "CHK_encoding", SI_s)
        f = open(encfile, "wb")
        f.write(aes.encrypt_data(encryptor, DATA))
        f.close()

        u = make_uploader(self.helper_furl, self.s)

        d = wait_a_few_turns()

        def _ready(res):
            assert u._helper
            return upload_data(u, DATA, convergence=b"test convergence string")
        d.addCallback(_ready)
        def _uploaded(results):
            the_uri = results.get_uri()
            assert b"CHK" in the_uri
        d.addCallback(_uploaded)

        def _check_empty(res):
            files = os.listdir(os.path.join(self.basedir, "CHK_encoding"))
            self.failUnlessEqual(files, [])
            files = os.listdir(os.path.join(self.basedir, "CHK_incoming"))
            self.failUnlessEqual(files, [])
        d.addCallback(_check_empty)

        return d

    @inline_callbacks
    def test_already_uploaded(self):
        """
        If enough shares to satisfy the needed parameter already exist, the upload
        succeeds without pushing any shares.
        """
        params = FakeClient.DEFAULT_ENCODING_PARAMETERS
        chk_checker = partial(
            FakeCHKCheckerAndUEBFetcher,
            sharemap=dictutil.DictOfSets({
                0: {b"server0"},
                1: {b"server1"},
            }),
            ueb_data={
                "size": len(DATA),
                "segment_size": min(params["max_segment_size"], len(DATA)),
                "needed_shares": params["k"],
                "total_shares": params["n"],
            },
        )
        self.basedir = "helper/AssistedUpload/test_already_uploaded"
        self.setUpHelper(
            self.basedir,
            chk_checker=chk_checker,
        )
        u = make_uploader(self.helper_furl, self.s)

        yield wait_a_few_turns()

        assert u._helper

        results = yield upload_data(u, DATA, convergence=b"some convergence string")
        the_uri = results.get_uri()
        assert b"CHK" in the_uri

        files = os.listdir(os.path.join(self.basedir, "CHK_encoding"))
        self.failUnlessEqual(files, [])
        files = os.listdir(os.path.join(self.basedir, "CHK_incoming"))
        self.failUnlessEqual(files, [])

        self.assertEqual(
            results.get_pushed_shares(),
            0,
        )


class CHKCheckerAndUEBFetcherTests(SyncTestCase):
    """
    Tests for ``CHKCheckerAndUEBFetcher``.
    """
    def test_check_no_peers(self):
        """
        If the supplied "peer getter" returns no peers then
        ``CHKCheckerAndUEBFetcher.check`` returns a ``Deferred`` that fires
        with ``False``.
        """
        storage_index = b"a" * 16
        peers = {storage_index: []}
        caf = offloaded.CHKCheckerAndUEBFetcher(
            peers.get,
            storage_index,
            None,
        )
        self.assertThat(
            caf.check(),
            succeeded(Equals(False)),
        )

    @inline_callbacks
    def test_check_ueb_unavailable(self):
        """
        If the UEB cannot be read from any of the peers supplied by the "peer
        getter" then ``CHKCheckerAndUEBFetcher.check`` returns a ``Deferred``
        that fires with ``False``.
        """
        storage_index = b"a" * 16
        serverid = b"b" * 20
        storage = FoolscapStorageServer(StorageServer(self.mktemp(), serverid))
        rref_without_ueb = LocalWrapper(storage, fireNow)
        yield write_bad_share(rref_without_ueb, storage_index)
        server_without_ueb = NoNetworkServer(serverid, rref_without_ueb)
        peers = {storage_index: [server_without_ueb]}
        caf = offloaded.CHKCheckerAndUEBFetcher(
            peers.get,
            storage_index,
            None,
        )
        self.assertThat(
            caf.check(),
            succeeded(Equals(False)),
        )

    @inline_callbacks
    def test_not_enough_shares(self):
        """
        If fewer shares are found than are required to reassemble the data then
        ``CHKCheckerAndUEBFetcher.check`` returns a ``Deferred`` that fires
        with ``False``.
        """
        storage_index = b"a" * 16
        serverid = b"b" * 20
        storage = FoolscapStorageServer(StorageServer(self.mktemp(), serverid))
        rref_with_ueb = LocalWrapper(storage, fireNow)
        ueb = {
            "needed_shares": 2,
            "total_shares": 2,
            "segment_size": 128 * 1024,
            "size": 1024,
        }
        yield write_good_share(rref_with_ueb, storage_index, ueb, [0])

        server_with_ueb = NoNetworkServer(serverid, rref_with_ueb)
        peers = {storage_index: [server_with_ueb]}
        caf = offloaded.CHKCheckerAndUEBFetcher(
            peers.get,
            storage_index,
            None,
        )
        self.assertThat(
            caf.check(),
            succeeded(Equals(False)),
        )

    @inline_callbacks
    def test_enough_shares(self):
        """
        If enough shares are found to reassemble the data then
        ``CHKCheckerAndUEBFetcher.check`` returns a ``Deferred`` that fires
        with share and share placement information.
        """
        storage_index = b"a" * 16
        serverids = list(
            ch * 20
            for ch
            in [b"b", b"c"]
        )
        storages = list(
            FoolscapStorageServer(StorageServer(self.mktemp(), serverid))
            for serverid
            in serverids
        )
        rrefs_with_ueb = list(
            LocalWrapper(storage, fireNow)
            for storage
            in storages
        )
        ueb = {
            "needed_shares": len(serverids),
            "total_shares": len(serverids),
            "segment_size": 128 * 1024,
            "size": 1024,
        }
        for n, rref_with_ueb in enumerate(rrefs_with_ueb):
            yield write_good_share(rref_with_ueb, storage_index, ueb, [n])

        servers_with_ueb = list(
            NoNetworkServer(serverid, rref_with_ueb)
            for (serverid, rref_with_ueb)
            in zip(serverids, rrefs_with_ueb)
        )
        peers = {storage_index: servers_with_ueb}
        caf = offloaded.CHKCheckerAndUEBFetcher(
            peers.get,
            storage_index,
            None,
        )
        self.assertThat(
            caf.check(),
            succeeded(MatchesListwise([
                Equals({
                    n: {serverid}
                    for (n, serverid)
                    in enumerate(serverids)
                }),
                Equals(ueb),
                IsInstance(bytes),
            ])),
        )


def write_bad_share(storage_rref, storage_index):
    """
    Write a share with a corrupt URI extension block.
    """
    # Write some trash to the right bucket on this storage server.  It won't
    # have a recoverable UEB block.
    return write_share(storage_rref, storage_index, [0], b"\0" * 1024)


def write_good_share(storage_rref, storage_index, ueb, sharenums):
    """
    Write a valid share with the given URI extension block.
    """
    write_proxy = make_write_bucket_proxy(
        storage_rref,
        None,
        1024,
        ueb["segment_size"],
        1,
        1,
        ueb["size"],
    )
    # See allmydata/immutable/layout.py
    offset = write_proxy._offsets["uri_extension"]
    filler = b"\0" * (offset - len(write_proxy._offset_data))
    ueb_data = uri.pack_extension(ueb)
    data = (
        write_proxy._offset_data +
        filler +
        pack(write_proxy.fieldstruct, len(ueb_data)) +
        ueb_data
    )
    return write_share(storage_rref, storage_index, sharenums, data)


@inline_callbacks
def write_share(storage_rref, storage_index, sharenums, sharedata):
    """
    Write the given share data to the given storage index using the given
    IStorageServer remote reference.

    :param foolscap.ipb.IRemoteReference storage_rref: A remote reference to
        an IStorageServer.

    :param bytes storage_index: The storage index to which to write the share
        data.

    :param [int] sharenums: The share numbers to which to write this sharedata.

    :param bytes sharedata: The ciphertext to write as the share.
    """
    ignored, writers = yield storage_rref.callRemote(
        "allocate_buckets",
        storage_index,
        b"x" * 16,
        b"x" * 16,
        sharenums,
        len(sharedata),
        LocalWrapper(None),

    )
    [writer] = writers.values()
    yield writer.callRemote("write", 0, sharedata)
    yield writer.callRemote("close")
