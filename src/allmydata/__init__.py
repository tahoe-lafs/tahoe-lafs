
"""
Decentralized storage grid.

maintainer web site: U{http://allmydata.com/}

community web site: U{http://allmydata.org/}
"""

__version__ = "unknown"
try:
    from _version import __version__
except ImportError:
    # we're running in a tree that hasn't run misc/make-version.py, so we
    # don't know what our version is. This should not happen very often.
    pass

hush_pyflakes = __version__
del hush_pyflakes

