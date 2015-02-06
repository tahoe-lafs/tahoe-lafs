# Note: please minimize imports in this file. In particular, do not import
# any module from Tahoe-LAFS or its dependencies, and do not import any
# modules at all at global level. That includes setuptools and pkg_resources.
# It is ok to import modules from the Python Standard Library if they are
# always available, or the import is protected by try...except ImportError.

# The semantics for requirement specs changed incompatibly in setuptools 8,
# which now follows PEP 440. The requirements used in this file must be valid
# under both the old and new semantics. That can be achieved by limiting
# requirement specs to one of the following forms:
#
#   * >= X, <= Y where X < Y
#   * >= X, != Y, != Z, ... where X < Y < Z...
#
# (In addition, check_requirement in allmydata/__init__.py only supports
# >=, <= and != operators.)

install_requires = [
    # We require newer versions of setuptools (actually
    # zetuptoolz) to build, but can handle older versions to run.
    "setuptools >= 0.6c6",

    "zfec >= 1.1.0",

    # Feisty has simplejson 1.4
    "simplejson >= 1.4",

    # zope.interface >= 3.6.0 is required for Twisted >= 12.1.0.
    # zope.interface 3.6.3 and 3.6.4 are incompatible with Nevow (#1435).
    "zope.interface >= 3.6.0, != 3.6.3, != 3.6.4",

    # * foolscap < 0.5.1 had a performance bug which spent O(N**2) CPU for
    #   transferring large mutable files of size N.
    # * foolscap < 0.6 is incompatible with Twisted 10.2.0.
    # * foolscap 0.6.1 quiets a DeprecationWarning.
    # * foolscap < 0.6.3 is incompatible with Twisted 11.1.0 and newer.
    "foolscap >= 0.6.3",

    # Needed for SFTP.
    # pycrypto 2.2 doesn't work due to <https://bugs.launchpad.net/pycrypto/+bug/620253>
    # pycrypto 2.4 doesn't work due to <https://bugs.launchpad.net/pycrypto/+bug/881130>
    "pycrypto >= 2.1.0, != 2.2, != 2.4",

    # <http://www.voidspace.org.uk/python/mock/>, 0.8.0 provides "call"
    "mock >= 0.8.0",

    # pycryptopp-0.6.0 includes ed25519
    "pycryptopp >= 0.6.0",
]

# Includes some indirect dependencies, but does not include allmydata.
# These are in the order they should be listed by --version, etc.
package_imports = [
    # package name       module name
    ('foolscap',         'foolscap'),
    ('pycryptopp',       'pycryptopp'),
    ('zfec',             'zfec'),
    ('Twisted',          'twisted'),
    ('Nevow',            'nevow'),
    ('zope.interface',   'zope.interface'),
    ('python',           None),
    ('platform',         None),
    ('pyOpenSSL',        'OpenSSL'),
    ('simplejson',       'simplejson'),
    ('pycrypto',         'Crypto'),
    ('pyasn1',           'pyasn1'),
    ('mock',             'mock'),
]

# Dependencies for which we don't know how to get a version number at run-time.
not_import_versionable = [
    'zope.interface',
    'mock',
    'pyasn1',
    'pyasn1-modules',
]

# Dependencies reported by pkg_resources that we can safely ignore.
ignorable = [
    'argparse',
    'pyutil',
    'zbase32',
    'distribute',
    'twisted-web',
    'twisted-core',
    'twisted-conch',
]

import sys

# Don't try to get the version number of setuptools in frozen builds, because
# that triggers 'site' processing that causes failures. Note that frozen
# builds still (unfortunately) import pkg_resources in .tac files, so the
# entry for setuptools in install_requires above isn't conditional.
if not hasattr(sys, 'frozen'):
    package_imports.append(('setuptools', 'setuptools'))


# Splitting the dependencies for Windows and non-Windows helps to fix
# <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2249> and
# <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2028>.

if True: # hack to test all platforms with Twisted 15.0.0
    install_requires += [
        # * On Linux we need at least Twisted 10.1.0 for inotify support
        #   used by the drop-upload frontend.
        # * Nevow 0.11.1 requires Twisted >= 13.0.0 so we might as well
        #   require it directly; this helps to work around
        #   <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2286>.
        #   This also satisfies the requirements for the FTP and SFTP
        #   frontends and cloud backend mentioned in the Windows section
        #   above.
        #
        "Twisted >= 15.0.0",

        # Nevow >= 0.11.1 can be installed using pip.
        "Nevow >= 0.11.1",

        "service-identity",         # this is needed to suppress complaints about being unable to verify certs
        "characteristic >= 14.0.0", # latest service-identity depends on this version
        "pyasn1 >= 0.1.4",          # latest pyasn1-modules depends on this version
        "pyasn1-modules",           # service-identity depends on this
    ]

    package_imports += [
        ('service-identity', 'service_identity'),
        ('characteristic',   'characteristic'),
        ('pyasn1-modules',   'pyasn1_modules'),
    ]


# * pyOpenSSL is required in order for foolscap to provide secure connections.
#   Since foolscap doesn't reliably declare this dependency in a machine-readable
#   way, we need to declare a dependency on pyOpenSSL ourselves. Tahoe-LAFS does
#   not *directly* depend on pyOpenSSL.
#
# * pyOpenSSL >= 0.13 is needed in order to avoid
#   <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2005>.
#
# * pyOpenSSL >= 0.14 is built on the 'cryptography' package which depends
#   on 'cffi' (and indirectly several other packages). Unfortunately cffi
#   attempts to compile code dynamically, which causes problems on many systems.
#   It also depends on the libffi OS package which may not be installed.
#   <https://bitbucket.org/cffi/cffi/issue/109/enable-sane-packaging-for-cffi>
#   <https://bitbucket.org/cffi/cffi/issue/70/cant-install-cffi-using-pip-on-windows>
#
#   So, if pyOpenSSL 0.14 has *already* been installed and is importable, we
#   want to accept it; otherwise we ask for pyOpenSSL 0.13 or 0.13.1.
#   <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2193>
#
#   We don't rely on pkg_resources to tell us the installed pyOpenSSL version
#   number, because pkg_resources telling us that we have 0.14 is not sufficient
#   evidence that 0.14 will be the imported version (or will work correctly).
#   One possible reason why it might not be is explained in
#   <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1246#comment:6> and
#   <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1258>.

_can_use_pyOpenSSL_0_14 = False
try:
    import OpenSSL
    pyOpenSSL_ver = OpenSSL.__version__.split('.')
    if int(pyOpenSSL_ver[0]) > 0 or int(pyOpenSSL_ver[1]) >= 14:
        _can_use_pyOpenSSL_0_14 = True
except Exception:
    pass

if _can_use_pyOpenSSL_0_14:
    install_requires += [
        # Although we checked for pyOpenSSL >= 0.14 above, we only actually
        # need pyOpenSSL >= 0.13; requiring 0.14 here cannot help.
        "pyOpenSSL >= 0.13",

        # ... and now all the new stuff that pyOpenSSL 0.14 transitively
        # depends on. We specify these explicitly because setuptools is
        # bad at correctly resolving indirect dependencies (e.g. see
        # <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2286>).
        #
        "cryptography",
        "cffi >= 0.8",          # latest cryptography depends on this version
        "six >= 1.4.1",         # latest cryptography depends on this version
        "enum34",               # latest cryptography depends on this
        "pycparser",            # cffi depends on this
    ]

    package_imports += [
        ('cryptography',     'cryptography'),
        ('cffi',             'cffi'),
        ('six',              'six'),
        ('enum34',           'enum'),
        ('pycparser',        'pycparser'),
    ]
else:
    install_requires += [
        "pyOpenSSL >= 0.13, <= 0.13.1",
    ]


# These are suppressed globally:

global_deprecation_messages = [
    "BaseException.message has been deprecated as of Python 2.6",
    "twisted.internet.interfaces.IFinishableConsumer was deprecated in Twisted 11.1.0: Please use IConsumer (and IConsumer.unregisterProducer) instead.",
    "twisted.internet.interfaces.IStreamClientEndpointStringParser was deprecated in Twisted 14.0.0: This interface has been superseded by IStreamClientEndpointStringParserWithReactor.",
]

# These are suppressed while importing dependencies:

deprecation_messages = [
    "the sha module is deprecated; use the hashlib module instead",
    "object.__new__\(\) takes no parameters",
    "The popen2 module is deprecated.  Use the subprocess module.",
    "the md5 module is deprecated; use hashlib instead",
    "twisted.web.error.NoResource is deprecated since Twisted 9.0.  See twisted.web.resource.NoResource.",
    "the sets module is deprecated",
]

runtime_warning_messages = [
    "Not using mpz_powm_sec.  You should rebuild using libgmp >= 5 to avoid timing attack vulnerability.",
]

warning_imports = [
    'nevow',
    'twisted.persisted.sob',
    'twisted.python.filepath',
    'Crypto.Hash.SHA',
]
