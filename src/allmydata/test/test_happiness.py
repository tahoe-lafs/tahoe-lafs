# -*- coding: utf-8 -*-

from twisted.trial import unittest
from hypothesis import given
from hypothesis.strategies import text, sets
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

    def test_trivial_maximum_graph(self):
        self.assertEqual(
            {},
            happiness_upload._compute_maximum_graph([], {})
        )

    def test_trivial_flow_graph(self):
        self.assertEqual(
            [],
            happiness_upload._servermap_flow_graph(set(), set(), {})
        )


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
            'share6', 'share7', 'share8',
            'share9',
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

        # actually many valid answers for this, so long as peer's 0,
        # 1, 2, 3 all have share 0, 1, 2 3.

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

        #h = happiness_upload.HappinessUpload(peers, readonly_peers, shares, peers_to_shares)
        #places = h.generate_mappings()
        #happiness = h.happiness()

        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)

        self.assertEqual(2, happiness)

    def test_100(self):
        peers = set(['peer{}'.format(x) for x in range(100)])
        shares = set(['share{}'.format(x) for x in range(100)])
        readonly_peers = set()
        peers_to_shares = dict()

        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)

        self.assertEqual(100, happiness)

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
        #print "places %s" % places
        #places = happiness_upload.slow_share_placement(peers, readonly_peers, shares, peers_to_shares)
        #print "places %s" % places

        happiness = happiness_upload.calculate_happiness(places)
        self.assertEqual(4, happiness)

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

    def test_hypothesis_0(self):
        """
        an error-case Hypothesis found
        """
        peers={u'0'}
        shares={u'0', u'1'}

        places = happiness_upload.share_placement(peers, set(), shares, {})
        happiness = happiness_upload.calculate_happiness(places)

        assert set(places.values()).issubset(peers)
        assert happiness == min(len(peers), len(shares))

    def test_hypothesis_1(self):
        """
        an error-case Hypothesis found
        """
        peers = {u'0', u'1', u'2', u'3'}
        shares = {u'0', u'1', u'2', u'3', u'4', u'5', u'6', u'7', u'8'}

        places = happiness_upload.share_placement(peers, set(), shares, {})
        happiness = happiness_upload.calculate_happiness(places)

        assert set(places.values()).issubset(peers)
        assert happiness == min(len(peers), len(shares))

    def test_everything_broken(self):
        peers = set()
        shares = {u'0', u'1', u'2', u'3'}

        places = happiness_upload.share_placement(peers, set(), shares, {})
        self.assertEqual(places, dict())


class PlacementTests(unittest.TestCase):

    @given(
        sets(elements=text(min_size=1, max_size=30), min_size=4, max_size=4),
        sets(elements=text(min_size=1, max_size=30), min_size=4),
    )
    def test_hypothesis_unhappy(self, peers, shares):
        """
        similar to test_unhappy we test that the resulting happiness is
        always 4 since the size of peers is 4.
        """
        # https://hypothesis.readthedocs.io/en/latest/data.html#hypothesis.strategies.sets
        # hypothesis.strategies.sets(elements=None, min_size=None, average_size=None, max_size=None)[source]
        readonly_peers = set()
        peers_to_shares = {}
        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)
        assert set(places.keys()) == shares
        assert happiness == 4

    @given(
        sets(elements=text(min_size=1, max_size=30), min_size=1, max_size=10),
        # can we make a readonly_peers that's a subset of           ^
        sets(elements=text(min_size=1, max_size=30), min_size=1, max_size=20),
    )
    def test_more_hypothesis(self, peers, shares):
        """
        similar to test_unhappy we test that the resulting happiness is
        always either the number of peers or the number of shares
        whichever is smaller.
        """
        # https://hypothesis.readthedocs.io/en/latest/data.html#hypothesis.strategies.sets
        # hypothesis.strategies.sets(elements=None, min_size=None, average_size=None, max_size=None)[source]
        # XXX would be nice to paramaterize these by hypothesis too
        readonly_peers = set()
        peers_to_shares = {}

        places = happiness_upload.share_placement(peers, readonly_peers, set(list(shares)), peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)

        # every share should get placed
        assert set(places.keys()) == shares

        # we should only use peers that exist
        assert set(places.values()).issubset(peers)

        # if we have more shares than peers, happiness is at most # of
        # peers; if we have fewer shares than peers happiness is capped at
        # # of peers.
        assert happiness == min(len(peers), len(shares))
