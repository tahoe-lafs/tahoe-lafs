
import re, urllib
from zope.interface import implements
from twisted.python.components import registerAdapter
from allmydata import storage
from allmydata.util import base62, idlib, hashutil
from allmydata.interfaces import IURI, IDirnodeURI, IFileURI, IVerifierURI, \
     IMutableFileURI, INewDirectoryURI, IReadonlyNewDirectoryURI

# the URI shall be an ascii representation of the file. It shall contain
# enough information to retrieve and validate the contents. It shall be
# expressed in a limited character set (namely [TODO]).

ZBASE32STR_128bits = '(%s{25}%s)' % (idlib.ZBASE32CHAR, idlib.ZBASE32CHAR_3bits)
ZBASE32STR_256bits = '(%s{51}%s)' % (idlib.ZBASE32CHAR, idlib.ZBASE32CHAR_1bits)
ZBASE62STR_128bits = '(%s{22})' % (base62.ZBASE62CHAR)

SEP='(?::|%3A)'
NUMBER='([0-9]+)'

# URIs (soon to be renamed "caps") are always allowed to come with a leading
# 'http://127.0.0.1:8123/uri/' that will be ignored.
OPTIONALHTTPLEAD=r'(?:https?://(127.0.0.1|localhost):8123/uri/)?'


class _BaseURI:
    def __hash__(self):
        return hash((self.__class__, self.to_string()))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.to_string(), them.to_string())
    def to_human_encoding(self):
        return 'http://127.0.0.1:8123/uri/'+self.to_string()

class CHKFileURI(_BaseURI):
    implements(IURI, IFileURI)

    STRING_RE=re.compile('^URI:CHK:'+ZBASE32STR_128bits+':'+
                         ZBASE32STR_256bits+':'+NUMBER+':'+NUMBER+':'+NUMBER+
                         '$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'CHK'+SEP+
                     ZBASE32STR_128bits+SEP+ZBASE32STR_256bits+SEP+NUMBER+
                     SEP+NUMBER+SEP+NUMBER+'$')

    def __init__(self, key, uri_extension_hash, needed_shares, total_shares,
                 size):
        self.key = key
        self.uri_extension_hash = uri_extension_hash
        self.needed_shares = needed_shares
        self.total_shares = total_shares
        self.size = size
        self.storage_index = hashutil.storage_index_hash(self.key)
        assert len(self.storage_index) == 16
        self.storage_index = hashutil.storage_index_hash(key)
        assert len(self.storage_index) == 16 # sha256 hash truncated to 128

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        assert mo, uri
        return cls(idlib.a2b(mo.group(1)), idlib.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        assert mo, uri
        return cls(idlib.a2b(mo.group(1)), idlib.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

    def to_string(self):
        assert isinstance(self.needed_shares, int)
        assert isinstance(self.total_shares, int)
        assert isinstance(self.size, (int,long))

        return ('URI:CHK:%s:%s:%d:%d:%d' %
                (idlib.b2a(self.key),
                 idlib.b2a(self.uri_extension_hash),
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

    def get_verifier(self):
        return CHKFileVerifierURI(storage_index=self.storage_index,
                                  uri_extension_hash=self.uri_extension_hash,
                                  needed_shares=self.needed_shares,
                                  total_shares=self.total_shares,
                                  size=self.size)

class CHKFileVerifierURI(_BaseURI):
    implements(IVerifierURI)

    STRING_RE=re.compile('^URI:CHK-Verifier:'+ZBASE62STR_128bits+':'+
                         ZBASE32STR_256bits+':'+NUMBER+':'+NUMBER+':'+NUMBER)
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'CHK-Verifier'+SEP+
                        ZBASE62STR_128bits+SEP+ZBASE32STR_256bits+SEP+NUMBER+
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
        assert mo, uri
        return cls(idlib.a2b(mo.group(1)), idlib.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        assert mo, (uri, cls, cls.STRING_RE)
        return cls(storage.si_a2b(mo.group(1)), idlib.a2b(mo.group(2)),
                   int(mo.group(3)), int(mo.group(4)), int(mo.group(5)))

    def to_string(self):
        assert isinstance(self.needed_shares, int)
        assert isinstance(self.total_shares, int)
        assert isinstance(self.size, (int,long))

        return ('URI:CHK-Verifier:%s:%s:%d:%d:%d' %
                (storage.si_b2a(self.storage_index),
                 idlib.b2a(self.uri_extension_hash),
                 self.needed_shares,
                 self.total_shares,
                 self.size))


class LiteralFileURI(_BaseURI):
    implements(IURI, IFileURI)

    STRING_RE=re.compile('^URI:LIT:'+idlib.ZBASE32STR_anybytes+'$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'LIT'+SEP+idlib.ZBASE32STR_anybytes+'$')

    def __init__(self, data=None):
        if data is not None:
            self.data = data

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        assert mo, uri
        return cls(idlib.a2b(mo.group(1)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        assert mo, uri
        return cls(idlib.a2b(mo.group(1)))

    def to_string(self):
        return 'URI:LIT:%s' % idlib.b2a(self.data)

    def is_readonly(self):
        return True
    def is_mutable(self):
        return False
    def get_readonly(self):
        return self

    def get_verifier(self):
        # LIT files need no verification, all the data is present in the URI
        return None

    def get_size(self):
        return len(self.data)

class WriteableSSKFileURI(_BaseURI):
    implements(IURI, IMutableFileURI)

    BASE_STRING='URI:SSK:'
    STRING_RE=re.compile('^'+BASE_STRING+ZBASE32STR_128bits+':'+
                         ZBASE32STR_256bits+'$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'SSK'+SEP+
                        ZBASE32STR_128bits+SEP+ZBASE32STR_256bits+'$')

    def __init__(self, writekey, fingerprint):
        self.writekey = writekey
        self.readkey = hashutil.ssk_readkey_hash(writekey)
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        assert len(self.storage_index) == 16
        self.fingerprint = fingerprint

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        assert mo, uri
        return cls(idlib.a2b(mo.group(1)), idlib.a2b(mo.group(2)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        assert mo, (uri, cls)
        return cls(idlib.a2b(mo.group(1)), idlib.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.writekey, str)
        assert isinstance(self.fingerprint, str)
        return 'URI:SSK:%s:%s' % (idlib.b2a(self.writekey),
                                  idlib.b2a(self.fingerprint))

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.abbrev())

    def abbrev(self):
        return idlib.b2a(self.writekey[:5])

    def is_readonly(self):
        return False
    def is_mutable(self):
        return True
    def get_readonly(self):
        return ReadonlySSKFileURI(self.readkey, self.fingerprint)
    def get_verifier(self):
        return SSKVerifierURI(self.storage_index, self.fingerprint)

class ReadonlySSKFileURI(_BaseURI):
    implements(IURI, IMutableFileURI)

    BASE_STRING='URI:SSK-RO:'
    STRING_RE=re.compile('^URI:SSK-RO:'+ZBASE32STR_128bits+':'+ZBASE32STR_256bits+'$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'SSK-RO'+SEP+ZBASE32STR_128bits+SEP+ZBASE32STR_256bits+'$')

    def __init__(self, readkey, fingerprint):
        self.readkey = readkey
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        assert len(self.storage_index) == 16
        self.fingerprint = fingerprint

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        assert mo, uri
        return cls(idlib.a2b(mo.group(1)), idlib.a2b(mo.group(2)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        assert mo, uri
        return cls(idlib.a2b(mo.group(1)), idlib.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.readkey, str)
        assert isinstance(self.fingerprint, str)
        return 'URI:SSK-RO:%s:%s' % (idlib.b2a(self.readkey),
                                     idlib.b2a(self.fingerprint))

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.abbrev())

    def abbrev(self):
        return idlib.b2a(self.readkey[:5])

    def is_readonly(self):
        return True
    def is_mutable(self):
        return True
    def get_readonly(self):
        return self
    def get_verifier(self):
        return SSKVerifierURI(self.storage_index, self.fingerprint)

class SSKVerifierURI(_BaseURI):
    implements(IVerifierURI)

    BASE_STRING='URI:SSK-Verifier:'
    STRING_RE=re.compile('^'+BASE_STRING+ZBASE62STR_128bits+':'+ZBASE32STR_256bits+'$')
    HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'SSK-RO'+SEP+ZBASE62STR_128bits+SEP+ZBASE32STR_256bits+'$')

    def __init__(self, storage_index, fingerprint):
        assert len(storage_index) == 16
        self.storage_index = storage_index
        self.fingerprint = fingerprint

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.HUMAN_RE.search(uri)
        assert mo, uri
        return cls(storage.si_a2b(mo.group(1)), idlib.a2b(mo.group(2)))

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.STRING_RE.search(uri)
        assert mo, (uri, cls)
        return cls(storage.si_a2b(mo.group(1)), idlib.a2b(mo.group(2)))

    def to_string(self):
        assert isinstance(self.storage_index, str)
        assert isinstance(self.fingerprint, str)
        return 'URI:SSK-Verifier:%s:%s' % (storage.si_b2a(self.storage_index),
                                           idlib.b2a(self.fingerprint))

class _NewDirectoryBaseURI(_BaseURI):
    implements(IURI, IDirnodeURI)
    def __init__(self, filenode_uri=None):
        self._filenode_uri = filenode_uri

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.abbrev())

    @classmethod
    def init_from_string(cls, uri):
        mo = cls.BASE_STRING_RE.search(uri)
        assert mo, (uri, cls)
        bits = uri[mo.end():]
        fn = cls.INNER_URI_CLASS.init_from_string(
            cls.INNER_URI_CLASS.BASE_STRING+bits)
        return cls(fn)

    @classmethod
    def init_from_human_encoding(cls, uri):
        mo = cls.BASE_HUMAN_RE.search(uri)
        assert mo, (uri, cls)
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

    def get_filenode_uri(self):
        return self._filenode_uri

    def is_mutable(self):
        return True

    def get_verifier(self):
        return NewDirectoryURIVerifier(self._filenode_uri.get_verifier())

class NewDirectoryURI(_NewDirectoryBaseURI):
    implements(INewDirectoryURI)

    BASE_STRING='URI:DIR2:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2'+SEP)
    INNER_URI_CLASS=WriteableSSKFileURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert not filenode_uri.is_readonly()
        _NewDirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return False

    def get_readonly(self):
        return ReadonlyNewDirectoryURI(self._filenode_uri.get_readonly())

class ReadonlyNewDirectoryURI(_NewDirectoryBaseURI):
    implements(IReadonlyNewDirectoryURI)

    BASE_STRING='URI:DIR2-RO:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2-RO'+SEP)
    INNER_URI_CLASS=ReadonlySSKFileURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert filenode_uri.is_readonly()
        _NewDirectoryBaseURI.__init__(self, filenode_uri)

    def is_readonly(self):
        return True

    def get_readonly(self):
        return self

class NewDirectoryURIVerifier(_NewDirectoryBaseURI):
    implements(IVerifierURI)

    BASE_STRING='URI:DIR2-Verifier:'
    BASE_STRING_RE=re.compile('^'+BASE_STRING)
    BASE_HUMAN_RE=re.compile('^'+OPTIONALHTTPLEAD+'URI'+SEP+'DIR2-Verifier'+SEP)
    INNER_URI_CLASS=SSKVerifierURI

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            filenode_uri = IVerifierURI(filenode_uri)
        self._filenode_uri = filenode_uri

    def get_filenode_uri(self):
        return self._filenode_uri



def from_string(s):
    if s.startswith('URI:CHK:'):
        return CHKFileURI.init_from_string(s)
    elif s.startswith('URI:CHK-Verifier:'):
        return CHKFileVerifierURI.init_from_string(s)
    elif s.startswith('URI:LIT:'):
        return LiteralFileURI.init_from_string(s)
    elif s.startswith('URI:SSK:'):
        return WriteableSSKFileURI.init_from_string(s)
    elif s.startswith('URI:SSK-RO:'):
        return ReadonlySSKFileURI.init_from_string(s)
    elif s.startswith('URI:SSK-Verifier:'):
        return SSKVerifierURI.init_from_string(s)
    elif s.startswith('URI:DIR2:'):
        return NewDirectoryURI.init_from_string(s)
    elif s.startswith('URI:DIR2-RO:'):
        return ReadonlyNewDirectoryURI.init_from_string(s)
    elif s.startswith('URI:DIR2-Verifier:'):
        return NewDirectoryURIVerifier.init_from_string(s)
    else:
        raise TypeError("unknown URI type: %s.." % s[:12])

registerAdapter(from_string, str, IURI)

def is_uri(s):
    try:
        uri = from_string(s)
        return True
    except (TypeError, AssertionError):
        return False

def from_string_dirnode(s):
    u = from_string(s)
    assert IDirnodeURI.providedBy(u)
    return u

registerAdapter(from_string_dirnode, str, IDirnodeURI)

def from_string_filenode(s):
    u = from_string(s)
    assert IFileURI.providedBy(u)
    return u

registerAdapter(from_string_filenode, str, IFileURI)

def from_string_mutable_filenode(s):
    u = from_string(s)
    assert IMutableFileURI.providedBy(u)
    return u
registerAdapter(from_string_mutable_filenode, str, IMutableFileURI)

def from_string_verifier(s):
    u = from_string(s)
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
            unpacked[k] = idlib.b2a(unpacked[k])
    return unpacked

