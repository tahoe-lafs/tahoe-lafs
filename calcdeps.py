
import os.path, sys

# This form is used when the unpacked source distribution is copied into our
# tree:
#  "file:misc/dependencies/zfec-1.0.2/"
# and this form is used when we provide a tarball
#  "file:misc/dependencies/zfec-1.0.2.tar.gz",
# The file: URL can start with either 'misc' or './misc' to get a relative path.

dependency_tarballs=[ os.path.join("misc", "dependencies", fn)
                      for fn in os.listdir(os.path.join("misc", "dependencies"))
                      if fn.endswith(".tar.gz") ]

dependency_links=["http://allmydata.org/trac/tahoe/wiki/Dependencies"] + dependency_tarballs

nevow_version = None
try:
    import nevow
    nevow_version = nevow.__version__
except ImportError:
    pass

install_requires=["zfec >= 1.0.3",
                  "foolscap >= 0.1.6",
                  "simplejson >= 1.4",
                  "pycryptopp >= 0.2.5",
                  ]


# We also require zope.interface, but some older versions of setuptools such
# as setuptools v0.6a9 don't handle the "." in its name correctly, and anyway
# people have to manually install Twisted before using our automatic
# dependency resolution, and they have to manually install zope.interface in
# order to install Twisted.

# Ubuntu Dapper includes nevow-0.6.0 and twisted-2.2.0, both of which work.
# However, setuptools doesn't know about them, so our install_requires=
# dependency upon nevow causes our 'build-deps' step to try and build the
# latest version (nevow-0.9.18), which *doesn't* work with twisted-2.2.0 . To
# work around this, remove nevow from our dependency list if we detect that
# we've got nevow-0.6.0 installed. This will allow build-deps (and everything
# else) to work on dapper systems that have the python-nevow package
# installed, and shouldn't hurt any other systems. Dapper systems *without*
# python-nevow will try to build it (and will fail unless they also have a
# newer version of Twisted installed).

if nevow_version != "0.6.0":
    install_requires.append("nevow >= 0.6.0")


if __name__ == '__main__':
    print "install_requires:"
    for ir in install_requires:
        print " ", ir
    print
    print "dependency_links:"
    for dl in dependency_links:
        print " ", dl
    print
