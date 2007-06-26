
from cStringIO import StringIO
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import failure
from allmydata import vdrive, filetable, uri
from allmydata.interfaces import IDirectoryNode
from allmydata.scripts import runner
from foolscap import eventual

class LocalReference:
    def __init__(self, target):
        self.target = target
    def callRemote(self, methname, *args, **kwargs):
        def _call(ignored):
            meth = getattr(self.target, methname)
            return meth(*args, **kwargs)
        d = eventual.fireEventually(None)
        d.addCallback(_call)
        return d

class MyTub:
    def __init__(self, vds, myfurl):
        self.vds = vds
        self.myfurl = myfurl
    def getReference(self, furl):
        assert furl == self.myfurl
        return eventual.fireEventually(LocalReference(self.vds))

class MyClient:
    def __init__(self, vds, myfurl):
        self.tub = MyTub(vds, myfurl)

class Test(unittest.TestCase):
    def test_create_directory(self):
        basedir = "vdrive/test_create_directory/vdrive"
        vds = filetable.VirtualDriveServer(basedir)
        vds.set_furl("myFURL")
        self.client = client = MyClient(vds, "myFURL")
        d = vdrive.create_directory(client, "myFURL")
        def _created(node):
            self.failUnless(IDirectoryNode.providedBy(node))
            self.failUnless(node.is_mutable())
        d.addCallback(_created)
        return d

    def test_one(self):
        self.basedir = basedir = "vdrive/test_one/vdrive"
        vds = filetable.VirtualDriveServer(basedir)
        vds.set_furl("myFURL")
        root_uri = vds.get_public_root_uri()

        self.client = client = MyClient(vds, "myFURL")
        d1 = vdrive.create_directory_node(client, root_uri)
        d2 = vdrive.create_directory_node(client, root_uri)
        d = defer.gatherResults( [d1,d2] )
        d.addCallback(self._test_one_1)
        return d

    def _test_one_1(self, (rootnode1, rootnode2) ):
        self.failUnlessEqual(rootnode1, rootnode2)
        self.failIfEqual(rootnode1, "not")

        self.rootnode = rootnode = rootnode1
        self.failUnless(rootnode.is_mutable())
        self.readonly_uri = rootnode.get_immutable_uri()
        d = vdrive.create_directory_node(self.client, self.readonly_uri)
        d.addCallback(self._test_one_2)
        return d

    def _test_one_2(self, ro_rootnode):
        self.ro_rootnode = ro_rootnode
        self.failIf(ro_rootnode.is_mutable())
        self.failUnlessEqual(ro_rootnode.get_immutable_uri(),
                             self.readonly_uri)

        rootnode = self.rootnode

        ignored = rootnode.dump()

        # root/
        d = rootnode.list()
        def _listed(res):
            self.failUnlessEqual(res, {})
        d.addCallback(_listed)

        file1 = uri.pack_uri("i"*32, "k"*16, "e"*32, 25, 100, 12345)
        file2 = uri.pack_uri("i"*31 + "2", "k"*16, "e"*32, 25, 100, 12345)
        file2_node = vdrive.FileNode(file2, None)
        d.addCallback(lambda res: rootnode.set_uri("foo", file1))
        # root/
        # root/foo =file1

        d.addCallback(lambda res: rootnode.list())
        def _listed2(res):
            self.failUnlessEqual(res.keys(), ["foo"])
            file1_node = res["foo"]
            self.failUnless(isinstance(file1_node, vdrive.FileNode))
            self.failUnlessEqual(file1_node.uri, file1)
        d.addCallback(_listed2)

        d.addCallback(lambda res: rootnode.get("foo"))
        def _got_foo(res):
            self.failUnless(isinstance(res, vdrive.FileNode))
            self.failUnlessEqual(res.uri, file1)
        d.addCallback(_got_foo)

        d.addCallback(lambda res: rootnode.get("missing"))
        # this should raise an exception
        d.addBoth(self.shouldFail, IndexError, "get('missing')",
                  "unable to find child named 'missing'")

        d.addCallback(lambda res: rootnode.create_empty_directory("bar"))
        # root/
        # root/foo =file1
        # root/bar/

        d.addCallback(lambda res: rootnode.list())
        d.addCallback(self.failUnlessKeysMatch, ["foo", "bar"])
        def _listed3(res):
            self.failIfEqual(res["foo"], res["bar"])
            self.failIfEqual(res["bar"], res["foo"])
            self.failIfEqual(res["foo"], "not")
            self.failIfEqual(res["bar"], self.rootnode)
            self.failUnlessEqual(res["foo"], res["foo"])
            # make sure the objects can be used as dict keys
            testdict = {res["foo"]: 1, res["bar"]: 2}
            bar_node = res["bar"]
            self.failUnless(isinstance(bar_node, vdrive.MutableDirectoryNode))
            self.bar_node = bar_node
            bar_ro_uri = bar_node.get_immutable_uri()
            return rootnode.set_uri("bar-ro", bar_ro_uri)
        d.addCallback(_listed3)
        # root/
        # root/foo =file1
        # root/bar/
        # root/bar-ro/  (read-only)

        d.addCallback(lambda res: rootnode.list())
        d.addCallback(self.failUnlessKeysMatch, ["foo", "bar", "bar-ro"])
        def _listed4(res):
            self.failIf(res["bar-ro"].is_mutable())
            self.bar_node_readonly = res["bar-ro"]

            # add another file to bar/
            bar = res["bar"]
            return bar.set_node("file2", file2_node)
        d.addCallback(_listed4)
        d.addCallback(self.failUnlessIdentical, file2_node)
        # and a directory
        d.addCallback(lambda res: self.bar_node.create_empty_directory("baz"))
        # root/
        # root/foo =file1
        # root/bar/
        # root/bar/file2 =file2
        # root/bar/baz/
        # root/bar-ro/  (read-only)
        # root/bar-ro/file2 =file2
        # root/bar-ro/baz/

        d.addCallback(lambda res: self.bar_node.list())
        d.addCallback(self.failUnlessKeysMatch, ["file2", "baz"])
        d.addCallback(lambda res:
                      self.failUnless(res["baz"].is_mutable()))

        d.addCallback(lambda res: self.bar_node_readonly.list())
        d.addCallback(self.failUnlessKeysMatch, ["file2", "baz"])
        d.addCallback(lambda res:
                      self.failIf(res["baz"].is_mutable()))

        # try to add a file to bar-ro, should get exception
        d.addCallback(lambda res:
                      self.bar_node_readonly.set_uri("file3", file2))
        d.addBoth(self.shouldFail, vdrive.NotMutableError,
                  "bar-ro.set('file3')")

        # try to delete a file from bar-ro, should get exception
        d.addCallback(lambda res: self.bar_node_readonly.delete("file2"))
        d.addBoth(self.shouldFail, vdrive.NotMutableError,
                  "bar-ro.delete('file2')")

        # try to mkdir in bar-ro, should get exception
        d.addCallback(lambda res:
                      self.bar_node_readonly.create_empty_directory("boffo"))
        d.addBoth(self.shouldFail, vdrive.NotMutableError,
                  "bar-ro.mkdir('boffo')")

        d.addCallback(lambda res: rootnode.delete("foo"))
        # root/
        # root/bar/
        # root/bar/file2 =file2
        # root/bar/baz/
        # root/bar-ro/  (read-only)
        # root/bar-ro/file2 =file2
        # root/bar-ro/baz/

        d.addCallback(lambda res: rootnode.list())
        d.addCallback(self.failUnlessKeysMatch, ["bar", "bar-ro"])

        d.addCallback(lambda res:
                      self.bar_node.move_child_to("file2",
                                                  self.rootnode, "file4"))
        # root/
        # root/file4 = file4
        # root/bar/
        # root/bar/baz/
        # root/bar-ro/  (read-only)
        # root/bar-ro/baz/

        d.addCallback(lambda res: rootnode.list())
        d.addCallback(self.failUnlessKeysMatch, ["bar", "bar-ro", "file4"])
        d.addCallback(lambda res:self.bar_node.list())
        d.addCallback(self.failUnlessKeysMatch, ["baz"])
        d.addCallback(lambda res:self.bar_node_readonly.list())
        d.addCallback(self.failUnlessKeysMatch, ["baz"])


        d.addCallback(lambda res:
                      rootnode.move_child_to("file4",
                                             self.bar_node_readonly, "boffo"))
        d.addBoth(self.shouldFail, vdrive.NotMutableError,
                  "mv root/file4 root/bar-ro/boffo")

        d.addCallback(lambda res: rootnode.list())
        d.addCallback(self.failUnlessKeysMatch, ["bar", "bar-ro", "file4"])
        d.addCallback(lambda res:self.bar_node.list())
        d.addCallback(self.failUnlessKeysMatch, ["baz"])
        d.addCallback(lambda res:self.bar_node_readonly.list())
        d.addCallback(self.failUnlessKeysMatch, ["baz"])


        d.addCallback(lambda res:
                      rootnode.move_child_to("file4", self.bar_node))

        d.addCallback(lambda res: rootnode.list())
        d.addCallback(self.failUnlessKeysMatch, ["bar", "bar-ro"])
        d.addCallback(lambda res:self.bar_node.list())
        d.addCallback(self.failUnlessKeysMatch, ["baz", "file4"])
        d.addCallback(lambda res:self.bar_node_readonly.list())
        d.addCallback(self.failUnlessKeysMatch, ["baz", "file4"])

        d.addCallback(self._test_one_3)
        return d

    def _test_one_3(self, res):
        # now test some of the diag tools with the data we've created
        s = StringIO()
        rc = runner.dump_root_dirnode("vdrive/test_one", {}, s)
        output = s.getvalue()
        self.failUnless(output.startswith("URI:DIR:fakeFURL:"))
        self.failUnlessEqual(rc, 0)

        s = StringIO()
        args = {'uri': self.bar_node.get_uri(),
                'verbose': True,
                }
        rc = runner.dump_directory_node("vdrive/test_one", args, s)
        output = s.getvalue()
        #print output
        self.failUnless("dirnode uri: URI:DIR:myFURL" in output)
        self.failUnless("write_enabler" in output)
        self.failIf("write_enabler: None" in output)
        self.failUnless("key baz\n" in output)
        self.failUnless(" write: URI:DIR:myFURL:" in output)
        self.failUnless(" read: URI:DIR-RO:myFURL:" in output)
        self.failUnless("key file4\n" in output)
        self.failUnless("H_key " in output)
        self.failUnlessEqual(rc, 0)

        s = StringIO()
        args = {'uri': self.bar_node.get_uri(),
                'verbose': False,
                }
        rc = runner.dump_directory_node("vdrive/test_one", args, s)
        output = s.getvalue()
        #print output
        self.failUnless("dirnode uri: URI:DIR:myFURL" in output)
        self.failUnless("write_enabler" in output)
        self.failIf("write_enabler: None" in output)
        self.failUnless("key baz\n" in output)
        self.failUnless(" write: URI:DIR:myFURL:" in output)
        self.failUnless(" read: URI:DIR-RO:myFURL:" in output)
        self.failUnless("key file4\n" in output)
        self.failIf("H_key " in output)
        self.failUnlessEqual(rc, 0)

        s = StringIO()
        args = {'uri': self.bar_node_readonly.get_uri(),
                'verbose': True,
                }
        rc = runner.dump_directory_node("vdrive/test_one", args, s)
        output = s.getvalue()
        #print output
        self.failUnless("dirnode uri: URI:DIR-RO:myFURL" in output)
        self.failUnless("write_enabler: None" in output)
        self.failUnless("key baz\n" in output)
        self.failIf(" write: URI:DIR:myFURL:" in output)
        self.failUnless(" read: URI:DIR-RO:myFURL:" in output)
        self.failUnless("key file4\n" in output)
        self.failUnlessEqual(rc, 0)

    def shouldFail(self, res, expected_failure, which, substring=None):
        if isinstance(res, failure.Failure):
            res.trap(expected_failure)
            if substring:
                self.failUnless(substring in str(res),
                                "substring '%s' not in '%s'"
                                % (substring, str(res)))
        else:
            self.fail("%s was supposed to raise %s, not get '%s'" %
                      (which, expected_failure, res))

    def failUnlessKeysMatch(self, res, expected_keys):
        self.failUnlessEqual(sorted(res.keys()),
                             sorted(expected_keys))
        return res

def flip_bit(data, offset):
    if offset < 0:
        offset = len(data) + offset
    return data[:offset] + chr(ord(data[offset]) ^ 0x01) + data[offset+1:]

class Encryption(unittest.TestCase):
    def test_loopback(self):
        key = "k" * 16
        data = "This is some plaintext data."
        crypttext = vdrive.encrypt(key, data)
        plaintext = vdrive.decrypt(key, crypttext)
        self.failUnlessEqual(data, plaintext)

    def test_hmac(self):
        key = "j" * 16
        data = "This is some more plaintext data."
        crypttext = vdrive.encrypt(key, data)
        # flip a bit in the IV
        self.failUnlessRaises(vdrive.IntegrityCheckError,
                              vdrive.decrypt,
                              key, flip_bit(crypttext, 0))
        # flip a bit in the crypttext
        self.failUnlessRaises(vdrive.IntegrityCheckError,
                              vdrive.decrypt,
                              key, flip_bit(crypttext, 16))
        # flip a bit in the HMAC
        self.failUnlessRaises(vdrive.IntegrityCheckError,
                              vdrive.decrypt,
                              key, flip_bit(crypttext, -1))
        plaintext = vdrive.decrypt(key, crypttext)
        self.failUnlessEqual(data, plaintext)

