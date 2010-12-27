=============================
Configuring a Tahoe-LAFS node
=============================

1.  `Overall Node Configuration`_
2.  `Client Configuration`_
3.  `Storage Server Configuration`_
4.  `Running A Helper`_
5.  `Running An Introducer`_
6.  `Other Files in BASEDIR`_
7.  `Other files`_
8.  `Backwards Compatibility Files`_
9.  `Example`_

A Tahoe-LAFS node is configured by writing to files in its base directory. These
files are read by the node when it starts, so each time you change them, you
need to restart the node.

The node also writes state to its base directory, so it will create files on
its own.

This document contains a complete list of the config files that are examined
by the client node, as well as the state files that you'll observe in its
base directory.

The main file is named "``tahoe.cfg``", and is an ".INI"-style configuration
file (parsed by the Python stdlib 'ConfigParser' module: "``[name]``" section
markers, lines with "``key.subkey: value``", rfc822-style continuations). There
are also other files containing information that does not easily fit into this
format. The "``tahoe create-node``" or "``tahoe create-client``" command will
create an initial ``tahoe.cfg`` file for you. After creation, the node will
never modify the ``tahoe.cfg`` file: all persistent state is put in other files.

The item descriptions below use the following types:

``boolean``
    one of (True, yes, on, 1, False, off, no, 0), case-insensitive

``strports string``
    a Twisted listening-port specification string, like "``tcp:80``"
    or "``tcp:3456:interface=127.0.0.1``". For a full description of
    the format, see `the Twisted strports documentation
    <http://twistedmatrix.com/documents/current/api/twisted.application.strports.html>`_.

``FURL string``
    a Foolscap endpoint identifier, like
    ``pb://soklj4y7eok5c3xkmjeqpw@192.168.69.247:44801/eqpwqtzm``


Overall Node Configuration
==========================

This section controls the network behavior of the node overall: which ports
and IP addresses are used, when connections are timed out, etc. This
configuration is independent of the services that the node is offering: the
same controls are used for client and introducer nodes.

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

    This controls where the node's webserver should listen, providing
    filesystem access and node status as defined in `webapi.rst
    <frontends/webapi.rst>`_. This file contains a Twisted "strports"
    specification such as "``3456``" or "``tcp:3456:interface=127.0.0.1``".
    The "``tahoe create-node``" or "``tahoe create-client``" commands set
    the ``web.port`` to "``tcp:3456:interface=127.0.0.1``" by default; this
    is overridable by the ``--webport`` option. You can make it use SSL by
    writing "``ssl:3456:privateKey=mykey.pem:certKey=cert.pem``" instead.

    If this is not provided, the node will not run a web server.

``web.static = (string, optional)``

    This controls where the ``/static`` portion of the URL space is served. The
    value is a directory name (``~username`` is allowed, and non-absolute names
    are interpreted relative to the node's basedir), which can contain HTML
    and other files. This can be used to serve a Javascript-based frontend to
    the Tahoe-LAFS node, or other services.

    The default value is "``public_html``", which will serve ``BASEDIR/public_html`` .
    With the default settings, ``http://127.0.0.1:3456/static/foo.html`` will
    serve the contents of ``BASEDIR/public_html/foo.html`` .

``tub.port = (integer, optional)``

    This controls which port the node uses to accept Foolscap connections
    from other nodes. If not provided, the node will ask the kernel for any
    available port. The port will be written to a separate file (named
    ``client.port`` or ``introducer.port``), so that subsequent runs will
    re-use the same port.

``tub.location = (string, optional)``

    In addition to running as a client, each Tahoe-LAFS node also runs as a
    server, listening for connections from other Tahoe-LAFS clients. The node
    announces its location by publishing a "FURL" (a string with some
    connection hints) to the Introducer. The string it publishes can be found
    in ``BASEDIR/private/storage.furl`` . The ``tub.location`` configuration
    controls what location is published in this announcement.

    If you don't provide ``tub.location``, the node will try to figure out a
    useful one by itself, by using tools like "``ifconfig``" to determine the
    set of IP addresses on which it can be reached from nodes both near and far.
    It will also include the TCP port number on which it is listening (either
    the one specified by ``tub.port``, or whichever port was assigned by the
    kernel when ``tub.port`` is left unspecified).

    You might want to override this value if your node lives behind a
    firewall that is doing inbound port forwarding, or if you are using other
    proxies such that the local IP address or port number is not the same one
    that remote clients should use to connect. You might also want to control
    this when using a Tor proxy to avoid revealing your actual IP address
    through the Introducer announcement.

    The value is a comma-separated string of host:port location hints, like
    this::

      123.45.67.89:8098,tahoe.example.com:8098,127.0.0.1:8098

    A few examples:

    * Emulate default behavior, assuming your host has IP address
      123.45.67.89 and the kernel-allocated port number was 8098::

        tub.port = 8098
        tub.location = 123.45.67.89:8098,127.0.0.1:8098

    * Use a DNS name so you can change the IP address more easily::

        tub.port = 8098
        tub.location = tahoe.example.com:8098

    * Run a node behind a firewall (which has an external IP address) that
      has been configured to forward port 7912 to our internal node's port
      8098::

        tub.port = 8098
        tub.location = external-firewall.example.com:7912

    * Run a node behind a Tor proxy (perhaps via ``torsocks``), in client-only
      mode (i.e. we can make outbound connections, but other nodes will not
      be able to connect to us). The literal '``unreachable.example.org``' will
      not resolve, but will serve as a reminder to human observers that this
      node cannot be reached. "Don't call us.. we'll call you"::

        tub.port = 8098
        tub.location = unreachable.example.org:0

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

        tub.port = 8098
        tub.location = ualhejtq2p7ohfbb.onion:29212

    Most users will not need to set ``tub.location``.

    Note that the old ``advertised_ip_addresses`` file from earlier releases is
    no longer supported. Tahoe-LAFS v1.3.0 and later will ignore this file.

``log_gatherer.furl = (FURL, optional)``

    If provided, this contains a single FURL string that is used to contact
    a "log gatherer", which will be granted access to the logport. This can
    be used by centralized storage grids to gather operational logs in a
    single place. Note that when an old-style ``BASEDIR/log_gatherer.furl`` file
    exists (see `Backwards Compatibility Files`_, below), both are used. (For
    most other items, the separate config file overrides the entry in
    ``tahoe.cfg``.)

``timeout.keepalive = (integer in seconds, optional)``

``timeout.disconnect = (integer in seconds, optional)``

    If ``timeout.keepalive`` is provided, it is treated as an integral number of
    seconds, and sets the Foolscap "keepalive timer" to that value. For each
    connection to another node, if nothing has been heard for a while, we
    will attempt to provoke the other end into saying something. The duration
    of silence that passes before sending the PING will be between KT and
    2*KT. This is mainly intended to keep NAT boxes from expiring idle TCP
    sessions, but also gives TCP's long-duration keepalive/disconnect timers
    some traffic to work with. The default value is 240 (i.e. 4 minutes).

    If timeout.disconnect is provided, this is treated as an integral number
    of seconds, and sets the Foolscap "disconnect timer" to that value. For
    each connection to another node, if nothing has been heard for a while,
    we will drop the connection. The duration of silence that passes before
    dropping the connection will be between DT-2*KT and 2*DT+2*KT (please see
    ticket `#521`_ for more details). If we are sending a large amount of data
    to the other end (which takes more than DT-2*KT to deliver), we might
    incorrectly drop the connection. The default behavior (when this value is
    not provided) is to disable the disconnect timer.

    See ticket `#521`_ for a discussion of how to pick these timeout values.
    Using 30 minutes means we'll disconnect after 22 to 68 minutes of
    inactivity. Receiving data will reset this timeout, however if we have
    more than 22min of data in the outbound queue (such as 800kB in two
    pipelined segments of 10 shares each) and the far end has no need to
    contact us, our ping might be delayed, so we may disconnect them by
    accident.

    .. _`#521`: http://tahoe-lafs.org/trac/tahoe-lafs/ticket/521

``ssh.port = (strports string, optional)``

``ssh.authorized_keys_file = (filename, optional)``

    This enables an SSH-based interactive Python shell, which can be used to
    inspect the internal state of the node, for debugging. To cause the node
    to accept SSH connections on port 8022 from the same keys as the rest of
    your account, use::

      [tub]
      ssh.port = 8022
      ssh.authorized_keys_file = ~/.ssh/authorized_keys

``tempdir = (string, optional)``

    This specifies a temporary directory for the web-API server to use, for
    holding large files while they are being uploaded. If a web-API client
    attempts to upload a 10GB file, this tempdir will need to have at least
    10GB available for the upload to complete.

    The default value is the ``tmp`` directory in the node's base directory
    (i.e. ``BASEDIR/tmp``), but it can be placed elsewhere. This directory is
    used for files that usually (on a Unix system) go into ``/tmp``. The string
    will be interpreted relative to the node's base directory.

Client Configuration
====================

``[client]``

``introducer.furl = (FURL string, mandatory)``

    This FURL tells the client how to connect to the introducer. Each Tahoe-LAFS
    grid is defined by an introducer. The introducer's FURL is created by the
    introducer node and written into its base directory when it starts,
    whereupon it should be published to everyone who wishes to attach a
    client to that grid

``helper.furl = (FURL string, optional)``

    If provided, the node will attempt to connect to and use the given helper
    for uploads. See `<helper.rst>`_ for details.

``key_generator.furl = (FURL string, optional)``

    If provided, the node will attempt to connect to and use the given
    key-generator service, using RSA keys from the external process rather
    than generating its own.

``stats_gatherer.furl = (FURL string, optional)``

    If provided, the node will connect to the given stats gatherer and
    provide it with operational statistics.

``shares.needed = (int, optional) aka "k", default 3``

``shares.total = (int, optional) aka "N", N >= k, default 10``

``shares.happy = (int, optional) 1 <= happy <= N, default 7``

    These three values set the default encoding parameters. Each time a new
    file is uploaded, erasure-coding is used to break the ciphertext into
    separate pieces. There will be ``N`` (i.e. ``shares.total``) pieces created,
    and the file will be recoverable if any ``k`` (i.e. ``shares.needed``)
    pieces are retrieved. The default values are 3-of-10 (i.e.
    ``shares.needed = 3``, ``shares.total = 10``). Setting ``k`` to 1 is
    equivalent to simple replication (uploading ``N`` copies of the file).

    These values control the tradeoff between storage overhead, performance,
    and reliability. To a first approximation, a 1MB file will use (1MB * ``N``/``k``)
    of backend storage space (the actual value will be a bit more, because of
    other forms of overhead). Up to ``N``-``k`` shares can be lost before the file
    becomes unrecoverable, so assuming there are at least ``N`` servers, up to
    ``N``-``k`` servers can be offline without losing the file. So large ``N``/``k``
    ratios are more reliable, and small ``N``/``k`` ratios use less disk space.
    Clearly, ``k`` must never be smaller than ``N``.

    Large values of ``N`` will slow down upload operations slightly, since more
    servers must be involved, and will slightly increase storage overhead due
    to the hash trees that are created. Large values of ``k`` will cause
    downloads to be marginally slower, because more servers must be involved.
    ``N`` cannot be larger than 256, because of the 8-bit erasure-coding
    algorithm that Tahoe-LAFS uses.

    ``shares.happy`` allows you control over the distribution of your immutable
    file. For a successful upload, shares are guaranteed to be initially
    placed on at least ``shares.happy`` distinct servers, the correct
    functioning of any ``k`` of which is sufficient to guarantee the availability
    of the uploaded file. This value should not be larger than the number of
    servers on your grid.

    A value of ``shares.happy`` <= ``k`` is allowed, but does not provide any
    redundancy if some servers fail or lose shares.

    (Mutable files use a different share placement algorithm that does not
    currently consider this parameter.)


Storage Server Configuration
============================

``[storage]``

``enabled = (boolean, optional)``

    If this is ``True``, the node will run a storage server, offering space to
    other clients. If it is ``False``, the node will not run a storage server,
    meaning that no shares will be stored on this node. Use ``False`` for
    clients who do not wish to provide storage service. The default value is
    ``True``.

``readonly = (boolean, optional)``

    If ``True``, the node will run a storage server but will not accept any
    shares, making it effectively read-only. Use this for storage servers
    that are being decommissioned: the ``storage/`` directory could be mounted
    read-only, while shares are moved to other servers. Note that this
    currently only affects immutable shares. Mutable shares (used for
    directories) will be written and modified anyway. See ticket `#390
    <http://tahoe-lafs.org/trac/tahoe-lafs/ticket/390>`_ for the current
    status of this bug. The default value is ``False``.

``reserved_space = (str, optional)``

    If provided, this value defines how much disk space is reserved: the
    storage server will not accept any share that causes the amount of free
    disk space to drop below this value. (The free space is measured by a
    call to statvfs(2) on Unix, or GetDiskFreeSpaceEx on Windows, and is the
    space available to the user account under which the storage server runs.)

    This string contains a number, with an optional case-insensitive scale
    suffix like "K" or "M" or "G", and an optional "B" or "iB" suffix. So
    "100MB", "100M", "100000000B", "100000000", and "100000kb" all mean the
    same thing. Likewise, "1MiB", "1024KiB", and "1048576B" all mean the same
    thing.

``expire.enabled =``

``expire.mode =``

``expire.override_lease_duration =``

``expire.cutoff_date =``

``expire.immutable =``

``expire.mutable =``

    These settings control garbage collection, in which the server will
    delete shares that no longer have an up-to-date lease on them. Please see
    `<garbage-collection.rst>`_ for full details.


Running A Helper
================

A "helper" is a regular client node that also offers the "upload helper"
service.

``[helper]``

``enabled = (boolean, optional)``

    If ``True``, the node will run a helper (see `<helper.rst>`_ for details).
    The helper's contact FURL will be placed in ``private/helper.furl``, from
    which it can be copied to any clients that wish to use it. Clearly nodes
    should not both run a helper and attempt to use one: do not create
    ``helper.furl`` and also define ``[helper]enabled`` in the same node.
    The default is ``False``.


Running An Introducer
=====================

The introducer node uses a different ``.tac`` file (named "``introducer.tac``"),
and pays attention to the ``[node]`` section, but not the others.

The Introducer node maintains some different state than regular client nodes.

``BASEDIR/introducer.furl``
  This is generated the first time the introducer node is started, and used
  again on subsequent runs, to give the introduction service a persistent
  long-term identity. This file should be published and copied into new client
  nodes before they are started for the first time.


Other Files in BASEDIR
======================

Some configuration is not kept in ``tahoe.cfg``, for the following reasons:

* it is generated by the node at startup, e.g. encryption keys. The node
  never writes to ``tahoe.cfg``.
* it is generated by user action, e.g. the "``tahoe create-alias``" command.

In addition, non-configuration persistent state is kept in the node's base
directory, next to the configuration knobs.

This section describes these other files.

``private/node.pem``
  This contains an SSL private-key certificate. The node
  generates this the first time it is started, and re-uses it on subsequent
  runs. This certificate allows the node to have a cryptographically-strong
  identifier (the Foolscap "TubID"), and to establish secure connections to
  other nodes.

``storage/``
  Nodes that host StorageServers will create this directory to hold shares
  of files on behalf of other clients. There will be a directory underneath
  it for each StorageIndex for which this node is holding shares. There is
  also an "incoming" directory where partially-completed shares are held
  while they are being received.

``client.tac``
  this file defines the client, by constructing the actual Client instance
  each time the node is started. It is used by the "``twistd``" daemonization
  program (in the ``-y`` mode), which is run internally by the "``tahoe start``"
  command. This file is created by the "``tahoe create-node``" or
  "``tahoe create-client``" commands.

``private/control.furl``
  this file contains a FURL that provides access to a control port on the
  client node, from which files can be uploaded and downloaded. This file is
  created with permissions that prevent anyone else from reading it (on
  operating systems that support such a concept), to insure that only the
  owner of the client node can use this feature. This port is intended for
  debugging and testing use.

``private/logport.furl``
  this file contains a FURL that provides access to a 'log port' on the
  client node, from which operational logs can be retrieved. Do not grant
  logport access to strangers, because occasionally secret information may be
  placed in the logs.

``private/helper.furl``
  if the node is running a helper (for use by other clients), its contact
  FURL will be placed here. See `<helper.rst>`_ for more details.

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

  So the set of people who know your ``private/convergence`` string is the set
  of people who converge their storage space with you when you and they upload
  identical immutable files, and it is also the set of people who could mount
  such an attack.

  The content of the ``private/convergence`` file is a base-32 encoded string.
  If the file doesn't exist, then when the Tahoe-LAFS client starts up it will
  generate a random 256-bit string and write the base-32 encoding of this
  string into the file. If you want to converge your immutable files with as
  many people as possible, put the empty string (so that ``private/convergence``
  is a zero-length file).

Other files
===========

``logs/``
  Each Tahoe-LAFS node creates a directory to hold the log messages produced as
  the node runs. These logfiles are created and rotated by the "``twistd``"
  daemonization program, so ``logs/twistd.log`` will contain the most recent
  messages, ``logs/twistd.log.1`` will contain the previous ones,
  ``logs/twistd.log.2`` will be older still, and so on. ``twistd`` rotates
  logfiles after they grow beyond 1MB in size. If the space consumed by logfiles
  becomes troublesome, they should be pruned: a cron job to delete all files
  that were created more than a month ago in this ``logs/`` directory should be
  sufficient.

``my_nodeid``
  this is written by all nodes after startup, and contains a base32-encoded
  (i.e. human-readable) NodeID that identifies this specific node. This
  NodeID is the same string that gets displayed on the web page (in the
  "which peers am I connected to" list), and the shortened form (the first
  few characters) is recorded in various log messages.

Backwards Compatibility Files
=============================

Tahoe-LAFS releases before v1.3.0 had no ``tahoe.cfg`` file, and used distinct
files for each item listed below. For each configuration knob, if the distinct
file exists, it will take precedence over the corresponding item in ``tahoe.cfg``.

===============================  ===================================  =================
Config setting                   File                                 Comment
===============================  ===================================  =================
``[node]nickname``               ``BASEDIR/nickname``
``[node]web.port``               ``BASEDIR/webport``
``[node]tub.port``               ``BASEDIR/client.port``              (for Clients, not Introducers)
``[node]tub.port``               ``BASEDIR/introducer.port``          (for Introducers, not Clients) (note that, unlike other keys, ``tahoe.cfg`` overrides this file)
``[node]tub.location``           ``BASEDIR/advertised_ip_addresses``
``[node]log_gatherer.furl``      ``BASEDIR/log_gatherer.furl``        (one per line)
``[node]timeout.keepalive``      ``BASEDIR/keepalive_timeout``
``[node]timeout.disconnect``     ``BASEDIR/disconnect_timeout``
``[client]introducer.furl``      ``BASEDIR/introducer.furl``
``[client]helper.furl``          ``BASEDIR/helper.furl``
``[client]key_generator.furl``   ``BASEDIR/key_generator.furl``
``[client]stats_gatherer.furl``  ``BASEDIR/stats_gatherer.furl``
``[storage]enabled``             ``BASEDIR/no_storage``               (``False`` if ``no_storage`` exists)
``[storage]readonly``            ``BASEDIR/readonly_storage``         (``True`` if ``readonly_storage`` exists)
``[storage]sizelimit``           ``BASEDIR/sizelimit``
``[storage]debug_discard``       ``BASEDIR/debug_discard_storage``
``[helper]enabled``              ``BASEDIR/run_helper``               (``True`` if ``run_helper`` exists)
===============================  ===================================  =================

Note: the functionality of ``[node]ssh.port`` and ``[node]ssh.authorized_keys_file``
were previously combined, controlled by the presence of a
``BASEDIR/authorized_keys.SSHPORT`` file, in which the suffix of the filename
indicated which port the ssh server should listen on, and the contents of the
file provided the ssh public keys to accept. Support for these files has been
removed completely. To ``ssh`` into your Tahoe-LAFS node, add ``[node]ssh.port``
and ``[node].ssh_authorized_keys_file`` statements to your ``tahoe.cfg``.

Likewise, the functionality of ``[node]tub.location`` is a variant of the
now-unsupported ``BASEDIR/advertised_ip_addresses`` . The old file was additive
(the addresses specified in ``advertised_ip_addresses`` were used in addition to
any that were automatically discovered), whereas the new ``tahoe.cfg`` directive
is not (``tub.location`` is used verbatim).


Example
=======

The following is a sample ``tahoe.cfg`` file, containing values for all keys
described above. Note that this is not a recommended configuration (most of
these are not the default values), merely a legal one.

::

  [node]
  nickname = Bob's Tahoe-LAFS Node
  tub.port = 34912
  tub.location = 123.45.67.89:8098,44.55.66.77:8098
  web.port = 3456
  log_gatherer.furl = pb://soklj4y7eok5c3xkmjeqpw@192.168.69.247:44801/eqpwqtzm
  timeout.keepalive = 240
  timeout.disconnect = 1800
  ssh.port = 8022
  ssh.authorized_keys_file = ~/.ssh/authorized_keys


  [client]
  introducer.furl = pb://ok45ssoklj4y7eok5c3xkmj@tahoe.example:44801/ii3uumo
  helper.furl = pb://ggti5ssoklj4y7eok5c3xkmj@helper.tahoe.example:7054/kk8lhr


  [storage]
  enabled = True
  readonly_storage = True
  sizelimit = 10000000000


  [helper]
  run_helper = True
