# -*- coding: utf-8 -*-

from hypothesis import given
from hypothesis.strategies import text, sets
from allmydata.immutable import happiness_upload


@given(
    sets(elements=text(min_size=1), min_size=4, max_size=4),
    sets(elements=text(min_size=1), min_size=4),
)
def test_hypothesis_old_unhappy(peers, shares):
    """
    similar to test_unhappy we test that the resulting happiness is
    always 4 since the size of peers is 4.
    """
    # https://hypothesis.readthedocs.io/en/latest/data.html#hypothesis.strategies.sets
    # hypothesis.strategies.sets(elements=None, min_size=None, average_size=None, max_size=None)[source]
    readonly_peers = set()
    peers_to_shares = {}
    h = happiness_upload.HappinessUpload(peers, readonly_peers, shares, peers_to_shares)
    places = h.generate_mappings()
    assert set(places.keys()) == shares
    assert h.happiness() == 4


@given(
    sets(elements=text(min_size=1), min_size=1, max_size=10),
    # can we make a readonly_peers that's a subset of ^
    sets(elements=text(min_size=1), min_size=1, max_size=20),
)
def test_hypothesis_old_more_happiness(peers, shares):
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
    h = happiness_upload.HappinessUpload(peers, readonly_peers, shares, peers_to_shares)
    places = h.generate_mappings()
    happiness = h.happiness()

    # every share should get placed
    assert set(places.keys()) == shares

    # we should only use peers that exist
    assert set(map(lambda x: list(x)[0], places.values())).issubset(peers) # XXX correct?

    # if we have more shares than peers, happiness is at most # of
    # peers; if we have fewer shares than peers happiness is capped at
    # # of peers.
    assert happiness == min(len(peers), len(shares))
