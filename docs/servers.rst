=========================
How To Configure A Server
=========================

Many Tahoe-LAFS nodes run as "servers", meaning they provide services for
other machines (i.e. "clients"). The two most important kinds are the
Introducer, and Storage Servers.

To be useful, servers must be reachable by clients. Tahoe servers can listen
on TCP ports, and advertise their "location" (hostname and TCP port number)
so clients can connect to them. They can also listen on Tor "onion services"
and I2P ports.

Storage servers advertise their location by announcing it to the Introducer,
which then broadcasts the location to all clients. So once the location is
determined, you don't need to do anything special to deliver it.

The Introducer itself has a location, which must be manually delivered to all
storage servers and clients. You might email it to the new members of your
grid. This location (along with other important cryptographic identifiers) is
written into a file named ``private/introducer.furl`` in the Introducer's
base directory, and should be provided as the ``--introducer=`` argument to
``tahoe create-client`` or ``tahoe create-node``.

The first step when setting up a server is to figure out how clients will
reach it. Then you need to configure the server to listen on some ports, and
then configure the location properly.

Manual Configuration
====================

Each server has two settings in their ``tahoe.cfg`` file: ``tub.port``, and
``tub.location``. The "port" controls what the server node listens to: this
is generally a TCP port.

The "location" controls what is advertised to the outside world. This is a
"foolscap connection hint", and it includes both the type of the connection
(tcp, tor, or i2p) and the connection details (hostname/address, port
number). Various proxies, port-forwardings, and privacy networks might be
involved, so it's not uncommon for ``tub.port`` and ``tub.location`` to look
different.

You can directly control the ``tub.port`` and ``tub.location`` configuration
settings by providing ``--port=`` and ``--location=`` when running ``tahoe
create-node``.

Automatic Configuration
=======================

Instead of providing ``--port=/--location=``, you can use ``--listen=``.
Servers can listen on TCP, Tor, I2P, a combination of those, or none at all.
The ``--listen=`` argument controls which kinds of listeners the new server
will use.

``--listen=none`` means the server should not listen at all. This doesn't
make sense for a server, but is appropriate for a client-only node. The
``tahoe create-client`` command automatically includes ``--listen=none``.

``--listen=tcp`` is the default, and turns on a standard TCP listening port.
Using ``--listen=tcp`` requires a ``--hostname=`` argument too, which will be
incorporated into the node's advertised location. We've found that computers
cannot reliably determine their externally-reachable hostname, so rather than
having the server make a guess (or scanning its interfaces for IP addresses
that might or might not be appropriate), node creation requires the user to
provide the hostname.

``--listen=tor`` will talk to a local Tor daemon and create a new "onion
server" address (which look like ``alzrgrdvxct6c63z.onion``). Likewise
``--listen=i2p`` will talk to a local I2P daemon and create a new server
address. See :doc:`anonymity-configuration` for details.

You could listen on all three by using ``--listen=tcp,tor,i2p``.

Deployment Scenarios
====================

The following are some suggested scenarios for configuring servers using
various network transports. These examples do not include specifying an
introducer FURL which normally you would want when provisioning storage
nodes. For these and other configuration details please refer to
:doc:`configuration`.

#.  `Server has a public DNS name`_
#.  `Server has a public IPv4/IPv6 address`_
#.  `Server is behind a firewall with port forwarding`_
#.  `Using I2P/Tor to Avoid Port-Forwarding`_


Server has a public DNS name
----------------------------

The simplest case is where your server host is directly connected to the
internet, without a firewall or NAT box in the way. Most VPS (Virtual Private
Server) and colocated servers are like this, although some providers block
many inbound ports by default.

For these servers, all you need to know is the external hostname. The system
administrator will tell you this. The main requirement is that this hostname
can be looked up in DNS, and it will map to an IPv4 or IPv6 address which
will reach the machine.

If your hostname is ``example.net``, then you'll create the introducer like
this::

  tahoe create-introducer --hostname example.com ~/introducer

or a storage server like::

  tahoe create-node --hostname=example.net

These will allocate a TCP port (e.g. 12345), assign ``tub.port`` to be
``tcp:12345``, and ``tub.location`` will be ``tcp:example.com:12345``.

Ideally this should work for IPv6-capable hosts too (where the DNS name
provides an "AAAA" record, or both "A" and "AAAA"). However Tahoe-LAFS
support for IPv6 is new, and may still have problems. Please see ticket
`#867`_ for details.

.. _#867: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/867


Server has a public IPv4/IPv6 address
-------------------------------------

If the host has a routeable (public) IPv4 address (e.g. ``203.0.113.1``), but
no DNS name, you will need to choose a TCP port (e.g. ``3457``), and use the
following::

  tahoe create-node --port=tcp:3457 --location=tcp:203.0.113.1:3457

``--port`` is an "endpoint specification string" that controls which local
port the node listens on. ``--location`` is the "connection hint" that it
advertises to others, and describes the outbound connections that those
clients will make, so it needs to work from their location on the network.

Tahoe-LAFS nodes listen on all interfaces by default. When the host is
multi-homed, you might want to make the listening port bind to just one
specific interface by adding a ``interface=`` option to the ``--port=``
argument::

  tahoe create-node --port=tcp:3457:interface=203.0.113.1 --location=tcp:203.0.113.1:3457

If the host's public address is IPv6 instead of IPv4, use square brackets to
wrap the address, and change the endpoint type to ``tcp6``::

  tahoe create-node --port=tcp6:3457 --location=tcp:[2001:db8::1]:3457

You can use ``interface=`` to bind to a specific IPv6 interface too, however
you must backslash-escape the colons, because otherwise they are interpreted
as delimiters by the Twisted "endpoint" specification language. The
``--location=`` argument does not need colons to be escaped, because they are
wrapped by the square brackets::

  tahoe create-node --port=tcp6:3457:interface=2001\:db8\:\:1 --location=tcp:[2001:db8::1]:3457

For IPv6-only hosts with AAAA DNS records, if the simple ``--hostname=``
configuration does not work, they can be told to listen specifically on an
IPv6-enabled port with this::

  tahoe create-node --port=tcp6:3457 --location=tcp:example.net:3457


Server is behind a firewall with port forwarding
------------------------------------------------

To configure a storage node behind a firewall with port forwarding you will
need to know:

* public IPv4 address of the router
* the TCP port that is available from outside your network
* the TCP port that is the forwarding destination
* internal IPv4 address of the storage node (the storage node itself is
  unaware of this address, and it is not used during ``tahoe create-node``,
  but the firewall must be configured to send connections to this)

The internal and external TCP port numbers could be the same or different
depending on how the port forwarding is configured. If it is mapping ports
1-to-1, and the public IPv4 address of the firewall is 203.0.113.1 (and
perhaps the internal IPv4 address of the storage node is 192.168.1.5), then
use a CLI command like this::

  tahoe create-node --port=tcp:3457 --location=tcp:203.0.113.1:3457

If however the firewall/NAT-box forwards external port *6656* to internal
port 3457, then do this::

  tahoe create-node --port=tcp:3457 --location=tcp:203.0.113.1:6656


Using I2P/Tor to Avoid Port-Forwarding
--------------------------------------

I2P and Tor onion services, among other great properties, also provide NAT
penetration without port-forwarding, hostnames, or IP addresses. So setting
up a server that listens only on Tor is simple::

  tahoe create-node --listen=tor

For more information about using Tahoe-LAFS with I2p and Tor see
:doc:`anonymity-configuration`
