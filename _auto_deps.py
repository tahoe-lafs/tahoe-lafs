install_requires=[
                  # we require newer versions of setuptools (actually
                  # zetuptoolz) to build, but can handle older versions to run
                  "setuptools >= 0.6c6",

                  "zfec >= 1.1.0",

                  # Feisty has simplejson 1.4
                  "simplejson >= 1.4",

                  "zope.interface",
                  "Twisted >= 2.4.0",
                  "foolscap[secure_connections] >= 0.4.1",
                  "Nevow >= 0.6.0",

                  # pycryptopp v0.5.15 applied a patch from Wei Dai to fix an
                  # error in x86 assembly on CPUs that can't do SSE2.  Fixes
                  # http://allmydata.org/trac/pycryptopp/ticket/24 .

                  # pycryptopp v0.5.14 patched the embedded Crypto++ to remove
                  # the usages of time functions, thus allowing mingw to build
                  # and link it for Python 2.6.  If I knew a convenient,
                  # reliable way to test whether the compiler that builds
                  # pycryptopp will be mingw then I guess I would add that,
                  # along with the Python >= v2.6 and the platform == Windows.
                  # This is to work-around
                  # http://sourceforge.net/tracker/?func=detail&aid=2805976&group_id=2435&atid=302435
                  # .
                  "pycryptopp >= 0.5.15",

                  # Needed for SFTP. Commented-out pending tests, see #953.
                  # "pycrypto >= 2.0.1",

                  # Will be needed to test web apps, but not yet. See #1001.
                  #"windmill >= 1.3",
                  ]

# Sqlite comes built into Python >= 2.5, and is provided by the "pysqlite"
# distribution for Python 2.4.
import sys
if sys.version_info < (2, 5):
    # pysqlite v2.0.5 was shipped in Ubuntu 6.06 LTS "dapper" and Nexenta NCP 1.
    install_requires.append("pysqlite >= 2.0.5")

## The following block is commented-out because there is not currently a pywin32 package which
## can be easy_install'ed and also which actually makes "import win32api" succeed.
## See http://sourceforge.net/tracker/index.php?func=detail&aid=1799934&group_id=78018&atid=551954
## Users have to manually install pywin32 on Windows before installing Tahoe.
##import platform
##if platform.system() == "Windows":
##    # Twisted requires pywin32 if it is going to offer process management functionality, or if
##    # it is going to offer iocp reactor.  We currently require process management.  It would be
##    # better if Twisted would declare that it requires pywin32 if it is going to offer process
##    # management.  That is twisted ticket #3238 -- http://twistedmatrix.com/trac/ticket/3238 .
##    # On the other hand, Tahoe also depends on pywin32 for getting free disk space statistics
##    # (although that is not a hard requirement: if win32api can't be imported then we don't
##    # rely on having the disk stats).
##    install_requires.append('pywin32')

if hasattr(sys, 'frozen'): # for py2exe
    install_requires=[]

def require_python_2_with_working_base64():
    import sys
    if sys.version_info[0] != 2:
        raise NotImplementedError("Tahoe-LAFS current requires Python v2.4.2 or greater (but less than v3), not %r" % (sys.version_info,))

    # make sure we have a working base64.b32decode. The one in
    # python2.4.[01] was broken.
    nodeid_b32 = 't5g7egomnnktbpydbuijt6zgtmw4oqi5'
    import base64
    nodeid = base64.b32decode(nodeid_b32.upper())
    if nodeid != "\x9fM\xf2\x19\xcckU0\xbf\x03\r\x10\x99\xfb&\x9b-\xc7A\x1d":
        raise NotImplementedError("There is a bug in this base64 module: %r.  This was a known issue in Python v2.4.0 and v2.4.1 (http://bugs.python.org/issue1171487 ).  Tahoe-LAFS current requires Python v2.4.2 or greater (but less than v3).  The current Python version is %r" % (base64, sys.version_info,))

def require_auto_deps():
    """
    The purpose of this function is to raise a pkg_resources exception if any of the
    requirements can't be imported.  This is just to give earlier and more explicit error
    messages, as opposed to waiting until the source code tries to import some module from one
    of these packages and gets an ImportError.  This function gets called from
    src/allmydata/__init__.py .
    """
    require_python_2_with_working_base64()

    import pkg_resources
    for requirement in install_requires:
        try:
            pkg_resources.require(requirement)
        except pkg_resources.DistributionNotFound:
            # there is no .egg-info present for this requirement, which
            # either means that it isn't installed, or it is installed in a
            # way that pkg_resources can't find it (but regular python
            # might).  There are several older Linux distributions which
            # provide our dependencies just fine, but they don't ship
            # .egg-info files. Note that if there *is* an .egg-info file,
            # but it shows a too-old version, then we'll get a
            # VersionConflict error instead of DistributionNotFound.
            pass

def get_package_versions_from_setuptools():
    import pkg_resources
    return dict([(p.project_name, (p.version, p.location)) for p in pkg_resources.require('allmydata-tahoe')])
