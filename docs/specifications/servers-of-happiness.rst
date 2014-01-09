.. -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-

Servers of Happiness
====================

When you upload a file to a Tahoe-LAFS grid, you expect that it will stay
there for a while, and that it will do so even if a few of the peers on the
grid stop working, or if something else goes wrong. An upload health metric
helps to make sure that this actually happens.  An upload health metric is a
test that looks at a file on a Tahoe-LAFS grid and says how robustly
distributed it is. Our current upload health metric for immutable files is
called 'servers-of-happiness'.

Servers-of-happiness looks at the mapping of peers to the shares that they
hold, and considers the size of the largest set of (server, share) pairs such
that no server appears more than once in the set and no share appears more
than once in the set. The size of the set is called the Happiness value.

For example, if server A is holding share 0, and server B is holding share 1,
then the Happiness value is 2.::

    example 1
    ---------

    server A → share 0
    server B → share 1

    Happiness value = 2

In this case, adding server C holding share 0 would not increase the
Happiness value.::

    example 2
    ---------

    server A → share 0
    server B → share 1
    server C → share 1

    Happiness value = 2

You can understand this intuitively as being that server C doesn't increase
the robustness of the file as well as it could. Server C will help if server
B disappears, but server C will not be any added help beyond what server B
provided, if server A disappears.

But if the added server C held a new share, then it would increase the
Happiness value.::

    example 3
    ---------

    server A → share 0
    server B → share 1
    server C → share 2

    Happiness value = 3

Now if each server holds at most one share, then this measure of robustness
is very intuitive — it is basically just "the number of servers that each
have a unique share".

However, if some servers have more than one share on them, then this measure
may not be as intuitive to some people.

For another example, if you have this distribution::

    example 4
    ---------

    server A → share 0, share 1
    server B → share 1, share 2

    Happiness value = 2

And you add a server C which holds share 1 and share 2, then you increase the
Happiness level to 3.::

    example 5
    ---------

    server A → share 0, share 1
    server B → share 1, share 2
    server C → share 1, share 2

    Happiness value = 3

    example 6
    ---------

    server A → share 0, share 1
    server B → share 1, share 2
    server C → share 0, share 2

    Happiness value = 3

    example 7
    ---------

    server A → share 0, share 1, share 2, share 3

    Happiness value = 1

    example 8
    ---------

    server A → share 0, share 1, share 2, share 3
    server B → share 0

    Happiness value = 2

    example 9
    ---------

    server A → share 0, share 1, share 2, share 3
    server B → share 0, share 1, share 2, share 3

    Happiness value = 2

    example 10
    ----------

    server A → share 0, share 1, share 2, share 3
    server B → share 0, share 1, share 2, share 3
    server C → share 0

    Happiness value = 3

Although the "servers-of-happiness" measure may not be intuitive when applied
to servers holding multiple shares, it is important that it gives a
reasonable answer when servers are holding multiple shares, because this can
happen in practice, and the upload algorithm needs to decide what to do in
that case.

Fortunately, using the "servers-of-happiness" measure has a very nice
consequence:

  *If you make sure that the Happiness level is greater than or equal to a certain number, H, then you are guaranteed that there are at least H servers any K of which can reconstruct the file.*

(In Tahoe-LAFS terminology, we use *“N”* to mean the total number of shares
created, and *“K”* to mean the number of shares required to reconstruct the
file. *N* and *K* are configuration parameters that the user can control.)

This is a simple, intuitive result which is exactly what you want. You want
your file to be “spread out” over a number of different servers, such that
*any K of them* will be able to deliver the file back to you.

Now you just need to decide “over how many servers do I require my file to be
spread out?”. That number is the *“H”* parameter that you pass to the
uploader. If it cannot arrange for the servers-of-happiness metric to meet or
exceed *H*, then it will abort the upload as a failure.

Understand that the uploader will always attempt to spread the file out over
as many servers as possible (up to *N* different servers, where *N* is the
total number of shares created), so setting the
servers-of-happiness-requirement *H* doesn't change which servers the upload
algorithm will use, it only tells the uploader the level of robustness below
which it should abort the upload attempt and report it as a failure.


Measuring Servers of Happiness
------------------------------

We calculate servers-of-happiness by constructing a graph with two kinds of
nodes: servers (represented here lined up on the left-hand side) and shares
(lined up on the right-hand size). The edges in the graph go from a server to
each share held by that server. This type of graph is called a “bipartite
graph”.

To compute the servers-of-happiness metric, find a “maximum matching” in this
bipartite graph. A “matching” is a set of edges such that no server appears
more than once in the set and no share appears more than once in the set. A
“maximum matching” is a largest such set. (There can be more than one set
tied for largest.)

Issues
------

We don't use servers-of-happiness for mutable files yet; this improvement
will likely come in Tahoe-LAFS version 1.12.


Upload Strategy of Happiness
============================

Okay, we have a metric of distribution (the servers-of-happiness metric), and
we have a threshold requirement for a minimum level of distribution to
achieve or else abort (the *H* parameter), and now we need an upload
algorithm that will find an optimal placement for the shares in order to
maximize the servers-of-happiness metric.

Calculating Share Placements
----------------------------

We calculate share placement like so:

1. Query *2N* servers for existing shares.

2. Construct a bipartite graph of *readonly* servers to shares, where an edge
   exists between an arbitrary readonly server S and an arbitrary share T if
   and only if S currently holds T.

3. Calculate a maximum matching graph of that bipartite graph. There may be
   more than one maximum matching for this graph; we choose one of them
   arbitrarily.

4. Construct a bipartite graph of servers (whether readonly or readwrite) to
   shares, removing any servers and shares used in the maximum matching graph
   from step 3. Let an edge exist between server S and share T if and only if
   S already holds T.

5. Calculate the maximum matching graph of the new graph.

6. Construct a bipartite graph of servers (whether readonly or readwrite) to
   share, removing any servers and shares used in the maximum matching graphs
   from steps 3 and 5. Let an edge exist between server S and share T if and
   only if S *could* hold T (i.e. S is readwrite and S has enough available
   space to hold a share of at least T's size).

7. Calculate the maximum matching graph of the new graph.

8. Renew the shares on their respective servers from steps 3 and 5.

9. Place share T on server S if an edge exists between S and T in the maximum
   matching graph from step 7.

10. If any placements from step 7 fail, remove the server from the set of
    possible servers and regenerate the matchings. XXX go back to step 4?


Properties of Upload Strategy of Happiness
------------------------------------------

The size of the maximum bipartite matching is bounded by the size of the smaller
set of vertices. Therefore in a situation where the set of servers is smaller
than the set of shares, placement is not generated for a subset of shares. In
this case the remaining shares are distributed as evenly as possible across the
set of writable servers.

If the servers-of-happiness criteria can be met, the upload strategy of
happiness guarantees that H shares will be placed on the network. During file
repair, if the set of servers is larger than N, the algorithm will attempt to
spread shares only over N distinct servers. For both initial file upload and
file repair, N should be viewed as the maximum number of distinct servers
shares can be placed on, and H as the minimum. The uploader will fail if the
number of distinct servers is less than H, and it will never attempt to
exceed N.
