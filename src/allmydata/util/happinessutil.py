"""
I contain utilities useful for calculating servers_of_happiness, and for
reporting it in messages
"""

from copy import deepcopy

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
    """
    I accept 'sharemap', a dict of shareid -> set(peerid) mappings. I
    return the 'servers_of_happiness' number that sharemap results in.

    To calculate the 'servers_of_happiness' number for the sharemap, I
    construct a bipartite graph with servers in one partition of vertices
    and shares in the other, and with an edge between a server s and a share t
    if s is to store t. I then compute the size of a maximum matching in
    the resulting graph; this is then returned as the 'servers_of_happiness'
    for my arguments.

    For example, consider the following layout:

      server 1: shares 1, 2, 3, 4
      server 2: share 6
      server 3: share 3
      server 4: share 4
      server 5: share 2

    From this, we can construct the following graph:

      L = {server 1, server 2, server 3, server 4, server 5}
      R = {share 1, share 2, share 3, share 4, share 6}
      V = L U R
      E = {(server 1, share 1), (server 1, share 2), (server 1, share 3),
           (server 1, share 4), (server 2, share 6), (server 3, share 3),
           (server 4, share 4), (server 5, share 2)}
      G = (V, E)

    Note that G is bipartite since every edge in e has one endpoint in L
    and one endpoint in R.

    A matching in a graph G is a subset M of E such that, for any vertex
    v in V, v is incident to at most one edge of M. A maximum matching
    in G is a matching that is no smaller than any other matching. For
    this graph, a matching of cardinality 5 is:

      M = {(server 1, share 1), (server 2, share 6),
           (server 3, share 3), (server 4, share 4),
           (server 5, share 2)}

    Since G is bipartite, and since |L| = 5, we cannot have an M' such
    that |M'| > |M|. Then M is a maximum matching in G. Intuitively, and
    as long as k <= 5, we can see that the layout above has
    servers_of_happiness = 5, which matches the results here.
    """
    if sharemap == {}:
        return 0
    sharemap = shares_by_server(sharemap)
    graph = flow_network_for(sharemap)
    # This is an implementation of the Ford-Fulkerson method for finding
    # a maximum flow in a flow network applied to a bipartite graph.
    # Specifically, it is the Edmonds-Karp algorithm, since it uses a
    # BFS to find the shortest augmenting path at each iteration, if one
    # exists.
    #
    # The implementation here is an adapation of an algorithm described in
    # "Introduction to Algorithms", Cormen et al, 2nd ed., pp 658-662.
    dim = len(graph)
    flow_function = [[0 for sh in xrange(dim)] for s in xrange(dim)]
    residual_graph, residual_function = residual_network(graph, flow_function)
    while augmenting_path_for(residual_graph):
        path = augmenting_path_for(residual_graph)
        # Delta is the largest amount that we can increase flow across
        # all of the edges in path. Because of the way that the residual
        # function is constructed, f[u][v] for a particular edge (u, v)
        # is the amount of unused capacity on that edge. Taking the
        # minimum of a list of those values for each edge in the
        # augmenting path gives us our delta.
        delta = min(map(lambda (u, v), rf=residual_function: rf[u][v],
                        path))
        for (u, v) in path:
            flow_function[u][v] += delta
            flow_function[v][u] -= delta
        residual_graph, residual_function = residual_network(graph,
                                                             flow_function)
    num_servers = len(sharemap)
    # The value of a flow is the total flow out of the source vertex
    # (vertex 0, in our graph). We could just as well sum across all of
    # f[0], but we know that vertex 0 only has edges to the servers in
    # our graph, so we can stop after summing flow across those. The
    # value of a flow computed in this way is the size of a maximum
    # matching on the bipartite graph described above.
    return sum([flow_function[0][v] for v in xrange(1, num_servers+1)])

def flow_network_for(sharemap):
    """
    I take my argument, a dict of peerid -> set(shareid) mappings, and
    turn it into a flow network suitable for use with Edmonds-Karp. I
    then return the adjacency list representation of that network.

    Specifically, I build G = (V, E), where:
      V = { peerid in sharemap } U { shareid in sharemap } U {s, t}
      E = {(s, peerid) for each peerid}
          U {(peerid, shareid) if peerid is to store shareid }
          U {(shareid, t) for each shareid}

    s and t will be source and sink nodes when my caller starts treating
    the graph I return like a flow network. Without s and t, the
    returned graph is bipartite.
    """
    # Servers don't have integral identifiers, and we can't make any
    # assumptions about the way shares are indexed -- it's possible that
    # there are missing shares, for example. So before making a graph,
    # we re-index so that all of our vertices have integral indices, and
    # that there aren't any holes. We start indexing at 1, so that we
    # can add a source node at index 0.
    sharemap, num_shares = reindex(sharemap, base_index=1)
    num_servers = len(sharemap)
    graph = [] # index -> [index], an adjacency list
    # Add an entry at the top (index 0) that has an edge to every server
    # in sharemap
    graph.append(sharemap.keys())
    # For each server, add an entry that has an edge to every share that it
    # contains (or will contain).
    for k in sharemap:
        graph.append(sharemap[k])
    # For each share, add an entry that has an edge to the sink.
    sink_num = num_servers + num_shares + 1
    for i in xrange(num_shares):
        graph.append([sink_num])
    # Add an empty entry for the sink, which has no outbound edges.
    graph.append([])
    return graph

def reindex(sharemap, base_index):
    """
    Given sharemap, I map peerids and shareids to integers that don't
    conflict with each other, so they're useful as indices in a graph. I
    return a sharemap that is reindexed appropriately, and also the
    number of distinct shares in the resulting sharemap as a convenience
    for my caller. base_index tells me where to start indexing.
    """
    shares  = {} # shareid  -> vertex index
    num = base_index
    ret = {} # peerid -> [shareid], a reindexed sharemap.
    # Number the servers first
    for k in sharemap:
        ret[num] = sharemap[k]
        num += 1
    # Number the shares
    for k in ret:
        for shnum in ret[k]:
            if not shares.has_key(shnum):
                shares[shnum] = num
                num += 1
        ret[k] = map(lambda x: shares[x], ret[k])
    return (ret, len(shares))

def residual_network(graph, f):
    """
    I return the residual network and residual capacity function of the
    flow network represented by my graph and f arguments. graph is a
    flow network in adjacency-list form, and f is a flow in graph.
    """
    new_graph = [[] for i in xrange(len(graph))]
    cf = [[0 for s in xrange(len(graph))] for sh in xrange(len(graph))]
    for i in xrange(len(graph)):
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
    color        = [WHITE for i in xrange(len(graph))]
    predecessor  = [None for i in xrange(len(graph))]
    distance     = [-1 for i in xrange(len(graph))]
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
