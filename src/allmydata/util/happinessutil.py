"""
I contain utilities useful for calculating servers_of_happiness, and for
reporting it in messages
"""

from copy import deepcopy
from allmydata.immutable.happiness_upload import share_placement, calculate_happiness

def failure_message(peer_count, k, happy, effective_happy):
    # If peer_count < needed_shares, this error message makes more
    # sense than any of the others, so use it.
    if peer_count < k:
        msg = ("shares could be placed or found on only %d "
               "server(s). "
               "We were asked to place shares on at least %d "
               "server(s) such that any %d of them have "
               "enough shares to recover the file." %
                (peer_count, happy, k))
    # Otherwise, if we've placed on at least needed_shares
    # peers, but there isn't an x-happy subset of those peers
    # for x >= needed_shares, we use this error message.
    elif effective_happy < k:
        msg = ("shares could be placed or found on %d "
               "server(s), but they are not spread out evenly "
               "enough to ensure that any %d of these servers "
               "would have enough shares to recover the file. "
               "We were asked to place "
               "shares on at least %d servers such that any "
               "%d of them have enough shares to recover the "
               "file." %
                (peer_count, k, happy, k))
    # Otherwise, if there is an x-happy subset of peers where
    # x >= needed_shares, but x < servers_of_happiness, then
    # we use this message.
    else:
        msg = ("shares could be placed on only %d server(s) "
               "such that any %d of them have enough shares "
               "to recover the file, but we were asked to "
               "place shares on at least %d such servers." %
                (effective_happy, k, happy))
    return msg


def shares_by_server(servermap):
    """
    I accept a dict of shareid -> set(peerid) mappings, and return a
    dict of peerid -> set(shareid) mappings. My argument is a dictionary
    with sets of peers, indexed by shares, and I transform that into a
    dictionary of sets of shares, indexed by peerids.
    """
    ret = {}
    for shareid, peers in servermap.iteritems():
        assert isinstance(peers, set)
        for peerid in peers:
            ret.setdefault(peerid, set()).add(shareid)
    return ret

def merge_servers(servermap, upload_trackers=None):
    """
    I accept a dict of shareid -> set(serverid) mappings, and optionally a
    set of ServerTrackers. If no set of ServerTrackers is provided, I return
    my first argument unmodified. Otherwise, I update a copy of my first
    argument to include the shareid -> serverid mappings implied in the
    set of ServerTrackers, returning the resulting dict.
    """
    # Since we mutate servermap, and are called outside of a
    # context where it is okay to do that, make a copy of servermap and
    # work with it.
    servermap = deepcopy(servermap)
    if not upload_trackers:
        return servermap

    assert(isinstance(servermap, dict))
    assert(isinstance(upload_trackers, set))

    for tracker in upload_trackers:
        for shnum in tracker.buckets:
            servermap.setdefault(shnum, set()).add(tracker.get_serverid())
    return servermap

def servers_of_happiness(sharemap):
    peers = sharemap.values()
    if len(peers) == 1:
        peers = peers[0]
    else:
        peers = [list(x)[0] for x in peers] # XXX
    shares = sharemap.keys()
    readonly_peers = set() # XXX
    peers_to_shares = shares_by_server(sharemap)
    places0 = share_placement(peers, readonly_peers, shares, peers_to_shares)
    return calculate_happiness(places0)
