# -*- coding: utf-8 -*-

from twisted.trial import unittest
from allmydata.immutable import happiness_upload


class HappinessUtils(unittest.TestCase):
    """
    test-cases for utility functions augmenting_path_for and residual_network
    """

    def test_residual_0(self):
        graph = happiness_upload._servermap_flow_graph(
            ['peer0'],
            ['share0'],
            servermap={
                'peer0': ['share0'],
            }
        )
        flow = [[0 for _ in graph] for _ in graph]

        residual, capacity = happiness_upload.residual_network(graph, flow)

        # XXX no idea if these are right; hand-verify
        self.assertEqual(residual, [[1], [2], [3], []])
        self.assertEqual(capacity, [[0, 1, 0, 0], [-1, 0, 1, 0], [0, -1, 0, 1], [0, 0, -1, 0]])


class Happiness(unittest.TestCase):

    def test_placement_simple(self):

        shares = {'share0', 'share1', 'share2'}
        peers = {
            'peer0',
            'peer1',
        }
        readonly_peers = {'peer0'}
        peers_to_shares = {
            'peer0': {'share2'},
            'peer1': [],
        }

        places0 = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)

        if False:
            print("places0")
            for k, v in places0.items():
                print("  {} -> {}".format(k, v))

        self.assertEqual(
            places0,
            {
                'share0': {'peer1'},
                'share1': {'peer1'},
                'share2': {'peer0'},
            }
        )


    def test_placement_1(self):

        shares = {
            'share0', 'share1', 'share2',
            'share3', 'share4', 'share5',
            'share7', 'share8', 'share9',
        }
        peers = {
            'peer0', 'peer1', 'peer2', 'peer3',
            'peer4', 'peer5', 'peer6', 'peer7',
            'peer8', 'peer9', 'peerA', 'peerB',
        }
        readonly_peers = {'peer0', 'peer1', 'peer2', 'peer3'}
        peers_to_shares = {
            'peer0': {'share0'},
            'peer1': {'share1'},
            'peer2': {'share2'},
            'peer3': {'share3'},
            'peer4': {'share4'},
            'peer5': {'share5'},
            'peer6': {'share6'},
            'peer7': {'share7'},
            'peer8': {'share8'},
            'peer9': {'share9'},
            'peerA': set(),
            'peerB': set(),
        }

        places0 = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)

        # share N maps to peer N
        # i.e. this says that share0 should be on peer0, share1 should
        # be on peer1, etc.
        expected = {
            'share{}'.format(i): {'peer{}'.format(i)}
            for i in range(10)
        }
        self.assertEqual(expected, places0)
