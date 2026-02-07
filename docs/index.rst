
Tahoe-LAFS
==========

.. Please view a nicely formatted version of this documentation at
   http://tahoe-lafs.readthedocs.io/en/latest/

   Please see the notes under "Organizing Tahoe-LAFS documentation" in
   docs/README.txt if you are editing this file.

Tahoe-LAFS is a Free and Open decentralized storage system.
It distributes your data across multiple servers.
Even if some of the servers fail or are taken over by an attacker,
the entire file store continues to function correctly,
preserving your privacy and security.

.. toctree::
   :maxdepth: 1
   :caption: Getting Started with Tahoe-LAFS

   about-tahoe

   Installation/install-tahoe
   operator-tutorial/index

   running
   configuration
   servers
   frontends/CLI
   frontends/FTP-and-SFTP
   frontends/download-status
   magic-wormhole-invites
   anonymity-configuration
   known_issues

   glossary

.. toctree::
   :maxdepth: 1
   :caption: Tahoe-LAFS in Depth

   architecture
   gpg-setup
   servers
   managed-grid
   helper
   convergence-secret
   garbage-collection
   filesystem-notes
   key-value-store
   frontends/webapi
   write_coordination
   cautions
   backupdb
   nodekeys
   performance
   logging
   stats

.. toctree::
   :maxdepth: 1
   :caption: Specifications

   specifications/index
   proposed/index

.. toctree::
   :maxdepth: 1
   :caption: Contributing to Tahoe-LAFS

   contributing
   CODE_OF_CONDUCT
   build/build-on-windows
   build/build-on-linux
   build/build-on-desert-island
   developer-guide
   ticket-triage
   release-checklist

.. toctree::
   :maxdepth: 1
   :caption: Notes of Community Interest

   backdoors
   donations
   accepting-donations
   expenses

.. toctree::
   :maxdepth: 1
   :caption: Notes of Historical Interest

   historical/configuration
   debian
   build/build-pyOpenSSL
