install_requires=["zfec >= 1.1.0",
                  "foolscap >= 0.2.5",
                  "simplejson >= 1.4",
                  "pycryptopp >= 0.2.8",
                  "nevow >= 0.6.0",
                  "zope.interface",
                  "twisted >= 2.4.0",
                  # we require 0.6c8 to build, but can handle older versions
                  # to run
                  "setuptools >= 0.6a9",
                  ]
import sys
if hasattr(sys, 'frozen'):
    install_requires=[]

def require_auto_deps():
    try:
        import pkg_resources
    except:
        # Then we can't assert that the versions of these packages are the right
        # versions, but we can still try to use them anyway...
        pass
    else:
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

if __name__ == "__main__":
    require_auto_deps()
