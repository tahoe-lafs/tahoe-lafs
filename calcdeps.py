import os

miscdeps=os.path.join('misc', 'dependencies')
dependency_links=[os.path.join(miscdeps, t) for t in os.listdir(miscdeps) if t.endswith(".tar")]

# By adding a web page to the dependency_links we are able to put new packages
# up there and have them be automatically discovered by existing copies of the
# tahoe source when that source was built.
dependency_links.append("http://allmydata.org/trac/tahoe/wiki/Dependencies")

install_requires=["zfec >= 1.0.3",
                  "foolscap >= 0.2.2",
                  "simplejson >= 1.4",
                  "pycryptopp >= 0.2.8",
                  ]

nevow_version = None
try:
    import nevow
    nevow_version = nevow.__version__
except ImportError:
    pass

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
