install_requires=["zfec >= 1.1.0",
                  "foolscap[secure_connections] >= 0.3.0",
                  "simplejson >= 1.4",

                  # pycryptopp < 0.5 had a bug which, using a Microsoft
                  # compiler, or using some versions of g++ while linking
                  # against certain older versions of Crypto++, would cause
                  # incorrect AES results.
                  "pycryptopp >= 0.5",
                  "Nevow >= 0.6.0",
                  "zope.interface",
                  "Twisted >= 2.4.0",

                  # we require 0.6c8 to build, but can handle older versions
                  # to run
                  "setuptools >= 0.6a9",
                  ]
import sys
if hasattr(sys, 'frozen'):
    install_requires=[]

def require_auto_deps():
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
