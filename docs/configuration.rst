.. -*- coding: utf-8-with-signature -*-

=============================
Configuring a Tahoe-LAFS node
=============================

#.  `Node Types`_
#.  `Overall Node Configuration`_
#.  `Connection Management`_
#.  `Client Configuration`_
#.  `Storage Server Configuration`_
#.  `Frontend Configuration`_
#.  `Running A Helper`_
#.  `Running An Introducer`_
#.  `Other Files in BASEDIR`_
#. `Static Server Definitions`_
#. `Other files`_
#. `Example`_

A Tahoe-LAFS node is configured by writing to files in its base directory.
These files are read by the node when it starts, so each time you change
them, you need to restart the node.

The node also writes state to its base directory, so it will create files on
its own.

This document contains a complete list of the config files that are examined
by the client node, as well as the state files that you'll observe in its
base directory.

The main file is named "``tahoe.cfg``", and is an ".INI"-style configuration
file (parsed by the Python stdlib 'ConfigParser' module: "``[name]``" section
markers, lines with "``key.subkey: value``", rfc822-style
continuations). There are also other files containing information that does
not easily fit into this format. The "``tahoe create-node``" or "``tahoe
create-client``" command will create an initial ``tahoe.cfg`` file for
you. After creation, the node will never modify the ``tahoe.cfg`` file: all
persistent state is put in other files.

The item descriptions below use the following types:

``boolean``

    one of (True, yes, on, 1, False, off, no, 0), case-insensitive

``strports string``

    a Twisted listening-port specification string, like "``tcp:80``" or
    "``tcp:3456:interface=127.0.0.1``". For a full description of the format,
    see `the Twisted strports documentation`_.  Please note, if interface= is
    not specified, Tahoe-LAFS will attempt to bind the port specified on all
    interfaces.

``endpoint specification string``

    a Twisted Endpoint specification string, like "``tcp:80``" or
    "``tcp:3456:interface=127.0.0.1``". These are replacing strports strings.
    For a full description of the format, see `the Twisted Endpoints
    documentation`_. Please note, if interface= is not specified, Tahoe-LAFS
    will attempt to bind the port specified on all interfaces. Also note that
    ``tub.port`` only works with TCP endpoints right now.

``FURL string``

    a Foolscap endpoint identifier, like
    ``pb://soklj4y7eok5c3xkmjeqpw@192.168.69.247:44801/eqpwqtzm``

.. _the Twisted strports documentation: https://twistedmatrix.com/documents/current/api/twisted.application.strports.html
.. _the Twisted Endpoints documentation: http://twistedmatrix.com/documents/current/core/howto/endpoints.html#endpoint-types-included-with-twisted

Node Types
==========

A node can be a client/server, an introducer, or a statistics gatherer.

Client/server nodes provide one or more of the following services:

* web-API service
* SFTP service
* FTP service
* Magic Folder service
* helper service
* storage service.

A client/server that provides storage service (i.e. storing shares for
clients) is called a "storage server". If it provides any of the other
services, it is a "storage client" (a node can be both a storage server and a
storage client). A client/server node that provides web-API service is called
a "gateway".


Overall Node Configuration
==========================

This section controls the network behavior of the node overall: which ports
and IP addresses are used, when connections are timed out, etc. This
configuration applies to all node types and is independent of the services
that the node is offering.

If your node is behind a firewall or NAT device and you want other clients to
connect to it, you'll need to open a port in the firewall or NAT, and specify
that port number in the tub.port option. If behind a NAT, you *may* need to
set the ``tub.location`` option described below.

``[node]``

``nickname = (UTF-8 string, optional)``

    This value will be displayed in management tools as this node's
    "nickname". If not provided, the nickname will be set to "<unspecified>".
    This string shall be a UTF-8 encoded Unicode string.

``web.port = (strports string, optional)``

    This controls where the node's web server should listen, providing node
    status and, if the node is a client/server, providing web-API service as
    defined in :doc:`frontends/webapi`.

    This file contains a Twisted "strports" specification such as "``3456``"
    or "``tcp:3456:interface=127.0.0.1``". The "``tahoe create-node``" or
    "``tahoe create-client``" commands set the ``web.port`` to
    "``tcp:3456:interface=127.0.0.1``" by default; this is overridable by the
    ``--webport`` option. You can make it use SSL by writing
    "``ssl:3456:privateKey=mykey.pem:certKey=cert.pem``" instead.

    If this is not provided, the node will not run a web server.

``web.static = (string, optional)``

    This controls where the ``/static`` portion of the URL space is
    served. The value is a directory name (``~username`` is allowed, and
    non-absolute names are interpreted relative to the node's basedir), which
    can contain HTML and other files. This can be used to serve a
    Javascript-based frontend to the Tahoe-LAFS node, or other services.

    The default value is "``public_html``", which will serve
    ``BASEDIR/public_html`` .  With the default settings,
    ``http://127.0.0.1:3456/static/foo.html`` will serve the contents of
    ``BASEDIR/public_html/foo.html`` .

``tub.port = (endpoint specification strings or "disabled", optional)``

    This controls which port the node uses to accept Foolscap connections
    from other nodes. It is parsed as a comma-separated list of Twisted
    "server endpoint descriptor" strings, each of which is a value like
    ``tcp:12345`` and ``tcp:23456:interface=127.0.0.1``.

    To listen on multiple ports at once (e.g. both TCP-on-IPv4 and TCP-on-IPv6),
    use something like ``tcp6:interface=2600\:3c01\:f03c\:91ff\:fe93\:d272:3456,tcp:interface=8.8.8.8:3456``.
    Lists of endpoint descriptor strings like the following ``tcp:12345,tcp6:12345``
    are known to not work because an ``Address already in use.`` error.

    If ``tub.port`` is the string ``disabled``, the node will not listen at
    all, and thus cannot accept connections from other nodes. If ``[storage]
    enabled = true``, or ``[helper] enabled = true``, or the node is an
    Introducer, then it is an error to have ``tub.port`` be empty. If
    ``tub.port`` is disabled, then ``tub.location`` must also be disabled,
    and vice versa.

    For backwards compatibility, if this contains a simple integer, it will
    be used as a TCP port number, like ``tcp:%d`` (which will accept
    connections on all interfaces). However ``tub.port`` cannot be ``0`` or
    ``tcp:0`` (older versions accepted this, but the node is no longer
    willing to ask Twisted to allocate port numbers in this way). If
    ``tub.port`` is present, it may not be empty.

    If the ``tub.port`` config key is not provided (e.g. ``tub.port`` appears
    nowhere in the ``[node]`` section, or is commented out), the node will
    look in ``BASEDIR/client.port`` (or ``BASEDIR/introducer.port``, for
    introducers) for the descriptor that was used last time.

    If neither ``tub.port`` nor the port file is available, the node will ask
    the kernel to allocate any available port (the moral equivalent of
    ``tcp:0``). The allocated port number will be written into a descriptor
    string in ``BASEDIR/client.port`` (or ``introducer.port``), so that
    subsequent runs will re-use the same port.

``tub.location = (hint string or "disabled", optional)``

    In addition to running as a client, each Tahoe-LAFS node can also run as
    a server, listening for connections from other Tahoe-LAFS clients. The
    node announces its location by publishing a "FURL" (a string with some
    connection hints) to the Introducer. The string it publishes can be found
    in ``BASEDIR/private/storage.furl`` . The ``tub.location`` configuration
    controls what location is published in this announcement.

    If your node is meant to run as a server, you should fill this in, using
    a hostname or IP address that is reachable from your intended clients.

    If ``tub.port`` is set to ``disabled``, then ``tub.location`` must also
    be ``disabled``.

    If you don't provide ``tub.location``, the node will try to figure out a
    useful one by itself, by using tools like "``ifconfig``" to determine the
    set of IP addresses on which it can be reached from nodes both near and
    far. It will also include the TCP port number on which it is listening
    (either the one specified by ``tub.port``, or whichever port was assigned
    by the kernel when ``tub.port`` is left unspecified). However this
    automatic address-detection is discouraged, and will probably be removed
    from a future release. It will include the ``127.0.0.1`` "localhost"
    address (which is only useful to clients running on the same computer),
    and RFC1918 private-network addresses like ``10.*.*.*`` and
    ``192.168.*.*`` (which are only useful to clients on the local LAN). In
    general, the automatically-detected IP addresses will only be useful if
    the node has a public IP address, such as a VPS or colo-hosted server.

    You will certainly need to set ``tub.location`` if your node lives behind
    a firewall that is doing inbound port forwarding, or if you are using
    other proxies such that the local IP address or port number is not the
    same one that remote clients should use to connect. You might also want
    to control this when using a Tor proxy to avoid revealing your actual IP
    address through the Introducer announcement.

    If ``tub.location`` is specified, by default it entirely replaces the
    automatically determined set of IP addresses. To include the automatically
    determined addresses as well as the specified ones, include the uppercase
    string "``AUTO``" in the list.

    The value is a comma-separated string of method:host:port location hints,
    like this::

      tcp:123.45.67.89:8098,tcp:tahoe.example.com:8098,tcp:127.0.0.1:8098

    A few examples:

    * Don't listen at all (client-only mode)::

        tub.port = disabled
        tub.location = disabled

    * Use a DNS name so you can change the IP address more easily::

        tub.port = tcp:8098
        tub.location = tcp:tahoe.example.com:8098

    * Run a node behind a firewall (which has an external IP address) that
      has been configured to forward external port 7912 to our internal
      node's port 8098::

        tub.port = tcp:8098
        tub.location = tcp:external-firewall.example.com:7912

    * Emulate default behavior, assuming your host has public IP address of
      123.45.67.89, and the kernel-allocated port number was 8098::

        tub.port = tcp:8098
        tub.location = tcp:123.45.67.89:8098,tcp:127.0.0.1:8098

    * Use a DNS name but also include the default set of addresses::

        tub.port = tcp:8098
        tub.location = tcp:tahoe.example.com:8098,AUTO

    * Run a node behind a Tor proxy (perhaps via ``torsocks``), in
      client-only mode (i.e. we can make outbound connections, but other
      nodes will not be able to connect to us). The literal
      '``unreachable.example.org``' will not resolve, but will serve as a
      reminder to human observers that this node cannot be reached. "Don't
      call us.. we'll call you"::

        tub.port = tcp:8098
        tub.location = tcp:unreachable.example.org:0

    * Run a node behind a Tor proxy, and make the server available as a Tor
      "hidden service". (This assumes that other clients are running their
      node with ``torsocks``, such that they are prepared to connect to a
      ``.onion`` address.) The hidden service must first be configured in
      Tor, by giving it a local port number and then obtaining a ``.onion``
      name, using something in the ``torrc`` file like::

        HiddenServiceDir /var/lib/tor/hidden_services/tahoe
        HiddenServicePort 29212 127.0.0.1:8098

      once Tor is restarted, the ``.onion`` hostname will be in
      ``/var/lib/tor/hidden_services/tahoe/hostname``. Then set up your
      ``tahoe.cfg`` like::

        tub.port = tcp:8098
        tub.location = tor:ualhejtq2p7ohfbb.onion:29212

``log_gatherer.furl = (FURL, optional)``

    If provided, this contains a single FURL string that is used to contact a
    "log gatherer", which will be granted access to the logport. This can be
    used to gather operational logs in a single place. Note that in previous
    releases of Tahoe-LAFS, if an old-style ``BASEDIR/log_gatherer.furl``
    file existed it would also be used in addition to this value, allowing
    multiple log gatherers to be used at once. As of Tahoe-LAFS v1.9.0, an
    old-style file is ignored and a warning will be emitted if one is
    detected. This means that as of Tahoe-LAFS v1.9.0 you can have at most
    one log gatherer per node. See ticket `#1423`_ about lifting this
    restriction and letting you have multiple log gatherers.

    .. _`#1423`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1423

``timeout.keepalive = (integer in seconds, optional)``

``timeout.disconnect = (integer in seconds, optional)``

    If ``timeout.keepalive`` is provided, it is treated as an integral number
    of seconds, and sets the Foolscap "keepalive timer" to that value. For
    each connection to another node, if nothing has been heard for a while,
    we will attempt to provoke the other end into saying something. The
    duration of silence that passes before sending the PING will be between
    KT and 2*KT. This is mainly intended to keep NAT boxes from expiring idle
    TCP sessions, but also gives TCP's long-duration keepalive/disconnect
    timers some traffic to work with. The default value is 240 (i.e. 4
    minutes).

    If timeout.disconnect is provided, this is treated as an integral number
    of seconds, and sets the Foolscap "disconnect timer" to that value. For
    each connection to another node, if nothing has been heard for a while,
    we will drop the connection. The duration of silence that passes before
    dropping the connection will be between DT-2*KT and 2*DT+2*KT (please see
    ticket `#521`_ for more details). If we are sending a large amount of
    data to the other end (which takes more than DT-2*KT to deliver), we
    might incorrectly drop the connection. The default behavior (when this
    value is not provided) is to disable the disconnect timer.

    See ticket `#521`_ for a discussion of how to pick these timeout values.
    Using 30 minutes means we'll disconnect after 22 to 68 minutes of
    inactivity. Receiving data will reset this timeout, however if we have
    more than 22min of data in the outbound queue (such as 800kB in two
    pipelined segments of 10 shares each) and the far end has no need to
    contact us, our ping might be delayed, so we may disconnect them by
    accident.

    .. _`#521`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/521

``tempdir = (string, optional)``

    This specifies a temporary directory for the web-API server to use, for
    holding large files while they are being uploaded. If a web-API client
    attempts to upload a 10GB file, this tempdir will need to have at least
    10GB available for the upload to complete.

    The default value is the ``tmp`` directory in the node's base directory
    (i.e. ``BASEDIR/tmp``), but it can be placed elsewhere. This directory is
    used for files that usually (on a Unix system) go into ``/tmp``. The
    string will be interpreted relative to the node's base directory.

``reveal-IP-address = (boolean, optional, defaults to True)``

    This is a safety flag. When set to False (aka "private mode"), the node
    will refuse to start if any of the other configuration options would
    reveal the node's IP address to servers or the external network. This
    flag does not directly affect the node's behavior: its only power is to
    veto node startup when something looks unsafe.

    The default is True (non-private mode), because setting it to False
    requires the installation of additional libraries (use ``pip install
    tahoe-lafs[tor]`` and/or ``pip install tahoe-lafs[i2p]`` to get them) as
    well as additional non-python software (Tor/I2P daemons). Performance is
    also generally reduced when operating in private mode.

    When False, any of the following configuration problems will cause
    ``tahoe start`` to throw a PrivacyError instead of starting the node:

    * ``[node] tub.location`` contains any ``tcp:`` hints

    * ``[node] tub.location`` uses ``AUTO``, or is missing/empty (because
      that defaults to AUTO)

    * ``[connections] tcp =`` is set to ``tcp`` (or left as the default),
      rather than being set to ``tor`` or ``disabled``


.. _Connection Management:

Connection Management
=====================

Three sections (``[tor]``, ``[i2p]``, and ``[connections]``) control how the
Tahoe node makes outbound connections. Tor and I2P are configured here. This
also controls when Tor and I2P are used: for all TCP connections (to hide
your IP address), or only when necessary (just for servers which declare that
they need Tor, because they use ``.onion`` addresses).

Note that if you want to protect your node's IP address, you should set
``[node] reveal-IP-address = False``, which will refuse to launch the node if
any of the other configuration settings might violate this privacy property.

``[connections]``
-----------------

This section controls *when* Tor and I2P are used. The ``[tor]`` and
``[i2p]`` sections (described later) control *how* Tor/I2P connections are
managed.

All Tahoe nodes need to make a connection to the Introducer; the ``[client]
introducer.furl`` setting (described below) indicates where the Introducer
lives. Tahoe client nodes must also make connections to storage servers:
these targets are specified in announcements that come from the Introducer.
Both are expressed as FURLs (a Foolscap URL), which include a list of
"connection hints". Each connection hint describes one (of perhaps many)
network endpoints where the service might live.

Connection hints include a type, and look like:

* ``tcp:tahoe.example.org:12345``
* ``tor:u33m4y7klhz3b.onion:1000``
* ``i2p:c2ng2pbrmxmlwpijn``

``tor`` hints are always handled by the ``tor`` handler (configured in the
``[tor]`` section, described below). Likewise, ``i2p`` hints are always
routed to the ``i2p`` handler. But either will be ignored if Tahoe was not
installed with the necessary Tor/I2P support libraries, or if the Tor/I2P
daemon is unreachable.

The ``[connections]`` section lets you control how ``tcp`` hints are handled.
By default, they use the normal TCP handler, which just makes direct
connections (revealing your node's IP address to both the target server and
the intermediate network). The node behaves this way if the ``[connections]``
section is missing entirely, or if it looks like this::

  [connections]
   tcp = tcp

To hide the Tahoe node's IP address from the servers that it uses, set the
``[connections]`` section to use Tor for TCP hints::

  [connections]
   tcp = tor

You can also disable TCP hints entirely, which would be appropriate when
running an I2P-only node::

  [connections]
   tcp = disabled

(Note that I2P does not support connections to normal TCP ports, so
``[connections] tcp = i2p`` is invalid)

In the future, Tahoe services may be changed to live on HTTP/HTTPS URLs
instead of Foolscap. In that case, connections will be made using whatever
handler is configured for ``tcp`` hints. So the same ``tcp = tor``
configuration will work.

``[tor]``
---------

This controls how Tor connections are made. The defaults (all empty) mean
that, when Tor is needed, the node will try to connect to a Tor daemon's
SOCKS proxy on localhost port 9050 or 9150. Port 9050 is the default Tor
SOCKS port, so it should be available under any system Tor instance (e.g. the
one launched at boot time when the standard Debian ``tor`` package is
installed). Port 9150 is the SOCKS port for the Tor Browser Bundle, so it
will be available any time the TBB is running.

You can set ``launch = True`` to cause the Tahoe node to launch a new Tor
daemon when it starts up (and kill it at shutdown), if you don't have a
system-wide instance available. Note that it takes 30-60 seconds for Tor to
get running, so using a long-running Tor process may enable a faster startup.
If your Tor executable doesn't live on ``$PATH``, use ``tor.executable=`` to
specify it.

``[tor]``

``enabled = (boolean, optional, defaults to True)``

    If False, this will disable the use of Tor entirely. The default of True
    means the node will use Tor, if necessary, and if possible.

``socks.port = (string, optional, endpoint specification string, defaults to empty)``

    This tells the node that Tor connections should be routed to a SOCKS
    proxy listening on the given endpoint. The default (of an empty value)
    will cause the node to first try localhost port 9050, then if that fails,
    try localhost port 9150. These are the default listening ports of the
    standard Tor daemon, and the Tor Browser Bundle, respectively.

    While this nominally accepts an arbitrary endpoint string, internal
    limitations prevent it from accepting anything but ``tcp:HOST:PORT``
    (unfortunately, unix-domain sockets are not yet supported). See ticket
    #2813 for details. Also note that using a HOST of anything other than
    localhost is discouraged, because you would be revealing your IP address
    to external (and possibly hostile) machines.

``control.port = (string, optional, endpoint specification string)``

    This tells the node to connect to a pre-existing Tor daemon on the given
    control port (which is typically ``unix://var/run/tor/control`` or
    ``tcp:localhost:9051``). The node will then ask Tor what SOCKS port it is
    using, and route Tor connections to that.

``launch = (bool, optional, defaults to False)``

    If True, the node will spawn a new (private) copy of Tor at startup, and
    will kill it at shutdown. The new Tor will be given a persistent state
    directory under ``NODEDIR/private/``, where Tor's microdescriptors will
    be cached, to speed up subsequent startup.

``tor.executable = (string, optional, defaults to empty)``

    This controls which Tor executable is used when ``launch = True``. If
    empty, the first executable program named ``tor`` found on ``$PATH`` will
    be used.

There are 5 valid combinations of these configuration settings:

* 1: ``(empty)``: use SOCKS on port 9050/9150
* 2: ``launch = true``: launch a new Tor
* 3: ``socks.port = tcp:HOST:PORT``: use an existing Tor on the given SOCKS port
* 4: ``control.port = ENDPOINT``: use an existing Tor at the given control port
* 5: ``enabled = false``: no Tor at all

1 is the default, and should work for any Linux host with the system Tor
package installed. 2 should work on any box with Tor installed into $PATH,
but will take an extra 30-60 seconds at startup. 3 and 4 can be used for
specialized installations, where Tor is already running, but not listening on
the default port. 5 should be used in environments where Tor is installed,
but should not be used (perhaps due to a site-wide policy).

Note that Tor support depends upon some additional Python libraries. To
install Tahoe with Tor support, use ``pip install tahoe-lafs[tor]``.

``[i2p]``
---------

This controls how I2P connections are made. Like with Tor, the all-empty
defaults will cause I2P connections to be routed to a pre-existing I2P daemon
on port 7656. This is the default SAM port for the ``i2p`` daemon.


``[i2p]``

``enabled = (boolean, optional, defaults to True)``

    If False, this will disable the use of I2P entirely. The default of True
    means the node will use I2P, if necessary, and if possible.

``sam.port = (string, optional, endpoint descriptor, defaults to empty)``

    This tells the node that I2P connections should be made via the SAM
    protocol on the given port. The default (of an empty value) will cause
    the node to try localhost port 7656. This is the default listening port
    of the standard I2P daemon.

``launch = (bool, optional, defaults to False)``

    If True, the node will spawn a new (private) copy of I2P at startup, and
    will kill it at shutdown. The new I2P will be given a persistent state
    directory under ``NODEDIR/private/``, where I2P's microdescriptors will
    be cached, to speed up subsequent startup. The daemon will allocate its
    own SAM port, which will be queried from the config directory.

``i2p.configdir = (string, optional, directory)``

    This tells the node to parse an I2P config file in the given directory,
    and use the SAM port it finds there. If ``launch = True``, the new I2P
    daemon will be told to use the given directory (which can be
    pre-populated with a suitable config file). If ``launch = False``, we
    assume there is a pre-running I2P daemon running from this directory, and
    can again parse the config file for the SAM port.

``i2p.executable = (string, optional, defaults to empty)``

    This controls which I2P executable is used when ``launch = True``. If
    empty, the first executable program named ``i2p`` found on ``$PATH`` will
    be used.


.. _Client Configuration:

Client Configuration
====================

``[client]``

``introducer.furl = (FURL string, mandatory)``

    This FURL tells the client how to connect to the introducer. Each
    Tahoe-LAFS grid is defined by an introducer. The introducer's FURL is
    created by the introducer node and written into its private base
    directory when it starts, whereupon it should be published to everyone
    who wishes to attach a client to that grid

``helper.furl = (FURL string, optional)``

    If provided, the node will attempt to connect to and use the given helper
    for uploads. See :doc:`helper` for details.

``stats_gatherer.furl = (FURL string, optional)``

    If provided, the node will connect to the given stats gatherer and
    provide it with operational statistics.

``shares.needed = (int, optional) aka "k", default 3``

``shares.total = (int, optional) aka "N", N >= k, default 10``

``shares.happy = (int, optional) 1 <= happy <= N, default 7``

    These three values set the default encoding parameters. Each time a new
    file is uploaded, erasure-coding is used to break the ciphertext into
    separate shares. There will be ``N`` (i.e. ``shares.total``) shares
    created, and the file will be recoverable if any ``k``
    (i.e. ``shares.needed``) shares are retrieved. The default values are
    3-of-10 (i.e.  ``shares.needed = 3``, ``shares.total = 10``). Setting
    ``k`` to 1 is equivalent to simple replication (uploading ``N`` copies of
    the file).

    These values control the tradeoff between storage overhead and
    reliability. To a first approximation, a 1MB file will use (1MB *
    ``N``/``k``) of backend storage space (the actual value will be a bit
    more, because of other forms of overhead). Up to ``N``-``k`` shares can
    be lost before the file becomes unrecoverable.  So large ``N``/``k``
    ratios are more reliable, and small ``N``/``k`` ratios use less disk
    space. ``N`` cannot be larger than 256, because of the 8-bit
    erasure-coding algorithm that Tahoe-LAFS uses. ``k`` can not be greater
    than ``N``. See :doc:`performance` for more details.

    ``shares.happy`` allows you control over how well to "spread out" the
    shares of an immutable file. For a successful upload, shares are
    guaranteed to be initially placed on at least ``shares.happy`` distinct
    servers, the correct functioning of any ``k`` of which is sufficient to
    guarantee the availability of the uploaded file. This value should not be
    larger than the number of servers on your grid.

    A value of ``shares.happy`` <= ``k`` is allowed, but this is not
    guaranteed to provide any redundancy if some servers fail or lose shares.
    It may still provide redundancy in practice if ``N`` is greater than
    the number of connected servers, because in that case there will typically
    be more than one share on at least some storage nodes. However, since a
    successful upload only guarantees that at least ``shares.happy`` shares
    have been stored, the worst case is still that there is no redundancy.

    (Mutable files use a different share placement algorithm that does not
    currently consider this parameter.)

``mutable.format = sdmf or mdmf``

    This value tells Tahoe-LAFS what the default mutable file format should
    be. If ``mutable.format=sdmf``, then newly created mutable files will be
    in the old SDMF format. This is desirable for clients that operate on
    grids where some peers run older versions of Tahoe-LAFS, as these older
    versions cannot read the new MDMF mutable file format. If
    ``mutable.format`` is ``mdmf``, then newly created mutable files will use
    the new MDMF format, which supports efficient in-place modification and
    streaming downloads. You can overwrite this value using a special
    mutable-type parameter in the webapi. If you do not specify a value here,
    Tahoe-LAFS will use SDMF for all newly-created mutable files.

    Note that this parameter applies only to files, not to directories.
    Mutable directories, which are stored in mutable files, are not
    controlled by this parameter and will always use SDMF. We may revisit
    this decision in future versions of Tahoe-LAFS.

    See :doc:`specifications/mutable` for details about mutable file formats.

``peers.preferred = (string, optional)``

    This is an optional comma-separated list of Node IDs of servers that will
    be tried first when selecting storage servers for reading or writing.

    Servers should be identified here by their Node ID as it appears in the web
    ui, underneath the server's nickname. For storage servers running tahoe
    versions >=1.10 (if the introducer is also running tahoe >=1.10) this will
    be a "Node Key" (which is prefixed with 'v0-'). For older nodes, it will be
    a TubID instead. When a preferred server (and/or the introducer) is
    upgraded to 1.10 or later, clients must adjust their configs accordingly.

    Every node selected for upload, whether preferred or not, will still
    receive the same number of shares (one, if there are ``N`` or more servers
    accepting uploads). Preferred nodes are simply moved to the front of the
    server selection lists computed for each file.

    This is useful if a subset of your nodes have different availability or
    connectivity characteristics than the rest of the grid. For instance, if
    there are more than ``N`` servers on the grid, and ``K`` or more of them
    are at a single physical location, it would make sense for clients at that
    location to prefer their local servers so that they can maintain access to
    all of their uploads without using the internet.


Frontend Configuration
======================

The Tahoe client process can run a variety of frontend file-access protocols.
You will use these to create and retrieve files from the virtual filesystem.
Configuration details for each are documented in the following
protocol-specific guides:

HTTP

    Tahoe runs a webserver by default on port 3456. This interface provides a
    human-oriented "WUI", with pages to create, modify, and browse
    directories and files, as well as a number of pages to check on the
    status of your Tahoe node. It also provides a machine-oriented "WAPI",
    with a REST-ful HTTP interface that can be used by other programs
    (including the CLI tools). Please see :doc:`frontends/webapi` for full
    details, and the ``web.port`` and ``web.static`` config variables above.
    :doc:`frontends/download-status` also describes a few WUI status pages.

CLI

    The main ``tahoe`` executable includes subcommands for manipulating the
    filesystem, uploading/downloading files, and creating/running Tahoe
    nodes. See :doc:`frontends/CLI` for details.

SFTP, FTP

    Tahoe can also run both SFTP and FTP servers, and map a username/password
    pair to a top-level Tahoe directory. See :doc:`frontends/FTP-and-SFTP`
    for instructions on configuring these services, and the ``[sftpd]`` and
    ``[ftpd]`` sections of ``tahoe.cfg``.

Magic Folder

    A node running on Linux or Windows can be configured to automatically
    upload files that are created or changed in a specified local directory.
    See :doc:`frontends/magic-folder` for details.


Storage Server Configuration
============================

``[storage]``

``enabled = (boolean, optional)``

    If this is ``True``, the node will run a storage server, offering space
    to other clients. If it is ``False``, the node will not run a storage
    server, meaning that no shares will be stored on this node. Use ``False``
    for clients who do not wish to provide storage service. The default value
    is ``True``.

``readonly = (boolean, optional)``

    If ``True``, the node will run a storage server but will not accept any
    shares, making it effectively read-only. Use this for storage servers
    that are being decommissioned: the ``storage/`` directory could be
    mounted read-only, while shares are moved to other servers. Note that
    this currently only affects immutable shares. Mutable shares (used for
    directories) will be written and modified anyway. See ticket `#390`_ for
    the current status of this bug. The default value is ``False``.

``reserved_space = (str, optional)``

    If provided, this value defines how much disk space is reserved: the
    storage server will not accept any share that causes the amount of free
    disk space to drop below this value. (The free space is measured by a
    call to ``statvfs(2)`` on Unix, or ``GetDiskFreeSpaceEx`` on Windows, and
    is the space available to the user account under which the storage server
    runs.)

    This string contains a number, with an optional case-insensitive scale
    suffix, optionally followed by "B" or "iB". The supported scale suffixes
    are "K", "M", "G", "T", "P" and "E", and a following "i" indicates to use
    powers of 1024 rather than 1000. So "100MB", "100 M", "100000000B",
    "100000000", and "100000kb" all mean the same thing. Likewise, "1MiB",
    "1024KiB", "1024 Ki", and "1048576 B" all mean the same thing.

    "``tahoe create-node``" generates a tahoe.cfg with
    "``reserved_space=1G``", but you may wish to raise, lower, or remove the
    reservation to suit your needs.

``expire.enabled =``

``expire.mode =``

``expire.override_lease_duration =``

``expire.cutoff_date =``

``expire.immutable =``

``expire.mutable =``

    These settings control garbage collection, in which the server will
    delete shares that no longer have an up-to-date lease on them. Please see
    :doc:`garbage-collection` for full details.

.. _#390: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/390


Running A Helper
================

A "helper" is a regular client node that also offers the "upload helper"
service.

``[helper]``

``enabled = (boolean, optional)``

    If ``True``, the node will run a helper (see :doc:`helper` for details).
    The helper's contact FURL will be placed in ``private/helper.furl``, from
    which it can be copied to any clients that wish to use it. Clearly nodes
    should not both run a helper and attempt to use one: do not create
    ``helper.furl`` and also define ``[helper]enabled`` in the same node. The
    default is ``False``.


Running An Introducer
=====================

The introducer node uses a different ``.tac`` file (named
"``introducer.tac``"), and pays attention to the ``[node]`` section, but not
the others.

The Introducer node maintains some different state than regular client nodes.

``BASEDIR/private/introducer.furl``

  This is generated the first time the introducer node is started, and used
  again on subsequent runs, to give the introduction service a persistent
  long-term identity. This file should be published and copied into new
  client nodes before they are started for the first time.


Other Files in BASEDIR
======================

Some configuration is not kept in ``tahoe.cfg``, for the following reasons:

* it doesn't fit into the INI format of ``tahoe.cfg`` (e.g.
  ``private/servers.yaml``)
* it is generated by the node at startup, e.g. encryption keys. The node
  never writes to ``tahoe.cfg``.
* it is generated by user action, e.g. the "``tahoe create-alias``" command.

In addition, non-configuration persistent state is kept in the node's base
directory, next to the configuration knobs.

This section describes these other files.

``private/node.pem``

  This contains an SSL private-key certificate. The node generates this the
  first time it is started, and re-uses it on subsequent runs. This
  certificate allows the node to have a cryptographically-strong identifier
  (the Foolscap "TubID"), and to establish secure connections to other nodes.

``storage/``

  Nodes that host StorageServers will create this directory to hold shares of
  files on behalf of other clients. There will be a directory underneath it
  for each StorageIndex for which this node is holding shares. There is also
  an "incoming" directory where partially-completed shares are held while
  they are being received.

``tahoe-client.tac``

  This file defines the client, by constructing the actual Client instance
  each time the node is started. It is used by the "``twistd``" daemonization
  program (in the ``-y`` mode), which is run internally by the "``tahoe
  start``" command. This file is created by the "``tahoe create-node``" or
  "``tahoe create-client``" commands.

``tahoe-introducer.tac``

  This file is used to construct an introducer, and is created by the
  "``tahoe create-introducer``" command.

``tahoe-stats-gatherer.tac``

  This file is used to construct a statistics gatherer, and is created by the
  "``tahoe create-stats-gatherer``" command.

``private/control.furl``

  This file contains a FURL that provides access to a control port on the
  client node, from which files can be uploaded and downloaded. This file is
  created with permissions that prevent anyone else from reading it (on
  operating systems that support such a concept), to insure that only the
  owner of the client node can use this feature. This port is intended for
  debugging and testing use.

``private/logport.furl``

  This file contains a FURL that provides access to a 'log port' on the
  client node, from which operational logs can be retrieved. Do not grant
  logport access to strangers, because occasionally secret information may be
  placed in the logs.

``private/helper.furl``

  If the node is running a helper (for use by other clients), its contact
  FURL will be placed here. See :doc:`helper` for more details.

``private/root_dir.cap`` (optional)

  The command-line tools will read a directory cap out of this file and use
  it, if you don't specify a '--dir-cap' option or if you specify
  '--dir-cap=root'.

``private/convergence`` (automatically generated)

  An added secret for encrypting immutable files. Everyone who has this same
  string in their ``private/convergence`` file encrypts their immutable files
  in the same way when uploading them. This causes identical files to
  "converge" -- to share the same storage space since they have identical
  ciphertext -- which conserves space and optimizes upload time, but it also
  exposes file contents to the possibility of a brute-force attack by people
  who know that string. In this attack, if the attacker can guess most of the
  contents of a file, then they can use brute-force to learn the remaining
  contents.

  So the set of people who know your ``private/convergence`` string is the
  set of people who converge their storage space with you when you and they
  upload identical immutable files, and it is also the set of people who
  could mount such an attack.

  The content of the ``private/convergence`` file is a base-32 encoded
  string.  If the file doesn't exist, then when the Tahoe-LAFS client starts
  up it will generate a random 256-bit string and write the base-32 encoding
  of this string into the file. If you want to converge your immutable files
  with as many people as possible, put the empty string (so that
  ``private/convergence`` is a zero-length file).

Additional Introducer Definitions
=================================

The ``private/introducers.yaml`` file defines additional Introducers. The
first introducer is defined in ``tahoe.cfg``, in ``[client]
introducer.furl``. To use two or more Introducers, choose a locally-unique
"petname" for each one, then define their FURLs in
``private/introducers.yaml`` like this::

  introducers:
    petname2:
      furl: FURL2
    petname3:
      furl: FURL3

Servers will announce themselves to all configured introducers. Clients will
merge the announcements they receive from all introducers. Nothing will
re-broadcast an announcement (i.e. telling introducer 2 about something you
heard from introducer 1).

If you omit the introducer definitions from both ``tahoe.cfg`` and
``introducers.yaml``, the node will not use an Introducer at all. Such
"introducerless" clients must be configured with static servers (described
below), or they will not be able to upload and download files.

Static Server Definitions
=========================

The ``private/servers.yaml`` file defines "static servers": those which are
not announced through the Introducer. This can also control how we connect to
those servers.

Most clients do not need this file. It is only necessary if you want to use
servers which are (for some specialized reason) not announced through the
Introducer, or to connect to those servers in different ways. You might do
this to "freeze" the server list: use the Introducer for a while, then copy
all announcements into ``servers.yaml``, then stop using the Introducer
entirely. Or you might have a private server that you don't want other users
to learn about (via the Introducer). Or you might run a local server which is
announced to everyone else as a Tor onion address, but which you can connect
to directly (via TCP).

The file syntax is `YAML`_, with a top-level dictionary named ``storage``.
Other items may be added in the future.

The ``storage`` dictionary takes keys which are server-ids, and values which
are dictionaries with two keys: ``ann`` and ``connections``. The ``ann``
value is a dictionary which will be used in lieu of the introducer
announcement, so it can be populated by copying the ``ann`` dictionary from
``NODEDIR/introducer_cache.yaml``.

The server-id can be any string, but ideally you should use the public key as
published by the server. Each server displays this as "Node ID:" in the
top-right corner of its "WUI" web welcome page. It can also be obtained from
other client nodes, which record it as ``key_s:`` in their
``introducer_cache.yaml`` file. The format is "v0-" followed by 52 base32
characters like so::

  v0-c2ng2pbrmxmlwpijn3mr72ckk5fmzk6uxf6nhowyosaubrt6y5mq

The ``ann`` dictionary really only needs one key:

* ``anonymous-storage-FURL``: how we connect to the server

(note that other important keys may be added in the future, as Accounting and
HTTP-based servers are implemented)

Optional keys include:

* ``nickname``: the name of this server, as displayed on the Welcome page
  server list
* ``permutation-seed-base32``: this controls how shares are mapped to
  servers. This is normally computed from the server-ID, but can be
  overridden to maintain the mapping for older servers which used to use
  Foolscap TubIDs as server-IDs. If your selected server-ID cannot be parsed
  as a public key, it will be hashed to compute the permutation seed. This is
  fine as long as all clients use the same thing, but if they don't, then
  your client will disagree with the other clients about which servers should
  hold each share. This will slow downloads for everybody, and may cause
  additional work or consume extra storage when repair operations don't
  converge.
* anything else from the ``introducer_cache.yaml`` announcement, like
  ``my-version``, which is displayed on the Welcome page server list

For example, a private static server could be defined with a
``private/servers.yaml`` file like this::

  storage:
    v0-4uazse3xb6uu5qpkb7tel2bm6bpea4jhuigdhqcuvvse7hugtsia:
      ann:
        nickname: my-server-1
        anonymous-storage-FURL: pb://u33m4y7klhz3bypswqkozwetvabelhxt@tcp:8.8.8.8:51298/eiu2i7p6d6mm4ihmss7ieou5hac3wn6b

Or, if you're feeling really lazy::

  storage:
    my-serverid-1:
      ann:
        anonymous-storage-FURL: pb://u33m4y7klhz3bypswqkozwetvabelhxt@tcp:8.8.8.8:51298/eiu2i7p6d6mm4ihmss7ieou5hac3wn6b

.. _YAML: http://yaml.org/

Overriding Connection-Handlers for Static Servers
-------------------------------------------------

A ``connections`` entry will override the default connection-handler mapping
(as established by ``tahoe.cfg [connections]``). This can be used to build a
"Tor-mostly client": one which is restricted to use Tor for all connections,
except for a few private servers to which normal TCP connections will be
made. To override the published announcement (and thus avoid connecting twice
to the same server), the server ID must exactly match.

``tahoe.cfg``::

  [connections]
   # this forces the use of Tor for all "tcp" hints
   tcp = tor

``private/servers.yaml``::

  storage:
    v0-c2ng2pbrmxmlwpijn3mr72ckk5fmzk6uxf6nhowyosaubrt6y5mq:
      ann:
        nickname: my-server-1
        anonymous-storage-FURL: pb://u33m4y7klhz3bypswqkozwetvabelhxt@tcp:10.1.2.3:51298/eiu2i7p6d6mm4ihmss7ieou5hac3wn6b
      connections:
        # this overrides the tcp=tor from tahoe.cfg, for just this server
        tcp: tcp

The ``connections`` table is needed to override the ``tcp = tor`` mapping
that comes from ``tahoe.cfg``. Without it, the client would attempt to use
Tor to connect to ``10.1.2.3``, which would fail because it is a
local/non-routeable (RFC1918) address.


Other files
===========

``logs/``

  Each Tahoe-LAFS node creates a directory to hold the log messages produced
  as the node runs. These logfiles are created and rotated by the
  "``twistd``" daemonization program, so ``logs/twistd.log`` will contain the
  most recent messages, ``logs/twistd.log.1`` will contain the previous ones,
  ``logs/twistd.log.2`` will be older still, and so on. ``twistd`` rotates
  logfiles after they grow beyond 1MB in size. If the space consumed by
  logfiles becomes troublesome, they should be pruned: a cron job to delete
  all files that were created more than a month ago in this ``logs/``
  directory should be sufficient.

``my_nodeid``

  this is written by all nodes after startup, and contains a base32-encoded
  (i.e. human-readable) NodeID that identifies this specific node. This
  NodeID is the same string that gets displayed on the web page (in the
  "which peers am I connected to" list), and the shortened form (the first
  few characters) is recorded in various log messages.

``access.blacklist``

  Gateway nodes may find it necessary to prohibit access to certain
  files. The web-API has a facility to block access to filecaps by their
  storage index, returning a 403 "Forbidden" error instead of the original
  file. For more details, see the "Access Blacklist" section of
  :doc:`frontends/webapi`.


Example
=======

The following is a sample ``tahoe.cfg`` file, containing values for some of
the keys described in the previous section. Note that this is not a
recommended configuration (most of these are not the default values), merely
a legal one.

::

  [node]
  nickname = Bob's Tahoe-LAFS Node
  tub.port = tcp:34912
  tub.location = tcp:123.45.67.89:8098,tcp:44.55.66.77:8098
  web.port = tcp:3456
  log_gatherer.furl = pb://soklj4y7eok5c3xkmjeqpw@192.168.69.247:44801/eqpwqtzm
  timeout.keepalive = 240
  timeout.disconnect = 1800
  
  [client]
  introducer.furl = pb://ok45ssoklj4y7eok5c3xkmj@tcp:tahoe.example:44801/ii3uumo
  helper.furl = pb://ggti5ssoklj4y7eok5c3xkmj@tcp:helper.tahoe.example:7054/kk8lhr
  
  [storage]
  enabled = True
  readonly = True
  reserved_space = 10000000000
  
  [helper]
  enabled = True


Old Configuration Files
=======================

Tahoe-LAFS releases before v1.3.0 had no ``tahoe.cfg`` file, and used
distinct files for each item. This is no longer supported and if you have
configuration in the old format you must manually convert it to the new
format for Tahoe-LAFS to detect it. See :doc:`historical/configuration`.
