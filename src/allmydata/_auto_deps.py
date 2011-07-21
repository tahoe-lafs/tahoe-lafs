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

    "zope.interface",

    # On Windows we need at least Twisted 9.0 to avoid an indirect dependency on pywin32.
    # We also need Twisted 10.1 for the FTP frontend in order for Twisted's FTP server to
    # support asynchronous close.
    "Twisted >= 10.1.0",

    # foolscap < 0.5.1 had a performance bug which spent
    # O(N**2) CPU for transferring large mutable files
    # of size N.
    # foolscap < 0.6 is incompatible with Twisted 10.2.0.
    # foolscap 0.6.1 quiets a DeprecationWarning.
    # pyOpenSSL is required by foolscap for it (foolscap) to provide secure
    # connections. Foolscap doesn't reliably declare this dependency in a
    # machine-readable way, so we need to declare a dependency on pyOpenSSL
    # ourselves. Tahoe-LAFS doesn't *really* depend directly on pyOpenSSL,
    # so if something changes in the relationship between foolscap and
    # pyOpenSSL, such as foolscap requiring a specific version of pyOpenSSL,
    # or foolscap switching from pyOpenSSL to a different crypto library, we
    # need to update this declaration here.
    "foolscap >= 0.6.1",
    "pyOpenSSL",

    "Nevow >= 0.6.0",

    # Needed for SFTP. pyasn1 is needed by twisted.conch in Twisted >= 9.0.
    # pycrypto 2.2 doesn't work due to https://bugs.launchpad.net/pycrypto/+bug/620253
    "pycrypto == 2.0.1, == 2.1.0, >= 2.3",
    "pyasn1 >= 0.0.8a",

    # http://www.voidspace.org.uk/python/mock/
    "mock",

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
    ('mock',            'mock'),
]

def require_more():
    import platform, sys

    if platform.machine().lower() in ['i386', 'x86_64', 'amd64', 'x86', '']:
        # pycryptopp v0.5.20 fixes bugs in SHA-256 and AES on x86 or amd64
        # (from Crypto++ revisions 470, 471, 480, 492).  The '' is there
        # in case platform.machine is broken and this is actually an x86
        # or amd64 machine.
        install_requires.append("pycryptopp >= 0.5.20")
    else:
        # pycryptopp v0.5.13 had a new bundled version of Crypto++
        # (v5.6.0) and a new bundled version of setuptools (although that
        # shouldn't make any difference to users of pycryptopp).
        install_requires.append("pycryptopp >= 0.5.14")

    # Sqlite comes built into Python >= 2.5, and is provided by the "pysqlite"
    # distribution for Python 2.4.
    try:
        import sqlite3
        sqlite3 # hush pyflakes
        package_imports.append(('sqlite3', 'sqlite3'))
    except ImportError:
        # pysqlite v2.0.5 was shipped in Ubuntu 6.06 LTS "dapper" and Nexenta NCP 1.
        install_requires.append("pysqlite >= 2.0.5")
        package_imports.append(('pysqlite', 'pysqlite2.dbapi2'))

    # Don't try to get the version number of setuptools in frozen builds, because
    # that triggers 'site' processing that causes failures. Note that frozen
    # builds still (unfortunately) import pkg_resources in .tac files, so the
    # entry for setuptools in install_requires above isn't conditional.
    if not hasattr(sys, 'frozen'):
        package_imports.append(('setuptools', 'setuptools'))

require_more()

deprecation_messages = [
    "the sha module is deprecated; use the hashlib module instead",
    "object.__new__\(\) takes no parameters",
    "The popen2 module is deprecated.  Use the subprocess module.",
    "the md5 module is deprecated; use hashlib instead",
    "twisted.web.error.NoResource is deprecated since Twisted 9.0.  See twisted.web.resource.NoResource.",
    "the sets module is deprecated",
]

deprecation_imports = [
    'nevow',
    'twisted.persisted.sob',
    'twisted.python.filepath',
    'Crypto.Hash.SHA',
]
