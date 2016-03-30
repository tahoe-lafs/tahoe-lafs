How To Build Tahoe-LAFS On A Desert Island
==========================================

(or an airplane, or anywhere else without internet connectivity)

Here's the story: you leave for the airport in 10 minutes, you know you want
to do some Tahoe hacking on the flight. What can you grab right now that will
let you build the necessary dependencies later, when you are offline?

Pip can help, with a technique described in the pip documentation
https://pip.pypa.io/en/stable/user_guide/#installing-from-local-packages .

Before you get shipwrecked (or leave the internet for a while), do this from
your tahoe source tree:

* ``pip download --dest tahoe-deps .``

That will create a directory named "tahoe-deps", and download everything that
the current project (".", i.e. tahoe) needs. It will fetch wheels if
available, otherwise it will fetch tarballs. It will not compile anything.

Later, on the plane, do this (in an active virtualenv):

* ``pip install --no-index --find-links=tahoe-deps --editable .``

That tells pip to not try to contact PyPI (--no-index) and to use the
tarballs and wheels in ``tahoe-deps/`` instead. That will compile anything
necessary, create (and cache) wheels, and install them.

If you need to rebuild the virtualenv for whatever reason, run the "pip
install" command again: it will re-use the cached wheels and skip the compile
step.

Compiling Ahead Of Time
-----------------------

If you want to save some battery on the flight, you can compile the wheels
ahead of time. Just do the install step before you go offline. The wheels
will be cached as a side-effect. Later, on the plane, you can populate a new
virtualenv with the same ``pip install`` command above, and it will use the
cached wheels instead of recompiling them.

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
``--find-links=`` argument is what provides an alternate source of packages.

The HTTP and wheel caches are not single flat directories: they use a
hierarchy of subdirectories, named after a hash of the URL or name of the
object being stored (this is to avoid filesystem limitations on the size of a
directory). As a result, the wheel cache is not suitable for use as a
``--find-links=`` target (but see below).

There is a command named ``pip wheel`` which only creates wheels (and stores
them in ``--wheel-dir=``, which defaults to the current directory). This
command does not populate the wheel cache: it reads from (and writes to) the
HTTP cache, and reads from the wheel cache, but will only save the generated
wheels into the directory you specify with ``--wheel-dir=``. It does not also
write them to the cache.

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

Another Approach
----------------

An alternate approach is to set your ``pip.conf`` to install wheels into the
same directory that it will search for links, and use ``pip wheel`` to add
wheels to the cache. The ``pip.conf`` will look like::

    [global]
    wheel-dir = ~/.pip/wheels
    find-links = ~/.pip/wheels

(see https://pip.pypa.io/en/stable/user_guide/#configuration to find out
where your ``pip.conf`` lives, but ``~/.pip/pip.conf`` probably works)

While online, you populate the wheel-dir (from a tahoe source tree) with:

* ``pip wheel .``

That compiles everything, so it may take a little while. Note that you can
also add specific packages (and their dependencies) any time you like, with
something like ``pip wheel zfec``.

Later, you do the offline install (in a virtualenv) with just:

* ``pip install --no-index --editable .``

If/when you have network access, omit the ``--no-index`` and it will check
with PyPI for the most recent versions (and still use the stashed wheels if
appropriate).

The upside is that the only extra ``pip install`` argument is ``--no-index``,
and you don't need to remember the ``--find-links`` or ``--dest`` arguments.

The downside of this approach is that ``pip install`` does not populate the
wheel-dir (it populates the normal wheel cache, but not ~/.pip/wheels). Only
an explicit ``pip wheel`` will populate ~/.pip/wheels. So if you do a ``pip
install`` (but not a ``pip wheel``), then go offline, a second ``pip install
--no-index`` may fail: the wheels it needs may be somewhere in the
wheel-cache, but not in the ``--find-links=`` directory.
