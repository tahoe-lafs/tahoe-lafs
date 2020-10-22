# -*- coding: utf-8 -*-
"""
Tests for allmydata.immutable.happiness_upload and
allmydata.util.happinessutil.

Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    # We omit dict, just in case newdict breaks things.
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, list, object, range, str, max, min  # noqa: F401

from twisted.trial import unittest
from hypothesis import given
from hypothesis.strategies import text, sets

from allmydata.immutable import happiness_upload
from allmydata.util.happinessutil import servers_of_happiness, \
    shares_by_server, merge_servers
from allmydata.test.common import ShouldFailMixin


class HappinessUploadUtils(unittest.TestCase):
    """
    test-cases for happiness_upload utility functions augmenting_path_for and
    residual_network.
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
        #print("places %s" % places)
        #places = happiness_upload.slow_share_placement(peers, readonly_peers, shares, peers_to_shares)
        #print("places %s" % places)

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


class FakeServerTracker(object):
    def __init__(self, serverid, buckets):
        self._serverid = serverid
        self.buckets = buckets
    def get_serverid(self):
        return self._serverid


class HappinessUtilTests(unittest.TestCase, ShouldFailMixin):
    """Tests for happinesutil.py."""

    def test_merge_servers(self):
        # merge_servers merges a list of upload_servers and a dict of
        # shareid -> serverid mappings.
        shares = {
                    1 : set(["server1"]),
                    2 : set(["server2"]),
                    3 : set(["server3"]),
                    4 : set(["server4", "server5"]),
                    5 : set(["server1", "server2"]),
                 }
        # if not provided with a upload_servers argument, it should just
        # return the first argument unchanged.
        self.failUnlessEqual(shares, merge_servers(shares, set([])))
        trackers = []
        for (i, server) in [(i, "server%d" % i) for i in range(5, 9)]:
            t = FakeServerTracker(server, [i])
            trackers.append(t)
        expected = {
                    1 : set(["server1"]),
                    2 : set(["server2"]),
                    3 : set(["server3"]),
                    4 : set(["server4", "server5"]),
                    5 : set(["server1", "server2", "server5"]),
                    6 : set(["server6"]),
                    7 : set(["server7"]),
                    8 : set(["server8"]),
                   }
        self.failUnlessEqual(expected, merge_servers(shares, set(trackers)))
        shares2 = {}
        expected = {
                    5 : set(["server5"]),
                    6 : set(["server6"]),
                    7 : set(["server7"]),
                    8 : set(["server8"]),
                   }
        self.failUnlessEqual(expected, merge_servers(shares2, set(trackers)))
        shares3 = {}
        trackers = []
        expected = {}
        for (i, server) in [(i, "server%d" % i) for i in range(10)]:
            shares3[i] = set([server])
            t = FakeServerTracker(server, [i])
            trackers.append(t)
            expected[i] = set([server])
        self.failUnlessEqual(expected, merge_servers(shares3, set(trackers)))


    def test_servers_of_happiness_utility_function(self):
        # These tests are concerned with the servers_of_happiness()
        # utility function, and its underlying matching algorithm. Other
        # aspects of the servers_of_happiness behavior are tested
        # elsehwere These tests exist to ensure that
        # servers_of_happiness doesn't under or overcount the happiness
        # value for given inputs.

        # servers_of_happiness expects a dict of
        # shnum => set(serverids) as a preexisting shares argument.
        test1 = {
                 1 : set(["server1"]),
                 2 : set(["server2"]),
                 3 : set(["server3"]),
                 4 : set(["server4"])
                }
        happy = servers_of_happiness(test1)
        self.failUnlessEqual(4, happy)
        test1[4] = set(["server1"])
        # We've added a duplicate server, so now servers_of_happiness
        # should be 3 instead of 4.
        happy = servers_of_happiness(test1)
        self.failUnlessEqual(3, happy)
        # The second argument of merge_servers should be a set of objects with
        # serverid and buckets as attributes. In actual use, these will be
        # ServerTracker instances, but for testing it is fine to make a
        # FakeServerTracker whose job is to hold those instance variables to
        # test that part.
        trackers = []
        for (i, server) in [(i, "server%d" % i) for i in range(5, 9)]:
            t = FakeServerTracker(server, [i])
            trackers.append(t)
        # Recall that test1 is a server layout with servers_of_happiness
        # = 3.  Since there isn't any overlap between the shnum ->
        # set([serverid]) correspondences in test1 and those in trackers,
        # the result here should be 7.
        test2 = merge_servers(test1, set(trackers))
        happy = servers_of_happiness(test2)
        self.failUnlessEqual(7, happy)
        # Now add an overlapping server to trackers. This is redundant,
        # so it should not cause the previously reported happiness value
        # to change.
        t = FakeServerTracker("server1", [1])
        trackers.append(t)
        test2 = merge_servers(test1, set(trackers))
        happy = servers_of_happiness(test2)
        self.failUnlessEqual(7, happy)
        test = {}
        happy = servers_of_happiness(test)
        self.failUnlessEqual(0, happy)
        # Test a more substantial overlap between the trackers and the
        # existing assignments.
        test = {
            1 : set(['server1']),
            2 : set(['server2']),
            3 : set(['server3']),
            4 : set(['server4']),
        }
        trackers = []
        t = FakeServerTracker('server5', [4])
        trackers.append(t)
        t = FakeServerTracker('server6', [3, 5])
        trackers.append(t)
        # The value returned by servers_of_happiness is the size
        # of a maximum matching in the bipartite graph that
        # servers_of_happiness() makes between serverids and share
        # numbers. It should find something like this:
        # (server 1, share 1)
        # (server 2, share 2)
        # (server 3, share 3)
        # (server 5, share 4)
        # (server 6, share 5)
        #
        # and, since there are 5 edges in this matching, it should
        # return 5.
        test2 = merge_servers(test, set(trackers))
        happy = servers_of_happiness(test2)
        self.failUnlessEqual(5, happy)
        # Zooko's first puzzle:
        # (from http://allmydata.org/trac/tahoe-lafs/ticket/778#comment:156)
        #
        # server 1: shares 0, 1
        # server 2: shares 1, 2
        # server 3: share 2
        #
        # This should yield happiness of 3.
        test = {
            0 : set(['server1']),
            1 : set(['server1', 'server2']),
            2 : set(['server2', 'server3']),
        }
        self.failUnlessEqual(3, servers_of_happiness(test))
        # Zooko's second puzzle:
        # (from http://allmydata.org/trac/tahoe-lafs/ticket/778#comment:158)
        #
        # server 1: shares 0, 1
        # server 2: share 1
        #
        # This should yield happiness of 2.
        test = {
            0 : set(['server1']),
            1 : set(['server1', 'server2']),
        }
        self.failUnlessEqual(2, servers_of_happiness(test))


    def test_shares_by_server(self):
        test = dict([(i, set(["server%d" % i])) for i in range(1, 5)])
        sbs = shares_by_server(test)
        self.failUnlessEqual(set([1]), sbs["server1"])
        self.failUnlessEqual(set([2]), sbs["server2"])
        self.failUnlessEqual(set([3]), sbs["server3"])
        self.failUnlessEqual(set([4]), sbs["server4"])
        test1 = {
                    1 : set(["server1"]),
                    2 : set(["server1"]),
                    3 : set(["server1"]),
                    4 : set(["server2"]),
                    5 : set(["server2"])
                }
        sbs = shares_by_server(test1)
        self.failUnlessEqual(set([1, 2, 3]), sbs["server1"])
        self.failUnlessEqual(set([4, 5]), sbs["server2"])
        # This should fail unless the serverid part of the mapping is a set
        test2 = {1: "server1"}
        self.shouldFail(AssertionError,
                       "test_shares_by_server",
                       "",
                       shares_by_server, test2)
