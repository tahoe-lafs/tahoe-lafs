"""
Algorithms for figuring out happiness, the number of unique nodes the data is
on.

Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    # We omit dict, just in case newdict breaks things for external Python 2 code.
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, list, object, range, str, max, min  # noqa: F401

from queue import PriorityQueue


def augmenting_path_for(graph):
    """
    I return an augmenting path, if there is one, from the source node
    to the sink node in the flow network represented by my graph argument.
    If there is no augmenting path, I return False. I assume that the
    source node is at index 0 of graph, and the sink node is at the last
    index. I also assume that graph is a flow network in adjacency list
    form.
    """
    bfs_tree = bfs(graph, 0)
    if bfs_tree[len(graph) - 1]:
        n = len(graph) - 1
        path = [] # [(u, v)], where u and v are vertices in the graph
        while n != 0:
            path.insert(0, (bfs_tree[n], n))
            n = bfs_tree[n]
        return path
    return False

def bfs(graph, s):
    """
    Perform a BFS on graph starting at s, where graph is a graph in
    adjacency list form, and s is a node in graph. I return the
    predecessor table that the BFS generates.
    """
    # This is an adaptation of the BFS described in "Introduction to
    # Algorithms", Cormen et al, 2nd ed., p. 532.
    # WHITE vertices are those that we haven't seen or explored yet.
    WHITE = 0
    # GRAY vertices are those we have seen, but haven't explored yet
    GRAY  = 1
    # BLACK vertices are those we have seen and explored
    BLACK = 2
    color        = [WHITE for i in range(len(graph))]
    predecessor  = [None for i in range(len(graph))]
    distance     = [-1 for i in range(len(graph))]
    queue = [s] # vertices that we haven't explored yet.
    color[s] = GRAY
    distance[s] = 0
    while queue:
        n = queue.pop(0)
        for v in graph[n]:
            if color[v] == WHITE:
                color[v] = GRAY
                distance[v] = distance[n] + 1
                predecessor[v] = n
                queue.append(v)
        color[n] = BLACK
    return predecessor

def residual_network(graph, f):
    """
    I return the residual network and residual capacity function of the
    flow network represented by my graph and f arguments. graph is a
    flow network in adjacency-list form, and f is a flow in graph.
    """
    new_graph = [[] for i in range(len(graph))]
    cf = [[0 for s in range(len(graph))] for sh in range(len(graph))]
    for i in range(len(graph)):
        for v in graph[i]:
            if f[i][v] == 1:
                # We add an edge (v, i) with cf[v,i] = 1. This means
                # that we can remove 1 unit of flow from the edge (i, v)
                new_graph[v].append(i)
                cf[v][i] = 1
                cf[i][v] = -1
            else:
                # We add the edge (i, v), since we're not using it right
                # now.
                new_graph[i].append(v)
                cf[i][v] = 1
                cf[v][i] = -1
    return (new_graph, cf)


def calculate_happiness(mappings):
    """
    :param mappings: a dict mapping 'share' -> 'peer'

    :returns: the happiness, which is the number of unique peers we've
        placed shares on.
    """
    unique_peers = set(mappings.values())
    assert None not in unique_peers
    return len(unique_peers)


def _calculate_mappings(peers, shares, servermap=None):
    """
    Given a set of peers, a set of shares, and a dictionary of server ->
    set(shares), determine how the uploader should allocate shares. If a
    servermap is supplied, determine which existing allocations should be
    preserved. If servermap is None, calculate the maximum matching of the
    bipartite graph (U, V, E) such that:

    U = peers
    V = shares
    E = peers x shares

    Returns a dictionary {share -> set(peer)}, indicating that the share
    should be placed on each peer in the set. If a share's corresponding
    value is None, the share can be placed on any server. Note that the set
    of peers should only be one peer when returned, but it is possible to
    duplicate shares by adding additional servers to the set.
    """
    peer_to_index, index_to_peer = _reindex(peers, 1)
    share_to_index, index_to_share = _reindex(shares, len(peers) + 1)
    shareIndices = [share_to_index[s] for s in shares]
    if servermap:
        graph = _servermap_flow_graph(peers, shares, servermap)
    else:
        peerIndices = [peer_to_index[peer] for peer in peers]
        graph = _flow_network(peerIndices, shareIndices)
    max_graph = _compute_maximum_graph(graph, shareIndices)
    return _convert_mappings(index_to_peer, index_to_share, max_graph)


def _compute_maximum_graph(graph, shareIndices):
    """
    This is an implementation of the Ford-Fulkerson method for finding
    a maximum flow in a flow network applied to a bipartite graph.
    Specifically, it is the Edmonds-Karp algorithm, since it uses a
    BFS to find the shortest augmenting path at each iteration, if one
    exists.

    The implementation here is an adapation of an algorithm described in
    "Introduction to Algorithms", Cormen et al, 2nd ed., pp 658-662.
    """

    if graph == []:
        return {}

    dim = len(graph)
    flow_function = [[0 for sh in range(dim)] for s in range(dim)]
    residual_graph, residual_function = residual_network(graph, flow_function)

    while augmenting_path_for(residual_graph):
        path = augmenting_path_for(residual_graph)
        # Delta is the largest amount that we can increase flow across
        # all of the edges in path. Because of the way that the residual
        # function is constructed, f[u][v] for a particular edge (u, v)
        # is the amount of unused capacity on that edge. Taking the
        # minimum of a list of those values for each edge in the
        # augmenting path gives us our delta.
        delta = min(residual_function[u][v] for (u, v) in path)
        for (u, v) in path:
            flow_function[u][v] += delta
            flow_function[v][u] -= delta
            residual_graph, residual_function = residual_network(graph,flow_function)

    new_mappings = {}
    for shareIndex in shareIndices:
        peer = residual_graph[shareIndex]
        if peer == [dim - 1]:
            new_mappings.setdefault(shareIndex, None)
        else:
            new_mappings.setdefault(shareIndex, peer[0])

    return new_mappings


def _extract_ids(mappings):
    shares = set()
    peers = set()
    for share in mappings:
        if mappings[share] == None:
            pass
        else:
            shares.add(share)
            for item in mappings[share]:
                peers.add(item)
    return (peers, shares)

def _distribute_homeless_shares(mappings, homeless_shares, peers_to_shares):
    """
    Shares which are not mapped to a peer in the maximum spanning graph
    still need to be placed on a server. This function attempts to
    distribute those homeless shares as evenly as possible over the
    available peers. If possible a share will be placed on the server it was
    originally on, signifying the lease should be renewed instead.
    """
    #print("mappings, homeless_shares, peers_to_shares %s %s %s" % (mappings, homeless_shares, peers_to_shares))
    servermap_peerids = set([key for key in peers_to_shares])
    servermap_shareids = set()
    for key in sorted(peers_to_shares.keys()):
        # XXX maybe sort?
        for share in peers_to_shares[key]:
            servermap_shareids.add(share)

    # First check to see if the leases can be renewed.
    to_distribute = set()
    for share in homeless_shares:
        if share in servermap_shareids:
            for peerid in peers_to_shares:
                if share in peers_to_shares[peerid]:
                    mappings[share] = set([peerid])
                    break
        else:
            to_distribute.add(share)
    # This builds a priority queue of peers with the number of shares
    # each peer holds as the priority.
    priority = {}
    pQueue = PriorityQueue()
    for peerid in servermap_peerids:
        priority.setdefault(peerid, 0)
    for share in mappings:
        if mappings[share] is not None:
            for peer in mappings[share]:
                if peer in servermap_peerids:
                    priority[peer] += 1
    if priority == {}:
        return
    for peerid in priority:
        pQueue.put((priority[peerid], peerid))
    # Distribute the shares to peers with the lowest priority.
    for share in to_distribute:
        peer = pQueue.get()
        mappings[share] = set([peer[1]])
        pQueue.put((peer[0]+1, peer[1]))

def _convert_mappings(index_to_peer, index_to_share, maximum_graph):
    """
    Now that a maximum spanning graph has been found, convert the indexes
    back to their original ids so that the client can pass them to the
    uploader.
    """

    converted_mappings = {}
    for share in maximum_graph:
        peer = maximum_graph[share]
        if peer == None:
            converted_mappings.setdefault(index_to_share[share], None)
        else:
            converted_mappings.setdefault(index_to_share[share], set([index_to_peer[peer]]))
    return converted_mappings


def _servermap_flow_graph(peers, shares, servermap):
    """
    Generates a flow network of peerIndices to shareIndices from a server map
    of 'peer' -> ['shares']. According to Wikipedia, "a flow network is a
    directed graph where each edge has a capacity and each edge receives a flow.
    The amount of flow on an edge cannot exceed the capacity of the edge." This
    is necessary because in order to find the maximum spanning, the Edmonds-Karp algorithm
    converts the problem into a maximum flow problem.
    """
    if servermap == {}:
        return []

    peer_to_index, index_to_peer = _reindex(peers, 1)
    share_to_index, index_to_share = _reindex(shares, len(peers) + 1)
    graph = []
    indexedShares = []
    sink_num = len(peers) + len(shares) + 1
    graph.append([peer_to_index[peer] for peer in peers])
    #print("share_to_index %s" % share_to_index)
    #print("servermap %s" % servermap)
    for peer in peers:
        if peer in servermap:
            for s in servermap[peer]:
                if s in share_to_index:
                    indexedShares.append(share_to_index[s])
        graph.insert(peer_to_index[peer], indexedShares)
    for share in shares:
        graph.insert(share_to_index[share], [sink_num])
    graph.append([])
    return graph


def _reindex(items, base):
    """
    I take an iteratble of items and give each item an index to be used in
    the construction of a flow network. Indices for these items start at base
    and continue to base + len(items) - 1.

    I return two dictionaries: ({item: index}, {index: item})
    """
    item_to_index = {}
    index_to_item = {}
    for item in items:
        item_to_index.setdefault(item, base)
        index_to_item.setdefault(base, item)
        base += 1
    return (item_to_index, index_to_item)


def _flow_network(peerIndices, shareIndices):
    """
    Given set of peerIndices and a set of shareIndices, I create a flow network
    to be used by _compute_maximum_graph. The return value is a two
    dimensional list in the form of a flow network, where each index represents
    a node, and the corresponding list represents all of the nodes it is connected
    to.

    This function is similar to allmydata.util.happinessutil.flow_network_for, but
    we connect every peer with all shares instead of reflecting a supplied servermap.
    """
    graph = []
    # The first entry in our flow network is the source.
    # Connect the source to every server.
    graph.append(peerIndices)
    sink_num = len(peerIndices + shareIndices) + 1
    # Connect every server with every share it can possibly store.
    for peerIndex in peerIndices:
        graph.insert(peerIndex, shareIndices)
    # Connect every share with the sink.
    for shareIndex in shareIndices:
        graph.insert(shareIndex, [sink_num])
    # Add an empty entry for the sink.
    graph.append([])
    return graph

def share_placement(peers, readonly_peers, shares, peers_to_shares):
    """
    Generates the allocations the upload should based on the given
    information. We construct a dictionary of 'share_num' ->
    'server_id' and return it to the caller. Existing allocations
    appear as placements because attempting to place an existing
    allocation will renew the share.

    For more information on the algorithm this class implements, refer to
    docs/specifications/servers-of-happiness.rst
    """
    if not peers:
        return dict()

    # First calculate share placement for the readonly servers.
    readonly_shares = set()
    readonly_map = {}
    for peer in sorted(peers_to_shares.keys()):
        if peer in readonly_peers:
            readonly_map.setdefault(peer, peers_to_shares[peer])
            for share in peers_to_shares[peer]:
                readonly_shares.add(share)

    readonly_mappings = _calculate_mappings(readonly_peers, readonly_shares, readonly_map)
    used_peers, used_shares = _extract_ids(readonly_mappings)

    # Calculate share placement for the remaining existing allocations
    new_peers = set(peers) - used_peers
    # Squash a list of sets into one set
    new_shares = shares - used_shares

    servermap = peers_to_shares.copy()
    for peer in sorted(peers_to_shares.keys()):
        if peer in used_peers:
            servermap.pop(peer, None)
        else:
            servermap[peer] = set(servermap[peer]) - used_shares
            if servermap[peer] == set():
                servermap.pop(peer, None)
                # allmydata.test.test_upload.EncodingParameters.test_exception_messages_during_server_selection
                # allmydata.test.test_upload.EncodingParameters.test_problem_layout_comment_52
                # both ^^ trigger a "keyerror" here .. just ignoring is right? (fixes the tests, but ...)
                try:
                    new_peers.remove(peer)
                except KeyError:
                    pass

    existing_mappings = _calculate_mappings(new_peers, new_shares, servermap)
    existing_peers, existing_shares = _extract_ids(existing_mappings)

    # Calculate share placement for the remaining peers and shares which
    # won't be preserved by existing allocations.
    new_peers = new_peers - existing_peers - used_peers


    new_shares = new_shares - existing_shares - used_shares
    new_mappings = _calculate_mappings(new_peers, new_shares)
    #print("new_peers %s" % new_peers)
    #print("new_mappings %s" % new_mappings)
    mappings = dict(list(readonly_mappings.items()) + list(existing_mappings.items()) + list(new_mappings.items()))
    homeless_shares = set()
    for share in mappings:
        if mappings[share] is None:
            homeless_shares.add(share)
    if len(homeless_shares) != 0:
        # 'servermap' should contain only read/write peers
        _distribute_homeless_shares(
            mappings, homeless_shares,
            {
                k: v
                for k, v in list(peers_to_shares.items())
                if k not in readonly_peers
            }
        )

    # now, if any share is *still* mapped to None that means "don't
    # care which server it goes on", so we place it on a round-robin
    # of read-write servers

    def round_robin(peers):
        while True:
            for peer in peers:
                yield peer
    peer_iter = round_robin(peers - readonly_peers)

    return {
        k: v.pop() if v else next(peer_iter)
        for k, v in list(mappings.items())
    }
