
Tahoe-LAFS
==========

.. Please view a nicely formatted version of this documentation at
   http://tahoe-lafs.readthedocs.io/en/latest/

Tahoe-LAFS is a Free and Open decentralized storage system.
It distributes your data across multiple servers.
Even if some of the servers fail or are taken over by an attacker,
the entire file store continues to function correctly,
preserving your privacy and security.

.. toctree::
   :maxdepth: 1
   :caption: Introduction

   about

.. toctree::
   :maxdepth: 1
   :caption: Using Tahoe-LAFS

   INSTALL
   running
   configuration
   servers
   frontends/CLI
   frontends/FTP-and-SFTP
   frontends/download-status
   magic-wormhole-invites
   anonymity-configuration
   known_issues

.. toctree::
   :maxdepth: 1
   :caption: Technical Notes

   architecture
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
   developer-guide
   ticket-triage
   release-checklist
   desert-island

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
   windows
   OS-X
   build/build-pyOpenSSL
