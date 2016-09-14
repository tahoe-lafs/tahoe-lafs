.. -*- coding: utf-8-with-signature -*-

==================================================================
Configuring a Tahoe-LAFS server node for various network scenarios
==================================================================

#.  `Storage node has a public DNS name`_
#.  `Storage node has a public IPv4/IPv6 address`_
#.  `Storage node is behind a firewall with port forwarding`_
#.  `Using I2P/Tor to Avoid Port-Forwarding`_


The following are some suggested scenarios for configuring storage
servers using various network transports. These examples do not
include specifying an introducer FURL which normally you would want
when provisioning storage nodes. For these and other configuration
details please refer to :doc:`configuration`


Storage node has a public DNS name
==================================

The simplest case is when your storage host has a public IPv4 address, and
there is a valid DNS "A" record that points to it (e.g. ``example.net``). In
this case, just do::

  tahoe create-node --hostname=example.net

Ideally this should work for IPv6-capable hosts too (where the DNS name
provides an "AAAA" record, or both "A" and "AAAA"). However Tahoe-LAFS
support for IPv6 is new, and may still have problems. Please see ticket
`#867`_ for details.

.. _#867: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/867


Storage node has a public IPv4/IPv6 address
===========================================

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


Storage node is behind a firewall with port forwarding
======================================================

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
======================================

I2P and Tor onion services, among other great properties, also provide NAT
penetration. So setting up a server that listens only on Tor is simple::

  tahoe create-node --listen=tor

For more information about using Tahoe-LAFS with I2p and Tor see
:doc:`anonymity-configuration`
