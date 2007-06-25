
from twisted.trial import unittest
from allmydata import filetable, uri
from allmydata.util import hashutil


class FileTable(unittest.TestCase):
    def test_vdrive_server(self):
        basedir = "filetable/FileTable/test_vdrive_server"
        vds = filetable.VirtualDriveServer(basedir)
        vds.set_furl("myFURL")

        root_uri = vds.get_public_root_uri()
        self.failUnless(uri.is_dirnode_uri(root_uri))
        self.failUnless(uri.is_mutable_dirnode_uri(root_uri))
        furl, key = uri.unpack_dirnode_uri(root_uri)
        self.failUnlessEqual(furl, "myFURL")
        self.failUnlessEqual(len(key), hashutil.KEYLEN)

        wk, we, rk, index = hashutil.generate_dirnode_keys_from_writekey(key)
        empty_list = vds.list(index)
        self.failUnlessEqual(empty_list, [])

        vds.set(index, we, "key1", "name1", "write1", "read1")
        vds.set(index, we, "key2", "name2", "", "read2")

        self.failUnlessRaises(filetable.ChildAlreadyPresentError,
                              vds.set,
                              index, we, "key2", "name2", "write2", "read2")

        self.failUnlessRaises(filetable.BadWriteEnablerError,
                              vds.set,
                              index, "not the write enabler",
                              "key2", "name2", "write2", "read2")

        self.failUnlessEqual(vds.get(index, "key1"),
                             ("write1", "read1"))
        self.failUnlessEqual(vds.get(index, "key2"),
                             ("", "read2"))
        self.failUnlessRaises(IndexError,
                              vds.get, index, "key3")

        self.failUnlessEqual(sorted(vds.list(index)),
                             [ ("name1", "write1", "read1"),
                               ("name2", "", "read2"),
                               ])

        self.failUnlessRaises(filetable.BadWriteEnablerError,
                              vds.delete,
                              index, "not the write enabler", "name1")
        self.failUnlessEqual(sorted(vds.list(index)),
                             [ ("name1", "write1", "read1"),
                               ("name2", "", "read2"),
                               ])
        self.failUnlessRaises(IndexError,
                              vds.delete,
                              index, we, "key3")

        vds.delete(index, we, "key1")
        self.failUnlessEqual(sorted(vds.list(index)),
                             [ ("name2", "", "read2"),
                               ])
        self.failUnlessRaises(IndexError,
                              vds.get, index, "key1")
        self.failUnlessEqual(vds.get(index, "key2"),
                             ("", "read2"))


        vds2 = filetable.VirtualDriveServer(basedir)
        vds2.set_furl("myFURL")
        root_uri2 = vds.get_public_root_uri()
        self.failUnless(uri.is_mutable_dirnode_uri(root_uri2))
        furl2, key2 = uri.unpack_dirnode_uri(root_uri2)
        (wk2, we2, rk2, index2) = \
              hashutil.generate_dirnode_keys_from_writekey(key2)
        self.failUnlessEqual(sorted(vds2.list(index2)),
                             [ ("name2", "", "read2"),
                               ])

    def test_no_root(self):
        basedir = "FileTable/test_no_root"
        vds = filetable.VirtualDriveServer(basedir, offer_public_root=False)
        vds.set_furl("myFURL")

        self.failUnlessRaises(filetable.NoPublicRootError,
                              vds.get_public_root_uri)
