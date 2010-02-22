
from twisted.trial import unittest
from allmydata import uri
from allmydata.util import hashutil, base32
from allmydata.interfaces import IURI, IFileURI, IDirnodeURI, IMutableFileURI, \
    IVerifierURI, CapConstraintError

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
        self.failUnless(u2.is_readonly())
        self.failIf(u2.is_mutable())

        u2i = uri.from_string(u.to_string(), deep_immutable=True)
        self.failUnless(IFileURI.providedBy(u2i))
        self.failIf(IDirnodeURI.providedBy(u2i))
        self.failUnlessEqual(u2i.data, data)
        self.failUnlessEqual(u2i.get_size(), len(data))
        self.failUnless(u2i.is_readonly())
        self.failIf(u2i.is_mutable())

        u3 = u.get_readonly()
        self.failUnlessIdentical(u, u3)
        self.failUnlessEqual(u.get_verify_cap(), None)

        he = u.to_human_encoding()
        u_h = uri.LiteralFileURI.init_from_human_encoding(he)
        self.failUnlessEqual(u, u_h)

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
        fileURI = 'URI:CHK:f5ahxa25t4qkktywz6teyfvcx4:opuioq7tj2y6idzfp6cazehtmgs5fdcebcz3cygrxyydvcozrmeq:3:10:345834'
        chk1 = uri.CHKFileURI.init_from_string(fileURI)
        chk2 = uri.CHKFileURI.init_from_string(fileURI)
        unk = uri.UnknownURI("lafs://from_the_future")
        self.failIfEqual(lit1, chk1)
        self.failUnlessEqual(chk1, chk2)
        self.failIfEqual(chk1, "not actually a URI")
        # these should be hashable too
        s = set([lit1, chk1, chk2, unk])
        self.failUnlessEqual(len(s), 3) # since chk1==chk2

    def test_is_uri(self):
        lit1 = uri.LiteralFileURI("some data").to_string()
        self.failUnless(uri.is_uri(lit1))
        self.failIf(uri.is_uri(None))

    def test_is_literal_file_uri(self):
        lit1 = uri.LiteralFileURI("some data").to_string()
        self.failUnless(uri.is_literal_file_uri(lit1))
        self.failIf(uri.is_literal_file_uri(None))
        self.failIf(uri.is_literal_file_uri("foo"))
        self.failIf(uri.is_literal_file_uri("ro.foo"))
        self.failIf(uri.is_literal_file_uri("URI:LITfoo"))
        self.failUnless(uri.is_literal_file_uri("ro.URI:LIT:foo"))
        self.failUnless(uri.is_literal_file_uri("imm.URI:LIT:foo"))

    def test_has_uri_prefix(self):
        self.failUnless(uri.has_uri_prefix("URI:foo"))
        self.failUnless(uri.has_uri_prefix("ro.URI:foo"))
        self.failUnless(uri.has_uri_prefix("imm.URI:foo"))
        self.failIf(uri.has_uri_prefix(None))
        self.failIf(uri.has_uri_prefix("foo"))

class CHKFile(unittest.TestCase):
    def test_pack(self):
        key = "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        storage_index = hashutil.storage_index_hash(key)
        uri_extension_hash = hashutil.uri_extension_hash("stuff")
        needed_shares = 25
        total_shares = 100
        size = 1234
        u = uri.CHKFileURI(key=key,
                           uri_extension_hash=uri_extension_hash,
                           needed_shares=needed_shares,
                           total_shares=total_shares,
                           size=size)
        self.failUnlessEqual(u.get_storage_index(), storage_index)
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

        u_ro = u.get_readonly()
        self.failUnlessIdentical(u, u_ro)
        he = u.to_human_encoding()
        self.failUnlessEqual(he, "http://127.0.0.1:3456/uri/" + u.to_string())
        self.failUnlessEqual(uri.CHKFileURI.init_from_human_encoding(he), u)

        u2 = uri.from_string(u.to_string())
        self.failUnlessEqual(u2.get_storage_index(), storage_index)
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

        u2i = uri.from_string(u.to_string(), deep_immutable=True)
        self.failUnlessEqual(u.to_string(), u2i.to_string())
        u2ro = uri.from_string(uri.ALLEGED_READONLY_PREFIX + u.to_string())
        self.failUnlessEqual(u.to_string(), u2ro.to_string())
        u2imm = uri.from_string(uri.ALLEGED_IMMUTABLE_PREFIX + u.to_string())
        self.failUnlessEqual(u.to_string(), u2imm.to_string())

        v = u.get_verify_cap()
        self.failUnless(isinstance(v.to_string(), str))
        self.failUnless(v.is_readonly())
        self.failIf(v.is_mutable())

        v2 = uri.from_string(v.to_string())
        self.failUnlessEqual(v, v2)
        he = v.to_human_encoding()
        v2_h = uri.CHKFileVerifierURI.init_from_human_encoding(he)
        self.failUnlessEqual(v2, v2_h)

        v3 = uri.CHKFileVerifierURI(storage_index="\x00"*16,
                                    uri_extension_hash="\x00"*32,
                                    needed_shares=3,
                                    total_shares=10,
                                    size=1234)
        self.failUnless(isinstance(v3.to_string(), str))
        self.failUnless(v3.is_readonly())
        self.failIf(v3.is_mutable())

    def test_pack_badly(self):
        key = "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        storage_index = hashutil.storage_index_hash(key)
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
        self.failUnlessEqual(readable["needed_shares"], 3)
        self.failUnlessEqual(readable["stuff"], "value")
        self.failUnlessEqual(readable["size"], 12)
        self.failUnlessEqual(readable["big_hash"],
                             base32.b2a(hashutil.tagged_hash("foo", "bar")))
        self.failUnlessEqual(readable["UEB_hash"],
                             base32.b2a(hashutil.uri_extension_hash(ext)))

class Unknown(unittest.TestCase):
    def test_from_future(self):
        # any URI type that we don't recognize should be treated as unknown
        future_uri = "I am a URI from the future. Whatever you do, don't "
        u = uri.from_string(future_uri)
        self.failUnless(isinstance(u, uri.UnknownURI))
        self.failUnlessEqual(u.to_string(), future_uri)
        self.failUnless(u.get_readonly() is None)
        self.failUnless(u.get_error() is None)

        u2 = uri.UnknownURI(future_uri, error=CapConstraintError("..."))
        self.failUnlessEqual(u.to_string(), future_uri)
        self.failUnless(u2.get_readonly() is None)
        self.failUnless(isinstance(u2.get_error(), CapConstraintError))

class Constraint(unittest.TestCase):
    def test_constraint(self):
       good="http://127.0.0.1:3456/uri/URI%3ADIR2%3Agh3l5rbvnv2333mrfvalmjfr4i%3Alz6l7u3z3b7g37s4zkdmfpx5ly4ib4m6thrpbusi6ys62qtc6mma/"
       uri.DirectoryURI.init_from_human_encoding(good)
       self.failUnlessRaises(uri.BadURIError, uri.DirectoryURI.init_from_string, good)
       bad = good + '==='
       self.failUnlessRaises(uri.BadURIError, uri.DirectoryURI.init_from_human_encoding, bad)
       self.failUnlessRaises(uri.BadURIError, uri.DirectoryURI.init_from_string, bad)
       fileURI = 'URI:CHK:gh3l5rbvnv2333mrfvalmjfr4i:lz6l7u3z3b7g37s4zkdmfpx5ly4ib4m6thrpbusi6ys62qtc6mma:3:10:345834'
       uri.CHKFileURI.init_from_string(fileURI)

class Mutable(unittest.TestCase):
    def test_pack(self):
        writekey = "\x01" * 16
        fingerprint = "\x02" * 32

        u = uri.WriteableSSKFileURI(writekey, fingerprint)
        self.failUnlessEqual(u.writekey, writekey)
        self.failUnlessEqual(u.fingerprint, fingerprint)
        self.failIf(u.is_readonly())
        self.failUnless(u.is_mutable())
        self.failUnless(IURI.providedBy(u))
        self.failUnless(IMutableFileURI.providedBy(u))
        self.failIf(IDirnodeURI.providedBy(u))
        self.failUnless("WriteableSSKFileURI" in str(u))

        he = u.to_human_encoding()
        u_h = uri.WriteableSSKFileURI.init_from_human_encoding(he)
        self.failUnlessEqual(u, u_h)

        u2 = uri.from_string(u.to_string())
        self.failUnlessEqual(u2.writekey, writekey)
        self.failUnlessEqual(u2.fingerprint, fingerprint)
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
        readkey = hashutil.ssk_readkey_hash(writekey)
        self.failUnlessEqual(u3.fingerprint, fingerprint)
        self.failUnlessEqual(u3.readkey, readkey)
        self.failUnless(u3.is_readonly())
        self.failUnless(u3.is_mutable())
        self.failUnless(IURI.providedBy(u3))
        self.failUnless(IMutableFileURI.providedBy(u3))
        self.failIf(IDirnodeURI.providedBy(u3))

        u3i = uri.from_string(u3.to_string(), deep_immutable=True)
        self.failUnless(isinstance(u3i, uri.UnknownURI), u3i)
        u3ro = uri.from_string(uri.ALLEGED_READONLY_PREFIX + u3.to_string())
        self.failUnlessEqual(u3.to_string(), u3ro.to_string())
        u3imm = uri.from_string(uri.ALLEGED_IMMUTABLE_PREFIX + u3.to_string())
        self.failUnless(isinstance(u3imm, uri.UnknownURI), u3imm)

        he = u3.to_human_encoding()
        u3_h = uri.ReadonlySSKFileURI.init_from_human_encoding(he)
        self.failUnlessEqual(u3, u3_h)

        u4 = uri.ReadonlySSKFileURI(readkey, fingerprint)
        self.failUnlessEqual(u4.fingerprint, fingerprint)
        self.failUnlessEqual(u4.readkey, readkey)
        self.failUnless(u4.is_readonly())
        self.failUnless(u4.is_mutable())
        self.failUnless(IURI.providedBy(u4))
        self.failUnless(IMutableFileURI.providedBy(u4))
        self.failIf(IDirnodeURI.providedBy(u4))

        u4i = uri.from_string(u4.to_string(), deep_immutable=True)
        self.failUnless(isinstance(u4i, uri.UnknownURI), u4i)
        u4ro = uri.from_string(uri.ALLEGED_READONLY_PREFIX + u4.to_string())
        self.failUnlessEqual(u4.to_string(), u4ro.to_string())
        u4imm = uri.from_string(uri.ALLEGED_IMMUTABLE_PREFIX + u4.to_string())
        self.failUnless(isinstance(u4imm, uri.UnknownURI), u4imm)

        u4a = uri.from_string(u4.to_string())
        self.failUnlessEqual(u4a, u4)
        self.failUnless("ReadonlySSKFileURI" in str(u4a))
        self.failUnlessIdentical(u4a.get_readonly(), u4a)

        u5 = u4.get_verify_cap()
        self.failUnless(IVerifierURI.providedBy(u5))
        self.failUnlessEqual(u5.get_storage_index(), u.get_storage_index())
        u7 = u.get_verify_cap()
        self.failUnless(IVerifierURI.providedBy(u7))
        self.failUnlessEqual(u7.get_storage_index(), u.get_storage_index())

        he = u5.to_human_encoding()
        u5_h = uri.SSKVerifierURI.init_from_human_encoding(he)
        self.failUnlessEqual(u5, u5_h)


class Dirnode(unittest.TestCase):
    def test_pack(self):
        writekey = "\x01" * 16
        fingerprint = "\x02" * 32

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
        self.failUnlessEqual(u1.to_string(), u2.to_string())
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
        self.failUnlessEqual(u4.to_string(), u3.to_string())
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
            self.failUnlessEqual(v._filenode_uri,
                                 u1.get_verify_cap()._filenode_uri)

    def test_immutable(self):
        readkey = "\x01" * 16
        uri_extension_hash = hashutil.uri_extension_hash("stuff")
        needed_shares = 3
        total_shares = 10
        size = 1234

        fnuri = uri.CHKFileURI(key=readkey,
                               uri_extension_hash=uri_extension_hash,
                               needed_shares=needed_shares,
                               total_shares=total_shares,
                               size=size)
        fncap = fnuri.to_string()
        self.failUnlessEqual(fncap, "URI:CHK:aeaqcaibaeaqcaibaeaqcaibae:nf3nimquen7aeqm36ekgxomalstenpkvsdmf6fplj7swdatbv5oa:3:10:1234")
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
        self.failUnlessEqual(u1_filenode.to_string(), fncap)
        self.failUnless(str(u1))

        u2 = uri.from_string(u1.to_string())
        self.failUnlessEqual(u1.to_string(), u2.to_string())
        self.failUnless(u2.is_readonly())
        self.failIf(u2.is_mutable())
        self.failUnless(IURI.providedBy(u2))
        self.failIf(IFileURI.providedBy(u2))
        self.failUnless(IDirnodeURI.providedBy(u2))

        u2i = uri.from_string(u1.to_string(), deep_immutable=True)
        self.failUnlessEqual(u1.to_string(), u2i.to_string())

        u3 = u2.get_readonly()
        self.failUnlessEqual(u3.to_string(), u2.to_string())
        self.failUnless(str(u3))

        u3i = uri.from_string(u2.to_string(), deep_immutable=True)
        self.failUnlessEqual(u2.to_string(), u3i.to_string())

        u2_verifier = u2.get_verify_cap()
        self.failUnless(isinstance(u2_verifier,
                                   uri.ImmutableDirectoryURIVerifier),
                        u2_verifier)
        self.failUnless(IVerifierURI.providedBy(u2_verifier))
        u2vs = u2_verifier.to_string()
        # URI:DIR2-CHK-Verifier:$key:$ueb:$k:$n:$size
        self.failUnless(u2vs.startswith("URI:DIR2-CHK-Verifier:"), u2vs)
        u2_verifier_fileuri = u2_verifier.get_filenode_cap()
        self.failUnless(IVerifierURI.providedBy(u2_verifier_fileuri))
        u2vfs = u2_verifier_fileuri.to_string()
        # URI:CHK-Verifier:$key:$ueb:$k:$n:$size
        self.failUnlessEqual(u2vfs, fnuri.get_verify_cap().to_string())
        self.failUnlessEqual(u2vs[len("URI:DIR2-"):], u2vfs[len("URI:"):])
        self.failUnless(str(u2_verifier))

    def test_literal(self):
        u0 = uri.LiteralFileURI("data")
        u1 = uri.LiteralDirectoryURI(u0)
        self.failUnless(str(u1))
        self.failUnlessEqual(u1.to_string(), "URI:DIR2-LIT:mrqxiyi")
        self.failUnless(u1.is_readonly())
        self.failIf(u1.is_mutable())
        self.failUnless(IURI.providedBy(u1))
        self.failIf(IFileURI.providedBy(u1))
        self.failUnless(IDirnodeURI.providedBy(u1))
        self.failUnlessEqual(u1.get_verify_cap(), None)
