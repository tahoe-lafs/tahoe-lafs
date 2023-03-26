"""
URIs (kinda sorta, really they're capabilities?).

Ported to Python 3.

Methods ending in to_string() are actually to_bytes(), possibly should be fixed
in follow-up port.
"""

from past.builtins import unicode, long

import re
from typing import Type

from zope.interface import implementer
from twisted.python.components import registerAdapter

from allmydata.storage.server import si_a2b, si_b2a
from allmydata.util import base32, hashutil
from allmydata.util.assertutil import _assert
from allmydata.interfaces import IURI, IDirnodeURI, IFileURI, IImmutableFileURI, \
    IVerifierURI, IMutableFileURI, IDirectoryURI, IReadonlyDirectoryURI, \
    MustBeDeepImmutableError, MustBeReadonlyError, CapConstraintError

class BadURIError(CapConstraintError):
    pass

# The URI shall be an ASCII representation of a reference to the file/directory.
# It shall contain enough information to retrieve and validate the contents.
# It shall be expressed in a limited character set (currently base32 plus ':' and
# capital letters, but future URIs might use a larger charset).

# TODO:
#  - rename all of the *URI classes/interfaces to *Cap
#  - make variable and method names consistently use _uri for an URI string,
#    and _cap for a Cap object (decoded URI)

BASE32STR_128bits = b'(%s{25}%s)' % (base32.BASE32CHAR, base32.BASE32CHAR_3bits)
BASE32STR_256bits = b'(%s{51}%s)' % (base32.BASE32CHAR, base32.BASE32CHAR_1bits)

NUMBER=b'([0-9]+)'


class _BaseURI(object):
    def __hash__(self):
        return self.to_string().__hash__()

    def __eq__(self, them):
        if isinstance(them, _BaseURI):
            return self.to_string() == them.to_string()
        else:
            return False

    def __ne__(self, them):
        if isinstance(them, _BaseURI):
            return self.to_string() != them.to_string()
        else:
            return True

    def get_storage_index(self):
        return self.storage_index


@implementer(IURI, IImmutableFileURI)
class CHKFileURI(_BaseURI):

    BASE_STRING=b'URI:CHK:'
    STRING_RE=re.compile(b'^URI:CHK:'+BASE32STR_128bits+b':'+
                         BASE32STR_256bits+b':'+NUMBER+b':'+NUMBER+b':'+NUMBER+
                         b'$')

    def __init__(self, key, uri_extension_hash, needed_shares, total_shares,
                 size):
        self.key = key
        self.uri_extension_hash = uri_extension_hash
        self.needed_shares = needed_shares
        self.total_shares = total_shares
        self.size = size
        self.storage_index = hashutil.storage_index_hash(self.key)
        if not len(self.storage_index) == 16: # sha256 hash truncated to 128
            raise BadURIError("storage index must be 16 bytes long")

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("%r doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

    def to_string(self):
        assert isinstance(self.needed_shares, int)
        assert isinstance(self.total_shares, int)
        assert isinstance(self.size, (int,long))

        return (b'URI:CHK:%s:%s:%d:%d:%d' %
                (base32.b2a(self.key),
                 base32.b2a(self.uri_extension_hash),
                 self.needed_shares,
                 self.total_shares,
                 self.size))

    def is_readonly(self):
        return True

    def is_mutable(self):
        return False

    def get_readonly(self):
        return self

    def get_size(self):
        return self.size

    def get_verify_cap(self):
        return CHKFileVerifierURI(storage_index=self.storage_index,
                                  uri_extension_hash=self.uri_extension_hash,
                                  needed_shares=self.needed_shares,
                                  total_shares=self.total_shares,
                                  size=self.size)


@implementer(IVerifierURI)
class CHKFileVerifierURI(_BaseURI):

    BASE_STRING=b'URI:CHK-Verifier:'
    STRING_RE=re.compile(b'^URI:CHK-Verifier:'+BASE32STR_128bits+b':'+
                         BASE32STR_256bits+b':'+NUMBER+b':'+NUMBER+b':'+NUMBER)

    def __init__(self, storage_index, uri_extension_hash,
                 needed_shares, total_shares, size):
        assert len(storage_index) == 16
        self.storage_index = storage_index
        self.uri_extension_hash = uri_extension_hash
        self.needed_shares = needed_shares
        self.total_shares = total_shares
        self.size = size

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(si_a2b(mo.group(1)), base32.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

    def to_string(self):
        assert isinstance(self.needed_shares, int)
        assert isinstance(self.total_shares, int)
        assert isinstance(self.size, (int,long))

        return (b'URI:CHK-Verifier:%s:%s:%d:%d:%d' %
                (si_b2a(self.storage_index),
                 base32.b2a(self.uri_extension_hash),
                 self.needed_shares,
                 self.total_shares,
                 self.size))

    def is_readonly(self):
        return True

    def is_mutable(self):
        return False

    def get_readonly(self):
        return self

    def get_verify_cap(self):
        return self


@implementer(IURI, IImmutableFileURI)
class LiteralFileURI(_BaseURI):

    BASE_STRING=b'URI:LIT:'
    STRING_RE=re.compile(b'^URI:LIT:'+base32.BASE32STR_anybytes+b'$')

    def __init__(self, data=None):
        if data is not None:
            assert isinstance(data, bytes)
            self.data = data

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)))

    def to_string(self):
        return b'URI:LIT:%s' % base32.b2a(self.data)

    def is_readonly(self):
        return True

    def is_mutable(self):
        return False

    def get_readonly(self):
        return self

    def get_storage_index(self):
        return None

    def get_verify_cap(self):
        # LIT files need no verification, all the data is present in the URI
        return None

    def get_size(self):
        return len(self.data)


@implementer(IURI, IMutableFileURI)
class WriteableSSKFileURI(_BaseURI):

    BASE_STRING=b'URI:SSK:'
    STRING_RE=re.compile(b'^'+BASE_STRING+BASE32STR_128bits+b':'+
                         BASE32STR_256bits+b'$')

    def __init__(self, writekey, fingerprint):
        self.writekey = writekey
        self.readkey = hashutil.ssk_readkey_hash(writekey)
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        assert len(self.storage_index) == 16
        self.fingerprint = fingerprint

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("%r doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.writekey, bytes)
        assert isinstance(self.fingerprint, bytes)
        return b'URI:SSK:%s:%s' % (base32.b2a(self.writekey),
                                   base32.b2a(self.fingerprint))

    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.abbrev())

    def abbrev(self):
        return base32.b2a(self.writekey[:5])

    def abbrev_si(self):
        return base32.b2a(self.storage_index)[:5]

    def is_readonly(self):
        return False

    def is_mutable(self):
        return True

    def get_readonly(self):
        return ReadonlySSKFileURI(self.readkey, self.fingerprint)

    def get_verify_cap(self):
        return SSKVerifierURI(self.storage_index, self.fingerprint)


@implementer(IURI, IMutableFileURI)
class ReadonlySSKFileURI(_BaseURI):

    BASE_STRING=b'URI:SSK-RO:'
    STRING_RE=re.compile(b'^URI:SSK-RO:'+BASE32STR_128bits+b':'+BASE32STR_256bits+b'$')

    def __init__(self, readkey, fingerprint):
        self.readkey = readkey
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        assert len(self.storage_index) == 16
        self.fingerprint = fingerprint

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("%r doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.readkey, bytes)
        assert isinstance(self.fingerprint, bytes)
        return b'URI:SSK-RO:%s:%s' % (base32.b2a(self.readkey),
                                      base32.b2a(self.fingerprint))

    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.abbrev())

    def abbrev(self):
        return base32.b2a(self.readkey[:5])

    def abbrev_si(self):
        return base32.b2a(self.storage_index)[:5]

    def is_readonly(self):
        return True

    def is_mutable(self):
        return True

    def get_readonly(self):
        return self

    def get_verify_cap(self):
        return SSKVerifierURI(self.storage_index, self.fingerprint)


@implementer(IVerifierURI)
class SSKVerifierURI(_BaseURI):

    BASE_STRING=b'URI:SSK-Verifier:'
    STRING_RE=re.compile(b'^'+BASE_STRING+BASE32STR_128bits+b':'+BASE32STR_256bits+b'$')

    def __init__(self, storage_index, fingerprint):
        assert len(storage_index) == 16
        self.storage_index = storage_index
        self.fingerprint = fingerprint

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("%r doesn't look like a %s cap" % (uri, cls))
        return cls(si_a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.storage_index, bytes)
        assert isinstance(self.fingerprint, bytes)
        return b'URI:SSK-Verifier:%s:%s' % (si_b2a(self.storage_index),
                                            base32.b2a(self.fingerprint))

    def is_readonly(self):
        return True

    def is_mutable(self):
        return False

    def get_readonly(self):
        return self

    def get_verify_cap(self):
        return self


@implementer(IURI, IMutableFileURI)
class WriteableMDMFFileURI(_BaseURI):

    BASE_STRING=b'URI:MDMF:'
    STRING_RE=re.compile(b'^'+BASE_STRING+BASE32STR_128bits+b':'+BASE32STR_256bits+b'(:|$)')

    def __init__(self, writekey, fingerprint):
        self.writekey = writekey
        self.readkey = hashutil.ssk_readkey_hash(writekey)
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        assert len(self.storage_index) == 16
        self.fingerprint = fingerprint

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("%r doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.writekey, bytes)
        assert isinstance(self.fingerprint, bytes)
        ret = b'URI:MDMF:%s:%s' % (base32.b2a(self.writekey),
                                   base32.b2a(self.fingerprint))
        return ret

    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.abbrev())

    def abbrev(self):
        return base32.b2a(self.writekey[:5])

    def abbrev_si(self):
        return base32.b2a(self.storage_index)[:5]

    def is_readonly(self):
        return False

    def is_mutable(self):
        return True

    def get_readonly(self):
        return ReadonlyMDMFFileURI(self.readkey, self.fingerprint)

    def get_verify_cap(self):
        return MDMFVerifierURI(self.storage_index, self.fingerprint)


@implementer(IURI, IMutableFileURI)
class ReadonlyMDMFFileURI(_BaseURI):

    BASE_STRING=b'URI:MDMF-RO:'
    STRING_RE=re.compile(b'^' +BASE_STRING+BASE32STR_128bits+b':'+BASE32STR_256bits+b'(:|$)')

    def __init__(self, readkey, fingerprint):
        self.readkey = readkey
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        assert len(self.storage_index) == 16
        self.fingerprint = fingerprint

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("%r doesn't look like a %s cap" % (uri, cls))

        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.readkey, bytes)
        assert isinstance(self.fingerprint, bytes)
        ret = b'URI:MDMF-RO:%s:%s' % (base32.b2a(self.readkey),
                                      base32.b2a(self.fingerprint))
        return ret

    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.abbrev())

    def abbrev(self):
        return base32.b2a(self.readkey[:5])

    def abbrev_si(self):
        return base32.b2a(self.storage_index)[:5]

    def is_readonly(self):
        return True

    def is_mutable(self):
        return True

    def get_readonly(self):
        return self

    def get_verify_cap(self):
        return MDMFVerifierURI(self.storage_index, self.fingerprint)


@implementer(IVerifierURI)
class MDMFVerifierURI(_BaseURI):

    BASE_STRING=b'URI:MDMF-Verifier:'
    STRING_RE=re.compile(b'^'+BASE_STRING+BASE32STR_128bits+b':'+BASE32STR_256bits+b'(:|$)')

    def __init__(self, storage_index, fingerprint):
        assert len(storage_index) == 16
        self.storage_index = storage_index
        self.fingerprint = fingerprint

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("%r doesn't look like a %s cap" % (uri, cls))
        return cls(si_a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.storage_index, bytes)
        assert isinstance(self.fingerprint, bytes)
        ret = b'URI:MDMF-Verifier:%s:%s' % (si_b2a(self.storage_index),
                                            base32.b2a(self.fingerprint))
        return ret

    def is_readonly(self):
        return True

    def is_mutable(self):
        return False

    def get_readonly(self):
        return self

    def get_verify_cap(self):
        return self


@implementer(IDirnodeURI)
class _DirectoryBaseURI(_BaseURI):
    def __init__(self, filenode_uri=None):
        self._filenode_uri = filenode_uri

    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.abbrev())

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.BASE_STRING_RE.search(uri)
        if not mo:
            raise BadURIError("%r doesn't look like a %s cap" % (uri, cls))
        bits = uri[mo.end():]
        fn = cls.INNER_URI_CLASS.init_from_string(
            cls.INNER_URI_CLASS.BASE_STRING+bits)
        return cls(fn)

    def to_string(self):
        fnuri = self._filenode_uri.to_string()
        mo = re.match(self.INNER_URI_CLASS.BASE_STRING, fnuri)
        assert mo, fnuri
        bits = fnuri[mo.end():]
        return self.BASE_STRING+bits

    def abbrev(self):
        return self._filenode_uri.to_string().split(b':')[2][:5]

    def abbrev_si(self):
        si = self._filenode_uri.get_storage_index()
        if si is None:
            return b"<LIT>"
        return base32.b2a(si)[:5]

    def is_mutable(self):
        return True

    def get_filenode_cap(self):
        return self._filenode_uri

    def get_verify_cap(self):
        return DirectoryURIVerifier(self._filenode_uri.get_verify_cap())

    def get_storage_index(self):
        return self._filenode_uri.get_storage_index()


@implementer(IURI, IDirectoryURI)
class DirectoryURI(_DirectoryBaseURI):

    BASE_STRING=b'URI:DIR2:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS=WriteableSSKFileURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert not filenode_uri.is_readonly()
        _DirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return False

    def get_readonly(self):
        return ReadonlyDirectoryURI(self._filenode_uri.get_readonly())


@implementer(IURI, IReadonlyDirectoryURI)
class ReadonlyDirectoryURI(_DirectoryBaseURI):

    BASE_STRING=b'URI:DIR2-RO:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS=ReadonlySSKFileURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert filenode_uri.is_readonly()
        _DirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return True

    def get_readonly(self):
        return self


@implementer(IURI, IDirnodeURI)
class _ImmutableDirectoryBaseURI(_DirectoryBaseURI):
    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert isinstance(filenode_uri, self.INNER_URI_CLASS), filenode_uri
            assert not filenode_uri.is_mutable()
        _DirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return True

    def is_mutable(self):
        return False

    def get_readonly(self):
        return self


class ImmutableDirectoryURI(_ImmutableDirectoryBaseURI):
    BASE_STRING=b'URI:DIR2-CHK:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS=CHKFileURI

    def get_verify_cap(self):
        vcap = self._filenode_uri.get_verify_cap()
        return ImmutableDirectoryURIVerifier(vcap)


class LiteralDirectoryURI(_ImmutableDirectoryBaseURI):
    BASE_STRING=b'URI:DIR2-LIT:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS=LiteralFileURI

    def get_verify_cap(self):
        # LIT caps have no verifier, since they aren't distributed
        return None


@implementer(IURI, IDirectoryURI)
class MDMFDirectoryURI(_DirectoryBaseURI):

    BASE_STRING=b'URI:DIR2-MDMF:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS=WriteableMDMFFileURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert not filenode_uri.is_readonly()
        _DirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return False

    def get_readonly(self):
        return ReadonlyMDMFDirectoryURI(self._filenode_uri.get_readonly())

    def get_verify_cap(self):
        return MDMFDirectoryURIVerifier(self._filenode_uri.get_verify_cap())


@implementer(IURI, IReadonlyDirectoryURI)
class ReadonlyMDMFDirectoryURI(_DirectoryBaseURI):

    BASE_STRING=b'URI:DIR2-MDMF-RO:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS=ReadonlyMDMFFileURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert filenode_uri.is_readonly()
        _DirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return True

    def get_readonly(self):
        return self

    def get_verify_cap(self):
        return MDMFDirectoryURIVerifier(self._filenode_uri.get_verify_cap())


def wrap_dirnode_cap(filecap):
    if isinstance(filecap, WriteableSSKFileURI):
        return DirectoryURI(filecap)
    if isinstance(filecap, ReadonlySSKFileURI):
        return ReadonlyDirectoryURI(filecap)
    if isinstance(filecap, CHKFileURI):
        return ImmutableDirectoryURI(filecap)
    if isinstance(filecap, LiteralFileURI):
        return LiteralDirectoryURI(filecap)
    if isinstance(filecap, WriteableMDMFFileURI):
        return MDMFDirectoryURI(filecap)
    if isinstance(filecap, ReadonlyMDMFFileURI):
        return ReadonlyMDMFDirectoryURI(filecap)
    raise AssertionError("cannot interpret as a directory cap: %s" % filecap.__class__)


@implementer(IURI, IVerifierURI)
class MDMFDirectoryURIVerifier(_DirectoryBaseURI):

    BASE_STRING=b'URI:DIR2-MDMF-Verifier:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS=MDMFVerifierURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            _assert(IVerifierURI.providedBy(filenode_uri))
        self._filenode_uri = filenode_uri

    def get_filenode_cap(self):
        return self._filenode_uri

    def is_mutable(self):
        return False

    def is_readonly(self):
        return True

    def get_readonly(self):
        return self


@implementer(IURI, IVerifierURI)
class DirectoryURIVerifier(_DirectoryBaseURI):

    BASE_STRING=b'URI:DIR2-Verifier:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS : Type[IVerifierURI] = SSKVerifierURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            _assert(IVerifierURI.providedBy(filenode_uri))
        self._filenode_uri = filenode_uri

    def get_filenode_cap(self):
        return self._filenode_uri

    def is_mutable(self):
        return False

    def is_readonly(self):
        return True

    def get_readonly(self):
        return self


@implementer(IVerifierURI)
class ImmutableDirectoryURIVerifier(DirectoryURIVerifier):
    BASE_STRING=b'URI:DIR2-CHK-Verifier:'
    BASE_STRING_RE=re.compile(b'^'+BASE_STRING)
    INNER_URI_CLASS=CHKFileVerifierURI


class UnknownURI(object):
    def __init__(self, uri, error=None):
        self._uri = uri
        self._error = error

    def to_string(self):
        return self._uri

    def get_readonly(self):
        return None

    def get_error(self):
        return self._error

    def get_verify_cap(self):
        return None


ALLEGED_READONLY_PREFIX = b'ro.'
ALLEGED_IMMUTABLE_PREFIX = b'imm.'

def from_string(u, deep_immutable=False, name=u"<unknown name>"):
    """Create URI from either unicode or byte string."""
    if isinstance(u, unicode):
        u = u.encode("utf-8")
    if not isinstance(u, bytes):
        raise TypeError("URI must be unicode string or bytes: %r" % (u,))

    # We allow and check ALLEGED_READONLY_PREFIX or ALLEGED_IMMUTABLE_PREFIX
    # on all URIs, even though we would only strictly need to do so for caps of
    # new formats (post Tahoe-LAFS 1.6). URIs that are not consistent with their
    # prefix are treated as unknown. This should be revisited when we add the
    # new cap formats. See ticket #833 comment:31.
    s = u
    can_be_mutable = can_be_writeable = not deep_immutable
    if s.startswith(ALLEGED_IMMUTABLE_PREFIX):
        can_be_mutable = can_be_writeable = False
        s = s[len(ALLEGED_IMMUTABLE_PREFIX):]
    elif s.startswith(ALLEGED_READONLY_PREFIX):
        can_be_writeable = False
        s = s[len(ALLEGED_READONLY_PREFIX):]

    error = None
    try:
        if s.startswith(b'URI:CHK:'):
            return CHKFileURI.init_from_string(s)
        elif s.startswith(b'URI:CHK-Verifier:'):
            return CHKFileVerifierURI.init_from_string(s)
        elif s.startswith(b'URI:LIT:'):
            return LiteralFileURI.init_from_string(s)
        elif s.startswith(b'URI:SSK:'):
            if can_be_writeable:
                return WriteableSSKFileURI.init_from_string(s)
            kind = "URI:SSK file writecap"
        elif s.startswith(b'URI:SSK-RO:'):
            if can_be_mutable:
                return ReadonlySSKFileURI.init_from_string(s)
            kind = "URI:SSK-RO readcap to a mutable file"
        elif s.startswith(b'URI:SSK-Verifier:'):
            return SSKVerifierURI.init_from_string(s)
        elif s.startswith(b'URI:MDMF:'):
            if can_be_writeable:
                return WriteableMDMFFileURI.init_from_string(s)
            kind = "URI:MDMF file writecap"
        elif s.startswith(b'URI:MDMF-RO:'):
            if can_be_mutable:
                return ReadonlyMDMFFileURI.init_from_string(s)
            kind = "URI:MDMF-RO readcap to a mutable file"
        elif s.startswith(b'URI:MDMF-Verifier:'):
            return MDMFVerifierURI.init_from_string(s)
        elif s.startswith(b'URI:DIR2:'):
            if can_be_writeable:
                return DirectoryURI.init_from_string(s)
            kind = "URI:DIR2 directory writecap"
        elif s.startswith(b'URI:DIR2-RO:'):
            if can_be_mutable:
                return ReadonlyDirectoryURI.init_from_string(s)
            kind = "URI:DIR2-RO readcap to a mutable directory"
        elif s.startswith(b'URI:DIR2-Verifier:'):
            return DirectoryURIVerifier.init_from_string(s)
        elif s.startswith(b'URI:DIR2-CHK:'):
            return ImmutableDirectoryURI.init_from_string(s)
        elif s.startswith(b'URI:DIR2-CHK-Verifier:'):
            return ImmutableDirectoryURIVerifier.init_from_string(s)
        elif s.startswith(b'URI:DIR2-LIT:'):
            return LiteralDirectoryURI.init_from_string(s)
        elif s.startswith(b'URI:DIR2-MDMF:'):
            if can_be_writeable:
                return MDMFDirectoryURI.init_from_string(s)
            kind = "URI:DIR2-MDMF directory writecap"
        elif s.startswith(b'URI:DIR2-MDMF-RO:'):
            if can_be_mutable:
                return ReadonlyMDMFDirectoryURI.init_from_string(s)
            kind = "URI:DIR2-MDMF-RO readcap to a mutable directory"
        elif s.startswith(b'URI:DIR2-MDMF-Verifier:'):
            return MDMFDirectoryURIVerifier.init_from_string(s)
        elif s.startswith(b'x-tahoe-future-test-writeable:') and not can_be_writeable:
            # For testing how future writeable caps would behave in read-only contexts.
            kind = "x-tahoe-future-test-writeable: testing cap"
        elif s.startswith(b'x-tahoe-future-test-mutable:') and not can_be_mutable:
            # For testing how future mutable readcaps would behave in immutable contexts.
            kind = "x-tahoe-future-test-mutable: testing cap"
        else:
            return UnknownURI(u)

        # We fell through because a constraint was not met.
        # Prefer to report the most specific constraint.
        if not can_be_mutable:
            error = MustBeDeepImmutableError(kind + " used in an immutable context", name)
        else:
            error = MustBeReadonlyError(kind + " used in a read-only context", name)

    except BadURIError as e:
        error = e

    return UnknownURI(u, error=error)

def is_uri(s):
    try:
        from_string(s, deep_immutable=False)
        return True
    except (TypeError, AssertionError):
        return False

def is_literal_file_uri(s):
    if isinstance(s, unicode):
        s = s.encode("utf-8")
    if not isinstance(s, bytes):
        return False
    return (s.startswith(b'URI:LIT:') or
            s.startswith(ALLEGED_READONLY_PREFIX + b'URI:LIT:') or
            s.startswith(ALLEGED_IMMUTABLE_PREFIX + b'URI:LIT:'))

def has_uri_prefix(s):
    if isinstance(s, unicode):
        s = s.encode("utf-8")
    if not isinstance(s, bytes):
        return False
    return (s.startswith(b"URI:") or
            s.startswith(ALLEGED_READONLY_PREFIX + b'URI:') or
            s.startswith(ALLEGED_IMMUTABLE_PREFIX + b'URI:'))


# These take the same keyword arguments as from_string above.

def from_string_dirnode(s, **kwargs):
    u = from_string(s, **kwargs)
    _assert(IDirnodeURI.providedBy(u))
    return u

registerAdapter(from_string_dirnode, bytes, IDirnodeURI)

def from_string_filenode(s, **kwargs):
    u = from_string(s, **kwargs)
    _assert(IFileURI.providedBy(u))
    return u

registerAdapter(from_string_filenode, bytes, IFileURI)

def from_string_mutable_filenode(s, **kwargs):
    u = from_string(s, **kwargs)
    _assert(IMutableFileURI.providedBy(u))
    return u
registerAdapter(from_string_mutable_filenode, bytes, IMutableFileURI)

def from_string_verifier(s, **kwargs):
    u = from_string(s, **kwargs)
    _assert(IVerifierURI.providedBy(u))
    return u
registerAdapter(from_string_verifier, bytes, IVerifierURI)


def pack_extension(data):
    pieces = []
    for k in sorted(data.keys()):
        value = data[k]
        if isinstance(value, (int, long)):
            value = b"%d" % value
        if isinstance(k, unicode):
            k = k.encode("utf-8")
        assert isinstance(value, bytes), k
        assert re.match(br'^[a-zA-Z_\-]+$', k)
        pieces.append(k + b':' + hashutil.netstring(value))
    uri_extension = b''.join(pieces)
    return uri_extension

def unpack_extension(data):
    d = {}
    while data:
        colon = data.index(b':')
        key = data[:colon]
        data = data[colon+1:]

        colon = data.index(b':')
        number = data[:colon]
        length = int(number)
        data = data[colon+1:]

        value = data[:length]
        assert data[length:length+1] == b','
        data = data[length+1:]

        d[str(key, "utf-8")] = value

    # convert certain things to numbers
    for intkey in ('size', 'segment_size', 'num_segments',
                   'needed_shares', 'total_shares'):
        if intkey in d:
            d[intkey] = int(d[intkey])
    return d


def unpack_extension_readable(data):
    unpacked = unpack_extension(data)
    unpacked["UEB_hash"] = hashutil.uri_extension_hash(data)
    for k in sorted(unpacked.keys()):
        if 'hash' in k:
            unpacked[k] = base32.b2a(unpacked[k])
    return unpacked

