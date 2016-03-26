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

1: Start a CLI shell

2: Create a new virtualenv. Everything specific to Tahoe will go into this.
You can use whatever name you like for the virtualenv, but example uses
"tahoe"::

    PS C:\Users\me> virtualenv tahoe
    New python executable in C:\Users\me\tahoe\Scripts\python.exe
    Installing setuptools, pip, wheel...done.
    >

3: Activate the new virtualenv. This puts the virtualenv's ``Scripts``
directory on your PATH, allowing you to run commands that are installed
there. The command prompt will change to include ``(tahoe)`` as a reminder
that you've activated the "tahoe" virtualenv::

    PS C:\Users\me> .\tahoe\Scripts\activate
    (tahoe) PS C:\Users\me>

4: Use ``pip`` to install the latest release of Tahoe-LAFS into this
virtualenv::

    (tahoe) PS C:\Users\me> pip install --find-links=https://tahoe-lafs.org/deps/ tahoe-lafs
    Collecting tahoe-lafs
    ...
    Installing collected packages: ...
    Successfully installed ...
    (tahoe) PS C:\Users\me>

5: Verify that Tahoe was installed correctly by running ``tahoe --version``::

    (tahoe) PS C:\Users\me> tahoe --version
    tahoe-lafs: 1.11
    foolscap: ...

Running Tahoe-LAFS
------------------

The rest of the documentation assumes you can run the ``tahoe`` executable
just as you did in step 5 above. If you start a new shell (say, the next time
your boot your computer), you'll need to re-activate the virtualenv as you
did in step 3.

Now use the docs in `<running.rst>`_ to learn how to configure your first
Tahoe node.

Installing A Different Version
------------------------------

The ``pip install tahoe-lafs`` command above will install the latest release
(from PyPI). If instead, you want to install from a git checkout, then run
the following command (in an activated virtualenv, from the root of your git
checkout)::

    $ (tahoe) pip install --find-links=https://tahoe-lafs.org/deps/ .

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
