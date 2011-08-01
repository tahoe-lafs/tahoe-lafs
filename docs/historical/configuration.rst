=======================
Old Configuration Files
=======================

Tahoe-LAFS releases before v1.3.0 had no ``tahoe.cfg`` file, and used
distinct files for each item listed below. If Tahoe-LAFS v1.9.0 or above
detects the old configuration files at start up it emits a warning and
aborts the start up. (This was issue ticket #1385.)

===============================  ===================================  =================
Config setting                   File                                 Comment
===============================  ===================================  =================
``[node]nickname``               ``BASEDIR/nickname``
``[node]web.port``               ``BASEDIR/webport``
``[node]tub.port``               ``BASEDIR/client.port``              (for Clients, not Introducers)
``[node]tub.port``               ``BASEDIR/introducer.port``          (for Introducers, not Clients) (note that, unlike other keys, ``tahoe.cfg`` overrode this file from Tahoe-LAFS v1.3.0 up to and including Tahoe-LAFS v1.8.2)
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

Note: the functionality of ``[node]ssh.port`` and
``[node]ssh.authorized_keys_file`` were previously (before Tahoe-LAFS
v1.3.0) combined, controlled by the presence of a
``BASEDIR/authorized_keys.SSHPORT`` file, in which the suffix of the
filename indicated which port the ssh server should listen on, and the
contents of the file provided the ssh public keys to accept. Support
for these files has been removed completely. To ``ssh`` into your
Tahoe-LAFS node, add ``[node]ssh.port`` and
``[node].ssh_authorized_keys_file`` statements to your ``tahoe.cfg``.

Likewise, the functionality of ``[node]tub.location`` is a variant of
the now (since Tahoe-LAFS v1.3.0) unsupported
``BASEDIR/advertised_ip_addresses`` . The old file was additive (the
addresses specified in ``advertised_ip_addresses`` were used in
addition to any that were automatically discovered), whereas the new
``tahoe.cfg`` directive is not (``tub.location`` is used verbatim).
