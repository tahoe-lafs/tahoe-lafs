install_requires=[
                  # we require newer versions of setuptools (actually
                  # zetuptoolz) to build, but can handle older versions to run
                  "setuptools >= 0.6c6",

                  # pycryptopp < 0.5 had a bug which, using a Microsoft
                  # compiler, or using some versions of g++ while linking
                  # against certain older versions of Crypto++, would cause
                  # incorrect AES results.
                  "pycryptopp >= 0.5",
                  "zfec >= 1.1.0",

                  # Feisty has simplejson 1.4
                  "simplejson >= 1.4",

                  "zope.interface",
                  "Twisted >= 2.4.0",
                  "foolscap[secure_connections] >= 0.4.1",
                  "Nevow >= 0.6.0",
                  ]

## The following block is commented-out because there is not currently a pywin32 package which
## can be easy_install'ed and also which actually makes "import win32api" succeed.  Users have
## to manually install pywin32 on Windows before installing Tahoe.
##import platform
##if platform.system() == "Windows":
##    # Twisted requires pywin32 if it is going to offer process management functionality, or if
##    # it is going to offer iocp reactor.  We currently require process management.  It would be
##    # better if Twisted would declare that it requires pywin32 if it is going to offer process
##    # management.  Then the specification and the evolution of Twisted's reliance on pywin32 can
##    # be confined to the Twisted setup data, and Tahoe can remain blissfully ignorant about such
##    # things as if a future version of Twisted requires a different version of pywin32, or if a
##    # future version of Twisted implements process management without using pywin32 at all,
##    # etc..  That is twisted ticket #3238 -- http://twistedmatrix.com/trac/ticket/3238 .  But
##    # until Twisted does that, Tahoe needs to be non-ignorant of the following requirement:
##    install_requires.append('pywin32')

import sys
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
