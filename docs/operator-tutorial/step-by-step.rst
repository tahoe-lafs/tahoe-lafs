============================
About the Steps
============================

This series is for newcomers to Tahoe-lafs who want to get familiar with the pieces of Tahoe-lafs and prefer a step-by-step approach to the All-in-One alternative.

    There are many ways to get started with Tahoe-lafs. This one works.
    -- Anonymous

.. note:: The complete series takes about 1 hour


.. _install tahoe client::

Before you begin
================

Create and activate a local venv for tahoe::

    python -m venv .venv && source .venv/bin/activate

Update the new venv and install tahoe-lafs::

    pip install -U pip setuptools wheel && \
    pip install attrs==23.2.0 'cryptography<42' tahoe-lafs

.. note:: Use multiple terminal sessions for each of the various consoles you will eventually start. Most IDE's support independent terminals.

``tmux`` is your friend
-----------------------

Most Tahoe operators are running Linux in terminal sessions. Using ``tmux`` will make life easier because you will be running several processes, it helps to have multiple terminal windows.
A Linux terminal user would create several sessions like this::

    $ tmux new -s storage_console
    $ tmux new -s client_console


