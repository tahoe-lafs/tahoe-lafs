import hashlib
from mock import Mock
from allmydata.util import base32, yamlutil

from twisted.trial import unittest
from twisted.internet.defer import succeed, inlineCallbacks

from allmydata.storage_client import NativeStorageServer
from allmydata.storage_client import StorageFarmBroker


class NativeStorageServerWithVersion(NativeStorageServer):
    def __init__(self, version):
        # note: these instances won't work for anything other than
        # get_available_space() because we don't upcall
        self.version = version
    def get_version(self):
        return self.version


class TestNativeStorageServer(unittest.TestCase):
    def test_get_available_space_new(self):
        nss = NativeStorageServerWithVersion(
            { "http://allmydata.org/tahoe/protocols/storage/v1":
                { "maximum-immutable-share-size": 111,
                  "available-space": 222,
                }
            })
        self.failUnlessEqual(nss.get_available_space(), 222)

    def test_get_available_space_old(self):
        nss = NativeStorageServerWithVersion(
            { "http://allmydata.org/tahoe/protocols/storage/v1":
                { "maximum-immutable-share-size": 111,
                }
            })
        self.failUnlessEqual(nss.get_available_space(), 111)

    def test_missing_nickname(self):
        ann = {"anonymous-storage-FURL": "pb://w2hqnbaa25yw4qgcvghl5psa3srpfgw3@tcp:127.0.0.1:51309/vucto2z4fxment3vfxbqecblbf6zyp6x",
               "permutation-seed-base32": "w2hqnbaa25yw4qgcvghl5psa3srpfgw3",
               }
        nss = NativeStorageServer("server_id", ann, None, {})
        self.assertEqual(nss.get_nickname(), "")

class TestStorageFarmBroker(unittest.TestCase):

    def test_static_servers(self):
        broker = StorageFarmBroker(True, lambda h: Mock())

        key_s = 'v0-1234-1'
        servers_yaml = """\
storage:
  v0-1234-1:
    ann:
      anonymous-storage-FURL: pb://ge@nowhere/fake
      permutation-seed-base32: aaaaaaaaaaaaaaaaaaaaaaaa
"""
        servers = yamlutil.safe_load(servers_yaml)
        permseed = base32.a2b("aaaaaaaaaaaaaaaaaaaaaaaa")
        broker.set_static_servers(servers["storage"])
        self.failUnlessEqual(len(broker._static_server_ids), 1)
        s = broker.servers[key_s]
        self.failUnlessEqual(s.announcement,
                             servers["storage"]["v0-1234-1"]["ann"])
        self.failUnlessEqual(s.get_serverid(), key_s)
        self.assertEqual(s.get_permutation_seed(), permseed)

        # if the Introducer announces the same thing, we're supposed to
        # ignore it

        ann2 = {
            "service-name": "storage",
            "anonymous-storage-FURL": "pb://{}@nowhere/fake2".format(base32.b2a(str(1))),
            "permutation-seed-base32": "bbbbbbbbbbbbbbbbbbbbbbbb",
        }
        broker._got_announcement(key_s, ann2)
        s2 = broker.servers[key_s]
        self.assertIdentical(s2, s)
        self.assertEqual(s2.get_permutation_seed(), permseed)

    def test_static_permutation_seed_pubkey(self):
        broker = StorageFarmBroker(True, lambda h: Mock())
        server_id = "v0-4uazse3xb6uu5qpkb7tel2bm6bpea4jhuigdhqcuvvse7hugtsia"
        k = "4uazse3xb6uu5qpkb7tel2bm6bpea4jhuigdhqcuvvse7hugtsia"
        ann = {
            "anonymous-storage-FURL": "pb://abcde@nowhere/fake",
        }
        broker.set_static_servers({server_id.decode("ascii"): {"ann": ann}})
        s = broker.servers[server_id]
        self.assertEqual(s.get_permutation_seed(), base32.a2b(k))

    def test_static_permutation_seed_explicit(self):
        broker = StorageFarmBroker(True, lambda h: Mock())
        server_id = "v0-4uazse3xb6uu5qpkb7tel2bm6bpea4jhuigdhqcuvvse7hugtsia"
        k = "w5gl5igiexhwmftwzhai5jy2jixn7yx7"
        ann = {
            "anonymous-storage-FURL": "pb://abcde@nowhere/fake",
            "permutation-seed-base32": k,
        }
        broker.set_static_servers({server_id.decode("ascii"): {"ann": ann}})
        s = broker.servers[server_id]
        self.assertEqual(s.get_permutation_seed(), base32.a2b(k))

    def test_static_permutation_seed_hashed(self):
        broker = StorageFarmBroker(True, lambda h: Mock())
        server_id = "unparseable"
        ann = {
            "anonymous-storage-FURL": "pb://abcde@nowhere/fake",
        }
        broker.set_static_servers({server_id.decode("ascii"): {"ann": ann}})
        s = broker.servers[server_id]
        self.assertEqual(s.get_permutation_seed(),
                         hashlib.sha256(server_id).digest())

    @inlineCallbacks
    def test_threshold_reached(self):
        introducer = Mock()
        new_tubs = []
        def make_tub(*args, **kwargs):
            return new_tubs.pop()
        broker = StorageFarmBroker(True, make_tub)
        done = broker.when_connected_enough(5)
        broker.use_introducer(introducer)
        # subscribes to "storage" to learn of new storage nodes
        subscribe = introducer.mock_calls[0]
        self.assertEqual(subscribe[0], 'subscribe_to')
        self.assertEqual(subscribe[1][0], 'storage')
        got_announcement = subscribe[1][1]

        data = {
            "service-name": "storage",
            "anonymous-storage-FURL": None,
            "permutation-seed-base32": "aaaaaaaaaaaaaaaaaaaaaaaa",
        }

        def add_one_server(x):
            data["anonymous-storage-FURL"] = "pb://{}@nowhere/fake".format(base32.b2a(str(x)))
            tub = Mock()
            new_tubs.append(tub)
            got_announcement('v0-1234-{}'.format(x), data)
            self.assertEqual(tub.mock_calls[-1][0], 'connectTo')
            got_connection = tub.mock_calls[-1][1][1]
            rref = Mock()
            rref.callRemote = Mock(return_value=succeed(1234))
            got_connection(rref)

        # first 4 shouldn't trigger connected_threashold
        for x in range(4):
            add_one_server(x)
            self.assertFalse(done.called)

        # ...but the 5th *should* trigger the threshold
        add_one_server(42)

        # so: the OneShotObserverList only notifies via
        # foolscap.eventually() -- which forces the Deferred call
        # through the reactor -- so it's no longer synchronous,
        # meaning that we have to do "real reactor stuff" for the
        # Deferred from when_connected_enough() to actually fire. (or
        # @patch() out the reactor in foolscap.eventually to be a
        # Clock() so we can advance time ourselves, but ... luckily
        # eventually() uses 0 as the timeout currently)

        yield done
        self.assertTrue(done.called)
