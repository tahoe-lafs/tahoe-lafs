from mock import Mock, patch
from allmydata.util import base32

from twisted.trial import unittest
from twisted.internet.defer import succeed, inlineCallbacks

from allmydata.storage_client import NativeStorageServer
from allmydata.storage_client import StorageFarmBroker, ConnectedEnough


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


class TestStorageFarmBroker(unittest.TestCase):

    @inlineCallbacks
    def test_threshold_reached(self):
        introducer = Mock()
        broker = StorageFarmBroker(True)
        done = ConnectedEnough(broker, 5).when_connected_enough()
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
            with patch("allmydata.storage_client.Tub", side_effect=[tub]):
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
