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
    # we don't need much out of setuptools, but the __init__.py stuff does
    # need pkg_resources . We use >=11.3 here because that's what
    # "cryptography" requires (which is a sub-dependency of TLS-using
    # packages), so there's no point in requiring less.
    "setuptools >= 11.3",

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
    # * foolscap 0.8.0 generates 2048-bit RSA-with-SHA-256 signatures,
    #   rather than 1024-bit RSA-with-MD5. This also allows us to work
    #   with a FIPS build of OpenSSL.
    # * foolscap >= 0.12.3 provides tcp/tor/i2p connection handlers we need,
    #   and allocate_tcp_port
    # * foolscap >= 0.12.5 has ConnectionInfo and ReconnectionInfo
    "foolscap >= 0.12.5",

    # Needed for SFTP.
    # pycrypto 2.2 doesn't work due to <https://bugs.launchpad.net/pycrypto/+bug/620253>
    # pycrypto 2.4 doesn't work due to <https://bugs.launchpad.net/pycrypto/+bug/881130>
    "pycrypto >= 2.1.0, != 2.2, != 2.4",

    # pycryptopp-0.6.0 includes ed25519
    "pycryptopp >= 0.6.0",

    "service-identity",         # this is needed to suppress complaints about being unable to verify certs
    "characteristic >= 14.0.0", # latest service-identity depends on this version
    "pyasn1 >= 0.1.8",          # latest pyasn1-modules depends on this version
    "pyasn1-modules >= 0.0.5",  # service-identity depends on this

    # * On Linux we need at least Twisted 10.1.0 for inotify support
    #   used by the drop-upload frontend.
    # * We also need Twisted 10.1.0 for the FTP frontend in order for
    #   Twisted's FTP server to support asynchronous close.
    # * The SFTP frontend depends on Twisted 11.0.0 to fix the SSH server
    #   rekeying bug <https://twistedmatrix.com/trac/ticket/4395>
    # * The FTP frontend depends on Twisted >= 11.1.0 for
    #   filepath.Permissions
    # * Nevow 0.11.1 depends on Twisted >= 13.0.0.
    # * The SFTP frontend and manhole depend on the conch extra. However, we
    #   can't explicitly declare that without an undesirable dependency on gmpy,
    #   as explained in ticket #2740.
    # * Due to a setuptools bug, we need to declare a dependency on the tls
    #   extra even though we only depend on it via foolscap.
    # * Twisted >= 15.1.0 is the first version that provided the [tls] extra.
    # * Twisted-16.1.0 fixes https://twistedmatrix.com/trac/ticket/8223,
    #   which otherwise causes test_system to fail (DirtyReactorError, due to
    #   leftover timers)
    "Twisted[tls] >= 16.1.0",

    # We need Nevow >= 0.11.1 which can be installed using pip.
    "Nevow >= 0.11.1",

    # * pyOpenSSL is required in order for foolscap to provide secure connections.
    #   Since foolscap doesn't reliably declare this dependency in a machine-readable
    #   way, we need to declare a dependency on pyOpenSSL ourselves. Tahoe-LAFS does
    #   not *directly* depend on pyOpenSSL.
    # * pyOpenSSL >= 0.13 is needed in order to avoid
    #   <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2005>, and also to check the
    #   version of OpenSSL that pyOpenSSL is using.
    # * pyOpenSSL >= 0.14 is needed in order to avoid
    #   <https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2474>.
    "pyOpenSSL >= 0.14",
    "PyYAML >= 3.11",

    # in Python 3.3 stdlib
    "shutilwhich >= 1.1.0",
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
    ('OpenSSL',          None),
    ('simplejson',       'simplejson'),
    ('pycrypto',         'Crypto'),
    ('pyasn1',           'pyasn1'),
    ('service-identity', 'service_identity'),
    ('characteristic',   'characteristic'),
    ('pyasn1-modules',   'pyasn1_modules'),
    ('cryptography',     'cryptography'),
    ('cffi',             'cffi'),
    ('six',              'six'),
    ('enum34',           'enum'),
    ('pycparser',        'pycparser'),
    ('PyYAML',           'yaml'),
]

# Dependencies for which we don't know how to get a version number at run-time.
not_import_versionable = [
    'zope.interface',
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

if sys.platform == "win32":
    install_requires.append('pypiwin32')
    package_imports.append(('pypiwin32', 'win32api'))

setup_requires = []


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
