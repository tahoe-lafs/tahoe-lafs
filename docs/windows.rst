Building Tahoe-LAFS on Windows
==============================

You'll need ``python``, ``pip``, and ``virtualenv``. But you won't need a
compiler.

Preliminaries
-------------

1: Install Python-2.7.11 . Use the "Windows x86-64 MSI installer" at
https://www.python.org/downloads/release/python-2711/

2: That should install ``pip``, but if it doesn't, look at
https://pip.pypa.io/en/stable/installing/ for installation instructions.

3: Install ``virtualenv`` with
https://virtualenv.pypa.io/en/latest/installation.html

Installation
------------

1: Start a CLI shell (e.g. PowerShell)

2: Create a new virtualenv. Everything specific to Tahoe will go into this.
You can use whatever name you like for the virtualenv, but example uses
"venv"::

    PS C:\Users\me> virtualenv venv
    New python executable in C:\Users\me\venv\Scripts\python.exe
    Installing setuptools, pip, wheel...done.
    >

3: Use the virtualenv's ``pip`` to install the latest release of Tahoe-LAFS
into this virtualenv::

    PS C:\Users\me> venv\Scripts\pip install --find-links=https://tahoe-lafs.org/deps/ tahoe-lafs
    Collecting tahoe-lafs
    ...
    Installing collected packages: ...
    Successfully installed ...
    >

4: Verify that Tahoe was installed correctly by running ``tahoe --version``,
using the ``tahoe`` from the virtualenv's Scripts directory::

    PS C:\Users\me> venv\Scripts\tahoe --version
    tahoe-lafs: 1.11
    foolscap: ...

Running Tahoe-LAFS
------------------

The rest of the documentation assumes you can run the ``tahoe`` executable
just as you did in step 4 above. If you want to type just ``tahoe`` instead
of ``venv\Scripts\tahoe``, you can either "`activate`_" the virtualenv (by
running ``venv\Scripts\activate``, or you can add the Scripts directory to
your ``%PATH%`` environment variable.

Now use the docs in :doc:`running` to learn how to configure your first
Tahoe node.

.. _activate: https://virtualenv.pypa.io/en/latest/userguide.html#activate-script

Installing A Different Version
------------------------------

The ``pip install tahoe-lafs`` command above will install the latest release
(from PyPI). If instead, you want to install from a git checkout, then run
the following command (using pip from the virtualenv, from the root of your
git checkout)::

    $ venv\Scripts\pip install --find-links=https://tahoe-lafs.org/deps/ .

If you're planning to hack on the source code, you might want to add
``--editable`` so you won't have to re-install each time you make a change.

Dependencies
------------

Tahoe-LAFS depends upon several packages that use compiled C code, such as
zfec, pycryptopp, and others. This code must be built separately for each
platform (Windows, OS-X, and different flavors of Linux).

Pre-compiled "wheels" of all Tahoe's dependencies are hosted on the
tahoe-lafs.org website in the ``deps/`` directory. The ``--find-links=``
argument (used in the examples above) instructs ``pip`` to look at that URL
for dependencies. This should avoid the need for anything to be compiled
during the install.
