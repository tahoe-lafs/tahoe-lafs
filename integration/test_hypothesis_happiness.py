# -*- coding: utf-8 -*-

from twisted.trial import unittest
from hypothesis import given
from hypothesis.strategies import text, sets
from allmydata.immutable import happiness_upload


class Happiness(unittest.TestCase):

    @given(sets(elements=text(min_size=1), min_size=4, max_size=4), sets(elements=text(min_size=1), min_size=4))
    def test_hypothesis_unhappy(self, peers, shares):
        """
        similar to test_unhappy we test that the resulting happiness is always 4 since the size of peers is 4.
        """
        # https://hypothesis.readthedocs.io/en/latest/data.html#hypothesis.strategies.sets
        # hypothesis.strategies.sets(elements=None, min_size=None, average_size=None, max_size=None)[source]
        readonly_peers = set()
        peers_to_shares = {}
        places = happiness_upload.share_placement(peers, readonly_peers, shares, peers_to_shares)
        happiness = happiness_upload.calculate_happiness(places)
        self.assertEqual(4, happiness)
