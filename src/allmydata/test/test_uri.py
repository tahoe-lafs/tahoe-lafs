
from twisted.trial import unittest
from allmydata import uri
from allmydata.util import hashutil
from allmydata.interfaces import IURI, IFileURI, IDirnodeURI, DirnodeURI
from foolscap.schema import Violation

class Literal(unittest.TestCase):
    def _help_test(self, data):
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
        
    def test_empty(self):
        data = "" # This data is some *very* small data!
        return self._help_test(data)
    
    def test_pack(self):
        data = "This is some small data"
        return self._help_test(data)

    def test_nonascii(self):
        data = "This contains \x00 and URI:LIT: and \n, oh my."
        return self._help_test(data)

class Compare(unittest.TestCase):
    def test_compare(self):
        lit1 = uri.LiteralFileURI("some data")
        fileURI = 'URI:CHK:f3mf6az85wpcai8ma4qayfmxuc:nnw518w5hu3t5oohwtp7ah9n81z9rfg6c1ywk33ia3m64o67nsgo:3:10:345834'
        chk1 = uri.CHKFileURI().init_from_string(fileURI)
        chk2 = uri.CHKFileURI().init_from_string(fileURI)
        self.failIfEqual(lit1, chk1)
        self.failUnlessEqual(chk1, chk2)
        self.failIfEqual(chk1, "not actually a URI")
        # these should be hashable too
        s = set([lit1, chk1, chk2])
        self.failUnlessEqual(len(s), 2) # since chk1==chk2

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

    def test_pack_badly(self):
        key = "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        storage_index = hashutil.storage_index_chk_hash(key)
        uri_extension_hash = hashutil.uri_extension_hash("stuff")
        needed_shares = 25
        total_shares = 100
        size = 1234
        self.failUnlessRaises(TypeError,
                              uri.CHKFileURI,
                              key=key,
                              uri_extension_hash=uri_extension_hash,
                              needed_shares=needed_shares,
                              total_shares=total_shares,
                              size=size,

                              bogus_extra_argument="reject me",
                              )

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

class Invalid(unittest.TestCase):
    def test_create_invalid(self):
        not_uri = "I am not a URI"
        self.failUnlessRaises(TypeError, uri.from_string, not_uri)


class Constraint(unittest.TestCase):
    def test_constraint(self):
       good = 'URI:DIR:pb://xextf3eap44o3wi27mf7ehiur6wvhzr6@207.7.153.180:56677,127.0.0.1:56677/vdrive:qj51rfpnukhjmo7cm9awe5ks5e'
       DirnodeURI.checkObject(good, False)
       bad = good + '==='
       self.failUnlessRaises(Violation, DirnodeURI.checkObject, bad, False)
       fileURI = 'URI:CHK:f3mf6az85wpcai8ma4qayfmxuc:nnw518w5hu3t5oohwtp7ah9n81z9rfg6c1ywk33ia3m64o67nsgo:3:10:345834'
       self.failUnlessRaises(Violation, DirnodeURI.checkObject, fileURI, False)

