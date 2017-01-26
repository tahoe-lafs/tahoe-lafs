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
        peers = {'peer0', 'peer1'}
        readonly_peers = {'peer0'}
        peers_to_shares = {
            'peer0': {'share2'},
            'peer1': [],
        }

        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)

        if False:
            for k, v in places.items():
                print("  {} -> {}".format(k, v))

        self.assertEqual(
            places,
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

        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)

        # share N maps to peer N
        # i.e. this says that share0 should be on peer0, share1 should
        # be on peer1, etc.
        expected = {
            'share{}'.format(i): {'peer{}'.format(i)}
            for i in range(10)
        }
        self.assertEqual(expected, places)

    def test_unhappy(self):
        shares = {
            'share1', 'share2', 'share3', 'share4', 'share5',
        }
        peers = {
            'peer1', 'peer2', 'peer3', 'peer4',
        }
        readonly_peers = set()
        peers_to_shares = {}
        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)
        self.assertEqual(4, happiness)

    def test_calc_happy(self):
        sharemap = {
            0: set(["\x0e\xd6\xb3>\xd6\x85\x9d\x94')'\xf03:R\x88\xf1\x04\x1b\xa4",
                    '\x8de\x1cqM\xba\xc3\x0b\x80\x9aC<5\xfc$\xdc\xd5\xd3\x8b&',
                    '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
                    '\xc4\x83\x9eJ\x7f\xac| .\xc90\xf4b\xe4\x92\xbe\xaa\xe6\t\x80']),
            1: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
            2: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
            3: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
            4: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
            5: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
            6: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
            7: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
            8: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
            9: set(['\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t']),
        }
        happy = happiness_upload.calculate_happiness(sharemap)
        self.assertEqual(2, happy)
