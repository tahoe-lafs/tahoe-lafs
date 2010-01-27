
import re, urllib
from zope.interface import implements
from twisted.python.components import registerAdapter
from allmydata.storage.server import si_a2b, si_b2a
from allmydata.util import base32, hashutil
from allmydata.interfaces import IURI, IDirnodeURI, IFileURI, IImmutableFileURI, \
    IVerifierURI, IMutableFileURI, IDirectoryURI, IReadonlyDirectoryURI, \
    MustBeDeepImmutableError, MustBeReadonlyError, CapConstraintError

class BadURIError(CapConstraintError):
    pass

# the URI shall be an ascii representation of the file. It shall contain
# enough information to retrieve and validate the contents. It shall be
# expressed in a limited character set (namely [TODO]).

BASE32STR_128bits = '(%s{25}%s)' % (base32.BASE32CHAR, base32.BASE32CHAR_3bits)
BASE32STR_256bits = '(%s{51}%s)' % (base32.BASE32CHAR, base32.BASE32CHAR_1bits)

SEP='(?::|%3A)'
NUMBER='([0-9]+)'
NUMBER_IGNORE='(?:[0-9]+)'

# URIs (soon to be renamed "caps") are always allowed to come with a leading
# 'http://127.0.0.1:(8123|3456)/uri/' that will be ignored.
OPTIONALHTTPLEAD=r'(?:https?://(?:[^:/]+)(?::%s)?/uri/)?' % NUMBER_IGNORE


class _BaseURI:
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
    def to_human_encoding(self):
        return 'http://127.0.0.1:3456/uri/'+self.to_string()

    def get_storage_index(self):
        return self.storage_index

class CHKFileURI(_BaseURI):
    implements(IURI, IImmutableFileURI)

    BASE_STRING='URI:CHK:'
    STRING_RE=re.compile('^URI:CHK:'+BASE32STR_128bits+':'+
                         BASE32STR_256bits+':'+NUMBER+':'+NUMBER+':'+NUMBER+
                         '$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'CHK'+SEP+
                     BASE32STR_128bits+SEP+BASE32STR_256bits+SEP+NUMBER+
                     SEP+NUMBER+SEP+NUMBER+'$')

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
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

    def to_string(self):
        assert isinstance(self.needed_shares, int)
        assert isinstance(self.total_shares, int)
        assert isinstance(self.size, (int,long))

        return ('URI:CHK:%s:%s:%d:%d:%d' %
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

class CHKFileVerifierURI(_BaseURI):
    implements(IVerifierURI)

    BASE_STRING='URI:CHK-Verifier:'
    STRING_RE=re.compile('^URI:CHK-Verifier:'+BASE32STR_128bits+':'+
                         BASE32STR_256bits+':'+NUMBER+':'+NUMBER+':'+NUMBER)
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'CHK-Verifier'+SEP+
                        BASE32STR_128bits+SEP+BASE32STR_256bits+SEP+NUMBER+
                        SEP+NUMBER+SEP+NUMBER)

    def __init__(self, storage_index, uri_extension_hash,
                 needed_shares, total_shares, size):
        assert len(storage_index) == 16
        self.storage_index = storage_index
        self.uri_extension_hash = uri_extension_hash
        self.needed_shares = needed_shares
        self.total_shares = total_shares
        self.size = size

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

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

        return ('URI:CHK-Verifier:%s:%s:%d:%d:%d' %
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


class LiteralFileURI(_BaseURI):
    implements(IURI, IImmutableFileURI)

    BASE_STRING='URI:LIT:'
    STRING_RE=re.compile('^URI:LIT:'+base32.BASE32STR_anybytes+'$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'LIT'+SEP+base32.BASE32STR_anybytes+'$')

    def __init__(self, data=None):
        if data is not None:
            assert isinstance(data, str)
            self.data = data

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)))

    def to_string(self):
        return 'URI:LIT:%s' % base32.b2a(self.data)

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


class WriteableSSKFileURI(_BaseURI):
    implements(IURI, IMutableFileURI)

    BASE_STRING='URI:SSK:'
    STRING_RE=re.compile('^'+BASE_STRING+BASE32STR_128bits+':'+
                         BASE32STR_256bits+'$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'SSK'+SEP+
                        BASE32STR_128bits+SEP+BASE32STR_256bits+'$')

    def __init__(self, writekey, fingerprint):
        self.writekey = writekey
        self.readkey = hashutil.ssk_readkey_hash(writekey)
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        assert len(self.storage_index) == 16
        self.fingerprint = fingerprint

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.writekey, str)
        assert isinstance(self.fingerprint, str)
        return 'URI:SSK:%s:%s' % (base32.b2a(self.writekey),
                                  base32.b2a(self.fingerprint))

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.abbrev())

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


class ReadonlySSKFileURI(_BaseURI):
    implements(IURI, IMutableFileURI)

    BASE_STRING='URI:SSK-RO:'
    STRING_RE=re.compile('^URI:SSK-RO:'+BASE32STR_128bits+':'+BASE32STR_256bits+'$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'SSK-RO'+SEP+BASE32STR_128bits+SEP+BASE32STR_256bits+'$')

    def __init__(self, readkey, fingerprint):
        self.readkey = readkey
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        assert len(self.storage_index) == 16
        self.fingerprint = fingerprint

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(base32.a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.readkey, str)
        assert isinstance(self.fingerprint, str)
        return 'URI:SSK-RO:%s:%s' % (base32.b2a(self.readkey),
                                     base32.b2a(self.fingerprint))

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.abbrev())

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


class SSKVerifierURI(_BaseURI):
    implements(IVerifierURI)

    BASE_STRING='URI:SSK-Verifier:'
    STRING_RE=re.compile('^'+BASE_STRING+BASE32STR_128bits+':'+BASE32STR_256bits+'$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'SSK-Verifier'+SEP+BASE32STR_128bits+SEP+BASE32STR_256bits+'$')

    def __init__(self, storage_index, fingerprint):
        assert len(storage_index) == 16
        self.storage_index = storage_index
        self.fingerprint = fingerprint

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(si_a2b(mo.group(1)), base32.a2b(mo.group(2)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        return cls(si_a2b(mo.group(1)), base32.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.storage_index, str)
        assert isinstance(self.fingerprint, str)
        return 'URI:SSK-Verifier:%s:%s' % (si_b2a(self.storage_index),
                                           base32.b2a(self.fingerprint))

    def is_readonly(self):
        return True

    def is_mutable(self):
        return False

    def get_readonly(self):
        return self

    def get_verify_cap(self):
        return self

class _DirectoryBaseURI(_BaseURI):
    implements(IURI, IDirnodeURI)
    def __init__(self, filenode_uri=None):
        self._filenode_uri = filenode_uri

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.abbrev())

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.BASE_STRING_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        bits = uri[mo.end():]
        fn = cls.INNER_URI_CLASS.init_from_string(
            cls.INNER_URI_CLASS.BASE_STRING+bits)
        return cls(fn)

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.BASE_HUMAN_RE.search(uri)
        if not mo:
            raise BadURIError("'%s' doesn't look like a %s cap" % (uri, cls))
        bits = uri[mo.end():]
        while bits and bits[-1] == '/':
            bits = bits[:-1]
        fn = cls.INNER_URI_CLASS.init_from_string(
            cls.INNER_URI_CLASS.BASE_STRING+urllib.unquote(bits))
        return cls(fn)

    def to_string(self):
        fnuri = self._filenode_uri.to_string()
        mo = re.match(self.INNER_URI_CLASS.BASE_STRING, fnuri)
        assert mo, fnuri
        bits = fnuri[mo.end():]
        return self.BASE_STRING+bits

    def abbrev(self):
        return self._filenode_uri.to_string().split(':')[2][:5]

    def abbrev_si(self):
        return base32.b2a(self._filenode_uri.storage_index)[:5]

    def is_mutable(self):
        return True

    def get_filenode_cap(self):
        return self._filenode_uri

    def get_verify_cap(self):
        return DirectoryURIVerifier(self._filenode_uri.get_verify_cap())

    def get_storage_index(self):
        return self._filenode_uri.get_storage_index()

class DirectoryURI(_DirectoryBaseURI):
    implements(IDirectoryURI)

    BASE_STRING='URI:DIR2:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2'+SEP)
    INNER_URI_CLASS=WriteableSSKFileURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert not filenode_uri.is_readonly()
        _DirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return False

    def get_readonly(self):
        return ReadonlyDirectoryURI(self._filenode_uri.get_readonly())


class ReadonlyDirectoryURI(_DirectoryBaseURI):
    implements(IReadonlyDirectoryURI)

    BASE_STRING='URI:DIR2-RO:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2-RO'+SEP)
    INNER_URI_CLASS=ReadonlySSKFileURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert filenode_uri.is_readonly()
        _DirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return True

    def get_readonly(self):
        return self


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
    BASE_STRING='URI:DIR2-CHK:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2-CHK'+SEP)
    INNER_URI_CLASS=CHKFileURI

    def get_verify_cap(self):
        vcap = self._filenode_uri.get_verify_cap()
        return ImmutableDirectoryURIVerifier(vcap)


class LiteralDirectoryURI(_ImmutableDirectoryBaseURI):
    BASE_STRING='URI:DIR2-LIT:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2-LIT'+SEP)
    INNER_URI_CLASS=LiteralFileURI

    def get_verify_cap(self):
        # LIT caps have no verifier, since they aren't distributed
        return None


def wrap_dirnode_cap(filecap):
    if isinstance(filecap, WriteableSSKFileURI):
        return DirectoryURI(filecap)
    if isinstance(filecap, ReadonlySSKFileURI):
        return ReadonlyDirectoryURI(filecap)
    if isinstance(filecap, CHKFileURI):
        return ImmutableDirectoryURI(filecap)
    if isinstance(filecap, LiteralFileURI):
        return LiteralDirectoryURI(filecap)
    assert False, "cannot interpret as a directory cap: %s" % filecap.__class__


class DirectoryURIVerifier(_DirectoryBaseURI):
    implements(IVerifierURI)

    BASE_STRING='URI:DIR2-Verifier:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2-Verifier'+SEP)
    INNER_URI_CLASS=SSKVerifierURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert IVerifierURI.providedBy(filenode_uri)
        self._filenode_uri = filenode_uri

    def get_filenode_cap(self):
        return self._filenode_uri

    def is_mutable(self):
        return False


class ImmutableDirectoryURIVerifier(DirectoryURIVerifier):
    implements(IVerifierURI)
    BASE_STRING='URI:DIR2-CHK-Verifier:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2-CHK-VERIFIER'+SEP)
    INNER_URI_CLASS=CHKFileVerifierURI


class UnknownURI:
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


ALLEGED_READONLY_PREFIX = 'ro.'
ALLEGED_IMMUTABLE_PREFIX = 'imm.'

def from_string(u, deep_immutable=False, name=u"<unknown name>"):
    if not isinstance(u, str):
        raise TypeError("unknown URI type: %s.." % str(u)[:100])

    # We allow and check ALLEGED_READONLY_PREFIX or ALLEGED_IMMUTABLE_PREFIX
    # on all URIs, even though we would only strictly need to do so for caps of
    # new formats (post Tahoe-LAFS 1.6). URIs that are not consistent with their
    # prefix are treated as unknown. This should be revisited when we add the
    # new cap formats. See <http://allmydata.org/trac/tahoe/ticket/833#comment:31>.
    s = u
    can_be_mutable = can_be_writeable = not deep_immutable
    if s.startswith(ALLEGED_IMMUTABLE_PREFIX):
        can_be_mutable = can_be_writeable = False
        s = s[len(ALLEGED_IMMUTABLE_PREFIX):]
    elif s.startswith(ALLEGED_READONLY_PREFIX):
        can_be_writeable = False
        s = s[len(ALLEGED_READONLY_PREFIX):]

    error = None
    kind = "cap"
    try:
        if s.startswith('URI:CHK:'):
            return CHKFileURI.init_from_string(s)
        elif s.startswith('URI:CHK-Verifier:'):
            return CHKFileVerifierURI.init_from_string(s)
        elif s.startswith('URI:LIT:'):
            return LiteralFileURI.init_from_string(s)
        elif s.startswith('URI:SSK:'):
            if can_be_writeable:
                return WriteableSSKFileURI.init_from_string(s)
            kind = "URI:SSK file writecap"
        elif s.startswith('URI:SSK-RO:'):
            if can_be_mutable:
                return ReadonlySSKFileURI.init_from_string(s)
            kind = "URI:SSK-RO readcap to a mutable file"
        elif s.startswith('URI:SSK-Verifier:'):
            return SSKVerifierURI.init_from_string(s)
        elif s.startswith('URI:DIR2:'):
            if can_be_writeable:
                return DirectoryURI.init_from_string(s)
            kind = "URI:DIR2 directory writecap"
        elif s.startswith('URI:DIR2-RO:'):
            if can_be_mutable:
                return ReadonlyDirectoryURI.init_from_string(s)
            kind = "URI:DIR2-RO readcap to a mutable directory"
        elif s.startswith('URI:DIR2-Verifier:'):
            return DirectoryURIVerifier.init_from_string(s)
        elif s.startswith('URI:DIR2-CHK:'):
            return ImmutableDirectoryURI.init_from_string(s)
        elif s.startswith('URI:DIR2-LIT:'):
            return LiteralDirectoryURI.init_from_string(s)
        elif s.startswith('x-tahoe-future-test-writeable:') and not can_be_writeable:
            # For testing how future writeable caps would behave in read-only contexts.
            kind = "x-tahoe-future-test-writeable: testing cap"
        elif s.startswith('x-tahoe-future-test-mutable:') and not can_be_mutable:
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
            
    except BadURIError, e:
        error = e

    return UnknownURI(u, error=error)

def is_uri(s):
    try:
        from_string(s, deep_immutable=False)
        return True
    except (TypeError, AssertionError):
        return False

def is_literal_file_uri(s):
    if not isinstance(s, str):
        return False
    return (s.startswith('URI:LIT:') or
            s.startswith(ALLEGED_READONLY_PREFIX + 'URI:LIT:') or
            s.startswith(ALLEGED_IMMUTABLE_PREFIX + 'URI:LIT:'))

def has_uri_prefix(s):
    if not isinstance(s, str):
        return False
    return (s.startswith("URI:") or
            s.startswith(ALLEGED_READONLY_PREFIX + 'URI:') or
            s.startswith(ALLEGED_IMMUTABLE_PREFIX + 'URI:'))


# These take the same keyword arguments as from_string above.

def from_string_dirnode(s, **kwargs):
    u = from_string(s, **kwargs)
    assert IDirnodeURI.providedBy(u)
    return u

registerAdapter(from_string_dirnode, str, IDirnodeURI)

def from_string_filenode(s, **kwargs):
    u = from_string(s, **kwargs)
    assert IFileURI.providedBy(u)
    return u

registerAdapter(from_string_filenode, str, IFileURI)

def from_string_mutable_filenode(s, **kwargs):
    u = from_string(s, **kwargs)
    assert IMutableFileURI.providedBy(u)
    return u
registerAdapter(from_string_mutable_filenode, str, IMutableFileURI)

def from_string_verifier(s, **kwargs):
    u = from_string(s, **kwargs)
    assert IVerifierURI.providedBy(u)
    return u
registerAdapter(from_string_verifier, str, IVerifierURI)


def pack_extension(data):
    pieces = []
    for k in sorted(data.keys()):
        value = data[k]
        if isinstance(value, (int, long)):
            value = "%d" % value
        assert isinstance(value, str), k
        assert re.match(r'^[a-zA-Z_\-]+$', k)
        pieces.append(k + ':' + hashutil.netstring(value))
    uri_extension = ''.join(pieces)
    return uri_extension

def unpack_extension(data):
    d = {}
    while data:
        colon = data.index(':')
        key = data[:colon]
        data = data[colon+1:]

        colon = data.index(':')
        number = data[:colon]
        length = int(number)
        data = data[colon+1:]

        value = data[:length]
        assert data[length] == ','
        data = data[length+1:]

        d[key] = value

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

