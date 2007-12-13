
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

def get_package_versions():
    import OpenSSL, allmydata, foolscap, nevow, pycryptopp, setuptools, simplejson, twisted, zfec
    return {
        'pyopenssl': OpenSSL.__version__,
        'allmydata': allmydata.__version__,
        'foolscap': foolscap.__version__,
        'nevow': nevow.__version__,
        'pycryptopp': pycryptopp.__version__,
        'setuptools': setuptools.__version__,
        'simplejson': simplejson.__version__,
        'twisted': twisted.__version__,
        'zfec': zfec.__version__,
        }

def get_package_versions_string():
    versions = get_package_versions()
    res = []
    for p in ["allmydata", "foolscap", "pycryptopp", "zfec", "twisted", "nevow"]:
        if versions.has_key(p):
            res.append(str(p) + ": " + str(versions[p]))
            del versions[p]
        else:
            res.append(str(p) + ": UNKNOWN")
    for p, v in versions.iteritems():
        res.append(str(p) + ": " + str(v))
    return ', '.join(res)
