
.. -*- coding: utf-8-with-signature -*-

==================================================================
Configuring a Tahoe-LAFS server node for various network scenarios
==================================================================

#.  `storage node has a public IPv4 address`_
#.  `storage node has a public IPv6 address`_
#.  `storage node is behind a firewall with port forwarding`_
#.  `storage node is behind a partial-cone NAT device`_


The following are some suggested scenarios for configuring storage
servers using various network transports. These examples do not
include specifying an introducer FURL which normally you would want
when provisioning storage nodes. For these and other configuration
details please refer to :doc:`configuration`


storage node has a public IPv4 address
======================================

If for example your publicly routable IPv4 address is 10.10.10.10,
then you could use the following to create a storage node::

  tahoe create-node --location=tcp:10.10.10.10:3456 --port=tcp:interface=10.10.10.10:3456

However if you have set a DNS A record for that IP address then the
simplest possible command to create the storage node would also choose
TCP port to listen on::

  tahoe create-node --hostname=example.net


storage node has a public IPv6 address
======================================

Create a storage node that listens on a public IPv6 address::

  tahoe create-node --location=tcp:[2001:0DB8:f00e:eb00::1]:3456 --port=tcp:interface=2001\:0DB8\:f00e\:eb00\:\:1:3456

Create a storage node that listens on the IPv6 loopback::

  tahoe create-node --location=tcp:[::1]:3456 --port=tcp:interface=\:\:1:3456


storage node is behind a firewall with port forwarding
======================================================

To configure a storage node behind a firewall with port forwarding you
will need to know::

  * public IPv4 address of the router
  * the TCP port that is available from outside your network
  * internal IPv4 address of the storage node
  * the TCP port that is the forwarding destination

The internal and external TCP port numbers could be the same or
different depending on how the port forwarding is configured.  If for
example the public IPv4 address of the router is 10.10.10.10 and the
internal IPv4 address of the storage node is 192.168.1.5 then use a
cli command like this::

  tahoe create-node --location=tcp:10.10.10.10:3456 --port=tcp:interface=192.168.1.5:3456

If however the port forwarding forwards external port 6656 to 3456
internally, then like this::

  tahoe create-node --location=tcp:10.10.10.10:6656 --port=tcp:interface=192.168.1.5:3456

  
storage node is behind a partial-cone NAT device
================================================

I2p and Tor onion services among other great properties also provide
NAT penetration::

  tahoe create-node --listen=tor

For more information about using Tahoe-LAFS with I2p and Tor see
:doc:`anonymity-configuration`
