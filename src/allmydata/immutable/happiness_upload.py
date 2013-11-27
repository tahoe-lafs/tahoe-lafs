from Queue import PriorityQueue
from allmydata.util.happinessutil import augmenting_path_for, residual_network

class HappinessUpload:
    """
    I handle the calculations involved with generating the maximum
    spanning graph for a file when given a set of peers, a set of shares,
    and a servermap of 'peer' -> [shares].

    peers is a set of serverids for servers that we think will accept shares.
    readonly_peers is a set of serverids for servers we know won't accept shares.
    shares is a set of share numbers, each representing a share that we want to place.

    For more information on the algorithm this class implements, refer to
    docs/specifications/servers-of-happiness.rst
    """

    def __init__(self, peers, readonly_peers, shares, servermap={}):
        self._happiness = 0
        self.homeless_shares = set()
        self.peers = peers
        self.readonly_peers = readonly_peers
        self.shares = shares
        self.servermap = servermap

    def happiness(self):
        return self._happiness


    def generate_upload_plan(self):
        """
        I calculate which shares the client should upload to which servers based on
        the information I was given in my constructor. I construct a dictionary of
        share_num -> set(server_ids) and return it. Each share should be placed on each
        server in the corresponding set. I include, in my return value, a mapping from
        share_num to server_id when that share is already on that server (according to
        the given information), because you should renew the lease on that share.
        """

        # First calculate share placement for the readonly servers.
        readonly_peers = self.readonly_peers
        readonly_shares = set()
        readonly_map = {}
        for peer in self.servermap:
            if peer in self.readonly_peers:
                readonly_map.setdefault(peer, self.servermap[peer])
                for share in self.servermap[peer]:
                    readonly_shares.add(share)

        readonly_mappings = self._calculate_mappings(readonly_peers, readonly_shares, readonly_map)
        used_peers, used_shares = self._extract_ids(readonly_mappings)

        # Calculate share placement for the remaining existing allocations
        peers = set(self.servermap.keys()) - used_peers
        # Squash a list of sets into one set
        shares = set(item for subset in self.servermap.values() for item in subset)
        shares -= used_shares
        servermap = self.servermap.copy()
        for peer in self.servermap:
            if peer in used_peers:
                servermap.pop(peer, None)
            else:
                servermap[peer] = servermap[peer] - used_shares
                if servermap[peer] == set():
                    servermap.pop(peer, None)
                    peers.remove(peer)

        existing_mappings = self._calculate_mappings(peers, shares, servermap)
        existing_peers, existing_shares = self._extract_ids(existing_mappings)

        # Calculate share placement for the remaining peers and shares which
        # won't be preserved by existing allocations.
        peers = self.peers - existing_peers - used_peers
        shares = self.shares - existing_shares - used_shares
        new_mappings = self._calculate_mappings(peers, shares)

        mappings = dict(readonly_mappings.items() + existing_mappings.items() + new_mappings.items())
        self._calculate_happiness(mappings)
        if len(self.homeless_shares) != 0:
            all_shares = set(item for subset in self.servermap.values() for item in subset)
            self._distribute_homeless_shares(mappings, all_shares)

        for share in mappings:
            if mappings[share] is None:
                mappings[share] = set()

        return mappings


    def _calculate_mappings(self, peers, shares, servermap=None):
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
        peer_to_index, index_to_peer = self._reindex(peers, 1)
        share_to_index, index_to_share = self._reindex(shares, len(peers) + 1)
        shareIndices = [share_to_index[s] for s in shares]
        if servermap:
            graph = self._servermap_flow_graph(peers, shares, servermap)
        else:
            peerIndices = [peer_to_index[peer] for peer in peers]
            graph = self._flow_network(peerIndices, shareIndices)
        max_graph = self._compute_maximum_graph(graph, shareIndices)
        return self._convert_mappings(index_to_peer, index_to_share, max_graph)


    def _compute_maximum_graph(self, graph, shareIndices):
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
            residual_graph, residual_function = residual_network(graph,flow_function)

        new_mappings = {}
        for shareIndex in shareIndices:
            peer = residual_graph[shareIndex]
            if peer == [dim - 1]:
                new_mappings.setdefault(shareIndex, None)
            else:
                new_mappings.setdefault(shareIndex, peer[0])

        return new_mappings


    def _extract_ids(self, mappings):
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


    def _calculate_happiness(self, mappings):
        """
        I calculate the happiness of the generated mappings and
        create the set self.homeless_shares.
        """
        self._happiness = 0
        self.homeless_shares = set()
        for share in mappings:
            if mappings[share] is not None:
                self._happiness += 1
            else:
                self.homeless_shares.add(share)


    def _distribute_homeless_shares(self, mappings, shares):
        """
        Shares which are not mapped to a peer in the maximum spanning graph
        still need to be placed on a server. This function attempts to
        distribute those homeless shares as evenly as possible over the
        available peers. If possible a share will be placed on the server it was
        originally on, signifying the lease should be renewed instead.
        """

        # First check to see if the leases can be renewed.
        to_distribute = set()

        for share in self.homeless_shares:
            if share in shares:
                for peer in self.servermap:
                    if share in self.servermap[peer]:
                        mappings[share] = set([peer])
                        break
            else:
                to_distribute.add(share)

        # This builds a priority queue of peers with the number of shares
        # each peer holds as the priority.

        priority = {}
        pQueue = PriorityQueue()
        for peer in self.peers:
            priority.setdefault(peer, 0)
        for share in mappings:
            if mappings[share] is not None:
                for peer in mappings[share]:
                    if peer in self.peers:
                        priority[peer] += 1

        if priority == {}:
            return

        for peer in priority:
            pQueue.put((priority[peer], peer))

        # Distribute the shares to peers with the lowest priority.
        for share in to_distribute:
            peer = pQueue.get()
            mappings[share] = set([peer[1]])
            pQueue.put((peer[0]+1, peer[1]))


    def _convert_mappings(self, index_to_peer, index_to_share, maximum_graph):
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


    def _servermap_flow_graph(self, peers, shares, servermap):
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

        peer_to_index, index_to_peer = self._reindex(peers, 1)
        share_to_index, index_to_share = self._reindex(shares, len(peers) + 1)
        graph = []
        sink_num = len(peers) + len(shares) + 1
        graph.append([peer_to_index[peer] for peer in peers])
        for peer in peers:
            indexedShares = [share_to_index[s] for s in servermap[peer]]
            graph.insert(peer_to_index[peer], indexedShares)
        for share in shares:
            graph.insert(share_to_index[share], [sink_num])
        graph.append([])
        return graph


    def _reindex(self, items, base):
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


    def _flow_network(self, peerIndices, shareIndices):
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
