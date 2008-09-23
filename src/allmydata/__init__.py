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

import platform, re, subprocess
_distributor_id = re.compile("(?:Distributor ID)?:?\s*(.*)", re.I)
_release = re.compile("(?:Release)?:?\s*(.*)", re.I)

def get_linux_distro():
    """ Tries to determine the name of the Linux OS distribution name.

    The function tries to execute "lsb_release", as standardized in 2001:

    http://refspecs.freestandards.org/LSB_1.0.0/gLSB/lsbrelease.html

    The current version of the standard is here:

    http://refspecs.freestandards.org/LSB_3.2.0/LSB-Core-generic/LSB-Core-generic/lsbrelease.html

    If executing "lsb_release" raises no exception, and returns exit code 0, and
    both the "distributor id" and "release" results are non-empty after being
    stripped of whitespace, then return a two-tuple containing the information
    that lsb_release emitted, as strings.  Else, invoke platform.dist() and
    return the first two elements of the tuple returned by that function.

    Returns a tuple (distname,version). Distname is what LSB calls a
    "distributor id", e.g. "Ubuntu".  Version is what LSB calls a "release",
    e.g. "8.04".

    A version of this has been submitted to python as a patch for the standard
    library module "platform":

    http://bugs.python.org/issue3937
    """
    _distname = ""
    _version = ""
    try:
        p = subprocess.Popen(["lsb_release", "--id"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rc = p.wait()
        if rc == 0:
            m = _distributor_id.search(p.stdout.read())
            if m:
                _distname = m.group(1).strip()

        p = subprocess.Popen(["lsb_release", "--release"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rc = p.wait()
        if rc == 0:
            m = _release.search(p.stdout.read())
            if m:
                _version = m.group(1).strip()
    except EnvironmentError:
        pass

    if _distname and _version:
        return (_distname, _version)
    else:
        return platform.dist()[:2]

def get_platform():
    # Our version of platform.platform(), telling us both less and more than the
    # Python Standard Library's version does.
    # We omit details such as the Linux kernel version number, but we add a
    # more detailed and correct rendition of the Linux distribution and
    # distribution-version.
    if "linux" in platform.system().lower():
        return platform.system()+"-"+"_".join(get_linux_distro())+"-"+platform.machine()+"-"+"_".join([x for x in platform.architecture() if x])
    else:
        return platform.platform()

def get_package_versions():
    import OpenSSL, allmydata, foolscap, nevow, platform, pycryptopp, setuptools, simplejson, twisted, zfec

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
        'python': platform.python_version(),
        'platform': get_platform()
        }

def get_package_versions_string():
    versions = get_package_versions()
    res = []
    for p in ["allmydata", "foolscap", "pycryptopp", "zfec", "twisted", "nevow", "python", "platform"]:
        if versions.has_key(p):
            res.append(str(p) + ": " + str(versions[p]))
            del versions[p]
        else:
            res.append(str(p) + ": UNKNOWN")
    for p, v in versions.iteritems():
        res.append(str(p) + ": " + str(v))
    return ', '.join(res)
