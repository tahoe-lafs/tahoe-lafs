
import re
from zope.interface import implements
from twisted.python.components import registerAdapter
from allmydata.util import idlib, hashutil
from allmydata.interfaces import IURI, IDirnodeURI, IFileURI, IVerifierURI, \
     IMutableFileURI, INewDirectoryURI

# the URI shall be an ascii representation of the file. It shall contain
# enough information to retrieve and validate the contents. It shall be
# expressed in a limited character set (namely [TODO]).


class _BaseURI:
    def __hash__(self):
        return hash((self.__class__, self.to_string()))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.to_string(), them.to_string())

class CHKFileURI(_BaseURI):
    implements(IURI, IFileURI)

    def __init__(self, **kwargs):
        # construct me with kwargs, since there are so many of them
        if not kwargs:
            return
        keys = ("key", "uri_extension_hash",
                "needed_shares", "total_shares", "size")
        for name in kwargs:
            if name in keys:
                value = kwargs[name]
                setattr(self, name, value)
            else:
                raise TypeError("CHKFileURI does not accept '%s=' argument"
                                % name)
        self.storage_index = hashutil.storage_index_chk_hash(self.key)

    def init_from_string(self, uri):
        assert uri.startswith("URI:CHK:"), uri
        d = {}
        (header_uri, header_chk,
         key_s, uri_extension_hash_s,
         needed_shares_s, total_shares_s, size_s) = uri.split(":")
        assert header_uri == "URI"
        assert header_chk == "CHK"

        self.key = idlib.a2b(key_s)
        assert isinstance(self.key, str)
        assert len(self.key) == 16 # AES-128

        self.storage_index = hashutil.storage_index_chk_hash(self.key)
        assert isinstance(self.storage_index, str)
        assert len(self.storage_index) == 16 # sha256 hash truncated to 128

        self.uri_extension_hash = idlib.a2b(uri_extension_hash_s)
        assert isinstance(self.uri_extension_hash, str)
        assert len(self.uri_extension_hash) == 32 # sha56 hash

        self.needed_shares = int(needed_shares_s)
        self.total_shares = int(total_shares_s)
        self.size = int(size_s)
        return self

    def to_string(self):
        assert isinstance(self.needed_shares, int)
        assert isinstance(self.total_shares, int)
        assert isinstance(self.size, (int,long))

        return ("URI:CHK:%s:%s:%d:%d:%d" %
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

    def __init__(self, **kwargs):
        # construct me with kwargs, since there are so many of them
        if not kwargs:
            return
        keys = ("storage_index", "uri_extension_hash",
                "needed_shares", "total_shares", "size")
        for name in kwargs:
            if name in keys:
                value = kwargs[name]
                setattr(self, name, value)
            else:
                raise TypeError("CHKFileVerifierURI does not accept "
                                "'%s=' argument"
                                % name)

    def init_from_string(self, uri):
        assert uri.startswith("URI:CHK-Verifier:"), uri
        d = {}
        (header_uri, header_chk,
         storage_index_s, uri_extension_hash_s,
         needed_shares_s, total_shares_s, size_s) = uri.split(":")
        assert header_uri == "URI"
        assert header_chk == "CHK-Verifier"

        self.storage_index = idlib.a2b(storage_index_s)
        assert isinstance(self.storage_index, str)
        assert len(self.storage_index) == 16 # sha256 hash truncated to 128

        self.uri_extension_hash = idlib.a2b(uri_extension_hash_s)
        assert isinstance(self.uri_extension_hash, str)
        assert len(self.uri_extension_hash) == 32 # sha56 hash

        self.needed_shares = int(needed_shares_s)
        self.total_shares = int(total_shares_s)
        self.size = int(size_s)
        return self

    def to_string(self):
        assert isinstance(self.needed_shares, int)
        assert isinstance(self.total_shares, int)
        assert isinstance(self.size, (int,long))

        return ("URI:CHK-Verifier:%s:%s:%d:%d:%d" %
                (idlib.b2a(self.storage_index),
                 idlib.b2a(self.uri_extension_hash),
                 self.needed_shares,
                 self.total_shares,
                 self.size))


class LiteralFileURI(_BaseURI):
    implements(IURI, IFileURI)

    def __init__(self, data=None):
        if data is not None:
            self.data = data

    def init_from_string(self, uri):
        assert uri.startswith("URI:LIT:")
        data_s = uri[len("URI:LIT:"):]
        self.data = idlib.a2b(data_s)
        return self

    def to_string(self):
        return "URI:LIT:%s" % idlib.b2a(self.data)

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

    def __init__(self, *args, **kwargs):
        if not args and not kwargs:
            return
        self.populate(*args, **kwargs)

    def populate(self, writekey, fingerprint):
        self.writekey = writekey
        self.readkey = hashutil.ssk_readkey_hash(writekey)
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        self.fingerprint = fingerprint

    def init_from_string(self, uri):
        assert uri.startswith("URI:SSK:"), uri
        (header_uri, header_ssk, writekey_s, fingerprint_s) = uri.split(":")
        self.populate(idlib.a2b(writekey_s), idlib.a2b(fingerprint_s))
        return self

    def to_string(self):
        assert isinstance(self.writekey, str)
        assert isinstance(self.fingerprint, str)
        return "URI:SSK:%s:%s" % (idlib.b2a(self.writekey),
                                  idlib.b2a(self.fingerprint))

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

    def __init__(self, *args, **kwargs):
        if not args and not kwargs:
            return
        self.populate(*args, **kwargs)

    def populate(self, readkey, fingerprint):
        self.readkey = readkey
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)
        self.fingerprint = fingerprint

    def init_from_string(self, uri):
        assert uri.startswith("URI:SSK-RO:"), uri
        (header_uri, header_ssk, readkey_s, fingerprint_s) = uri.split(":")
        self.populate(idlib.a2b(readkey_s), idlib.a2b(fingerprint_s))
        return self

    def to_string(self):
        assert isinstance(self.readkey, str)
        assert isinstance(self.fingerprint, str)
        return "URI:SSK-RO:%s:%s" % (idlib.b2a(self.readkey),
                                     idlib.b2a(self.fingerprint))

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

    def __init__(self, *args, **kwargs):
        if not args and not kwargs:
            return
        self.populate(*args, **kwargs)

    def populate(self, storage_index, fingerprint):
        self.storage_index = storage_index
        self.fingerprint = fingerprint

    def init_from_string(self, uri):
        assert uri.startswith("URI:SSK-Verifier:"), uri
        (header_uri, header_ssk,
         storage_index_s, fingerprint_s) = uri.split(":")
        self.populate(idlib.a2b(storage_index_s), idlib.a2b(fingerprint_s))
        return self

    def to_string(self):
        assert isinstance(self.storage_index, str)
        assert isinstance(self.fingerprint, str)
        return "URI:SSK-Verifier:%s:%s" % (idlib.b2a(self.storage_index),
                                           idlib.b2a(self.fingerprint))

class NewDirectoryURI(_BaseURI):
    implements(IURI, IDirnodeURI, INewDirectoryURI)

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert not filenode_uri.is_readonly()
        self._filenode_uri = filenode_uri

    def init_from_string(self, uri):
        assert uri.startswith("URI:DIR2:")
        (header_uri, header_dir2, bits) = uri.split(":", 2)
        fn = WriteableSSKFileURI()
        fn.init_from_string("URI:SSK:" + bits)
        self._filenode_uri = fn
        return self

    def to_string(self):
        assert isinstance(self._filenode_uri, WriteableSSKFileURI)
        fn_u = self._filenode_uri.to_string()
        (header_uri, header_ssk, bits) = fn_u.split(":", 2)
        return "URI:DIR2:" + bits

    def get_filenode_uri(self):
        return self._filenode_uri

    def is_readonly(self):
        return False
    def is_mutable(self):
        return True
    def get_readonly(self):
        return ReadonlyNewDirectoryURI(self._filenode_uri.get_readonly())
    def get_verifier(self):
        return NewDirectoryURIVerifier(self._filenode_uri.get_verifier())

class ReadonlyNewDirectoryURI(_BaseURI):
    implements(IURI, IDirnodeURI)

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            assert filenode_uri.is_readonly()
        self._filenode_uri = filenode_uri

    def init_from_string(self, uri):
        assert uri.startswith("URI:DIR2-RO:")
        (header_uri, header_dir2, bits) = uri.split(":", 2)
        fn = ReadonlySSKFileURI()
        fn.init_from_string("URI:SSK-RO:" + bits)
        self._filenode_uri = fn
        return self

    def to_string(self):
        assert isinstance(self._filenode_uri, ReadonlySSKFileURI)
        fn_u = self._filenode_uri.to_string()
        (header_uri, header_ssk, bits) = fn_u.split(":", 2)
        return "URI:DIR2-RO:" + bits

    def get_filenode_uri(self):
        return self._filenode_uri

    def is_readonly(self):
        return True
    def is_mutable(self):
        return True
    def get_readonly(self):
        return self
    def get_verifier(self):
        return NewDirectoryURIVerifier(self._filenode_uri.get_verifier())

class NewDirectoryURIVerifier(_BaseURI):
    implements(IVerifierURI)

    def __init__(self, filenode_uri=None):
        if filenode_uri:
            filenode_uri = IVerifierURI(filenode_uri)
        self._filenode_uri = filenode_uri

    def init_from_string(self, uri):
        assert uri.startswith("URI:DIR2-Verifier:")
        (header_uri, header_dir2, bits) = uri.split(":", 2)
        fn = SSKVerifierURI()
        fn.init_from_string("URI:SSK-Verifier:" + bits)
        self._filenode_uri = fn
        return self

    def to_string(self):
        assert isinstance(self._filenode_uri, SSKVerifierURI)
        fn_u = self._filenode_uri.to_string()
        (header_uri, header_ssk, bits) = fn_u.split(":", 2)
        return "URI:DIR2-Verifier:" + bits

    def get_filenode_uri(self):
        return self._filenode_uri



class DirnodeURI(_BaseURI):
    implements(IURI, IDirnodeURI)

    def __init__(self, furl=None, writekey=None):
        if furl is not None or writekey is not None:
            assert furl is not None
            assert writekey is not None
            self.furl = furl
            self.writekey = writekey
            self._derive_values()

    def init_from_string(self, uri):
        # URI:DIR:furl:key
        #  but note that the furl contains colons
        prefix = "URI:DIR:"
        assert uri.startswith(prefix)
        uri = uri[len(prefix):]
        colon = uri.rindex(":")
        self.furl = uri[:colon]
        self.writekey = idlib.a2b(uri[colon+1:])
        self._derive_values()
        return self

    def _derive_values(self):
        wk, we, rk, index = \
            hashutil.generate_dirnode_keys_from_writekey(self.writekey)
        self.write_enabler = we
        self.readkey = rk
        self.storage_index = index

    def to_string(self):
        return "URI:DIR:%s:%s" % (self.furl, idlib.b2a(self.writekey))

    def is_readonly(self):
        return False
    def is_mutable(self):
        return True
    def get_readonly(self):
        return ReadOnlyDirnodeURI(self.furl, self.readkey)
    def get_verifier(self):
        return DirnodeVerifierURI(self.furl, self.storage_index)

class ReadOnlyDirnodeURI(_BaseURI):
    implements(IURI, IDirnodeURI)

    def __init__(self, furl=None, readkey=None):
        if furl is not None or readkey is not None:
            assert furl is not None
            assert readkey is not None
            self.furl = furl
            self.readkey = readkey
            self._derive_values()

    def init_from_string(self, uri):
        # URI:DIR-RO:furl:key
        #  but note that the furl contains colons
        prefix = "URI:DIR-RO:"
        assert uri.startswith(prefix)
        uri = uri[len(prefix):]
        colon = uri.rindex(":")
        self.furl = uri[:colon]
        self.readkey = idlib.a2b(uri[colon+1:])
        self._derive_values()
        return self

    def _derive_values(self):
        wk, we, rk, index = \
            hashutil.generate_dirnode_keys_from_readkey(self.readkey)
        self.writekey = wk # None
        self.write_enabler = we # None
        self.storage_index = index

    def to_string(self):
        return "URI:DIR-RO:%s:%s" % (self.furl, idlib.b2a(self.readkey))

    def is_readonly(self):
        return True
    def is_mutable(self):
        return True
    def get_readonly(self):
        return self
    def get_verifier(self):
        return DirnodeVerifierURI(self.furl, self.storage_index)

class DirnodeVerifierURI(_BaseURI):
    implements(IVerifierURI)

    def __init__(self, furl=None, storage_index=None):
        if furl is not None or storage_index is not None:
            assert furl is not None
            assert storage_index is not None
            self.furl = furl
            self.storage_index = storage_index

    def init_from_string(self, uri):
        # URI:DIR-Verifier:furl:storageindex
        #  but note that the furl contains colons
        prefix = "URI:DIR-Verifier:"
        assert uri.startswith(prefix)
        uri = uri[len(prefix):]
        colon = uri.rindex(":")
        self.furl = uri[:colon]
        self.storage_index = idlib.a2b(uri[colon+1:])
        return self

    def to_string(self):
        return "URI:DIR-Verifier:%s:%s" % (self.furl,
                                           idlib.b2a(self.storage_index))



def from_string(s):
    if s.startswith("URI:CHK:"):
        return CHKFileURI().init_from_string(s)
    elif s.startswith("URI:CHK-Verifier:"):
        return CHKFileVerifierURI().init_from_string(s)
    elif s.startswith("URI:LIT:"):
        return LiteralFileURI().init_from_string(s)
    elif s.startswith("URI:DIR:"):
        return DirnodeURI().init_from_string(s)
    elif s.startswith("URI:DIR-RO:"):
        return ReadOnlyDirnodeURI().init_from_string(s)
    elif s.startswith("URI:DIR-Verifier:"):
        return DirnodeVerifierURI().init_from_string(s)
    elif s.startswith("URI:SSK:"):
        return WriteableSSKFileURI().init_from_string(s)
    elif s.startswith("URI:SSK-RO:"):
        return ReadonlySSKFileURI().init_from_string(s)
    elif s.startswith("URI:SSK-Verifier:"):
        return SSKVerifierURI().init_from_string(s)
    elif s.startswith("URI:DIR2:"):
        return NewDirectoryURI().init_from_string(s)
    elif s.startswith("URI:DIR2-RO:"):
        return ReadonlyNewDirectoryURI().init_from_string(s)
    elif s.startswith("URI:DIR2-Verifier:"):
        return NewDirectoryURIVerifier().init_from_string(s)
    else:
        raise TypeError("unknown URI type: %s.." % s[:12])

registerAdapter(from_string, str, IURI)

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
        pieces.append(k + ":" + hashutil.netstring(value))
    uri_extension = "".join(pieces)
    return uri_extension

def unpack_extension(data):
    d = {}
    while data:
        colon = data.index(":")
        key = data[:colon]
        data = data[colon+1:]

        colon = data.index(":")
        number = data[:colon]
        length = int(number)
        data = data[colon+1:]

        value = data[:length]
        assert data[length] == ","
        data = data[length+1:]

        d[key] = value

    # convert certain things to numbers
    for intkey in ("size", "segment_size", "num_segments",
                   "needed_shares", "total_shares"):
        if intkey in d:
            d[intkey] = int(d[intkey])
    return d


def unpack_extension_readable(data):
    unpacked = unpack_extension(data)
    for k in sorted(unpacked.keys()):
        if "hash" in k:
            unpacked[k] = idlib.b2a(unpacked[k])
    return unpacked

