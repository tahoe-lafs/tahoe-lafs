install_requires=["zfec >= 1.1.0",
                  "foolscap >= 0.2.3",
                  "simplejson >= 1.7.1",
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
            pkg_resources.require(requirement)
    for requirement in install_requires:
        reqparts = requirement.split()
        name = reqparts[0]
        __import__(name)

if __name__ == "__main__":
    require_auto_deps()
