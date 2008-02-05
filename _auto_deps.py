install_requires=["zfec >= 1.1.0",
                  "foolscap >= 0.2.4",
                  "simplejson >= 1.4",
                  "pycryptopp >= 0.2.8",
                  "nevow >= 0.6.0",
                  "zope.interface",
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
                # either means that it isn't installed, or it is installed in
                # a way that pkg_resources can't find it (but regular python
                # might). The __import__ below will pass the second case,
                # which is good enough for us. There are several
                # distributions which provide our dependencies just fine, but
                # they don't ship .egg-info files. Note that if there *is* an
                # .egg-info file, but it indicates an older version, then
                # we'll get a VersionConflict error instead of
                # DistributionNotFound.
                pass
    for requirement in install_requires:
        reqparts = requirement.split()
        name = reqparts[0]
        __import__(name)

if __name__ == "__main__":
    require_auto_deps()
