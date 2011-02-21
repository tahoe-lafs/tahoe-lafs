"""
Decentralized storage grid.

community web site: U{http://tahoe-lafs.org/}
"""

class PackagingError(EnvironmentError):
    """
    Raised when there is an error in packaging of Tahoe-LAFS or its
    dependencies which makes it impossible to proceed safely.
    """
    pass

__version__ = "unknown"
try:
    from allmydata._version import __version__
except ImportError:
    # We're running in a tree that hasn't run "./setup.py darcsver", and didn't
    # come with a _version.py, so we don't know what our version is. This should
    # not happen very often.
    pass

__appname__ = "unknown"
try:
    from allmydata._appname import __appname__
except ImportError:
    # We're running in a tree that hasn't run "./setup.py".  This shouldn't happen.
    pass

# __full_version__ is the one that you ought to use when identifying yourself in the
# "application" part of the Tahoe versioning scheme:
# http://allmydata.org/trac/tahoe/wiki/Versioning
__full_version__ = __appname__ + '/' + str(__version__)

import os, platform, re, subprocess, sys
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


from allmydata.util import verlib
def normalized_version(verstr):
    return verlib.NormalizedVersion(verlib.suggest_normalized_version(verstr))


def get_package_versions_and_locations():
    import warnings
    from _auto_deps import package_imports, deprecation_messages, deprecation_imports

    def package_dir(srcfile):
        return os.path.dirname(os.path.dirname(os.path.normcase(os.path.realpath(srcfile))))

    # pkg_resources.require returns the distribution that pkg_resources attempted to put
    # on sys.path, which can differ from the one that we actually import due to #1258,
    # or any other bug that causes sys.path to be set up incorrectly. Therefore we
    # must import the packages in order to check their versions and paths.

    # This warning is generated by twisted, PyRex, and possibly other packages,
    # but can happen at any time, not only when they are imported. See
    # http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1129 .
    warnings.filterwarnings("ignore", category=DeprecationWarning,
        message="BaseException.message has been deprecated as of Python 2.6",
        append=True)

    # This is to suppress various DeprecationWarnings that occur when modules are imported.
    # See http://allmydata.org/trac/tahoe/ticket/859 and http://divmod.org/trac/ticket/2994 .

    for msg in deprecation_messages:
        warnings.filterwarnings("ignore", category=DeprecationWarning, message=msg, append=True)
    try:
        for modulename in deprecation_imports:
            try:
                __import__(modulename)
            except ImportError:
                pass
    finally:
        for ign in deprecation_messages:
            warnings.filters.pop()

    packages = []

    def get_version(module, attr):
        return str(getattr(module, attr, 'unknown'))

    for pkgname, modulename in [(__appname__, 'allmydata')] + package_imports:
        if modulename:
            try:
                __import__(modulename)
                module = sys.modules[modulename]
            except ImportError:
                packages.append( (pkgname, (None, None, modulename)) )
            else:
                if 'sqlite' in pkgname:
                    packages.append( (pkgname, (get_version(module, 'version'), package_dir(module.__file__),
                                               'sqlite %s' % (get_version(module, 'sqlite_version'),))) )
                else:
                    comment = None
                    if pkgname == 'setuptools' and hasattr(module, '_distribute'):
                        # distribute does not report its version in any module variables
                        comment = 'distribute'
                    packages.append( (pkgname, (get_version(module, '__version__'), package_dir(module.__file__), comment)) )
        elif pkgname == 'python':
            packages.append( (pkgname, (platform.python_version(), sys.executable, None)) )
        elif pkgname == 'platform':
            packages.append( (pkgname, (get_platform(), None, None)) )

    return packages


def check_requirement(req, vers_and_locs):
    # TODO: check [] options
    # We support only disjunctions of >= and ==

    reqlist = req.split(',')
    name = reqlist[0].split('>=')[0].split('==')[0].strip(' ').split('[')[0]
    if name not in vers_and_locs:
        raise PackagingError("no version info for %s" % (name,))
    if req.strip(' ') == name:
        return
    (actual, location, comment) = vers_and_locs[name]
    if actual is None:
        # comment is the module name
        raise ImportError("could not import %r for requirement %r" % (comment, req))
    if actual == 'unknown':
        return
    actualver = normalized_version(actual)

    for r in reqlist:
        s = r.split('>=')
        if len(s) == 2:
            required = s[1].strip(' ')
            if actualver >= normalized_version(required):
                return  # minimum requirement met
        else:
            s = r.split('==')
            if len(s) == 2:
                required = s[1].strip(' ')
                if actualver == normalized_version(required):
                    return  # exact requirement met
            else:
                raise PackagingError("no version info or could not understand requirement %r" % (req,))

    msg = ("We require %s, but could only find version %s.\n" % (req, actual))
    if location and location != 'unknown':
        msg += "The version we found is from %r.\n" % (location,)
    msg += ("To resolve this problem, uninstall that version, either using your\n"
            "operating system's package manager or by moving aside the directory.")
    raise PackagingError(msg)


_vers_and_locs_list = get_package_versions_and_locations()


def cross_check_pkg_resources_versus_import():
    """This function returns a list of errors due to any failed cross-checks."""

    import pkg_resources
    from _auto_deps import install_requires

    pkg_resources_vers_and_locs = dict([(p.project_name.lower(), (str(p.version), p.location))
                                        for p in pkg_resources.require(install_requires)])

    return cross_check(pkg_resources_vers_and_locs, _vers_and_locs_list)


def cross_check(pkg_resources_vers_and_locs, imported_vers_and_locs_list):
    """This function returns a list of errors due to any failed cross-checks."""

    errors = []
    not_pkg_resourceable = set(['sqlite3', 'python', 'platform', __appname__.lower()])
    not_import_versionable = set(['zope.interface', 'mock', 'pyasn1'])
    ignorable = set(['argparse', 'pyutil', 'zbase32', 'distribute'])

    for name, (imp_ver, imp_loc, imp_comment) in imported_vers_and_locs_list:
        name = name.lower()
        if name not in not_pkg_resourceable:
            if name not in pkg_resources_vers_and_locs:
                if name == "setuptools" and "distribute" in pkg_resources_vers_and_locs:
                    pr_ver, pr_loc = pkg_resources_vers_and_locs["distribute"]
                    if not (os.path.normpath(os.path.realpath(pr_loc)) == os.path.normpath(os.path.realpath(imp_loc))
                            and imp_comment == "distribute"):
                        errors.append("Warning: dependency 'setuptools' found to be version %r of 'distribute' from %r "
                                      "by pkg_resources, but 'import setuptools' gave version %r [%s] from %r. "
                                      "A version mismatch is expected, but a location mismatch is not."
                                      % (pr_ver, pr_loc, imp_ver, imp_comment or 'probably *not* distribute', imp_loc))
                else:
                    errors.append("Warning: dependency %r (version %r imported from %r) was not found by pkg_resources."
                                  % (name, imp_ver, imp_loc))
                continue

            pr_ver, pr_loc = pkg_resources_vers_and_locs[name]
            try:
                pr_normver = normalized_version(pr_ver)
            except Exception, e:
                errors.append("Warning: version number %r found for dependency %r by pkg_resources could not be parsed. "
                              "The version found by import was %r from %r. "
                              "pkg_resources thought it should be found at %r. "
                              "The exception was %s: %s"
                              % (pr_ver, name, imp_ver, imp_loc, pr_loc, e.__class__.__name__, e))
            else:
                if imp_ver == 'unknown':
                    if name not in not_import_versionable:
                        errors.append("Warning: unexpectedly could not find a version number for dependency %r imported from %r. "
                                      "pkg_resources thought it should be version %r at %r."
                                      % (name, imp_loc, pr_ver, pr_loc))
                else:
                    try:
                        imp_normver = normalized_version(imp_ver)
                    except Exception, e:
                        errors.append("Warning: version number %r found for dependency %r (imported from %r) could not be parsed. "
                                      "pkg_resources thought it should be version %r at %r. "
                                      "The exception was %s: %s"
                                      % (imp_ver, name, imp_loc, pr_ver, pr_loc, e.__class__.__name__, e))
                    else:
                        if pr_ver == 'unknown' or (pr_normver != imp_normver):
                            if not os.path.normpath(os.path.realpath(pr_loc)) == os.path.normpath(os.path.realpath(imp_loc)):
                                errors.append("Warning: dependency %r found to have version number %r (normalized to %r, from %r) "
                                              "by pkg_resources, but version %r (normalized to %r, from %r) by import."
                                              % (name, pr_ver, str(pr_normver), pr_loc, imp_ver, str(imp_normver), imp_loc))

    imported_packages = set([p.lower() for (p, _) in imported_vers_and_locs_list])
    for pr_name, (pr_ver, pr_loc) in pkg_resources_vers_and_locs.iteritems():
        if pr_name not in imported_packages and pr_name not in ignorable:
            errors.append("Warning: dependency %r (version %r) found by pkg_resources not found by import."
                          % (pr_name, pr_ver))

    return errors


def get_error_string(errors, debug=False):
    from allmydata._auto_deps import install_requires

    msg = "\n%s\n" % ("\n".join(errors),)
    if debug:
        msg += ("\n"
                "For debugging purposes, the PYTHONPATH was\n"
                "  %r\n"
                "install_requires was\n"
                "  %r\n"
                "sys.path after importing pkg_resources was\n"
                "  %s\n"
                % (os.environ.get('PYTHONPATH'), install_requires, (os.pathsep+"\n  ").join(sys.path)) )
    return msg

def check_all_requirements():
    """This function returns a list of errors due to any failed checks."""

    from allmydata._auto_deps import install_requires

    errors = []

    # we require 2.4.4 on non-UCS-2, non-Redhat builds to avoid <http://www.python.org/news/security/PSF-2006-001/>
    # we require 2.4.3 on non-UCS-2 Redhat, because 2.4.3 is common on Redhat-based distros and will have patched the above bug
    # we require at least 2.4.2 in any case to avoid a bug in the base64 module: <http://bugs.python.org/issue1171487>
    if sys.maxunicode == 65535:
        if sys.version_info < (2, 4, 2) or sys.version_info[0] > 2:
            errors.append("Tahoe-LAFS current requires Python v2.4.2 or greater "
                          "for a UCS-2 build (but less than v3), not %r" %
                          (sys.version_info,))
    elif platform.platform().lower().find('redhat') >= 0:
        if sys.version_info < (2, 4, 3) or sys.version_info[0] > 2:
            errors.append("Tahoe-LAFS current requires Python v2.4.3 or greater "
                          "on Redhat-based distributions (but less than v3), not %r" %
                          (sys.version_info,))
    else:
        if sys.version_info < (2, 4, 4) or sys.version_info[0] > 2:
            errors.append("Tahoe-LAFS current requires Python v2.4.4 or greater "
                          "for a non-UCS-2 build (but less than v3), not %r" %
                          (sys.version_info,))

    vers_and_locs = dict(_vers_and_locs_list)
    for requirement in install_requires:
        try:
            check_requirement(requirement, vers_and_locs)
        except Exception, e:
            errors.append("%s: %s" % (e.__class__.__name__, e))

    if errors:
        raise PackagingError(get_error_string(errors, debug=True))

check_all_requirements()


def get_package_versions():
    return dict([(k, v) for k, (v, l, c) in _vers_and_locs_list])

def get_package_locations():
    return dict([(k, l) for k, (v, l, c) in _vers_and_locs_list])

def get_package_versions_string(show_paths=False, debug=False):
    res = []
    for p, (v, loc, comment) in _vers_and_locs_list:
        info = str(p) + ": " + str(v)
        if comment:
            info = info + " [%s]" % str(comment)
        if show_paths:
            info = info + " (%s)" % str(loc)
        res.append(info)

    output = ",\n".join(res) + "\n"

    if not hasattr(sys, 'frozen'):
        errors = cross_check_pkg_resources_versus_import()
        if errors:
            output += get_error_string(errors, debug=debug)

    return output
