
from twisted.trial import unittest
from allmydata import uri
from allmydata.util import hashutil

class LIT(unittest.TestCase):
    def test_pack(self):
        data = "This is some small data"
        u = uri.pack_lit(data)
        self.failUnlessEqual(uri.get_uri_type(u), "LIT")
        self.failUnlessEqual(uri.unpack_lit(u), data)
        self.failUnless(uri.is_filenode_uri(u))
        self.failUnlessEqual(uri.get_filenode_size(u), len(data))

    def test_nonascii(self):
        data = "This contains \x00 and URI:LIT: and \n, oh my."
        u = uri.pack_lit(data)
        self.failUnlessEqual(uri.get_uri_type(u), "LIT")
        self.failUnlessEqual(uri.unpack_lit(u), data)

class CHK(unittest.TestCase):
    def test_pack(self):
        storage_index = hashutil.tagged_hash("foo", "bar")
        key = "\x00" * 16
        uri_extension_hash = hashutil.uri_extension_hash("stuff")
        needed_shares = 25
        total_shares = 100
        size = 1234
        u = uri.pack_uri(storage_index=storage_index,
                         key=key,
                         uri_extension_hash=uri_extension_hash,
                         needed_shares=needed_shares,
                         total_shares=total_shares,
                         size=size)
        self.failUnlessEqual(uri.get_uri_type(u), "CHK")
        d = uri.unpack_uri(u)
        self.failUnlessEqual(d['storage_index'], storage_index)
        self.failUnlessEqual(d['key'], key)
        self.failUnlessEqual(d['uri_extension_hash'], uri_extension_hash)
        self.failUnlessEqual(d['needed_shares'], needed_shares)
        self.failUnlessEqual(d['total_shares'], total_shares)
        self.failUnlessEqual(d['size'], size)

        self.failUnless(uri.is_filenode_uri(u))
        self.failUnlessEqual(uri.get_filenode_size(u), size)

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

        u = uri.pack_dirnode_uri(furl, writekey)
        self.failUnless(uri.is_dirnode_uri(u))
        self.failIf(uri.is_dirnode_uri("NOT A DIRNODE URI"))
        self.failIf(uri.is_dirnode_uri("URI:stuff"))
        self.failUnless(uri.is_mutable_dirnode_uri(u))
        self.failIf(uri.is_mutable_dirnode_uri("NOT A DIRNODE URI"))
        self.failIf(uri.is_mutable_dirnode_uri("URI:stuff"))
        self.failUnlessEqual(uri.get_uri_type(u), "DIR")

        rou = uri.make_immutable_dirnode_uri(u)
        self.failUnless(uri.is_dirnode_uri(rou))
        self.failIf(uri.is_mutable_dirnode_uri(rou))
        self.failUnlessEqual(uri.get_uri_type(rou), "DIR-RO")

        d = uri.unpack_dirnode_uri(u)
        self.failUnlessEqual(d[0], furl)
        self.failUnlessEqual(d[1], writekey)

        d2 = uri.unpack_dirnode_uri(rou)
        self.failUnlessEqual(d2[0], furl)
        rk = hashutil.dir_read_key_hash(writekey)
        self.failUnlessEqual(d2[1], rk)

