import os

miscdeps=os.path.join('misc', 'dependencies')
dependency_links=[os.path.join(miscdeps, t) for t in os.listdir(miscdeps) if t.endswith(".tar")]

# By adding a web page to the dependency_links we are able to put new packages
# up there and have them be automatically discovered by existing copies of the
# tahoe source when that source was built.
dependency_links.append("http://allmydata.org/trac/tahoe/wiki/Dependencies")

install_requires=["zfec >= 1.3.0",
                  "foolscap >= 0.2.3",
                  "simplejson >= 1.7.3",
                  "pycryptopp >= 0.2.9",
                  "nevow >= 0.6.0",
                  "zope.interface >= 3.1.0",
                  ]

if __name__ == '__main__':
    print "install_requires:"
    for ir in install_requires:
        print " ", ir
    print
    print "dependency_links:"
    for dl in dependency_links:
        print " ", dl
    print
