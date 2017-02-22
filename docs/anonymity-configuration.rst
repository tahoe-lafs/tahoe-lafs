.. -*- coding: utf-8-with-signature; fill-column: 77 -*-

======================================================
Using Tahoe-LAFS with an anonymizing network: Tor, I2P
======================================================

#. `Overview`_
#. `Use cases`_

#. `Software Dependencies`_

   #. `Tor`_
   #. `I2P`_

#. `Connection configuration`_

#. `Anonymity configuration`_

   #. `Client anonymity`_
   #. `Server anonymity, manual configuration`_
   #. `Server anonymity, automatic configuration`_

#. `Performance and security issues`_



Overview
========

Tor is an anonymizing network used to help hide the identity of internet
clients and servers. Please see the Tor Project's website for more information:
https://www.torproject.org/

I2P is a decentralized anonymizing network that focuses on end-to-end anonymity
between clients and servers. Please see the I2P website for more information:
https://geti2p.net/



Use cases
=========

There are three potential use-cases for Tahoe-LAFS on the client side:

1. User wishes to always use an anonymizing network (Tor, I2P) to protect
   their anonymity when connecting to Tahoe-LAFS storage grids (whether or
   not the storage servers are anonymous).

2. User does not care to protect their anonymity but they wish to connect to
   Tahoe-LAFS storage servers which are accessible only via Tor Hidden Services or I2P.

   * Tor is only used if a server connection hint uses ``tor:``. These hints
     generally have a ``.onion`` address.
   * I2P is only used if a server connection hint uses ``i2p:``. These hints
     generally have a ``.i2p`` address.

3. User does not care to protect their anonymity or to connect to anonymous
   storage servers. This document is not useful to you... so stop reading.


For Tahoe-LAFS storage servers there are three use-cases:

1. The operator wishes to protect their anonymity by making their Tahoe
   server accessible only over I2P, via Tor Hidden Services, or both.

2. The operator does not *require* anonymity for the storage server, but they
   want it to be available over both publicly routed TCP/IP and through an
   anonymizing network (I2P, Tor Hidden Services). One possible reason to do
   this is because being reachable through an anonymizing network is a
   convenient way to bypass NAT or firewall that prevents publicly routed
   TCP/IP connections to your server (for clients capable of connecting to
   such servers). Another is that making your storage server reachable
   through an anonymizing network can provide better protection for your
   clients who themselves use that anonymizing network to protect their
   anonymity.

3. Storage server operator does not care to protect their own anonymity nor
   to help the clients protect theirs. Stop reading this document and run
   your Tahoe-LAFS storage server using publicly routed TCP/IP.


   See this Tor Project page for more information about Tor Hidden Services:
   https://www.torproject.org/docs/hidden-services.html.en

   See this I2P Project page for more information about I2P:
   https://geti2p.net/en/about/intro


Software Dependencies
=====================

Tor
---

Clients who wish to connect to Tor-based servers must install the following.

* Tor (tor) must be installed. See here:
  https://www.torproject.org/docs/installguide.html.en . On Debian/Ubuntu,
  use ``apt-get install tor``. You can also install and run the Tor Browser
  Bundle.

* Tahoe-LAFS must be installed with the ``[tor]`` "extra" enabled. This will
  install ``txtorcon`` ::

   pip install tahoe-lafs[tor]

Manually-configured Tor-based servers must install Tor, but do not need
``txtorcon`` or the ``[tor]`` extra. Automatic configuration, when
implemented, will need these, just like clients.

I2P
---

Clients who wish to connect to I2P-based servers must install the following.
As with Tor, manually-configured I2P-based servers need the I2P daemon, but
no special Tahoe-side supporting libraries.

* I2P must be installed. See here:
  https://geti2p.net/en/download

* The SAM API must be enabled.

  * Start I2P.
  * Visit http://127.0.0.1:7657/configclients in your browser.
  * Under "Client Configuration", check the "Run at Startup?" box for "SAM
    application bridge".
  * Click "Save Client Configuration".
  * Click the "Start" control for "SAM application bridge", or restart I2P.

* Tahoe-LAFS must be installed with the ``[i2p]`` extra enabled, to get
  ``txi2p`` ::

   pip install tahoe-lafs[i2p]

Both Tor and I2P
----------------

Clients who wish to connect to both Tor- and I2P-based servers must install
all of the above. In particular, Tahoe-LAFS must be installed with both
extras enabled::

   pip install tahoe-lafs[tor,i2p]



Connection configuration
========================

See :ref:`Connection Management` for a description of the ``[tor]`` and
``[i2p]`` sections of ``tahoe.cfg``. These control how the Tahoe client will
connect to a Tor/I2P daemon, and thus make connections to Tor/I2P -based
servers.

The ``[tor]`` and ``[i2p]`` sections only need to be modified to use unusual
configurations, or to enable automatic server setup.

The default configuration will attempt to contact a local Tor/I2P daemon
listening on the usual ports (9050/9150 for Tor, 7656 for I2P). As long as
there is a daemon running on the local host, and the necessary support
libraries were installed, clients will be able to use Tor-based servers
without any special configuration.

However note that this default configuration does not improve the client's
anonymity: normal TCP connections will still be made to any server that
offers a regular address (it fulfills the second client use case above, not
the third). To protect their anonymity, users must configure the
``[connections]`` section as follows::

  [connections]
  tcp = tor

With this in place, the client will use Tor (instead of an
IP-address -revealing direct connection) to reach TCP-based servers.

Anonymity configuration
=======================

Tahoe-LAFS provides a configuration "safety flag" for explicitly stating
whether or not IP-address privacy is required for a node::

   [node]
   reveal-IP-address = (boolean, optional)

When ``reveal-IP-address = False``, Tahoe-LAFS will refuse to start if any of
the configuration options in ``tahoe.cfg`` would reveal the node's network
location:

* ``[connections] tcp = tor`` is required: otherwise the client would make
  direct connections to the Introducer, or any TCP-based servers it learns
  from the Introducer, revealing its IP address to those servers and a
  network eavesdropper. With this in place, Tahoe-LAFS will only make
  outgoing connections through a supported anonymizing network.

* ``tub.location`` must either be disabled, or contain safe values. This
  value is advertised to other nodes via the Introducer: it is how a server
  advertises it's location so clients can connect to it. In private mode, it
  is an error to include a ``tcp:`` hint in ``tub.location``. Private mode
  rejects the default value of ``tub.location`` (when the key is missing
  entirely), which is ``AUTO``, which uses ``ifconfig`` to guess the node's
  external IP address, which would reveal it to the server and other clients.

This option is **critical** to preserving the client's anonymity (client
use-case 3 from `Use cases`_, above). It is also necessary to preserve a
server's anonymity (server use-case 3).

This flag can be set (to False) by providing the ``--hide-ip`` argument to
the ``create-node``, ``create-client``, or ``create-introducer`` commands.

Note that the default value of ``reveal-IP-address`` is True, because
unfortunately hiding the node's IP address requires additional software to be
installed (as described above), and reduces performance.

Client anonymity
----------------

To configure a client node for anonymity, ``tahoe.cfg`` **must** contain the
following configuration flags::

   [node]
   reveal-IP-address = False
   tub.port = disabled
   tub.location = disabled

Once the Tahoe-LAFS node has been restarted, it can be used anonymously (client
use-case 3).

Server anonymity, manual configuration
--------------------------------------

To configure a server node to listen on an anonymizing network, we must first
configure Tor to run an "Onion Service", and route inbound connections to the
local Tahoe port. Then we configure Tahoe to advertise the ``.onion`` address
to clients. We also configure Tahoe to not make direct TCP connections.

* Decide on a local listening port number, named PORT. This can be any unused
  port from about 1024 up to 65535 (depending upon the host's kernel/network
  config). We will tell Tahoe to listen on this port, and we'll tell Tor to
  route inbound connections to it.
* Decide on an external port number, named VIRTPORT. This will be used in the
  advertised location, and revealed to clients. It can be any number from 1
  to 65535. It can be the same as PORT, if you like.
* Decide on a "hidden service directory", usually in ``/var/lib/tor/NAME``.
  We'll be asking Tor to save the onion-service state here, and Tor will
  write the ``.onion`` address here after it is generated.

Then, do the following:

* Create the Tahoe server node (with ``tahoe create-node``), but do **not**
  launch it yet.

* Edit the Tor config file (typically in ``/etc/tor/torrc``). We need to add
  a section to define the hidden service. If our PORT is 2000, VIRTPORT is
  3000, and we're using ``/var/lib/tor/tahoe`` as the hidden service
  directory, the section should look like::

    HiddenServiceDir /var/lib/tor/tahoe
    HiddenServicePort 3000 127.0.0.1:2000

* Restart Tor, with ``systemctl restart tor``. Wait a few seconds.

* Read the ``hostname`` file in the hidden service directory (e.g.
  ``/var/lib/tor/tahoe/hostname``). This will be a ``.onion`` address, like
  ``u33m4y7klhz3b.onion``. Call this ONION.

* Edit ``tahoe.cfg`` to set ``tub.port`` to use
  ``tcp:PORT:interface=127.0.0.1``, and ``tub.location`` to use
  ``tor:ONION.onion:VIRTPORT``. Using the examples above, this would be::

    [node]
    reveal-IP-address = false
    tub.port = tcp:2000:interface=127.0.0.1
    tub.location = tor:u33m4y7klhz3b.onion:3000
    [connections]
    tcp = tor

* Launch the Tahoe server with ``tahoe start $NODEDIR``

The ``tub.port`` section will cause the Tahoe server to listen on PORT, but
bind the listening socket to the loopback interface, which is not reachable
from the outside world (but *is* reachable by the local Tor daemon). Then the
``tcp = tor`` section causes Tahoe to use Tor when connecting to the
Introducer, hiding it's IP address. The node will then announce itself to all
clients using ``tub.location``, so clients will know that they must use Tor
to reach this server (and not revealing it's IP address through the
announcement). When clients connect to the onion address, their packets will
flow through the anonymizing network and eventually land on the local Tor
daemon, which will then make a connection to PORT on localhost, which is
where Tahoe is listening for connections.

Follow a similar process to build a Tahoe server that listens on I2P. The
same process can be used to listen on both Tor and I2P (``tub.location =
tor:ONION.onion:VIRTPORT,i2p:ADDR.i2p``). It can also listen on both Tor and
plain TCP (use-case 2), with ``tub.port = tcp:PORT``, ``tub.location =
tcp:HOST:PORT,tor:ONION.onion:VIRTPORT``, and ``anonymous = false`` (and omit
the ``tcp = tor`` setting, as the address is already being broadcast through
the location announcement).


Server anonymity, automatic configuration
-----------------------------------------

To configure a server node to listen on an anonymizing network, create the
node with the ``--listen=tor`` option. This requires a Tor configuration that
either launches a new Tor daemon, or has access to the Tor control port (and
enough authority to create a new onion service). On Debian/Ubuntu systems, do
``apt install tor``, add yourself to the control group with ``adduser
YOURUSERNAME debian-tor``, and then logout and log back in: if the ``groups``
command includes ``debian-tor`` in the output, you should have permission to
use the unix-domain control port at ``/var/run/tor/control``.

This option will set ``reveal-IP-address = False`` and ``[connections] tcp =
tor``. It will allocate the necessary ports, instruct Tor to create the onion
service (saving the private key somewhere inside NODEDIR/private/), obtain
the ``.onion`` address, and populate ``tub.port`` and ``tub.location``
correctly.


Performance and security issues
===============================

If you are running a server which does not itself need to be
anonymous, should you make it reachable via an anonymizing network or
not? Or should you make it reachable *both* via an anonymizing network
and as a publicly traceable TCP/IP server?

There are several trade-offs effected by this decision.

NAT/Firewall penetration
------------------------

Making a server be reachable via Tor or I2P makes it reachable (by
Tor/I2P-capable clients) even if there are NATs or firewalls preventing
direct TCP/IP connections to the server.

Anonymity
---------

Making a Tahoe-LAFS server accessible *only* via Tor or I2P can be used to
guarantee that the Tahoe-LAFS clients use Tor or I2P to connect
(specifically, the server should only advertise Tor/I2P addresses in the
``tub.location`` config key). This prevents misconfigured clients from
accidentally de-anonymizing themselves by connecting to your server through
the traceable Internet.

Clearly, a server which is available as both a Tor/I2P service *and* a
regular TCP address is not itself anonymous: the .onion address and the real
IP address of the server are easily linkable.

Also, interaction, through Tor, with a Tor Hidden Service may be more
protected from network traffic analysis than interaction, through Tor,
with a publicly traceable TCP/IP server.

**XXX is there a document maintained by Tor developers which substantiates or refutes this belief?
If so we need to link to it. If not, then maybe we should explain more here why we think this?**

Linkability
-----------

As of 1.12.0, the node uses a single persistent Tub key for outbound
connections to the Introducer, and inbound connections to the Storage Server
(and Helper). For clients, a new Tub key is created for each storage server
we learn about, and these keys are *not* persisted (so they will change each
time the client reboots).

Clients traversing directories (from rootcap to subdirectory to filecap) are
likely to request the same storage-indices (SIs) in the same order each time.
A client connected to multiple servers will ask them all for the same SI at
about the same time. And two clients which are sharing files or directories
will visit the same SIs (at various times).

As a result, the following things are linkable, even with ``reveal-IP-address
= false``:

* Storage servers can link recognize multiple connections from the same
  not-yet-rebooted client. (Note that the upcoming Accounting feature may
  cause clients to present a persistent client-side public key when
  connecting, which will be a much stronger linkage).
* Storage servers can probably deduce which client is accessing data, by
  looking at the SIs being requested. Multiple servers can collude to
  determine that the same client is talking to all of them, even though the
  TubIDs are different for each connection.
* Storage servers can deduce when two different clients are sharing data.
* The Introducer could deliver different server information to each
  subscribed client, to partition clients into distinct sets according to
  which server connections they eventually make. For client+server nodes, it
  can also correlate the server announcement with the deduced client
  identity.

Performance
-----------

A client connecting to a publicly traceable Tahoe-LAFS server through Tor
incurs substantially higher latency and sometimes worse throughput than the
same client connecting to the same server over a normal traceable TCP/IP
connection. When the server is on a Tor Hidden Service, it incurs even more
latency, and possibly even worse throughput.

Connecting to Tahoe-LAFS servers which are I2P servers incurs higher latency
and worse throughput too.

Positive and negative effects on other Tor users
------------------------------------------------

Sending your Tahoe-LAFS traffic over Tor adds cover traffic for other
Tor users who are also transmitting bulk data. So that is good for
them -- increasing their anonymity.

However, it makes the performance of other Tor users' interactive
sessions -- e.g. ssh sessions -- much worse. This is because Tor
doesn't currently have any prioritization or quality-of-service
features, so someone else's ssh keystrokes may have to wait in line
while your bulk file contents get transmitted. The added delay might
make other people's interactive sessions unusable.

Both of these effects are doubled if you upload or download files to a
Tor Hidden Service, as compared to if you upload or download files
over Tor to a publicly traceable TCP/IP server.

Positive and negative effects on other I2P users
------------------------------------------------

Sending your Tahoe-LAFS traffic over I2P adds cover traffic for other I2P users
who are also transmitting data. So that is good for them -- increasing their
anonymity. It will not directly impair the performance of other I2P users'
interactive sessions, because the I2P network has several congestion control and
quality-of-service features, such as prioritizing smaller packets.

However, if many users are sending Tahoe-LAFS traffic over I2P, and do not have
their I2P routers configured to participate in much traffic, then the I2P
network as a whole will suffer degradation. Each Tahoe-LAFS router using I2P has
their own anonymizing tunnels that their data is sent through. On average, one
Tahoe-LAFS node requires 12 other I2P routers to participate in their tunnels.

It is therefore important that your I2P router is sharing bandwidth with other
routers, so that you can give back as you use I2P. This will never impair the
performance of your Tahoe-LAFS node, because your I2P router will always
prioritize your own traffic.

