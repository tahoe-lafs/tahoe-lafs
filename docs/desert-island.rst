******************************************
How To Build Tahoe-LAFS On A Desert Island
******************************************

(or an airplane, or anywhere else without internet connectivity)

Here's the story: you leave for the airport in an hour, you know you want to
do some Tahoe hacking on the flight. What can you grab right now that will
let you install the necessary dependencies later, when you are offline?

Pip can help, with a technique described in the pip documentation
https://pip.pypa.io/en/stable/user_guide/#installing-from-local-packages .

First, do two setup steps:

* ``mkdir ~/.pip/wheels``
* edit ``~/.pip/pip.conf`` to set ``[global] find-links = ~/.pip/wheels``

(the filename may vary on non-unix platforms: check the pip documentation for
details)

This instructs all ``pip install`` commands to look in your local directory
for compiled wheels, in addition to asking PyPI and the normal wheel cache.

Before you get shipwrecked (or leave the internet for a while), do this from
your tahoe source tree (or any python source tree that you want to hack on):

* ``pip wheel -w ~/.pip/wheels .``

That command will require network and time: it will download and compile
whatever is necessary right away. Schedule your shipwreck for *after* it
completes.

Specifically, it will get wheels for everything that the current project
(".", i.e. tahoe) needs, and write them to the ``~/.pip/wheels`` directory.
It will query PyPI to learn the current version of every dependency, then
acquire wheels from the first source that has one:

* copy from our ``~/.pip/wheels`` directory
* copy from the local wheel cache (see below for where this lives)
* download a wheel from PyPI
* build a wheel from a tarball (cached or downloaded)

Later, on the plane, do this:

* ``virtualenv --no-download ve``
* ``. ve/bin/activate``
* ``pip install --no-index --editable .``

That tells virtualenv/pip to not try to contact PyPI, and your ``pip.conf``
"find-links" tells them to use the wheels in ``~/.pip/wheels/`` instead.

How This Works
==============

The pip wheel cache
-------------------

Modern versions of pip and setuptools will, by default, cache both their HTTP
downloads and their generated wheels. When pip is asked to install a package,
it will first check with PyPI. If the PyPI index says it needs to download a
newer version, but it can find a copy of the tarball/zipball/wheel in the
HTTP cache, it will not actually download anything. Then it tries to build a
wheel: if it already has one in the wheel cache (downloaded or built
earlier), it will not actually build anything.

If it cannot contact PyPI, it will fail. The ``--no-index`` above is to tell
it to skip the PyPI step, but that leaves it with no source of packages. The
``find-links`` setting is what provides an alternate source of packages.

The HTTP and wheel caches are not single flat directories: they use a
hierarchy of subdirectories, named after a hash of the URL or name of the
object being stored (this is to avoid filesystem limitations on the size of a
directory). As a result, the wheel cache is not suitable for use as a
``find-links`` target (but see below).

There is a command named ``pip wheel`` which only creates wheels (and stores
them in ``--wheel-dir=``, which defaults to the current directory). This
command does not populate the wheel cache: it reads from (and writes to) the
HTTP cache, and reads from the wheel cache, but will only save the generated
wheels into the directory you specify with ``--wheel-dir=``.

Where Does The Cache Live?
--------------------------

Pip's cache location depends upon the platform. On linux, it defaults to
~/.cache/pip/ (both http/ and wheels/). On OS-X (homebrew), it uses
~/Library/Caches/pip/ . On Windows, try ~\AppData\Local\pip\cache .

The location can be overridden by ``pip.conf``. Look for the "wheel-dir",
"cache-dir", and "find-links" options.

How Can I Tell If It's Using The Cache?
---------------------------------------

When "pip install" has to download a source tarball (and build a wheel), it
will say things like::

 Collecting zfec
  Downloading zfec-1.4.24.tar.gz (175kB)
 Building wheels for collected packages: zfec
  Running setup.py bdist_wheel for zfec ... done
  Stored in directory: $CACHEDIR
 Successfully built zfec
 Installing collected packages: zfec
 Successfully installed zfec-1.4.24

When "pip install" can use a cached downloaded tarball, but does not have a
cached wheel, it will say::

 Collecting zfec
  Using cached zfec-1.4.24.tar.gz
 Building wheels for collected packages: zfec
  Running setup.py bdist_wheel for zfec ... done
  Stored in directory: $CACHEDIR
 Successfully built zfec
 Installing collected packages: zfec
 Successfully installed zfec-1.4.24

When "pip install" can use a cached wheel, it will just say::

 Collecting zfec
 Installed collected packages: zfec
 Successfully installed zfec-1.4.24

Many packages publish pre-built wheels next to their source tarballs. This is
common for non-platform-specific (pure-python) packages. It is also common
for them to provide pre-compiled windows and OS-X wheel, so users do not have
to have a compiler installed (pre-compiled Linux wheels are not common,
because there are too many platform variations). When "pip install" can use a
downloaded wheel like this, it will say::

 Collecting six
  Downloading six-1.10.0-py2.py3-none-any.whl
 Installing collected packages: six
 Successfully installed six-1.10.0

Note that older versions of pip do not always use wheels, or the cache. Pip
8.0.0 or newer should be ok. The version of setuptools may also be
significant.
