# Note: please minimize imports in this file. In particular, do not import
# any module from Tahoe-LAFS or its dependencies, and do not import any
# modules at all at global level. That includes setuptools and pkg_resources.
# It is ok to import modules from the Python Standard Library if they are
# always available, or the import is protected by try...except ImportError.

# Includes some indirect dependencies, but does not include allmydata.
# These are in the order they should be listed by --version, etc.
package_imports = [
    # package name       module name
    ('foolscap',         'foolscap'),
    ('zfec',             'zfec'),
    ('Twisted',          'twisted'),
    ('Nevow',            'nevow'),
    ('zope.interface',   'zope.interface'),
    ('python',           None),
    ('platform',         None),
    ('pyOpenSSL',        'OpenSSL'),
    ('OpenSSL',          None),
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
    ('magic-wormhole',   'wormhole'),
    ('setuptools',       'setuptools'),
    ('eliot',            'eliot'),
    ('attrs',            'attr'),
    ('autobahn',         'autobahn'),
]

# Dependencies for which we don't know how to get a version number at run-time.
not_import_versionable = [
    'zope.interface',
]

# Dependencies reported by pkg_resources that we can safely ignore.
ignorable = [
    'argparse',
    'distribute',
    'twisted-web',
    'twisted-core',
    'twisted-conch',
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
]
