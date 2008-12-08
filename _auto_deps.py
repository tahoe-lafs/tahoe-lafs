install_requires=[
    # we require 0.6c6 to build, but can handle older versions to run
    "setuptools >= 0.6c6",

                  # pycryptopp < 0.5 had a bug which, using a Microsoft
                  # compiler, or using some versions of g++ while linking
                  # against certain older versions of Crypto++, would cause
                  # incorrect AES results.
                  "pycryptopp >= 0.5",
                  "zfec >= 1.1.0",

                  # We had a unicode problem with simplejson 1.8.1 on dapper -- see ticket #543,
                  # but we want to install using Gutsy or Hardy simplejson .deb's if possible --
                  # see ticket #555.  Feisty has simplejson 1.4
                  "simplejson >= 1.4",

                  "zope.interface",
                  "Twisted >= 2.4.0",
                  "foolscap[secure_connections] >= 0.3.1",
                  "Nevow >= 0.6.0",
                  ]
import sys
if hasattr(sys, 'frozen'):
    install_requires=[]

def require_auto_deps():
    """
    The purpose of this function is to raise a pkg_resources exception if any of the
    requirements can't be imported.  This is just to give earlier and more explicit error
    messages, as opposed to waiting until the source code tries to import some module from one
    of these packages and gets an ImportError.  This function gets called from
    src/allmydata/__init__.py .
    """
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
