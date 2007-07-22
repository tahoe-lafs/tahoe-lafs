
from twisted.trial import unittest
from allmydata import uri
from allmydata.util import hashutil
from allmydata.interfaces import IURI, IFileURI, IDirnodeURI

class Literal(unittest.TestCase):
    def test_pack(self):
        data = "This is some small data"
        u = uri.LiteralFileURI(data)
        self.failUnless(IURI.providedBy(u))
        self.failUnless(IFileURI.providedBy(u))
        self.failIf(IDirnodeURI.providedBy(u))
        self.failUnlessEqual(u.data, data)
        self.failUnlessEqual(u.get_size(), len(data))
        self.failUnless(u.is_readonly())
        self.failIf(u.is_mutable())

        u2 = uri.from_string(u.to_string())
        self.failUnless(IURI.providedBy(u2))
        self.failUnless(IFileURI.providedBy(u2))
        self.failIf(IDirnodeURI.providedBy(u2))
        self.failUnlessEqual(u2.data, data)
        self.failUnlessEqual(u2.get_size(), len(data))
        self.failUnless(u.is_readonly())
        self.failIf(u.is_mutable())

    def test_nonascii(self):
        data = "This contains \x00 and URI:LIT: and \n, oh my."
        u = uri.LiteralFileURI(data)
        self.failUnless(IURI.providedBy(u))
        self.failUnless(IFileURI.providedBy(u))
        self.failIf(IDirnodeURI.providedBy(u))
        self.failUnlessEqual(u.data, data)
        self.failUnlessEqual(u.get_size(), len(data))
        self.failUnless(u.is_readonly())
        self.failIf(u.is_mutable())

        u2 = uri.from_string(u.to_string())
        self.failUnless(IURI.providedBy(u2))
        self.failUnless(IFileURI.providedBy(u2))
        self.failIf(IDirnodeURI.providedBy(u2))
        self.failUnlessEqual(u2.data, data)
        self.failUnlessEqual(u2.get_size(), len(data))
        self.failUnless(u.is_readonly())
        self.failIf(u.is_mutable())

class CHKFile(unittest.TestCase):
    def test_pack(self):
        key = "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        storage_index = hashutil.storage_index_chk_hash(key)
        uri_extension_hash = hashutil.uri_extension_hash("stuff")
        needed_shares = 25
        total_shares = 100
        size = 1234
        u = uri.CHKFileURI(key=key,
                           uri_extension_hash=uri_extension_hash,
                           needed_shares=needed_shares,
                           total_shares=total_shares,
                           size=size)
        self.failUnlessEqual(u.storage_index, storage_index)
        self.failUnlessEqual(u.key, key)
        self.failUnlessEqual(u.uri_extension_hash, uri_extension_hash)
        self.failUnlessEqual(u.needed_shares, needed_shares)
        self.failUnlessEqual(u.total_shares, total_shares)
        self.failUnlessEqual(u.size, size)
        self.failUnless(u.is_readonly())
        self.failIf(u.is_mutable())
        self.failUnless(IURI.providedBy(u))
        self.failUnless(IFileURI.providedBy(u))
        self.failIf(IDirnodeURI.providedBy(u))
        self.failUnlessEqual(u.get_size(), 1234)
        self.failUnless(u.is_readonly())
        self.failIf(u.is_mutable())

        u2 = uri.from_string(u.to_string())
        self.failUnlessEqual(u2.storage_index, storage_index)
        self.failUnlessEqual(u2.key, key)
        self.failUnlessEqual(u2.uri_extension_hash, uri_extension_hash)
        self.failUnlessEqual(u2.needed_shares, needed_shares)
        self.failUnlessEqual(u2.total_shares, total_shares)
        self.failUnlessEqual(u2.size, size)
        self.failUnless(u2.is_readonly())
        self.failIf(u2.is_mutable())
        self.failUnless(IURI.providedBy(u2))
        self.failUnless(IFileURI.providedBy(u2))
        self.failIf(IDirnodeURI.providedBy(u2))
        self.failUnlessEqual(u2.get_size(), 1234)
        self.failUnless(u2.is_readonly())
        self.failIf(u2.is_mutable())

class Extension(unittest.TestCase):
    def test_pack(self):
        data = {"stuff": "value",
                "size": 12,
                "needed_shares": 3,
                "big_hash": hashutil.tagged_hash("foo", "bar"),
                }
        ext = uri.pack_extension(data)
        d = uri.unpack_extension(ext)
        self.failUnlessEqual(d["stuff"], "value")
        self.failUnlessEqual(d["size"], 12)
        self.failUnlessEqual(d["big_hash"], hashutil.tagged_hash("foo", "bar"))

        readable = uri.unpack_extension_readable(ext)

class Dirnode(unittest.TestCase):
    def test_pack(self):
        furl = "pb://stuff@morestuff:stuff/andstuff"
        writekey = "\x01" * 16

        u = uri.DirnodeURI(furl, writekey)
        self.failUnlessEqual(u.furl, furl)
        self.failUnlessEqual(u.writekey, writekey)
        self.failIf(u.is_readonly())
        self.failUnless(u.is_mutable())
        self.failUnless(IURI.providedBy(u))
        self.failIf(IFileURI.providedBy(u))
        self.failUnless(IDirnodeURI.providedBy(u))

        u2 = uri.from_string(u.to_string())
        self.failUnlessEqual(u2.furl, furl)
        self.failUnlessEqual(u2.writekey, writekey)
        self.failIf(u2.is_readonly())
        self.failUnless(u2.is_mutable())
        self.failUnless(IURI.providedBy(u2))
        self.failIf(IFileURI.providedBy(u2))
        self.failUnless(IDirnodeURI.providedBy(u2))

        u3 = u2.get_readonly()
        readkey = hashutil.dir_read_key_hash(writekey)
        self.failUnlessEqual(u3.furl, furl)
        self.failUnlessEqual(u3.readkey, readkey)
        self.failUnless(u3.is_readonly())
        self.failUnless(u3.is_mutable())
        self.failUnless(IURI.providedBy(u3))
        self.failIf(IFileURI.providedBy(u3))
        self.failUnless(IDirnodeURI.providedBy(u3))

        u4 = uri.ReadOnlyDirnodeURI(furl, readkey)
        self.failUnlessEqual(u4.furl, furl)
        self.failUnlessEqual(u4.readkey, readkey)
        self.failUnless(u4.is_readonly())
        self.failUnless(u4.is_mutable())
        self.failUnless(IURI.providedBy(u4))
        self.failIf(IFileURI.providedBy(u4))
        self.failUnless(IDirnodeURI.providedBy(u4))

