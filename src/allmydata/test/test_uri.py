"""
Tests for allmydata.uri.

Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, dict, hex, input, next, oct, open, pow, round, super, bytes, list, object, range, str, max, min  # noqa: F401

import os
from twisted.trial import unittest
from allmydata import uri
from allmydata.util import hashutil, base32
from allmydata.interfaces import IURI, IFileURI, IDirnodeURI, IMutableFileURI, \
    IVerifierURI, CapConstraintError
import allmydata.test.common_util as testutil

class Literal(testutil.ReallyEqualMixin, unittest.TestCase):
    def _help_test(self, data):
        u = uri.LiteralFileURI(data)
        self.failUnless(IURI.providedBy(u))
        self.failUnless(IFileURI.providedBy(u))
        self.failIf(IDirnodeURI.providedBy(u))
        self.failUnlessReallyEqual(u.data, data)
        self.failUnlessReallyEqual(u.get_size(), len(data))
        self.failUnless(u.is_readonly())
        self.failIf(u.is_mutable())

        u2 = uri.from_string(u.to_string())
        self.failUnless(IURI.providedBy(u2))
        self.failUnless(IFileURI.providedBy(u2))
        self.failIf(IDirnodeURI.providedBy(u2))
        self.failUnlessReallyEqual(u2.data, data)
        self.failUnlessReallyEqual(u2.get_size(), len(data))
        self.failUnless(u2.is_readonly())
        self.failIf(u2.is_mutable())

        u2i = uri.from_string(u.to_string(), deep_immutable=True)
        self.failUnless(IFileURI.providedBy(u2i))
        self.failIf(IDirnodeURI.providedBy(u2i))
        self.failUnlessReallyEqual(u2i.data, data)
        self.failUnlessReallyEqual(u2i.get_size(), len(data))
        self.failUnless(u2i.is_readonly())
        self.failIf(u2i.is_mutable())

        u3 = u.get_readonly()
        self.failUnlessIdentical(u, u3)
        self.failUnlessReallyEqual(u.get_verify_cap(), None)

    def test_empty(self):
        data = b"" # This data is some *very* small data!
        return self._help_test(data)

    def test_pack(self):
        data = b"This is some small data"
        return self._help_test(data)

    def test_nonascii(self):
        data = b"This contains \x00 and URI:LIT: and \n, oh my."
        return self._help_test(data)

class Compare(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_compare(self):
        lit1 = uri.LiteralFileURI(b"some data")
        fileURI = b'URI:CHK:f5ahxa25t4qkktywz6teyfvcx4:opuioq7tj2y6idzfp6cazehtmgs5fdcebcz3cygrxyydvcozrmeq:3:10:345834'
        chk1 = uri.CHKFileURI.init_from_string(fileURI)
        chk2 = uri.CHKFileURI.init_from_string(fileURI)
        unk = uri.UnknownURI(b"lafs://from_the_future")
        self.failIfEqual(lit1, chk1)
        self.failUnlessReallyEqual(chk1, chk2)
        self.failIfEqual(chk1, "not actually a URI")
        # these should be hashable too
        s = set([lit1, chk1, chk2, unk])
        self.failUnlessReallyEqual(len(s), 3) # since chk1==chk2

    def test_is_uri(self):
        lit1 = uri.LiteralFileURI(b"some data").to_string()
        self.failUnless(uri.is_uri(lit1))
        self.failIf(uri.is_uri(None))

    def test_is_literal_file_uri(self):
        lit1 = uri.LiteralFileURI(b"some data").to_string()
        self.failUnless(uri.is_literal_file_uri(lit1))
        self.failIf(uri.is_literal_file_uri(None))
        self.failIf(uri.is_literal_file_uri("foo"))
        self.failIf(uri.is_literal_file_uri("ro.foo"))
        self.failIf(uri.is_literal_file_uri(b"URI:LITfoo"))
        self.failUnless(uri.is_literal_file_uri("ro.URI:LIT:foo"))
        self.failUnless(uri.is_literal_file_uri("imm.URI:LIT:foo"))

    def test_has_uri_prefix(self):
        self.failUnless(uri.has_uri_prefix(b"URI:foo"))
        self.failUnless(uri.has_uri_prefix(b"ro.URI:foo"))
        self.failUnless(uri.has_uri_prefix(b"imm.URI:foo"))
        self.failUnless(uri.has_uri_prefix("URI:foo"))
        self.failUnless(uri.has_uri_prefix("ro.URI:foo"))
        self.failUnless(uri.has_uri_prefix("imm.URI:foo"))
        self.failIf(uri.has_uri_prefix(None))
        self.failIf(uri.has_uri_prefix("foo"))

class CHKFile(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_pack(self):
        key = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        storage_index = hashutil.storage_index_hash(key)
        uri_extension_hash = hashutil.uri_extension_hash(b"stuff")
        needed_shares = 25
        total_shares = 100
        size = 1234
        u = uri.CHKFileURI(key=key,
                           uri_extension_hash=uri_extension_hash,
                           needed_shares=needed_shares,
                           total_shares=total_shares,
                           size=size)
        self.failUnlessReallyEqual(u.get_storage_index(), storage_index)
        self.failUnlessReallyEqual(u.key, key)
        self.failUnlessReallyEqual(u.uri_extension_hash, uri_extension_hash)
        self.failUnlessReallyEqual(u.needed_shares, needed_shares)
        self.failUnlessReallyEqual(u.total_shares, total_shares)
        self.failUnlessReallyEqual(u.size, size)
        self.failUnless(u.is_readonly())
        self.failIf(u.is_mutable())
        self.failUnless(IURI.providedBy(u))
        self.failUnless(IFileURI.providedBy(u))
        self.failIf(IDirnodeURI.providedBy(u))
        self.failUnlessReallyEqual(u.get_size(), 1234)

        u_ro = u.get_readonly()
        self.failUnlessIdentical(u, u_ro)

        u2 = uri.from_string(u.to_string())
        self.failUnlessReallyEqual(u2.get_storage_index(), storage_index)
        self.failUnlessReallyEqual(u2.key, key)
        self.failUnlessReallyEqual(u2.uri_extension_hash, uri_extension_hash)
        self.failUnlessReallyEqual(u2.needed_shares, needed_shares)
        self.failUnlessReallyEqual(u2.total_shares, total_shares)
        self.failUnlessReallyEqual(u2.size, size)
        self.failUnless(u2.is_readonly())
        self.failIf(u2.is_mutable())
        self.failUnless(IURI.providedBy(u2))
        self.failUnless(IFileURI.providedBy(u2))
        self.failIf(IDirnodeURI.providedBy(u2))
        self.failUnlessReallyEqual(u2.get_size(), 1234)

        u2i = uri.from_string(u.to_string(), deep_immutable=True)
        self.failUnlessReallyEqual(u.to_string(), u2i.to_string())
        u2ro = uri.from_string(uri.ALLEGED_READONLY_PREFIX + u.to_string())
        self.failUnlessReallyEqual(u.to_string(), u2ro.to_string())
        u2imm = uri.from_string(uri.ALLEGED_IMMUTABLE_PREFIX + u.to_string())
        self.failUnlessReallyEqual(u.to_string(), u2imm.to_string())

        v = u.get_verify_cap()
        self.failUnless(isinstance(v.to_string(), bytes))
        self.failUnless(v.is_readonly())
        self.failIf(v.is_mutable())

        v2 = uri.from_string(v.to_string())
        self.failUnlessReallyEqual(v, v2)

        v3 = uri.CHKFileVerifierURI(storage_index=b"\x00"*16,
                                    uri_extension_hash=b"\x00"*32,
                                    needed_shares=3,
                                    total_shares=10,
                                    size=1234)
        self.failUnless(isinstance(v3.to_string(), bytes))
        self.failUnless(v3.is_readonly())
        self.failIf(v3.is_mutable())

    def test_pack_badly(self):
        key = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        storage_index = hashutil.storage_index_hash(key)
        uri_extension_hash = hashutil.uri_extension_hash(b"stuff")
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
        self.failUnlessRaises(TypeError,
                              uri.CHKFileVerifierURI,
                              bogus="bogus")
        self.failUnlessRaises(TypeError,
                              uri.CHKFileVerifierURI,
                              storage_index=storage_index,
                              uri_extension_hash=uri_extension_hash,
                              needed_shares=3,
                              total_shares=10,
                              # leave size= missing
                              )


class Extension(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_pack(self):
        data = {b"stuff": b"value",
                b"size": 12,
                b"needed_shares": 3,
                b"big_hash": hashutil.tagged_hash(b"foo", b"bar"),
                }
        ext = uri.pack_extension(data)
        d = uri.unpack_extension(ext)
        self.failUnlessReallyEqual(d["stuff"], b"value")
        self.failUnlessReallyEqual(d["size"], 12)
        self.failUnlessReallyEqual(d["big_hash"], hashutil.tagged_hash(b"foo", b"bar"))

        readable = uri.unpack_extension_readable(ext)
        self.failUnlessReallyEqual(readable["needed_shares"], 3)
        self.failUnlessReallyEqual(readable["stuff"], b"value")
        self.failUnlessReallyEqual(readable["size"], 12)
        self.failUnlessReallyEqual(readable["big_hash"],
                             base32.b2a(hashutil.tagged_hash(b"foo", b"bar")))
        self.failUnlessReallyEqual(readable["UEB_hash"],
                             base32.b2a(hashutil.uri_extension_hash(ext)))

class Unknown(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_from_future(self):
        # any URI type that we don't recognize should be treated as unknown
        future_uri = b"I am a URI from the future. Whatever you do, don't "
        u = uri.from_string(future_uri)
        self.failUnless(isinstance(u, uri.UnknownURI))
        self.failUnlessReallyEqual(u.to_string(), future_uri)
        self.failUnless(u.get_readonly() is None)
        self.failUnless(u.get_error() is None)
        future_uri_unicode = future_uri.decode("utf-8")
        self.assertEqual(future_uri, uri.from_string(future_uri_unicode).to_string())

        u2 = uri.UnknownURI(future_uri, error=CapConstraintError("..."))
        self.failUnlessReallyEqual(u.to_string(), future_uri)
        self.failUnless(u2.get_readonly() is None)
        self.failUnless(isinstance(u2.get_error(), CapConstraintError))

        # Future caps might have non-ASCII chars in them. (Or maybe not, who can tell about the future?)
        future_uri = u"I am a cap from the \u263A future. Whatever you ".encode("utf-8")
        u = uri.from_string(future_uri)
        self.failUnless(isinstance(u, uri.UnknownURI))
        self.failUnlessReallyEqual(u.to_string(), future_uri)
        self.failUnless(u.get_readonly() is None)
        self.failUnless(u.get_error() is None)

        u2 = uri.UnknownURI(future_uri, error=CapConstraintError("..."))
        self.failUnlessReallyEqual(u.to_string(), future_uri)
        self.failUnless(u2.get_readonly() is None)
        self.failUnless(isinstance(u2.get_error(), CapConstraintError))

class Constraint(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_constraint(self):
        bad = b"http://127.0.0.1:3456/uri/URI%3ADIR2%3Agh3l5rbvnv2333mrfvalmjfr4i%3Alz6l7u3z3b7g37s4zkdmfpx5ly4ib4m6thrpbusi6ys62qtc6mma/"
        self.failUnlessRaises(uri.BadURIError, uri.DirectoryURI.init_from_string, bad)
        fileURI = b'URI:CHK:gh3l5rbvnv2333mrfvalmjfr4i:lz6l7u3z3b7g37s4zkdmfpx5ly4ib4m6thrpbusi6ys62qtc6mma:3:10:345834'
        uri.CHKFileURI.init_from_string(fileURI)

class Mutable(testutil.ReallyEqualMixin, unittest.TestCase):
    def setUp(self):
        self.writekey = b"\x01" * 16
        self.fingerprint = b"\x02" * 32
        self.readkey = hashutil.ssk_readkey_hash(self.writekey)
        self.storage_index = hashutil.ssk_storage_index_hash(self.readkey)

    def test_pack(self):
        u = uri.WriteableSSKFileURI(self.writekey, self.fingerprint)
        self.failUnlessReallyEqual(u.writekey, self.writekey)
        self.failUnlessReallyEqual(u.fingerprint, self.fingerprint)
        self.failIf(u.is_readonly())
        self.failUnless(u.is_mutable())
        self.failUnless(IURI.providedBy(u))
        self.failUnless(IMutableFileURI.providedBy(u))
        self.failIf(IDirnodeURI.providedBy(u))
        self.failUnless("WriteableSSKFileURI" in str(u))

        u2 = uri.from_string(u.to_string())
        self.failUnlessReallyEqual(u2.writekey, self.writekey)
        self.failUnlessReallyEqual(u2.fingerprint, self.fingerprint)
        self.failIf(u2.is_readonly())
        self.failUnless(u2.is_mutable())
        self.failUnless(IURI.providedBy(u2))
        self.failUnless(IMutableFileURI.providedBy(u2))
        self.failIf(IDirnodeURI.providedBy(u2))

        u2i = uri.from_string(u.to_string(), deep_immutable=True)
        self.failUnless(isinstance(u2i, uri.UnknownURI), u2i)
        u2ro = uri.from_string(uri.ALLEGED_READONLY_PREFIX + u.to_string())
        self.failUnless(isinstance(u2ro, uri.UnknownURI), u2ro)
        u2imm = uri.from_string(uri.ALLEGED_IMMUTABLE_PREFIX + u.to_string())
        self.failUnless(isinstance(u2imm, uri.UnknownURI), u2imm)

        u3 = u2.get_readonly()
        readkey = hashutil.ssk_readkey_hash(self.writekey)
        self.failUnlessReallyEqual(u3.fingerprint, self.fingerprint)
        self.failUnlessReallyEqual(u3.readkey, readkey)
        self.failUnless(u3.is_readonly())
        self.failUnless(u3.is_mutable())
        self.failUnless(IURI.providedBy(u3))
        self.failUnless(IMutableFileURI.providedBy(u3))
        self.failIf(IDirnodeURI.providedBy(u3))

        u3i = uri.from_string(u3.to_string(), deep_immutable=True)
        self.failUnless(isinstance(u3i, uri.UnknownURI), u3i)
        u3ro = uri.from_string(uri.ALLEGED_READONLY_PREFIX + u3.to_string())
        self.failUnlessReallyEqual(u3.to_string(), u3ro.to_string())
        u3imm = uri.from_string(uri.ALLEGED_IMMUTABLE_PREFIX + u3.to_string())
        self.failUnless(isinstance(u3imm, uri.UnknownURI), u3imm)

        u4 = uri.ReadonlySSKFileURI(readkey, self.fingerprint)
        self.failUnlessReallyEqual(u4.fingerprint, self.fingerprint)
        self.failUnlessReallyEqual(u4.readkey, readkey)
        self.failUnless(u4.is_readonly())
        self.failUnless(u4.is_mutable())
        self.failUnless(IURI.providedBy(u4))
        self.failUnless(IMutableFileURI.providedBy(u4))
        self.failIf(IDirnodeURI.providedBy(u4))

        u4i = uri.from_string(u4.to_string(), deep_immutable=True)
        self.failUnless(isinstance(u4i, uri.UnknownURI), u4i)
        u4ro = uri.from_string(uri.ALLEGED_READONLY_PREFIX + u4.to_string())
        self.failUnlessReallyEqual(u4.to_string(), u4ro.to_string())
        u4imm = uri.from_string(uri.ALLEGED_IMMUTABLE_PREFIX + u4.to_string())
        self.failUnless(isinstance(u4imm, uri.UnknownURI), u4imm)

        u4a = uri.from_string(u4.to_string())
        self.failUnlessReallyEqual(u4a, u4)
        self.failUnless("ReadonlySSKFileURI" in str(u4a))
        self.failUnlessIdentical(u4a.get_readonly(), u4a)

        u5 = u4.get_verify_cap()
        self.failUnless(IVerifierURI.providedBy(u5))
        self.failUnlessReallyEqual(u5.get_storage_index(), u.get_storage_index())
        u7 = u.get_verify_cap()
        self.failUnless(IVerifierURI.providedBy(u7))
        self.failUnlessReallyEqual(u7.get_storage_index(), u.get_storage_index())

    def test_writeable_mdmf_cap(self):
        u1 = uri.WriteableMDMFFileURI(self.writekey, self.fingerprint)
        cap = u1.to_string()
        u = uri.WriteableMDMFFileURI.init_from_string(cap)

        self.failUnless(IMutableFileURI.providedBy(u))
        self.failUnlessReallyEqual(u.fingerprint, self.fingerprint)
        self.failUnlessReallyEqual(u.writekey, self.writekey)
        self.failUnless(u.is_mutable())
        self.failIf(u.is_readonly())
        self.failUnlessEqual(cap, u.to_string())

        # Now get a readonly cap from the writeable cap, and test that it
        # degrades gracefully.
        ru = u.get_readonly()
        self.failUnlessReallyEqual(self.readkey, ru.readkey)
        self.failUnlessReallyEqual(self.fingerprint, ru.fingerprint)
        self.failUnless(ru.is_mutable())
        self.failUnless(ru.is_readonly())

        # Now get a verifier cap.
        vu = ru.get_verify_cap()
        self.failUnlessReallyEqual(self.storage_index, vu.storage_index)
        self.failUnlessReallyEqual(self.fingerprint, vu.fingerprint)
        self.failUnless(IVerifierURI.providedBy(vu))

    def test_readonly_mdmf_cap(self):
        u1 = uri.ReadonlyMDMFFileURI(self.readkey, self.fingerprint)
        cap = u1.to_string()
        u2 = uri.ReadonlyMDMFFileURI.init_from_string(cap)

        self.failUnlessReallyEqual(u2.fingerprint, self.fingerprint)
        self.failUnlessReallyEqual(u2.readkey, self.readkey)
        self.failUnless(u2.is_readonly())
        self.failUnless(u2.is_mutable())

        vu = u2.get_verify_cap()
        self.failUnlessEqual(vu.storage_index, self.storage_index)
        self.failUnlessEqual(vu.fingerprint, self.fingerprint)

    def test_create_writeable_mdmf_cap_from_readcap(self):
        # we shouldn't be able to create a writeable MDMF cap given only a
        # readcap.
        u1 = uri.ReadonlyMDMFFileURI(self.readkey, self.fingerprint)
        cap = u1.to_string()
        self.failUnlessRaises(uri.BadURIError,
                              uri.WriteableMDMFFileURI.init_from_string,
                              cap)

    def test_create_writeable_mdmf_cap_from_verifycap(self):
        u1 = uri.MDMFVerifierURI(self.storage_index, self.fingerprint)
        cap = u1.to_string()
        self.failUnlessRaises(uri.BadURIError,
                              uri.WriteableMDMFFileURI.init_from_string,
                              cap)

    def test_create_readonly_mdmf_cap_from_verifycap(self):
        u1 = uri.MDMFVerifierURI(self.storage_index, self.fingerprint)
        cap = u1.to_string()
        self.failUnlessRaises(uri.BadURIError,
                              uri.ReadonlyMDMFFileURI.init_from_string,
                              cap)

    def test_mdmf_verifier_cap(self):
        u1 = uri.MDMFVerifierURI(self.storage_index, self.fingerprint)
        self.failUnless(u1.is_readonly())
        self.failIf(u1.is_mutable())
        self.failUnlessReallyEqual(self.storage_index, u1.storage_index)
        self.failUnlessReallyEqual(self.fingerprint, u1.fingerprint)

        cap = u1.to_string()
        u2 = uri.MDMFVerifierURI.init_from_string(cap)
        self.failUnless(u2.is_readonly())
        self.failIf(u2.is_mutable())
        self.failUnlessReallyEqual(self.storage_index, u2.storage_index)
        self.failUnlessReallyEqual(self.fingerprint, u2.fingerprint)

        u3 = u2.get_readonly()
        self.failUnlessReallyEqual(u3, u2)

        u4 = u2.get_verify_cap()
        self.failUnlessReallyEqual(u4, u2)

    def test_mdmf_cap_ignore_extensions(self):
        # MDMF caps can be arbitrarily extended after the fingerprint and
        # key/storage index fields. tahoe-1.9 is supposed to ignore any
        # extensions, and not add any itself.
        u1 = uri.WriteableMDMFFileURI(self.writekey, self.fingerprint)
        cap = u1.to_string()

        cap2 = cap+b":I COME FROM THE FUTURE"
        u2 = uri.WriteableMDMFFileURI.init_from_string(cap2)
        self.failUnlessReallyEqual(self.writekey, u2.writekey)
        self.failUnlessReallyEqual(self.fingerprint, u2.fingerprint)
        self.failIf(u2.is_readonly())
        self.failUnless(u2.is_mutable())


        cap3 = cap+b":" + os.urandom(40)
        u3 = uri.WriteableMDMFFileURI.init_from_string(cap3)
        self.failUnlessReallyEqual(self.writekey, u3.writekey)
        self.failUnlessReallyEqual(self.fingerprint, u3.fingerprint)
        self.failIf(u3.is_readonly())
        self.failUnless(u3.is_mutable())

        cap4 = u1.get_readonly().to_string()+b":ooh scary future stuff"
        u4 = uri.from_string_mutable_filenode(cap4)
        self.failUnlessReallyEqual(self.readkey, u4.readkey)
        self.failUnlessReallyEqual(self.fingerprint, u4.fingerprint)
        self.failUnless(u4.is_readonly())
        self.failUnless(u4.is_mutable())

        cap5 = u1.get_verify_cap().to_string()+b":spoilers!"
        u5 = uri.from_string(cap5)
        self.failUnlessReallyEqual(self.storage_index, u5.storage_index)
        self.failUnlessReallyEqual(self.fingerprint, u5.fingerprint)
        self.failUnless(u5.is_readonly())
        self.failIf(u5.is_mutable())

    def test_mdmf_from_string(self):
        # Make sure that the from_string utility function works with
        # MDMF caps.
        u1 = uri.WriteableMDMFFileURI(self.writekey, self.fingerprint)
        cap = u1.to_string()
        self.failUnless(uri.is_uri(cap))
        u2 = uri.from_string(cap)
        self.failUnlessReallyEqual(u1, u2)
        u3 = uri.from_string_mutable_filenode(cap)
        self.failUnlessEqual(u3, u1)

        u1 = uri.ReadonlyMDMFFileURI(self.readkey, self.fingerprint)
        cap = u1.to_string()
        self.failUnless(uri.is_uri(cap))
        u2 = uri.from_string(cap)
        self.failUnlessReallyEqual(u1, u2)
        u3 = uri.from_string_mutable_filenode(cap)
        self.failUnlessEqual(u3, u1)

        u1 = uri.MDMFVerifierURI(self.storage_index, self.fingerprint)
        cap = u1.to_string()
        self.failUnless(uri.is_uri(cap))
        u2 = uri.from_string(cap)
        self.failUnlessReallyEqual(u1, u2)
        u3 = uri.from_string_verifier(cap)
        self.failUnlessEqual(u3, u1)


class Dirnode(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_pack(self):
        writekey = b"\x01" * 16
        fingerprint = b"\x02" * 32

        n = uri.WriteableSSKFileURI(writekey, fingerprint)
        u1 = uri.DirectoryURI(n)
        self.failIf(u1.is_readonly())
        self.failUnless(u1.is_mutable())
        self.failUnless(IURI.providedBy(u1))
        self.failIf(IFileURI.providedBy(u1))
        self.failUnless(IDirnodeURI.providedBy(u1))
        self.failUnless("DirectoryURI" in str(u1))
        u1_filenode = u1.get_filenode_cap()
        self.failUnless(u1_filenode.is_mutable())
        self.failIf(u1_filenode.is_readonly())

        u2 = uri.from_string(u1.to_string())
        self.failUnlessReallyEqual(u1.to_string(), u2.to_string())
        self.failIf(u2.is_readonly())
        self.failUnless(u2.is_mutable())
        self.failUnless(IURI.providedBy(u2))
        self.failIf(IFileURI.providedBy(u2))
        self.failUnless(IDirnodeURI.providedBy(u2))

        u2i = uri.from_string(u1.to_string(), deep_immutable=True)
        self.failUnless(isinstance(u2i, uri.UnknownURI))

        u3 = u2.get_readonly()
        self.failUnless(u3.is_readonly())
        self.failUnless(u3.is_mutable())
        self.failUnless(IURI.providedBy(u3))
        self.failIf(IFileURI.providedBy(u3))
        self.failUnless(IDirnodeURI.providedBy(u3))

        u3i = uri.from_string(u2.to_string(), deep_immutable=True)
        self.failUnless(isinstance(u3i, uri.UnknownURI))

        u3n = u3._filenode_uri
        self.failUnless(u3n.is_readonly())
        self.failUnless(u3n.is_mutable())
        u3_filenode = u3.get_filenode_cap()
        self.failUnless(u3_filenode.is_mutable())
        self.failUnless(u3_filenode.is_readonly())

        u3a = uri.from_string(u3.to_string())
        self.failUnlessIdentical(u3a, u3a.get_readonly())

        u4 = uri.ReadonlyDirectoryURI(u2._filenode_uri.get_readonly())
        self.failUnlessReallyEqual(u4.to_string(), u3.to_string())
        self.failUnless(u4.is_readonly())
        self.failUnless(u4.is_mutable())
        self.failUnless(IURI.providedBy(u4))
        self.failIf(IFileURI.providedBy(u4))
        self.failUnless(IDirnodeURI.providedBy(u4))

        u4_verifier = u4.get_verify_cap()
        u4_verifier_filenode = u4_verifier.get_filenode_cap()
        self.failUnless(isinstance(u4_verifier_filenode, uri.SSKVerifierURI))

        verifiers = [u1.get_verify_cap(), u2.get_verify_cap(),
                     u3.get_verify_cap(), u4.get_verify_cap(),
                     uri.DirectoryURIVerifier(n.get_verify_cap()),
                     ]
        for v in verifiers:
            self.failUnless(IVerifierURI.providedBy(v))
            self.failUnlessReallyEqual(v._filenode_uri,
                                 u1.get_verify_cap()._filenode_uri)

    def test_immutable(self):
        readkey = b"\x01" * 16
        uri_extension_hash = hashutil.uri_extension_hash(b"stuff")
        needed_shares = 3
        total_shares = 10
        size = 1234

        fnuri = uri.CHKFileURI(key=readkey,
                               uri_extension_hash=uri_extension_hash,
                               needed_shares=needed_shares,
                               total_shares=total_shares,
                               size=size)
        fncap = fnuri.to_string()
        self.failUnlessReallyEqual(fncap, b"URI:CHK:aeaqcaibaeaqcaibaeaqcaibae:nf3nimquen7aeqm36ekgxomalstenpkvsdmf6fplj7swdatbv5oa:3:10:1234")
        u1 = uri.ImmutableDirectoryURI(fnuri)
        self.failUnless(u1.is_readonly())
        self.failIf(u1.is_mutable())
        self.failUnless(IURI.providedBy(u1))
        self.failIf(IFileURI.providedBy(u1))
        self.failUnless(IDirnodeURI.providedBy(u1))
        self.failUnless("DirectoryURI" in str(u1))
        u1_filenode = u1.get_filenode_cap()
        self.failIf(u1_filenode.is_mutable())
        self.failUnless(u1_filenode.is_readonly())
        self.failUnlessReallyEqual(u1_filenode.to_string(), fncap)
        self.failUnless(str(u1))

        u2 = uri.from_string(u1.to_string())
        self.failUnlessReallyEqual(u1.to_string(), u2.to_string())
        self.failUnless(u2.is_readonly())
        self.failIf(u2.is_mutable())
        self.failUnless(IURI.providedBy(u2))
        self.failIf(IFileURI.providedBy(u2))
        self.failUnless(IDirnodeURI.providedBy(u2))

        u2i = uri.from_string(u1.to_string(), deep_immutable=True)
        self.failUnlessReallyEqual(u1.to_string(), u2i.to_string())

        u3 = u2.get_readonly()
        self.failUnlessReallyEqual(u3.to_string(), u2.to_string())
        self.failUnless(str(u3))

        u3i = uri.from_string(u2.to_string(), deep_immutable=True)
        self.failUnlessReallyEqual(u2.to_string(), u3i.to_string())

        u2_verifier = u2.get_verify_cap()
        self.failUnless(isinstance(u2_verifier,
                                   uri.ImmutableDirectoryURIVerifier),
                        u2_verifier)
        self.failUnless(IVerifierURI.providedBy(u2_verifier))
        u2vs = u2_verifier.to_string()
        # URI:DIR2-CHK-Verifier:$key:$ueb:$k:$n:$size
        self.failUnless(u2vs.startswith(b"URI:DIR2-CHK-Verifier:"), u2vs)
        u2_verifier_fileuri = u2_verifier.get_filenode_cap()
        self.failUnless(IVerifierURI.providedBy(u2_verifier_fileuri))
        u2vfs = u2_verifier_fileuri.to_string()
        # URI:CHK-Verifier:$key:$ueb:$k:$n:$size
        self.failUnlessReallyEqual(u2vfs, fnuri.get_verify_cap().to_string())
        self.failUnlessReallyEqual(u2vs[len(b"URI:DIR2-"):], u2vfs[len(b"URI:"):])
        self.failUnless(str(u2_verifier))

    def test_literal(self):
        u0 = uri.LiteralFileURI(b"data")
        u1 = uri.LiteralDirectoryURI(u0)
        self.failUnless(str(u1))
        self.failUnlessReallyEqual(u1.to_string(), b"URI:DIR2-LIT:mrqxiyi")
        self.failUnless(u1.is_readonly())
        self.failIf(u1.is_mutable())
        self.failUnless(IURI.providedBy(u1))
        self.failIf(IFileURI.providedBy(u1))
        self.failUnless(IDirnodeURI.providedBy(u1))
        self.failUnlessReallyEqual(u1.get_verify_cap(), None)
        self.failUnlessReallyEqual(u1.get_storage_index(), None)
        self.failUnlessReallyEqual(u1.abbrev_si(), b"<LIT>")

    def test_mdmf(self):
        writekey = b"\x01" * 16
        fingerprint = b"\x02" * 32
        uri1 = uri.WriteableMDMFFileURI(writekey, fingerprint)
        d1 = uri.MDMFDirectoryURI(uri1)
        self.failIf(d1.is_readonly())
        self.failUnless(d1.is_mutable())
        self.failUnless(IURI.providedBy(d1))
        self.failUnless(IDirnodeURI.providedBy(d1))
        d1_uri = d1.to_string()

        d2 = uri.from_string(d1_uri)
        self.failUnlessIsInstance(d2, uri.MDMFDirectoryURI)
        self.failIf(d2.is_readonly())
        self.failUnless(d2.is_mutable())
        self.failUnless(IURI.providedBy(d2))
        self.failUnless(IDirnodeURI.providedBy(d2))

        # It doesn't make sense to ask for a deep immutable URI for a
        # mutable directory, and we should get back a result to that
        # effect.
        d3 = uri.from_string(d2.to_string(), deep_immutable=True)
        self.failUnlessIsInstance(d3, uri.UnknownURI)

    def test_mdmf_attenuation(self):
        writekey = b"\x01" * 16
        fingerprint = b"\x02" * 32

        uri1 = uri.WriteableMDMFFileURI(writekey, fingerprint)
        d1 = uri.MDMFDirectoryURI(uri1)
        self.failUnless(d1.is_mutable())
        self.failIf(d1.is_readonly())
        self.failUnless(IURI.providedBy(d1))
        self.failUnless(IDirnodeURI.providedBy(d1))

        d1_uri = d1.to_string()
        d1_uri_from_fn = uri.MDMFDirectoryURI(d1.get_filenode_cap()).to_string()
        self.failUnlessEqual(d1_uri_from_fn, d1_uri)

        uri2 = uri.from_string(d1_uri)
        self.failUnlessIsInstance(uri2, uri.MDMFDirectoryURI)
        self.failUnless(IURI.providedBy(uri2))
        self.failUnless(IDirnodeURI.providedBy(uri2))
        self.failUnless(uri2.is_mutable())
        self.failIf(uri2.is_readonly())

        ro = uri2.get_readonly()
        self.failUnlessIsInstance(ro, uri.ReadonlyMDMFDirectoryURI)
        self.failUnless(ro.is_mutable())
        self.failUnless(ro.is_readonly())
        self.failUnless(IURI.providedBy(ro))
        self.failUnless(IDirnodeURI.providedBy(ro))

        ro_uri = ro.to_string()
        n = uri.from_string(ro_uri, deep_immutable=True)
        self.failUnlessIsInstance(n, uri.UnknownURI)

        fn_cap = ro.get_filenode_cap()
        fn_ro_cap = fn_cap.get_readonly()
        d3 = uri.ReadonlyMDMFDirectoryURI(fn_ro_cap)
        self.failUnlessEqual(ro.to_string(), d3.to_string())
        self.failUnless(ro.is_mutable())
        self.failUnless(ro.is_readonly())

    def test_mdmf_verifier(self):
        # I'm not sure what I want to write here yet.
        writekey = b"\x01" * 16
        fingerprint = b"\x02" * 32
        uri1 = uri.WriteableMDMFFileURI(writekey, fingerprint)
        d1 = uri.MDMFDirectoryURI(uri1)
        v1 = d1.get_verify_cap()
        self.failUnlessIsInstance(v1, uri.MDMFDirectoryURIVerifier)
        self.failIf(v1.is_mutable())

        d2 = uri.from_string(d1.to_string())
        v2 = d2.get_verify_cap()
        self.failUnlessIsInstance(v2, uri.MDMFDirectoryURIVerifier)
        self.failIf(v2.is_mutable())
        self.failUnlessEqual(v2.to_string(), v1.to_string())

        # Now attenuate and make sure that works correctly.
        r3 = d2.get_readonly()
        v3 = r3.get_verify_cap()
        self.failUnlessIsInstance(v3, uri.MDMFDirectoryURIVerifier)
        self.failIf(v3.is_mutable())
        self.failUnlessEqual(v3.to_string(), v1.to_string())
        r4 = uri.from_string(r3.to_string())
        v4 = r4.get_verify_cap()
        self.failUnlessIsInstance(v4, uri.MDMFDirectoryURIVerifier)
        self.failIf(v4.is_mutable())
        self.failUnlessEqual(v4.to_string(), v3.to_string())
