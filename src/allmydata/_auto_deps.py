# Note: please minimize imports in this file. In particular, do not import
# any module from Tahoe-LAFS or its dependencies, and do not import any
# modules at all at global level. That includes setuptools and pkg_resources.
# It is ok to import modules from the Python Standard Library if they are
# always available, or the import is protected by try...except ImportError.

install_requires = [
    # we require newer versions of setuptools (actually
    # zetuptoolz) to build, but can handle older versions to run
    "setuptools >= 0.6c6",

    "zfec >= 1.1.0",

    # Feisty has simplejson 1.4
    "simplejson >= 1.4",

    # zope.interface >= 3.6.0 is required for Twisted >= 12.1.0.
    # zope.interface 3.6.3 and 3.6.4 are incompatible with Nevow (#1435).
    "zope.interface == 3.6.0, == 3.6.1, == 3.6.2, >= 3.6.5",

    # * On Windows we need at least Twisted 9.0 to avoid an indirect
    #   dependency on pywin32.
    # * On Linux we need at least Twisted 10.1.0 for inotify support used by
    #   the drop-upload frontend.
    # * We also need Twisted 10.1 for the FTP frontend in order for Twisted's
    #   FTP server to support asynchronous close.
    # * The cloud backend depends on Twisted 10.2.0 which includes the fix to
    #   https://twistedmatrix.com/trac/ticket/411
    # * The SFTP frontend depends on Twisted 11.0.0 to fix the SSH server
    #   rekeying bug http://twistedmatrix.com/trac/ticket/4395
    # * The cloud backend depends on Twisted 12.1.0 for HTTPConnectionPool.
    # * IPv6 support will also depend on Twisted 12.1.0.
    #
    "Twisted >= 12.1.0",

    # * foolscap < 0.5.1 had a performance bug which spent O(N**2) CPU for
    #   transferring large mutable files of size N.
    # * foolscap < 0.6 is incompatible with Twisted 10.2.0.
    # * foolscap 0.6.1 quiets a DeprecationWarning.
    # * foolscap < 0.6.3 is incompatible with Twisted-11.1.0 and newer.
    # * foolscap 0.8.0 generates 2048-bit RSA-with-SHA-256 signatures,
    #   rather than 1024-bit RSA-with-MD5. This also allows us to work
    #   with a FIPS build of OpenSSL.
    # * pyOpenSSL is required by foolscap for it (foolscap) to provide secure
    #   connections. Foolscap doesn't reliably declare this dependency in a
    #   machine-readable way, so we need to declare a dependency on pyOpenSSL
    #   ourselves. Tahoe-LAFS doesn't *really* depend directly on pyOpenSSL,
    #   so if something changes in the relationship between foolscap and
    #   pyOpenSSL, such as foolscap requiring a specific version of
    #   pyOpenSSL, or foolscap switching from pyOpenSSL to a different crypto
    #   library, we need to update this declaration here.
    #
    "foolscap >= 0.8.0",
    "pyOpenSSL >= 0.13",

    "Nevow >= 0.6.0",

    # Needed for SFTP. pyasn1 is needed by twisted.conch in Twisted >= 9.0.
    # pycrypto 2.2 doesn't work due to https://bugs.launchpad.net/pycrypto/+bug/620253
    # pycrypto 2.4 doesn't work due to https://bugs.launchpad.net/pycrypto/+bug/881130
    "pycrypto == 2.1.0, == 2.3, >= 2.4.1",
    "pyasn1 >= 0.0.8a",

    # pycryptopp-0.6.0 includes ed25519
    "pycryptopp >= 0.6.0",

    # needed for cloud backend
    "txAWS == 0.2.1.post5",
    "oauth2client == 1.1.0",

    # Will be needed to test web apps, but not yet. See #1001.
    #"windmill >= 1.3",

]

# Includes some indirect dependencies, but does not include allmydata.
# These are in the order they should be listed by --version, etc.
package_imports = [
    # package name      module name
    ('foolscap',        'foolscap'),
    ('pycryptopp',      'pycryptopp'),
    ('zfec',            'zfec'),
    ('Twisted',         'twisted'),
    ('Nevow',           'nevow'),
    ('zope.interface',  'zope.interface'),
    ('python',          None),
    ('platform',        None),
    ('pyOpenSSL',       'OpenSSL'),
    ('simplejson',      'simplejson'),
    ('pycrypto',        'Crypto'),
    ('pyasn1',          'pyasn1'),
    ('txAWS',           'txaws'),
    ('oauth2client',    'oauth2client'),
    ('python-dateutil', 'dateutil'),
    ('httplib2',        'httplib2'),
    ('python-gflags',   'gflags'),
]

# Packages we cannot find a version number for by importing.
not_import_versionable_packages = ('zope.interface', 'mock', 'pyasn1', 'python-gflags')

# Packages that pkg_resources might report, but we don't care about checking their version.
ignorable_packages = ('argparse', 'pyutil', 'zbase32', 'distribute', 'twisted-web', 'twisted-core', 'twisted-conch', 'six')


def require_more():
    import sys

    # Don't try to get the version number of setuptools in frozen builds, because
    # that triggers 'site' processing that causes failures. Note that frozen
    # builds still (unfortunately) import pkg_resources in .tac files, so the
    # entry for setuptools in install_requires above isn't conditional.
    if not hasattr(sys, 'frozen'):
        package_imports.append(('setuptools', 'setuptools'))

require_more()


# These are suppressed globally:

global_deprecation_messages = [
    "BaseException.message has been deprecated as of Python 2.6",
    "twisted.internet.interfaces.IFinishableConsumer was deprecated in Twisted 11.1.0: Please use IConsumer (and IConsumer.unregisterProducer) instead.",
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

user_warning_messages = [
    "Hashing uninitialized InterfaceClass instance",
    "Reliable disconnection notification requires pywin32 215 or later",
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
