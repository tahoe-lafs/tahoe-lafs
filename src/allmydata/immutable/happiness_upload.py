from Queue import PriorityQueue
from allmydata.util.happinessutil import augmenting_path_for, residual_network

class Happiness_Upload:
    """
    I handle the calculations involved with generating the maximum
    spanning graph for a file when given a set of peerids, shareids, and
    a servermap of 'peerid' -> [shareids]. Mappings are returned in a
    dictionary of 'shareid' -> 'peerid'
    """

    def __init__(self, peerids, readonly_peers, shareids, servermap={}):
        self.happy = 0
        self.homeless_shares = set()
        self.peerids = peerids
        self.readonly_peers = readonly_peers
        self.shareids = shareids
        self.servermap = servermap
        self.servermap_peerids = set([key for key in servermap])
        self.servermap_shareids = set()
        for key in servermap:
            for share in servermap[key]:
                self.servermap_shareids.add(share)


    def happiness(self):
        return self.happy


    def generate_mappings(self):
        """
        Generate a flow network of peerids to existing shareids and find
        its maximum spanning graph. The leases of these shares should be renewed
        by the client.
        """

        # First find the maximum spanning of the readonly servers.
        readonly_peers = self.readonly_peers
        readonly_shares = set()
        readonly_map = {}
        for peer in self.servermap:
            if peer in self.readonly_peers:
                readonly_map.setdefault(peer, self.servermap[peer])
                for share in self.servermap[peer]:
                    readonly_shares.add(share)

        peer_to_index = self._index_peers(readonly_peers, 1)
        share_to_index, index_to_share = self._reindex_shares(readonly_shares,
                                                        len(readonly_peers) + 1)
        graph = self._servermap_flow_graph(readonly_peers, readonly_shares, readonly_map)
        shareids = [share_to_index[s] for s in readonly_shares]
        max_graph = self._compute_maximum_graph(graph, shareids)
        readonly_mappings = self._convert_mappings(peer_to_index,
                                                    index_to_share, max_graph)

        used_peers, used_shares = self._extract_ids(readonly_mappings)

        # Now find the maximum matching for the rest of the existing allocations.
        # Remove any peers and shares used in readonly_mappings.
        peers = self.servermap_peerids - used_peers
        shares = self.servermap_shareids - used_shares
        servermap = self.servermap.copy()
        for peer in self.servermap:
            if peer in used_peers:
                servermap.pop(peer, None)
            else:
                servermap[peer] = servermap[peer] - used_shares
                if servermap[peer] == set():
                    servermap.pop(peer, None)
                    peers.remove(peer)

        # Reindex and find the maximum matching of the graph.
        peer_to_index = self._index_peers(peers, 1)
        share_to_index, index_to_share = self._reindex_shares(shares, len(peers) + 1)
        graph = self._servermap_flow_graph(peers, shares, servermap)
        shareids = [share_to_index[s] for s in shares]
        max_server_graph = self._compute_maximum_graph(graph, shareids)
        existing_mappings = self._convert_mappings(peer_to_index,
                                            index_to_share, max_server_graph)

        existing_peers, existing_shares = self._extract_ids(existing_mappings)
        peers = self.peerids - existing_peers - used_peers
        shares = self.shareids - existing_shares - used_shares

        # Generate a flow network of peerids to shareids for all peers
        # and shares which cannot be reused from previous file allocations.
        # These mappings represent new allocations the uploader must make.
        peer_to_index = self._index_peers(peers, 1)
        share_to_index, index_to_share = self._reindex_shares(shares, len(peers) + 1)
        peerids = [peer_to_index[peer] for peer in peers]
        shareids = [share_to_index[share] for share in shares]
        graph = self._flow_network(peerids, shareids)
        max_graph = self._compute_maximum_graph(graph, shareids)
        new_mappings = self._convert_mappings(peer_to_index, index_to_share,
                                                                    max_graph)

        mappings = dict(readonly_mappings.items() + existing_mappings.items()
                                                        + new_mappings.items())
        self._calculate_happiness(mappings)
        if len(self.homeless_shares) != 0:
            self._distribute_homeless_shares(mappings)

        return mappings


    def _compute_maximum_graph(self, graph, shareids):
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
        for share in shareids:
            peer = residual_graph[share]
            if peer == [dim - 1]:
                new_mappings.setdefault(share, None)
            else:
                new_mappings.setdefault(share, peer[0])

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
        self.happy = 0
        self.homeless_shares = set()
        for share in mappings:
            if mappings[share] is not None:
                self.happy += 1
            else:
                self.homeless_shares.add(share)


    def _distribute_homeless_shares(self, mappings):
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
            if share in self.servermap_shareids:
                for peerid in self.servermap:
                    if share in self.servermap[peerid]:
                        mappings[share] = set([peerid])
                        break
            else:
                to_distribute.add(share)

        # This builds a priority queue of peers with the number of shares
        # each peer holds as the priority.

        priority = {}
        pQueue = PriorityQueue()
        for peerid in self.peerids:
            priority.setdefault(peerid, 0)
        for share in mappings:
            if mappings[share] is not None:
                for peer in mappings[share]:
                    if peer in self.peerids:
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


    def _convert_mappings(self, peer_to_index, share_to_index, maximum_graph):
        """
        Now that a maximum spanning graph has been found, convert the indexes
        back to their original ids so that the client can pass them to the
        uploader.
        """

        converted_mappings = {}
        for share in maximum_graph:
            peer = maximum_graph[share]
            if peer == None:
                converted_mappings.setdefault(share_to_index[share], None)
            else:
                converted_mappings.setdefault(share_to_index[share],
                                                    set([peer_to_index[peer]]))
        return converted_mappings


    def _servermap_flow_graph(self, peers, shares, servermap):
        """
        Generates a flow network of peerids to shareids from a server map
        of 'peerids' -> ['shareids']. According to Wikipedia, "a flow network is a
        directed graph where each edge has a capacity and each edge receives a flow.
        The amount of flow on an edge cannot exceed the capacity of the edge." This
        is necessary because in order to find the maximum spanning, the Edmonds-Karp algorithm
        converts the problem into a maximum flow problem.
        """
        if servermap == {}:
            return []

        peerids = peers
        shareids = shares
        peer_to_index = self._index_peers(peerids, 1)
        share_to_index, index_to_share = self._reindex_shares(shareids, len(peerids) + 1)
        graph = []
        sink_num = len(peerids) + len(shareids) + 1
        graph.append([peer_to_index[peer] for peer in peerids])
        for peerid in peerids:
            shares = [share_to_index[s] for s in servermap[peerid]]
            graph.insert(peer_to_index[peerid], shares)
        for shareid in shareids:
            graph.insert(share_to_index[shareid], [sink_num])
        graph.append([])
        return graph


    def _index_peers(self, ids, base):
        """
        I create a bidirectional dictionary of indexes to ids with
        indexes from base to base + |ids| - 1 inclusively. I am used
        in order to create a flow network with vertices 0 through n.
        """
        reindex_to_name = {}
        for item in ids:
            reindex_to_name.setdefault(item, base)
            reindex_to_name.setdefault(base, item)
            base += 1
        return reindex_to_name


    def _reindex_shares(self, shares, base):
        """
        I create a dictionary of sharenum -> index (where 'index' is as defined
        in _index_peers) and a dictionary of index -> sharenum. Since share
        numbers  use the same name space as the indexes, two dictionaries need
        to be created instead of one like in _reindex_peers.
        """
        share_to_index = {}
        index_to_share = {}
        for share in shares:
            share_to_index.setdefault(share, base)
            index_to_share.setdefault(base, share)
            base += 1
        return (share_to_index, index_to_share)


    def _flow_network(self, peerids, shareids):
        """
        Given set of peerids and shareids, I create a flow network
        to be used by _compute_maximum_graph.
        """
        graph = []
        graph.append(peerids)
        sink_num = len(peerids + shareids) + 1
        for peerid in peerids:
            graph.insert(peerid, shareids)
        for shareid in shareids:
            graph.insert(shareid, [sink_num])
        graph.append([])
        return graph