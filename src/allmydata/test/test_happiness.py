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
                'share0': 'peer1',
                'share1': 'peer1',
                'share2': 'peer0',
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
            'share{}'.format(i): 'peer{}'.format(i)
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

    def test_hypothesis0(self):
        peers={u'0', u'00'}
        shares={u'0', u'1'}
        readonly_peers = set()
        peers_to_shares = dict()

        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)

        self.assertEqual(2, happiness)

    # process just gets killed with anything like 200 (see
    # test_upload.py)
    def test_50(self):
        peers = set(['peer{}'.format(x) for x in range(50)])
        shares = set(['share{}'.format(x) for x in range(50)])
        readonly_peers = set()
        peers_to_shares = dict()

        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)

        self.assertEqual(50, happiness)

    def test_50_orig_code(self):
        peers = set(['peer{}'.format(x) for x in range(50)])
        shares = set(['share{}'.format(x) for x in range(50)])
        readonly_peers = set()
        peers_to_shares = dict()

        h = happiness_upload.Happiness_Upload(peers, readonly_peers, shares, peers_to_shares)
        places = h.generate_mappings()

        self.assertEqual(50, h.happy)
        self.assertEqual(50, len(places))
        for share in shares:
            self.assertTrue(share in places)
            self.assertTrue(places[share].pop() in peers)

    def test_redistribute(self):
        """
        with existing shares 0, 3 on a single servers we can achieve
        higher happiness by moving one of those shares to a new server
        """
        peers = {'a', 'b', 'c', 'd'}
        shares = {'0', '1', '2', '3'}
        readonly_peers = set()
        peers_to_shares = {
            'a': set(['0']),
            'b': set(['1']),
            'c': set(['2', '3']),
        }
        # we can achieve more happiness by moving "2" or "3" to server "d"

        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)
        self.assertEqual(4, happiness)

    def test_redistribute2(self):
        """
        with existing shares 0, 3 on a single servers we can achieve
        higher happiness by moving one of those shares to a new server
        """
        peers = {'a', 'b', 'c', 'd'}
        shares = {'0', '1', '2', '3'}
        readonly_peers = set()
        peers_to_shares = {
            'a': set(['0']),
            'b': set(['1']),
            'c': set(['2', '3']),
        }
        # we can achieve more happiness by moving "2" or "3" to server "d"

        h = happiness_upload.Happiness_Upload(peers, readonly_peers, shares, peers_to_shares)
        places = h.generate_mappings()
        self.assertEqual(4, h.happy)
        print(places)

    def test_calc_happy(self):
        # share -> server
        share_placements = {
            0: "\x0e\xd6\xb3>\xd6\x85\x9d\x94')'\xf03:R\x88\xf1\x04\x1b\xa4",
            1: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
            2: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
            3: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
            4: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
            5: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
            6: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
            7: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
            8: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
            9: '\xb9\xa3N\x80u\x9c_\xf7\x97FSS\xa7\xbd\x02\xf9f$:\t',
        }
        happy = happiness_upload.calculate_happiness(share_placements)
        self.assertEqual(2, happy)

    def test_bar(self):
        peers = {'peer0', 'peer1', 'peer2', 'peer3'}
        shares = {'share0', 'share1', 'share2'}
        readonly_peers = {'peer0'}
        servermap = {
            'peer0': {'share2', 'share0'},
            'peer1': {'share1'},
        }
        h = happiness_upload.Happiness_Upload(peers, readonly_peers, shares, servermap)
        maps = h.generate_mappings()
        print("maps:")
        for k in sorted(maps.keys()):
            print("{} -> {}".format(k, maps[k]))

    def test_foo(self):
        peers = ['peer0', 'peer1']
        shares = ['share0', 'share1', 'share2']
        h = happiness_upload.Happiness_Upload(peers, [], shares, {})

        # servermap must have all peers -> [share, share, share, ...]
        graph = h._servermap_flow_graph(
            peers,
            shares,
            {
                'peer0': ['share0', 'share1', 'share2'],
                'peer1': ['share1'],
            },
        )
        peer_to_index = h._index_peers(peers, 1)
        share_to_index, index_to_share = h._reindex_shares(shares, len(peers) + 1)

        print("graph:")
        for row in graph:
            print(row)
        shareids = [3, 4, 5]
        max_server_graph = h._compute_maximum_graph(graph, shareids)
        print("max_server_graph:", max_server_graph)
        for k, v in max_server_graph.items():
            print("{} -> {}".format(k, v))

        mappings = h._convert_mappings(peer_to_index, index_to_share, max_server_graph)
        print("mappings:", mappings)
        used_peers, used_shares = h._extract_ids(mappings)
        print("existing used peers", used_peers)
        print("existing used shares", used_shares)

        unused_peers = peers - used_peers
        unused_shares = shares - used_shares

