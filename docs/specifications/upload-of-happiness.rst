============================
Upload Strategy of Happiness
============================

Introduced in version 1.7, Tahoe uses a health metric called 'servers- of-
happiness' to compute whether a distribution of shares is healthy (see servers-
of-happiness.rst for details). The uploader is good at detecting instances which
do not pass the servers-of-happiness test, but the share distribution algorithm
is not always successful in instances where happiness can be achieved. A new
placement algorithm designed to pass the servers-of-happiness test,  titled
'Upload Strategy of Happiness', is meant to fix these instances where the uploader
is unable to achieve happiness.

Calculating Share Placements
============================

We calculate share placement like so:

1. Query all servers for existing shares.

2. Construct a bipartite graph of readonly servers to shares, where an edge
exists between an arbitrary server s and an arbitrary share n if and only if s
holds n.

3. Calculate the maximum matching graph of the bipartite graph.

4. Construct a bipartite graph of servers to shares, removing any servers and
shares used in the maximum matching graph from step 3. Let an edge exist between
an arbitrary server s and an arbitrary share n if and only if s holds n.

5. Calculate the maximum matching graph of the new graph.

6. Construct a bipartite graph of servers to share, removing any servers and
shares used in the maximum matching graphs from steps 3 and 5. Let an edge exist
between an arbitrary server s and an arbitrary server n if and only if s can
hold n.

7. Calculate the maximum matching graph of the new graph.

8. Renew the shares on their respective servers from steps 3
and 5. Place share n on server s if an edge exists between s and n in the
maximum matching graph from step 7.

9. If any placements from step 7 fail, remove the server from the set of possible
servers and regenerate the matchings.


Properties of Upload Strategy of Happiness
==========================================

The size of the maximum bipartite matching is bounded by the size of the smaller
set of vertices. Therefore in a situation where the set of servers is smaller
than the set of shares, placement is not generated for a subset of shares. In
this case the remaining shares are distributed as evenly as possible across the
set of writable servers.

If the servers-of-happiness criteria can be met, the upload strategy of
happiness guarantees that N shares will be placed on the network. During file
repair, if the set of servers is larger than N, the algorithm will only attempt
to spread shares over N distinct servers. For both initial file upload and file
repair, N should be viewed as the maximum number of distinct servers shares
can be placed on, and H as the minimum amount. The uploader will fail if
the number of distinct servers is less than H, and it will never attempt to
exceed N. 





