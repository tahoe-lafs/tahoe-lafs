
"""
Decentralized storage grid.

maintainer web site: U{http://allmydata.com/}

community web site: U{http://allmydata.org/}
"""

__version__ = "unknown"
try:
    from _version import __version__
except ImportError:
    # We're running in a tree that hasn't run "./setup.py darcsver", and didn't
    # come with a _version.py, so we don't know what our version is. This should
    # not happen very often.
    pass

hush_pyflakes = __version__
del hush_pyflakes

import _auto_deps
_auto_deps.require_auto_deps()

def get_package_versions():
    import OpenSSL, allmydata, foolscap, nevow, pycryptopp, simplejson, twisted, zfec, sys

    try:
        pyver = '.'.join([str(c) for c in sys.version_info])
    except:
        # This will probably never happen, but if it does:
        pyver = sys.version

    setuptools_version = "unavailable"
    try:
        import setuptools
        setuptools_version = setuptools.__version__
    except ImportError:
        pass
    return {
        'pyopenssl': OpenSSL.__version__,
        'allmydata': allmydata.__version__,
        'foolscap': foolscap.__version__,
        'nevow': nevow.__version__,
        'pycryptopp': pycryptopp.__version__,
        'setuptools': setuptools_version,
        'simplejson': simplejson.__version__,
        'twisted': twisted.__version__,
        'zfec': zfec.__version__,
        'python': pyver,
        }

def get_package_versions_string():
    versions = get_package_versions()
    res = []
    for p in ["allmydata", "foolscap", "pycryptopp", "zfec", "twisted", "nevow", "python"]:
        if versions.has_key(p):
            res.append(str(p) + ": " + str(versions[p]))
            del versions[p]
        else:
            res.append(str(p) + ": UNKNOWN")
    for p, v in versions.iteritems():
        res.append(str(p) + ": " + str(v))
    return ', '.join(res)
