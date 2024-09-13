============================
Getting Started Step-by-Step
============================

This series is for newcomers to Tahoe-lafs who want to get familiar with the pieces of Tahoe-lafs and prefer a step-by-step approach to the All-in-One alternative.

    There are many ways to get started with Tahoe-lafs. This one works.
    -- Anonymous

.. note:: The complete series takes about 1 hour

Before you begin
================

Create and activate a local venv for tahoe::

    python -m venv .venv && source .venv/bin/activate

Update the new venv and install tahoe-lafs::

    pip install -U pip setuptools wheel && \
    pip install attrs==23.2.0 cryptography==42.0.8 tahoe-lafs


``tmux`` is your friend
-----------------------

Since you will be running several processes, it helps to have multiple terminal windows.
A Linux terminal user would create several sessions like this::

    $ tmux new -s storage_console
    $ tmux new -s client_console

Most IDEs also support the ability to have several terminal sessions.
