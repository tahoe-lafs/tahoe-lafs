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

import os, platform, re, subprocess
_distributor_id_cmdline_re = re.compile("(?:Distributor ID:)\s*(.*)", re.I)
_release_cmdline_re = re.compile("(?:Release:)\s*(.*)", re.I)

_distributor_id_file_re = re.compile("(?:DISTRIB_ID\s*=)\s*(.*)", re.I)
_release_file_re = re.compile("(?:DISTRIB_RELEASE\s*=)\s*(.*)", re.I)

global _distname,_version
_distname = None
_version = None

def get_linux_distro():
    """ Tries to determine the name of the Linux OS distribution name.

    First, try to parse a file named "/etc/lsb-release".  If it exists, and
    contains the "DISTRIB_ID=" line and the "DISTRIB_RELEASE=" line, then return
    the strings parsed from that file.

    If that doesn't work, then invoke platform.dist().

    If that doesn't work, then try to execute "lsb_release", as standardized in
    2001:

    http://refspecs.freestandards.org/LSB_1.0.0/gLSB/lsbrelease.html

    The current version of the standard is here:

    http://refspecs.freestandards.org/LSB_3.2.0/LSB-Core-generic/LSB-Core-generic/lsbrelease.html

    that lsb_release emitted, as strings.

    Returns a tuple (distname,version). Distname is what LSB calls a
    "distributor id", e.g. "Ubuntu".  Version is what LSB calls a "release",
    e.g. "8.04".

    A version of this has been submitted to python as a patch for the standard
    library module "platform":

    http://bugs.python.org/issue3937
    """
    global _distname,_version
    if _distname and _version:
        return (_distname, _version)

    try:
        etclsbrel = open("/etc/lsb-release", "rU")
        for line in etclsbrel:
            m = _distributor_id_file_re.search(line)
            if m:
                _distname = m.group(1).strip()
                if _distname and _version:
                    return (_distname, _version)
            m = _release_file_re.search(line)
            if m:
                _version = m.group(1).strip()
                if _distname and _version:
                    return (_distname, _version)
    except EnvironmentError:
            pass

    (_distname, _version) = platform.dist()[:2]
    if _distname and _version:
        return (_distname, _version)

    try:
        p = subprocess.Popen(["lsb_release", "--all"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rc = p.wait()
        if rc == 0:
            for line in p.stdout.readlines():
                m = _distributor_id_cmdline_re.search(line)
                if m:
                    _distname = m.group(1).strip()
                    if _distname and _version:
                        return (_distname, _version)

                m = _release_cmdline_re.search(p.stdout.read())
                if m:
                    _version = m.group(1).strip()
                    if _distname and _version:
                        return (_distname, _version)
    except EnvironmentError:
        pass

    if os.path.exists("/etc/arch-release"):
        return ("Arch_Linux", "")

    return (_distname,_version)

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

def get_package_locations():
    import OpenSSL, allmydata, foolscap, nevow, platform, pycryptopp, setuptools, simplejson, twisted, zfec

    return {
        'pyopenssl': os.path.dirname(OpenSSL.__file__),
        'allmydata': os.path.dirname(allmydata.__file__),
        'foolscap': os.path.dirname(foolscap.__file__),
        'nevow': os.path.dirname(nevow.__file__),
        'pycryptopp': os.path.dirname(pycryptopp.__file__),
        'setuptools': os.path.dirname(setuptools.__file__),
        'simplejson': os.path.dirname(simplejson.__file__),
        'twisted': os.path.dirname(twisted.__file__),
        'zfec': os.path.dirname(zfec.__file__),
        'python': platform.python_version(),
        'platform': get_platform()
        }

def get_package_versions_string(show_paths=False):
    versions = get_package_versions()
    paths = None
    if show_paths:
        paths = get_package_locations()

    res = []
    for p in ["allmydata", "foolscap", "pycryptopp", "zfec", "twisted", "nevow", "python", "platform"]:
        if versions.has_key(p):
            info = str(p) + ": " + str(versions[p])
            del versions[p]
        else:
            info = str(p) + ": UNKNOWN"
        if show_paths:
            info = info + " (%s)" % str(paths[p])
        res.append(info)

    for p, v in versions.iteritems():
        res.append(str(p) + ": " + str(v))
    return ', '.join(res)
