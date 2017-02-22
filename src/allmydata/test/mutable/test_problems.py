import os, base64
from twisted.trial import unittest
from twisted.internet import defer
from foolscap.logging import log
from allmydata import uri
from allmydata.interfaces import NotEnoughSharesError, SDMF_VERSION, MDMF_VERSION
from allmydata.util import fileutil
from allmydata.util.hashutil import ssk_writekey_hash, ssk_pubkey_fingerprint_hash
from allmydata.mutable.common import \
     MODE_CHECK, MODE_WRITE, MODE_READ, \
     UncoordinatedWriteError, \
     NotEnoughServersError
from allmydata.mutable.publish import MutableData
from allmydata.storage.common import storage_index_to_dir
from ..common import TEST_RSA_KEY_SIZE
from ..no_network import GridTestMixin
from .. import common_util as testutil
from ..common_util import DevNullDictionary

class SameKeyGenerator:
    def __init__(self, pubkey, privkey):
        self.pubkey = pubkey
        self.privkey = privkey
    def generate(self, keysize=None):
        return defer.succeed( (self.pubkey, self.privkey) )

class FirstServerGetsKilled:
    done = False
    def notify(self, retval, wrapper, methname):
        if not self.done:
            wrapper.broken = True
            self.done = True
        return retval

class FirstServerGetsDeleted:
    def __init__(self):
        self.done = False
        self.silenced = None
    def notify(self, retval, wrapper, methname):
        if not self.done:
            # this query will work, but later queries should think the share
            # has been deleted
            self.done = True
            self.silenced = wrapper
            return retval
        if wrapper == self.silenced:
            assert methname == "slot_testv_and_readv_and_writev"
            return (True, {})
        return retval

class Problems(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin):
    def do_publish_surprise(self, version):
        self.basedir = "mutable/Problems/test_publish_surprise_%s" % version
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        d = nm.create_mutable_file(MutableData("contents 1"),
                                    version=version)
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
            d.addCallback(_got_smap1)
            # then modify the file, leaving the old map untouched
            d.addCallback(lambda res: log.msg("starting winning write"))
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            # now attempt to modify the file with the old servermap. This
            # will look just like an uncoordinated write, in which every
            # single share got updated between our mapupdate and our publish
            d.addCallback(lambda res: log.msg("starting doomed write"))
            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "test_publish_surprise", None,
                                          n.upload,
                                          MutableData("contents 2a"), self.old_map))
            return d
        d.addCallback(_created)
        return d

    def test_publish_surprise_sdmf(self):
        return self.do_publish_surprise(SDMF_VERSION)

    def test_publish_surprise_mdmf(self):
        return self.do_publish_surprise(MDMF_VERSION)

    def test_retrieve_surprise(self):
        self.basedir = "mutable/Problems/test_retrieve_surprise"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        d = nm.create_mutable_file(MutableData("contents 1"*4000))
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_READ))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
            d.addCallback(_got_smap1)
            # then modify the file, leaving the old map untouched
            d.addCallback(lambda res: log.msg("starting winning write"))
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            # now attempt to retrieve the old version with the old servermap.
            # This will look like someone has changed the file since we
            # updated the servermap.
            d.addCallback(lambda res: log.msg("starting doomed read"))
            d.addCallback(lambda res:
                          self.shouldFail(NotEnoughSharesError,
                                          "test_retrieve_surprise",
                                          "ran out of servers: have 0 of 1",
                                          n.download_version,
                                          self.old_map,
                                          self.old_map.best_recoverable_version(),
                                          ))
            return d
        d.addCallback(_created)
        return d


    def test_unexpected_shares(self):
        # upload the file, take a servermap, shut down one of the servers,
        # upload it again (causing shares to appear on a new server), then
        # upload using the old servermap. The last upload should fail with an
        # UncoordinatedWriteError, because of the shares that didn't appear
        # in the servermap.
        self.basedir = "mutable/Problems/test_unexpected_shares"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        d = nm.create_mutable_file(MutableData("contents 1"))
        def _created(n):
            d = defer.succeed(None)
            d.addCallback(lambda res: n.get_servermap(MODE_WRITE))
            def _got_smap1(smap):
                # stash the old state of the file
                self.old_map = smap
                # now shut down one of the servers
                peer0 = list(smap.make_sharemap()[0])[0].get_serverid()
                self.g.remove_server(peer0)
                # then modify the file, leaving the old map untouched
                log.msg("starting winning write")
                return n.overwrite(MutableData("contents 2"))
            d.addCallback(_got_smap1)
            # now attempt to modify the file with the old servermap. This
            # will look just like an uncoordinated write, in which every
            # single share got updated between our mapupdate and our publish
            d.addCallback(lambda res: log.msg("starting doomed write"))
            d.addCallback(lambda res:
                          self.shouldFail(UncoordinatedWriteError,
                                          "test_surprise", None,
                                          n.upload,
                                          MutableData("contents 2a"), self.old_map))
            return d
        d.addCallback(_created)
        return d

    def test_multiply_placed_shares(self):
        self.basedir = "mutable/Problems/test_multiply_placed_shares"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        d = nm.create_mutable_file(MutableData("contents 1"))
        # remove one of the servers and reupload the file.
        def _created(n):
            self._node = n

            servers = self.g.get_all_serverids()
            self.ss = self.g.remove_server(servers[len(servers)-1])

            new_server = self.g.make_server(len(servers)-1)
            self.g.add_server(len(servers)-1, new_server)

            return self._node.download_best_version()
        d.addCallback(_created)
        d.addCallback(lambda data: MutableData(data))
        d.addCallback(lambda data: self._node.overwrite(data))

        # restore the server we removed earlier, then download+upload
        # the file again
        def _overwritten(ign):
            self.g.add_server(len(self.g.servers_by_number), self.ss)
            return self._node.download_best_version()
        d.addCallback(_overwritten)
        d.addCallback(lambda data: MutableData(data))
        d.addCallback(lambda data: self._node.overwrite(data))
        d.addCallback(lambda ignored:
            self._node.get_servermap(MODE_CHECK))
        def _overwritten_again(smap):
            # Make sure that all shares were updated by making sure that
            # there aren't any other versions in the sharemap.
            self.failUnlessEqual(len(smap.recoverable_versions()), 1)
            self.failUnlessEqual(len(smap.unrecoverable_versions()), 0)
        d.addCallback(_overwritten_again)
        return d

    def test_bad_server(self):
        # Break one server, then create the file: the initial publish should
        # complete with an alternate server. Breaking a second server should
        # not prevent an update from succeeding either.
        self.basedir = "mutable/Problems/test_bad_server"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker

        # to make sure that one of the initial peers is broken, we have to
        # get creative. We create an RSA key and compute its storage-index.
        # Then we make a KeyGenerator that always returns that one key, and
        # use it to create the mutable file. This will get easier when we can
        # use #467 static-server-selection to disable permutation and force
        # the choice of server for share[0].

        d = nm.key_generator.generate(TEST_RSA_KEY_SIZE)
        def _got_key( (pubkey, privkey) ):
            nm.key_generator = SameKeyGenerator(pubkey, privkey)
            pubkey_s = pubkey.serialize()
            privkey_s = privkey.serialize()
            u = uri.WriteableSSKFileURI(ssk_writekey_hash(privkey_s),
                                        ssk_pubkey_fingerprint_hash(pubkey_s))
            self._storage_index = u.get_storage_index()
        d.addCallback(_got_key)
        def _break_peer0(res):
            si = self._storage_index
            servers = nm.storage_broker.get_servers_for_psi(si)
            self.g.break_server(servers[0].get_serverid())
            self.server1 = servers[1]
        d.addCallback(_break_peer0)
        # now "create" the file, using the pre-established key, and let the
        # initial publish finally happen
        d.addCallback(lambda res: nm.create_mutable_file(MutableData("contents 1")))
        # that ought to work
        def _got_node(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            # now break the second peer
            def _break_peer1(res):
                self.g.break_server(self.server1.get_serverid())
            d.addCallback(_break_peer1)
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            # that ought to work too
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            def _explain_error(f):
                print f
                if f.check(NotEnoughServersError):
                    print "first_error:", f.value.first_error
                return f
            d.addErrback(_explain_error)
            return d
        d.addCallback(_got_node)
        return d

    def test_bad_server_overlap(self):
        # like test_bad_server, but with no extra unused servers to fall back
        # upon. This means that we must re-use a server which we've already
        # used. If we don't remember the fact that we sent them one share
        # already, we'll mistakenly think we're experiencing an
        # UncoordinatedWriteError.

        # Break one server, then create the file: the initial publish should
        # complete with an alternate server. Breaking a second server should
        # not prevent an update from succeeding either.
        self.basedir = "mutable/Problems/test_bad_server_overlap"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        sb = nm.storage_broker

        peerids = [s.get_serverid() for s in sb.get_connected_servers()]
        self.g.break_server(peerids[0])

        d = nm.create_mutable_file(MutableData("contents 1"))
        def _created(n):
            d = n.download_best_version()
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 1"))
            # now break one of the remaining servers
            def _break_second_server(res):
                self.g.break_server(peerids[1])
            d.addCallback(_break_second_server)
            d.addCallback(lambda res: n.overwrite(MutableData("contents 2")))
            # that ought to work too
            d.addCallback(lambda res: n.download_best_version())
            d.addCallback(lambda res: self.failUnlessEqual(res, "contents 2"))
            return d
        d.addCallback(_created)
        return d

    def test_publish_all_servers_bad(self):
        # Break all servers: the publish should fail
        self.basedir = "mutable/Problems/test_publish_all_servers_bad"
        self.set_up_grid()
        nm = self.g.clients[0].nodemaker
        for s in nm.storage_broker.get_connected_servers():
            s.get_rref().broken = True

        d = self.shouldFail(NotEnoughServersError,
                            "test_publish_all_servers_bad",
                            "ran out of good servers",
                            nm.create_mutable_file, MutableData("contents"))
        return d

    def test_publish_no_servers(self):
        # no servers at all: the publish should fail
        self.basedir = "mutable/Problems/test_publish_no_servers"
        self.set_up_grid(num_servers=0)
        nm = self.g.clients[0].nodemaker

        d = self.shouldFail(NotEnoughServersError,
                            "test_publish_no_servers",
                            "Ran out of non-bad servers",
                            nm.create_mutable_file, MutableData("contents"))
        return d


    def test_privkey_query_error(self):
        # when a servermap is updated with MODE_WRITE, it tries to get the
        # privkey. Something might go wrong during this query attempt.
        # Exercise the code in _privkey_query_failed which tries to handle
        # such an error.
        self.basedir = "mutable/Problems/test_privkey_query_error"
        self.set_up_grid(num_servers=20)
        nm = self.g.clients[0].nodemaker
        nm._node_cache = DevNullDictionary() # disable the nodecache

        # we need some contents that are large enough to push the privkey out
        # of the early part of the file
        LARGE = "These are Larger contents" * 2000 # about 50KB
        LARGE_uploadable = MutableData(LARGE)
        d = nm.create_mutable_file(LARGE_uploadable)
        def _created(n):
            self.uri = n.get_uri()
            self.n2 = nm.create_from_cap(self.uri)

            # When a mapupdate is performed on a node that doesn't yet know
            # the privkey, a short read is sent to a batch of servers, to get
            # the verinfo and (hopefully, if the file is short enough) the
            # encprivkey. Our file is too large to let this first read
            # contain the encprivkey. Each non-encprivkey-bearing response
            # that arrives (until the node gets the encprivkey) will trigger
            # a second read to specifically read the encprivkey.
            #
            # So, to exercise this case:
            #  1. notice which server gets a read() call first
            #  2. tell that server to start throwing errors
            killer = FirstServerGetsKilled()
            for s in nm.storage_broker.get_connected_servers():
                s.get_rref().post_call_notifier = killer.notify
        d.addCallback(_created)

        # now we update a servermap from a new node (which doesn't have the
        # privkey yet, forcing it to use a separate privkey query). Note that
        # the map-update will succeed, since we'll just get a copy from one
        # of the other shares.
        d.addCallback(lambda res: self.n2.get_servermap(MODE_WRITE))

        return d

    def test_privkey_query_missing(self):
        # like test_privkey_query_error, but the shares are deleted by the
        # second query, instead of raising an exception.
        self.basedir = "mutable/Problems/test_privkey_query_missing"
        self.set_up_grid(num_servers=20)
        nm = self.g.clients[0].nodemaker
        LARGE = "These are Larger contents" * 2000 # about 50KiB
        LARGE_uploadable = MutableData(LARGE)
        nm._node_cache = DevNullDictionary() # disable the nodecache

        d = nm.create_mutable_file(LARGE_uploadable)
        def _created(n):
            self.uri = n.get_uri()
            self.n2 = nm.create_from_cap(self.uri)
            deleter = FirstServerGetsDeleted()
            for s in nm.storage_broker.get_connected_servers():
                s.get_rref().post_call_notifier = deleter.notify
        d.addCallback(_created)
        d.addCallback(lambda res: self.n2.get_servermap(MODE_WRITE))
        return d


    def test_block_and_hash_query_error(self):
        # This tests for what happens when a query to a remote server
        # fails in either the hash validation step or the block getting
        # step (because of batching, this is the same actual query).
        # We need to have the storage server persist up until the point
        # that its prefix is validated, then suddenly die. This
        # exercises some exception handling code in Retrieve.
        self.basedir = "mutable/Problems/test_block_and_hash_query_error"
        self.set_up_grid(num_servers=20)
        nm = self.g.clients[0].nodemaker
        CONTENTS = "contents" * 2000
        CONTENTS_uploadable = MutableData(CONTENTS)
        d = nm.create_mutable_file(CONTENTS_uploadable)
        def _created(node):
            self._node = node
        d.addCallback(_created)
        d.addCallback(lambda ignored:
            self._node.get_servermap(MODE_READ))
        def _then(servermap):
            # we have our servermap. Now we set up the servers like the
            # tests above -- the first one that gets a read call should
            # start throwing errors, but only after returning its prefix
            # for validation. Since we'll download without fetching the
            # private key, the next query to the remote server will be
            # for either a block and salt or for hashes, either of which
            # will exercise the error handling code.
            killer = FirstServerGetsKilled()
            for s in nm.storage_broker.get_connected_servers():
                s.get_rref().post_call_notifier = killer.notify
            ver = servermap.best_recoverable_version()
            assert ver
            return self._node.download_version(servermap, ver)
        d.addCallback(_then)
        d.addCallback(lambda data:
            self.failUnlessEqual(data, CONTENTS))
        return d

    def test_1654(self):
        # test that the Retrieve object unconditionally verifies the block
        # hash tree root for mutable shares. The failure mode is that
        # carefully crafted shares can cause undetected corruption (the
        # retrieve appears to finish successfully, but the result is
        # corrupted). When fixed, these shares always cause a
        # CorruptShareError, which results in NotEnoughSharesError in this
        # 2-of-2 file.
        self.basedir = "mutable/Problems/test_1654"
        self.set_up_grid(num_servers=2)
        cap = uri.from_string(TEST_1654_CAP)
        si = cap.get_storage_index()

        for share, shnum in [(TEST_1654_SH0, 0), (TEST_1654_SH1, 1)]:
            sharedata = base64.b64decode(share)
            storedir = self.get_serverdir(shnum)
            storage_path = os.path.join(storedir, "shares",
                                        storage_index_to_dir(si))
            fileutil.make_dirs(storage_path)
            fileutil.write(os.path.join(storage_path, "%d" % shnum),
                           sharedata)

        nm = self.g.clients[0].nodemaker
        n = nm.create_from_cap(TEST_1654_CAP)
        # to exercise the problem correctly, we must ensure that sh0 is
        # processed first, and sh1 second. NoNetworkGrid has facilities to
        # stall the first request from a single server, but it's not
        # currently easy to extend that to stall the second request (mutable
        # retrievals will see two: first the mapupdate, then the fetch).
        # However, repeated executions of this run without the #1654 fix
        # suggests that we're failing reliably even without explicit stalls,
        # probably because the servers are queried in a fixed order. So I'm
        # ok with relying upon that.
        d = self.shouldFail(NotEnoughSharesError, "test #1654 share corruption",
                            "ran out of servers",
                            n.download_best_version)
        return d


TEST_1654_CAP = "URI:SSK:6jthysgozssjnagqlcxjq7recm:yxawei54fmf2ijkrvs2shs6iey4kpdp6joi7brj2vrva6sp5nf3a"

TEST_1654_SH0 = """\
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
72mXGlqyLyWYuAAAAAA="""

TEST_1654_SH1 = """\
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
72mXGlqyLyWYuAAAAAA="""
