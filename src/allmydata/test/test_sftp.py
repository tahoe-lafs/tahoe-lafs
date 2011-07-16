
import re, struct, traceback, time, calendar
from stat import S_IFREG, S_IFDIR

from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python.failure import Failure
from twisted.internet.error import ProcessDone, ProcessTerminated
from allmydata.util import deferredutil

conch_interfaces = None
sftp = None
sftpd = None
have_pycrypto = False
try:
    from Crypto import Util
    Util  # hush pyflakes
    have_pycrypto = True
except ImportError:
    pass

if have_pycrypto:
    from twisted.conch import interfaces as conch_interfaces
    from twisted.conch.ssh import filetransfer as sftp
    from allmydata.frontends import sftpd

from allmydata.interfaces import IDirectoryNode, ExistingChildError, NoSuchChildError
from allmydata.mutable.common import NotWriteableError

from allmydata.util.consumer import download_to_data
from allmydata.immutable import upload
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common import ShouldFailMixin
from allmydata.test.common_util import ReallyEqualMixin

timeout = 240

class Handler(GridTestMixin, ShouldFailMixin, ReallyEqualMixin, unittest.TestCase):
    """This is a no-network unit test of the SFTPUserHandler and the abstractions it uses."""

    if not have_pycrypto:
        skip = "SFTP support requires pycrypto, which is not installed"

    def shouldFailWithSFTPError(self, expected_code, which, callable, *args, **kwargs):
        assert isinstance(expected_code, int), repr(expected_code)
        assert isinstance(which, str), repr(which)
        s = traceback.format_stack()
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def _done(res):
            if isinstance(res, Failure):
                res.trap(sftp.SFTPError)
                self.failUnlessReallyEqual(res.value.code, expected_code,
                                           "%s was supposed to raise SFTPError(%r), not SFTPError(%r): %s" %
                                           (which, expected_code, res.value.code, res))
            else:
                print '@' + '@'.join(s)
                self.fail("%s was supposed to raise SFTPError(%r), not get %r" %
                          (which, expected_code, res))
        d.addBoth(_done)
        return d

    def _set_up(self, basedir, num_clients=1, num_servers=10):
        self.basedir = "sftp/" + basedir
        self.set_up_grid(num_clients=num_clients, num_servers=num_servers)

        self.client = self.g.clients[0]
        self.username = "alice"

        d = self.client.create_dirnode()
        def _created_root(node):
            self.root = node
            self.root_uri = node.get_uri()
            sftpd._reload()
            self.handler = sftpd.SFTPUserHandler(self.client, self.root, self.username)
        d.addCallback(_created_root)
        return d

    def _set_up_tree(self):
        d = self.client.create_mutable_file("mutable file contents")
        d.addCallback(lambda node: self.root.set_node(u"mutable", node))
        def _created_mutable(n):
            self.mutable = n
            self.mutable_uri = n.get_uri()
        d.addCallback(_created_mutable)

        d.addCallback(lambda ign:
                      self.root._create_and_validate_node(None, self.mutable.get_readonly_uri(), name=u"readonly"))
        d.addCallback(lambda node: self.root.set_node(u"readonly", node))
        def _created_readonly(n):
            self.readonly = n
            self.readonly_uri = n.get_uri()
        d.addCallback(_created_readonly)

        gross = upload.Data("0123456789" * 101, None)
        d.addCallback(lambda ign: self.root.add_file(u"gro\u00DF", gross))
        def _created_gross(n):
            self.gross = n
            self.gross_uri = n.get_uri()
        d.addCallback(_created_gross)

        small = upload.Data("0123456789", None)
        d.addCallback(lambda ign: self.root.add_file(u"small", small))
        def _created_small(n):
            self.small = n
            self.small_uri = n.get_uri()
        d.addCallback(_created_small)

        small2 = upload.Data("Small enough for a LIT too", None)
        d.addCallback(lambda ign: self.root.add_file(u"small2", small2))
        def _created_small2(n):
            self.small2 = n
            self.small2_uri = n.get_uri()
        d.addCallback(_created_small2)

        empty_litdir_uri = "URI:DIR2-LIT:"

        # contains one child which is itself also LIT:
        tiny_litdir_uri = "URI:DIR2-LIT:gqytunj2onug64tufqzdcosvkjetutcjkq5gw4tvm5vwszdgnz5hgyzufqydulbshj5x2lbm"

        unknown_uri = "x-tahoe-crazy://I_am_from_the_future."

        d.addCallback(lambda ign: self.root._create_and_validate_node(None, empty_litdir_uri, name=u"empty_lit_dir"))
        def _created_empty_lit_dir(n):
            self.empty_lit_dir = n
            self.empty_lit_dir_uri = n.get_uri()
            self.root.set_node(u"empty_lit_dir", n)
        d.addCallback(_created_empty_lit_dir)

        d.addCallback(lambda ign: self.root._create_and_validate_node(None, tiny_litdir_uri, name=u"tiny_lit_dir"))
        def _created_tiny_lit_dir(n):
            self.tiny_lit_dir = n
            self.tiny_lit_dir_uri = n.get_uri()
            self.root.set_node(u"tiny_lit_dir", n)
        d.addCallback(_created_tiny_lit_dir)

        d.addCallback(lambda ign: self.root._create_and_validate_node(None, unknown_uri, name=u"unknown"))
        def _created_unknown(n):
            self.unknown = n
            self.unknown_uri = n.get_uri()
            self.root.set_node(u"unknown", n)
        d.addCallback(_created_unknown)

        fall_of_the_Berlin_wall = calendar.timegm(time.strptime("1989-11-09 20:00:00 UTC", "%Y-%m-%d %H:%M:%S %Z"))
        md = {'mtime': fall_of_the_Berlin_wall, 'tahoe': {'linkmotime': fall_of_the_Berlin_wall}}
        d.addCallback(lambda ign: self.root.set_node(u"loop", self.root, metadata=md))
        return d

    def test_basic(self):
        d = self._set_up("basic")
        def _check(ign):
            # Test operations that have no side-effects, and don't need the tree.

            version = self.handler.gotVersion(3, {})
            self.failUnless(isinstance(version, dict))

            self.failUnlessReallyEqual(self.handler._path_from_string(""), [])
            self.failUnlessReallyEqual(self.handler._path_from_string("/"), [])
            self.failUnlessReallyEqual(self.handler._path_from_string("."), [])
            self.failUnlessReallyEqual(self.handler._path_from_string("//"), [])
            self.failUnlessReallyEqual(self.handler._path_from_string("/."), [])
            self.failUnlessReallyEqual(self.handler._path_from_string("/./"), [])
            self.failUnlessReallyEqual(self.handler._path_from_string("foo"), [u"foo"])
            self.failUnlessReallyEqual(self.handler._path_from_string("/foo"), [u"foo"])
            self.failUnlessReallyEqual(self.handler._path_from_string("foo/"), [u"foo"])
            self.failUnlessReallyEqual(self.handler._path_from_string("/foo/"), [u"foo"])
            self.failUnlessReallyEqual(self.handler._path_from_string("foo/bar"), [u"foo", u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("/foo/bar"), [u"foo", u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("foo/bar//"), [u"foo", u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("/foo/bar//"), [u"foo", u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("foo/./bar"), [u"foo", u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("./foo/./bar"), [u"foo", u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("foo/../bar"), [u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("/foo/../bar"), [u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("../bar"), [u"bar"])
            self.failUnlessReallyEqual(self.handler._path_from_string("/../bar"), [u"bar"])

            self.failUnlessReallyEqual(self.handler.realPath(""), "/")
            self.failUnlessReallyEqual(self.handler.realPath("/"), "/")
            self.failUnlessReallyEqual(self.handler.realPath("."), "/")
            self.failUnlessReallyEqual(self.handler.realPath("//"), "/")
            self.failUnlessReallyEqual(self.handler.realPath("/."), "/")
            self.failUnlessReallyEqual(self.handler.realPath("/./"), "/")
            self.failUnlessReallyEqual(self.handler.realPath("foo"), "/foo")
            self.failUnlessReallyEqual(self.handler.realPath("/foo"), "/foo")
            self.failUnlessReallyEqual(self.handler.realPath("foo/"), "/foo")
            self.failUnlessReallyEqual(self.handler.realPath("/foo/"), "/foo")
            self.failUnlessReallyEqual(self.handler.realPath("foo/bar"), "/foo/bar")
            self.failUnlessReallyEqual(self.handler.realPath("/foo/bar"), "/foo/bar")
            self.failUnlessReallyEqual(self.handler.realPath("foo/bar//"), "/foo/bar")
            self.failUnlessReallyEqual(self.handler.realPath("/foo/bar//"), "/foo/bar")
            self.failUnlessReallyEqual(self.handler.realPath("foo/./bar"), "/foo/bar")
            self.failUnlessReallyEqual(self.handler.realPath("./foo/./bar"), "/foo/bar")
            self.failUnlessReallyEqual(self.handler.realPath("foo/../bar"), "/bar")
            self.failUnlessReallyEqual(self.handler.realPath("/foo/../bar"), "/bar")
            self.failUnlessReallyEqual(self.handler.realPath("../bar"), "/bar")
            self.failUnlessReallyEqual(self.handler.realPath("/../bar"), "/bar")
        d.addCallback(_check)

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "_path_from_string invalid UTF-8",
                                         self.handler._path_from_string, "\xFF"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "realPath invalid UTF-8",
                                         self.handler.realPath, "\xFF"))

        return d

    def test_convert_error(self):
        self.failUnlessReallyEqual(sftpd._convert_error(None, "request"), None)
        
        d = defer.succeed(None)
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "_convert_error SFTPError",
                                         sftpd._convert_error, Failure(sftp.SFTPError(sftp.FX_FAILURE, "foo")), "request"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "_convert_error NoSuchChildError",
                                         sftpd._convert_error, Failure(NoSuchChildError("foo")), "request"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "_convert_error ExistingChildError",
                                         sftpd._convert_error, Failure(ExistingChildError("foo")), "request"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "_convert_error NotWriteableError",
                                         sftpd._convert_error, Failure(NotWriteableError("foo")), "request"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_OP_UNSUPPORTED, "_convert_error NotImplementedError",
                                         sftpd._convert_error, Failure(NotImplementedError("foo")), "request"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_EOF, "_convert_error EOFError",
                                         sftpd._convert_error, Failure(EOFError("foo")), "request"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_EOF, "_convert_error defer.FirstError",
                                         sftpd._convert_error, Failure(defer.FirstError(
                                                                 Failure(sftp.SFTPError(sftp.FX_EOF, "foo")), 0)), "request"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "_convert_error AssertionError",
                                         sftpd._convert_error, Failure(AssertionError("foo")), "request"))

        return d

    def test_not_implemented(self):
        d = self._set_up("not_implemented")

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_OP_UNSUPPORTED, "readLink link",
                                         self.handler.readLink, "link"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_OP_UNSUPPORTED, "makeLink link file",
                                         self.handler.makeLink, "link", "file"))

        return d

    def _compareDirLists(self, actual, expected):
       actual_list = sorted(actual)
       expected_list = sorted(expected)
       self.failUnlessReallyEqual(len(actual_list), len(expected_list),
                            "%r is wrong length, expecting %r" % (actual_list, expected_list))
       for (a, b) in zip(actual_list, expected_list):
           (name, text, attrs) = a
           (expected_name, expected_text_re, expected_attrs) = b
           self.failUnlessReallyEqual(name, expected_name)
           self.failUnless(re.match(expected_text_re, text),
                           "%r does not match %r in\n%r" % (text, expected_text_re, actual_list))
           self._compareAttributes(attrs, expected_attrs)

    def _compareAttributes(self, attrs, expected_attrs):
        # It is ok for there to be extra actual attributes.
        # TODO: check times
        for e in expected_attrs:
            self.failUnless(e in attrs, "%r is not in\n%r" % (e, attrs))
            self.failUnlessReallyEqual(attrs[e], expected_attrs[e],
                                       "%r:%r is not %r in\n%r" % (e, attrs[e], expected_attrs[e], attrs))

    def test_openDirectory_and_attrs(self):
        d = self._set_up("openDirectory_and_attrs")
        d.addCallback(lambda ign: self._set_up_tree())

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openDirectory small",
                                         self.handler.openDirectory, "small"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openDirectory unknown",
                                         self.handler.openDirectory, "unknown"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openDirectory nodir",
                                         self.handler.openDirectory, "nodir"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openDirectory nodir/nodir",
                                         self.handler.openDirectory, "nodir/nodir"))

        gross = u"gro\u00DF".encode("utf-8")
        expected_root = [
            ('empty_lit_dir', r'dr-xr-xr-x .* 0 .* empty_lit_dir$',       {'permissions': S_IFDIR | 0555}),
            (gross,           r'-rw-rw-rw- .* 1010 .* '+gross+'$',        {'permissions': S_IFREG | 0666, 'size': 1010}),
            # The fall of the Berlin wall may have been on 9th or 10th November 1989 depending on the gateway's timezone.
            #('loop',          r'drwxrwxrwx .* 0 Nov (09|10)  1989 loop$', {'permissions': S_IFDIR | 0777}),
            ('loop',          r'drwxrwxrwx .* 0 .* loop$',                {'permissions': S_IFDIR | 0777}),
            ('mutable',       r'-rw-rw-rw- .* 0 .* mutable$',             {'permissions': S_IFREG | 0666}),
            ('readonly',      r'-r--r--r-- .* 0 .* readonly$',            {'permissions': S_IFREG | 0444}),
            ('small',         r'-rw-rw-rw- .* 10 .* small$',              {'permissions': S_IFREG | 0666, 'size': 10}),
            ('small2',        r'-rw-rw-rw- .* 26 .* small2$',             {'permissions': S_IFREG | 0666, 'size': 26}),
            ('tiny_lit_dir',  r'dr-xr-xr-x .* 0 .* tiny_lit_dir$',        {'permissions': S_IFDIR | 0555}),
            ('unknown',       r'\?--------- .* 0 .* unknown$',            {'permissions': 0}),
        ]

        d.addCallback(lambda ign: self.handler.openDirectory(""))
        d.addCallback(lambda res: self._compareDirLists(res, expected_root))

        d.addCallback(lambda ign: self.handler.openDirectory("loop"))
        d.addCallback(lambda res: self._compareDirLists(res, expected_root))

        d.addCallback(lambda ign: self.handler.openDirectory("loop/loop"))
        d.addCallback(lambda res: self._compareDirLists(res, expected_root))

        d.addCallback(lambda ign: self.handler.openDirectory("empty_lit_dir"))
        d.addCallback(lambda res: self._compareDirLists(res, []))

        # The UTC epoch may either be in Jan 1 1970 or Dec 31 1969 depending on the gateway's timezone.
        expected_tiny_lit = [
            ('short', r'-r--r--r-- .* 8 (Jan 01  1970|Dec 31  1969) short$', {'permissions': S_IFREG | 0444, 'size': 8}),
        ]

        d.addCallback(lambda ign: self.handler.openDirectory("tiny_lit_dir"))
        d.addCallback(lambda res: self._compareDirLists(res, expected_tiny_lit))

        d.addCallback(lambda ign: self.handler.getAttrs("small", True))
        d.addCallback(lambda attrs: self._compareAttributes(attrs, {'permissions': S_IFREG | 0666, 'size': 10}))

        d.addCallback(lambda ign: self.handler.setAttrs("small", {}))
        d.addCallback(lambda res: self.failUnlessReallyEqual(res, None))

        d.addCallback(lambda ign: self.handler.getAttrs("small", True))
        d.addCallback(lambda attrs: self._compareAttributes(attrs, {'permissions': S_IFREG | 0666, 'size': 10}))

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_OP_UNSUPPORTED, "setAttrs size",
                                         self.handler.setAttrs, "small", {'size': 0}))

        d.addCallback(lambda ign: self.failUnlessEqual(sftpd.all_heisenfiles, {}))
        d.addCallback(lambda ign: self.failUnlessEqual(self.handler._heisenfiles, {}))
        return d

    def test_openFile_read(self):
        d = self._set_up("openFile_read")
        d.addCallback(lambda ign: self._set_up_tree())

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "openFile small 0 bad",
                                         self.handler.openFile, "small", 0, {}))

        # attempting to open a non-existent file should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openFile nofile READ nosuch",
                                         self.handler.openFile, "nofile", sftp.FXF_READ, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openFile nodir/file READ nosuch",
                                         self.handler.openFile, "nodir/file", sftp.FXF_READ, {}))

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile unknown READ denied",
                                         self.handler.openFile, "unknown", sftp.FXF_READ, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile unknown/file READ denied",
                                         self.handler.openFile, "unknown/file", sftp.FXF_READ, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir READ denied",
                                         self.handler.openFile, "tiny_lit_dir", sftp.FXF_READ, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile unknown uri READ denied",
                                         self.handler.openFile, "uri/"+self.unknown_uri, sftp.FXF_READ, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir uri READ denied",
                                         self.handler.openFile, "uri/"+self.tiny_lit_dir_uri, sftp.FXF_READ, {}))
        # FIXME: should be FX_NO_SUCH_FILE?
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile noexist uri READ denied",
                                         self.handler.openFile, "uri/URI:noexist", sftp.FXF_READ, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openFile invalid UTF-8 uri READ denied",
                                         self.handler.openFile, "uri/URI:\xFF", sftp.FXF_READ, {}))

        # reading an existing file should succeed
        d.addCallback(lambda ign: self.handler.openFile("small", sftp.FXF_READ, {}))
        def _read_small(rf):
            d2 = rf.readChunk(0, 10)
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))

            d2.addCallback(lambda ign: rf.readChunk(2, 6))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "234567"))

            d2.addCallback(lambda ign: rf.readChunk(1, 0))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, ""))

            d2.addCallback(lambda ign: rf.readChunk(8, 4))  # read that starts before EOF is OK
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "89"))

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_EOF, "readChunk starting at EOF (0-byte)",
                                             rf.readChunk, 10, 0))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_EOF, "readChunk starting at EOF",
                                             rf.readChunk, 10, 1))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_EOF, "readChunk starting after EOF",
                                             rf.readChunk, 11, 1))

            d2.addCallback(lambda ign: rf.getAttrs())
            d2.addCallback(lambda attrs: self._compareAttributes(attrs, {'permissions': S_IFREG | 0666, 'size': 10}))

            d2.addCallback(lambda ign: self.handler.getAttrs("small", followLinks=0))
            d2.addCallback(lambda attrs: self._compareAttributes(attrs, {'permissions': S_IFREG | 0666, 'size': 10}))

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "writeChunk on read-only handle denied",
                                             rf.writeChunk, 0, "a"))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "setAttrs on read-only handle denied",
                                             rf.setAttrs, {}))

            d2.addCallback(lambda ign: rf.close())

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "readChunk on closed file bad",
                                             rf.readChunk, 0, 1))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "getAttrs on closed file bad",
                                             rf.getAttrs))

            d2.addCallback(lambda ign: rf.close()) # should be no-op
            return d2
        d.addCallback(_read_small)

        # repeat for a large file
        gross = u"gro\u00DF".encode("utf-8")
        d.addCallback(lambda ign: self.handler.openFile(gross, sftp.FXF_READ, {}))
        def _read_gross(rf):
            d2 = rf.readChunk(0, 10)
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))

            d2.addCallback(lambda ign: rf.readChunk(2, 6))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "234567"))

            d2.addCallback(lambda ign: rf.readChunk(1, 0))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, ""))

            d2.addCallback(lambda ign: rf.readChunk(1008, 4))  # read that starts before EOF is OK
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "89"))

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_EOF, "readChunk starting at EOF (0-byte)",
                                             rf.readChunk, 1010, 0))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_EOF, "readChunk starting at EOF",
                                             rf.readChunk, 1010, 1))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_EOF, "readChunk starting after EOF",
                                             rf.readChunk, 1011, 1))

            d2.addCallback(lambda ign: rf.getAttrs())
            d2.addCallback(lambda attrs: self._compareAttributes(attrs, {'permissions': S_IFREG | 0666, 'size': 1010}))

            d2.addCallback(lambda ign: self.handler.getAttrs(gross, followLinks=0))
            d2.addCallback(lambda attrs: self._compareAttributes(attrs, {'permissions': S_IFREG | 0666, 'size': 1010}))

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "writeChunk on read-only handle denied",
                                             rf.writeChunk, 0, "a"))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "setAttrs on read-only handle denied",
                                             rf.setAttrs, {}))

            d2.addCallback(lambda ign: rf.close())

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "readChunk on closed file",
                                             rf.readChunk, 0, 1))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "getAttrs on closed file",
                                             rf.getAttrs))

            d2.addCallback(lambda ign: rf.close()) # should be no-op
            return d2
        d.addCallback(_read_gross)

        # reading an existing small file via uri/ should succeed
        d.addCallback(lambda ign: self.handler.openFile("uri/"+self.small_uri, sftp.FXF_READ, {}))
        def _read_small_uri(rf):
            d2 = rf.readChunk(0, 10)
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))
            d2.addCallback(lambda ign: rf.close())
            return d2
        d.addCallback(_read_small_uri)

        # repeat for a large file
        d.addCallback(lambda ign: self.handler.openFile("uri/"+self.gross_uri, sftp.FXF_READ, {}))
        def _read_gross_uri(rf):
            d2 = rf.readChunk(0, 10)
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))
            d2.addCallback(lambda ign: rf.close())
            return d2
        d.addCallback(_read_gross_uri)

        # repeat for a mutable file
        d.addCallback(lambda ign: self.handler.openFile("uri/"+self.mutable_uri, sftp.FXF_READ, {}))
        def _read_mutable_uri(rf):
            d2 = rf.readChunk(0, 100)
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "mutable file contents"))
            d2.addCallback(lambda ign: rf.close())
            return d2
        d.addCallback(_read_mutable_uri)

        # repeat for a file within a directory referenced by URI
        d.addCallback(lambda ign: self.handler.openFile("uri/"+self.tiny_lit_dir_uri+"/short", sftp.FXF_READ, {}))
        def _read_short(rf):
            d2 = rf.readChunk(0, 100)
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "The end."))
            d2.addCallback(lambda ign: rf.close())
            return d2
        d.addCallback(_read_short)

        d.addCallback(lambda ign: self.failUnlessEqual(sftpd.all_heisenfiles, {}))
        d.addCallback(lambda ign: self.failUnlessEqual(self.handler._heisenfiles, {}))
        return d

    def test_openFile_write(self):
        d = self._set_up("openFile_write")
        d.addCallback(lambda ign: self._set_up_tree())

        # '' is an invalid filename
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openFile '' WRITE|CREAT|TRUNC nosuch",
                                         self.handler.openFile, "", sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_TRUNC, {}))

        # TRUNC is not valid without CREAT if the file does not already exist
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openFile newfile WRITE|TRUNC nosuch",
                                         self.handler.openFile, "newfile", sftp.FXF_WRITE | sftp.FXF_TRUNC, {}))

        # EXCL is not valid without CREAT
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "openFile small WRITE|EXCL bad",
                                         self.handler.openFile, "small", sftp.FXF_WRITE | sftp.FXF_EXCL, {}))

        # cannot write to an existing directory
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir WRITE denied",
                                         self.handler.openFile, "tiny_lit_dir", sftp.FXF_WRITE, {}))

        # cannot write to an existing unknown
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile unknown WRITE denied",
                                         self.handler.openFile, "unknown", sftp.FXF_WRITE, {}))

        # cannot create a child of an unknown
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile unknown/newfile WRITE|CREAT denied",
                                         self.handler.openFile, "unknown/newfile",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT, {}))

        # cannot write to a new file in an immutable directory
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir/newfile WRITE|CREAT|TRUNC denied",
                                         self.handler.openFile, "tiny_lit_dir/newfile",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_TRUNC, {}))

        # cannot write to an existing immutable file in an immutable directory (with or without CREAT and EXCL)
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir/short WRITE denied",
                                         self.handler.openFile, "tiny_lit_dir/short", sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir/short WRITE|CREAT denied",
                                         self.handler.openFile, "tiny_lit_dir/short",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT, {}))

        # cannot write to a mutable file via a readonly cap (by path or uri)
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile readonly WRITE denied",
                                         self.handler.openFile, "readonly", sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile readonly uri WRITE denied",
                                         self.handler.openFile, "uri/"+self.readonly_uri, sftp.FXF_WRITE, {}))

        # cannot create a file with the EXCL flag if it already exists
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "openFile small WRITE|CREAT|EXCL failure",
                                         self.handler.openFile, "small",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "openFile mutable WRITE|CREAT|EXCL failure",
                                         self.handler.openFile, "mutable",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "openFile mutable uri WRITE|CREAT|EXCL failure",
                                         self.handler.openFile, "uri/"+self.mutable_uri,
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "openFile tiny_lit_dir/short WRITE|CREAT|EXCL failure",
                                         self.handler.openFile, "tiny_lit_dir/short",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))

        # cannot write to an immutable file if we don't have its parent (with or without CREAT, TRUNC, or EXCL)
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile small uri WRITE denied",
                                         self.handler.openFile, "uri/"+self.small_uri, sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile small uri WRITE|CREAT denied",
                                         self.handler.openFile, "uri/"+self.small_uri,
                                         sftp.FXF_WRITE | sftp.FXF_CREAT, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile small uri WRITE|CREAT|TRUNC denied",
                                         self.handler.openFile, "uri/"+self.small_uri,
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_TRUNC, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile small uri WRITE|CREAT|EXCL denied",
                                         self.handler.openFile, "uri/"+self.small_uri,
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))

        # test creating a new file with truncation and extension
        d.addCallback(lambda ign:
                      self.handler.openFile("newfile", sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_TRUNC, {}))
        def _write(wf):
            d2 = wf.writeChunk(0, "0123456789")
            d2.addCallback(lambda res: self.failUnlessReallyEqual(res, None))

            d2.addCallback(lambda ign: wf.writeChunk(8, "0123"))
            d2.addCallback(lambda ign: wf.writeChunk(13, "abc"))

            d2.addCallback(lambda ign: wf.getAttrs())
            d2.addCallback(lambda attrs: self._compareAttributes(attrs, {'permissions': S_IFREG | 0666, 'size': 16}))

            d2.addCallback(lambda ign: self.handler.getAttrs("newfile", followLinks=0))
            d2.addCallback(lambda attrs: self._compareAttributes(attrs, {'permissions': S_IFREG | 0666, 'size': 16}))

            d2.addCallback(lambda ign: wf.setAttrs({}))

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "setAttrs with negative size bad",
                                             wf.setAttrs, {'size': -1}))

            d2.addCallback(lambda ign: wf.setAttrs({'size': 14}))
            d2.addCallback(lambda ign: wf.getAttrs())
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['size'], 14))

            d2.addCallback(lambda ign: wf.setAttrs({'size': 14}))
            d2.addCallback(lambda ign: wf.getAttrs())
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['size'], 14))

            d2.addCallback(lambda ign: wf.setAttrs({'size': 17}))
            d2.addCallback(lambda ign: wf.getAttrs())
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['size'], 17))
            d2.addCallback(lambda ign: self.handler.getAttrs("newfile", followLinks=0))
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['size'], 17))

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "readChunk on write-only handle denied",
                                             wf.readChunk, 0, 1))

            d2.addCallback(lambda ign: wf.close())

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "writeChunk on closed file bad",
                                             wf.writeChunk, 0, "a"))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "setAttrs on closed file bad",
                                             wf.setAttrs, {'size': 0}))

            d2.addCallback(lambda ign: wf.close()) # should be no-op
            return d2
        d.addCallback(_write)
        d.addCallback(lambda ign: self.root.get(u"newfile"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "012345670123\x00a\x00\x00\x00"))

        # test APPEND flag, and also replacing an existing file ("newfile" created by the previous test)
        d.addCallback(lambda ign:
                      self.handler.openFile("newfile", sftp.FXF_WRITE | sftp.FXF_CREAT |
                                                       sftp.FXF_TRUNC | sftp.FXF_APPEND, {}))
        def _write_append(wf):
            d2 = wf.writeChunk(0, "0123456789")
            d2.addCallback(lambda ign: wf.writeChunk(8, "0123"))

            d2.addCallback(lambda ign: wf.setAttrs({'size': 17}))
            d2.addCallback(lambda ign: wf.getAttrs())
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['size'], 17))

            d2.addCallback(lambda ign: wf.writeChunk(0, "z"))
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_append)
        d.addCallback(lambda ign: self.root.get(u"newfile"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "01234567890123\x00\x00\x00z"))

        # test WRITE | TRUNC without CREAT, when the file already exists
        # This is invalid according to section 6.3 of the SFTP spec, but required for interoperability,
        # since POSIX does allow O_WRONLY | O_TRUNC.
        d.addCallback(lambda ign:
                      self.handler.openFile("newfile", sftp.FXF_WRITE | sftp.FXF_TRUNC, {}))
        def _write_trunc(wf):
            d2 = wf.writeChunk(0, "01234")
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_trunc)
        d.addCallback(lambda ign: self.root.get(u"newfile"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "01234"))

        # test WRITE | TRUNC with permissions: 0
        d.addCallback(lambda ign:
                      self.handler.openFile("newfile", sftp.FXF_WRITE | sftp.FXF_TRUNC, {'permissions': 0}))
        d.addCallback(_write_trunc)
        d.addCallback(lambda ign: self.root.get(u"newfile"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "01234"))
        d.addCallback(lambda ign: self.root.get_metadata_for(u"newfile"))
        d.addCallback(lambda metadata: self.failIf(metadata.get('no-write', False), metadata))

        # test EXCL flag
        d.addCallback(lambda ign:
                      self.handler.openFile("excl", sftp.FXF_WRITE | sftp.FXF_CREAT |
                                                    sftp.FXF_TRUNC | sftp.FXF_EXCL, {}))
        def _write_excl(wf):
            d2 = self.root.get(u"excl")
            d2.addCallback(lambda node: download_to_data(node))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, ""))

            d2.addCallback(lambda ign: wf.writeChunk(0, "0123456789"))
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_excl)
        d.addCallback(lambda ign: self.root.get(u"excl"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))

        # test that writing a zero-length file with EXCL only updates the directory once
        d.addCallback(lambda ign:
                      self.handler.openFile("zerolength", sftp.FXF_WRITE | sftp.FXF_CREAT |
                                                          sftp.FXF_EXCL, {}))
        def _write_excl_zerolength(wf):
            d2 = self.root.get(u"zerolength")
            d2.addCallback(lambda node: download_to_data(node))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, ""))

            # FIXME: no API to get the best version number exists (fix as part of #993)
            """
            d2.addCallback(lambda ign: self.root.get_best_version_number())
            def _check_version(version):
                d3 = wf.close()
                d3.addCallback(lambda ign: self.root.get_best_version_number())
                d3.addCallback(lambda new_version: self.failUnlessReallyEqual(new_version, version))
                return d3
            d2.addCallback(_check_version)
            """
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_excl_zerolength)
        d.addCallback(lambda ign: self.root.get(u"zerolength"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, ""))

        # test WRITE | CREAT | EXCL | APPEND
        d.addCallback(lambda ign:
                      self.handler.openFile("exclappend", sftp.FXF_WRITE | sftp.FXF_CREAT |
                                                          sftp.FXF_EXCL | sftp.FXF_APPEND, {}))
        def _write_excl_append(wf):
            d2 = self.root.get(u"exclappend")
            d2.addCallback(lambda node: download_to_data(node))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, ""))

            d2.addCallback(lambda ign: wf.writeChunk(10, "0123456789"))
            d2.addCallback(lambda ign: wf.writeChunk(5, "01234"))
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_excl_append)
        d.addCallback(lambda ign: self.root.get(u"exclappend"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "012345678901234"))

        # test WRITE | CREAT | APPEND when the file does not already exist
        d.addCallback(lambda ign:
                      self.handler.openFile("creatappend", sftp.FXF_WRITE | sftp.FXF_CREAT |
                                                           sftp.FXF_APPEND, {}))
        def _write_creat_append_new(wf):
            d2 = wf.writeChunk(10, "0123456789")
            d2.addCallback(lambda ign: wf.writeChunk(5, "01234"))
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_creat_append_new)
        d.addCallback(lambda ign: self.root.get(u"creatappend"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "012345678901234"))

        # ... and when it does exist
        d.addCallback(lambda ign:
                      self.handler.openFile("creatappend", sftp.FXF_WRITE | sftp.FXF_CREAT |
                                                           sftp.FXF_APPEND, {}))
        def _write_creat_append_existing(wf):
            d2 = wf.writeChunk(5, "01234")
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_creat_append_existing)
        d.addCallback(lambda ign: self.root.get(u"creatappend"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "01234567890123401234"))

        # test WRITE | CREAT without TRUNC, when the file does not already exist
        d.addCallback(lambda ign:
                      self.handler.openFile("newfile2", sftp.FXF_WRITE | sftp.FXF_CREAT, {}))
        def _write_creat_new(wf):
            d2 =  wf.writeChunk(0, "0123456789")
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_creat_new)
        d.addCallback(lambda ign: self.root.get(u"newfile2"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))

        # ... and when it does exist
        d.addCallback(lambda ign:
                      self.handler.openFile("newfile2", sftp.FXF_WRITE | sftp.FXF_CREAT, {}))
        def _write_creat_existing(wf):
            d2 =  wf.writeChunk(0, "abcde")
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_creat_existing)
        d.addCallback(lambda ign: self.root.get(u"newfile2"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "abcde56789"))

        d.addCallback(lambda ign: self.root.set_node(u"mutable2", self.mutable))

        # test writing to a mutable file
        d.addCallback(lambda ign:
                      self.handler.openFile("mutable", sftp.FXF_WRITE, {}))
        def _write_mutable(wf):
            d2 = wf.writeChunk(8, "new!")
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_mutable)
        d.addCallback(lambda ign: self.root.get(u"mutable"))
        def _check_same_file(node):
            self.failUnless(node.is_mutable())
            self.failIf(node.is_readonly())
            self.failUnlessReallyEqual(node.get_uri(), self.mutable_uri)
            return node.download_best_version()
        d.addCallback(_check_same_file)
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "mutable new! contents"))

        # ... and with permissions, which should be ignored
        d.addCallback(lambda ign:
                      self.handler.openFile("mutable", sftp.FXF_WRITE, {'permissions': 0}))
        d.addCallback(_write_mutable)
        d.addCallback(lambda ign: self.root.get(u"mutable"))
        d.addCallback(_check_same_file)
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "mutable new! contents"))

        # ... and with a setAttrs call that diminishes the parent link to read-only, first by path
        d.addCallback(lambda ign:
                      self.handler.openFile("mutable", sftp.FXF_WRITE, {}))
        def _write_mutable_setattr(wf):
            d2 = wf.writeChunk(8, "read-only link from parent")

            d2.addCallback(lambda ign: self.handler.setAttrs("mutable", {'permissions': 0444}))

            d2.addCallback(lambda ign: self.root.get(u"mutable"))
            d2.addCallback(lambda node: self.failUnless(node.is_readonly()))

            d2.addCallback(lambda ign: wf.getAttrs())
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['permissions'], S_IFREG | 0666))
            d2.addCallback(lambda ign: self.handler.getAttrs("mutable", followLinks=0))
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['permissions'], S_IFREG | 0444))

            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_mutable_setattr)
        d.addCallback(lambda ign: self.root.get(u"mutable"))
        def _check_readonly_file(node):
            self.failUnless(node.is_mutable())
            self.failUnless(node.is_readonly())
            self.failUnlessReallyEqual(node.get_write_uri(), None)
            self.failUnlessReallyEqual(node.get_storage_index(), self.mutable.get_storage_index())
            return node.download_best_version()
        d.addCallback(_check_readonly_file)
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "mutable read-only link from parent"))

        # ... and then by handle
        d.addCallback(lambda ign:
                      self.handler.openFile("mutable2", sftp.FXF_WRITE, {}))
        def _write_mutable2_setattr(wf):
            d2 = wf.writeChunk(7, "2")

            d2.addCallback(lambda ign: wf.setAttrs({'permissions': 0444, 'size': 8}))

            # The link isn't made read-only until the file is closed.
            d2.addCallback(lambda ign: self.root.get(u"mutable2"))
            d2.addCallback(lambda node: self.failIf(node.is_readonly()))

            d2.addCallback(lambda ign: wf.getAttrs())
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['permissions'], S_IFREG | 0444))
            d2.addCallback(lambda ign: self.handler.getAttrs("mutable2", followLinks=0))
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['permissions'], S_IFREG | 0666))

            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_mutable2_setattr)
        d.addCallback(lambda ign: self.root.get(u"mutable2"))
        d.addCallback(_check_readonly_file)  # from above
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "mutable2"))

        # test READ | WRITE without CREAT or TRUNC
        d.addCallback(lambda ign:
                      self.handler.openFile("small", sftp.FXF_READ | sftp.FXF_WRITE, {}))
        def _read_write(rwf):
            d2 = rwf.writeChunk(8, "0123")
            d2.addCallback(lambda ign: rwf.readChunk(0, 100))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "012345670123"))
            d2.addCallback(lambda ign: rwf.close())
            return d2
        d.addCallback(_read_write)
        d.addCallback(lambda ign: self.root.get(u"small"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "012345670123"))

        # test WRITE and rename while still open
        d.addCallback(lambda ign:
                      self.handler.openFile("small", sftp.FXF_WRITE, {}))
        def _write_rename(wf):
            d2 = wf.writeChunk(0, "abcd")
            d2.addCallback(lambda ign: self.handler.renameFile("small", "renamed"))
            d2.addCallback(lambda ign: wf.writeChunk(4, "efgh"))
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_rename)
        d.addCallback(lambda ign: self.root.get(u"renamed"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "abcdefgh0123"))
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "rename small while open", "small",
                                      self.root.get, u"small"))

        # test WRITE | CREAT | EXCL and rename while still open
        d.addCallback(lambda ign:
                      self.handler.openFile("newexcl", sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))
        def _write_creat_excl_rename(wf):
            d2 = wf.writeChunk(0, "abcd")
            d2.addCallback(lambda ign: self.handler.renameFile("newexcl", "renamedexcl"))
            d2.addCallback(lambda ign: wf.writeChunk(4, "efgh"))
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_creat_excl_rename)
        d.addCallback(lambda ign: self.root.get(u"renamedexcl"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "abcdefgh"))
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "rename newexcl while open", "newexcl",
                                      self.root.get, u"newexcl"))

        # it should be possible to rename even before the open has completed
        def _open_and_rename_race(ign):
            slow_open = defer.Deferred()
            reactor.callLater(1, slow_open.callback, None)
            d2 = self.handler.openFile("new", sftp.FXF_WRITE | sftp.FXF_CREAT, {}, delay=slow_open)

            # deliberate race between openFile and renameFile
            d3 = self.handler.renameFile("new", "new2")
            d3.addErrback(lambda err: self.fail("renameFile failed: %r" % (err,)))
            return d2
        d.addCallback(_open_and_rename_race)
        def _write_rename_race(wf):
            d2 = wf.writeChunk(0, "abcd")
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_rename_race)
        d.addCallback(lambda ign: self.root.get(u"new2"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "abcd"))
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "rename new while open", "new",
                                      self.root.get, u"new"))

        d.addCallback(lambda ign: self.failUnlessEqual(sftpd.all_heisenfiles, {}))
        d.addCallback(lambda ign: self.failUnlessEqual(self.handler._heisenfiles, {}))
        return d

    def test_removeFile(self):
        d = self._set_up("removeFile")
        d.addCallback(lambda ign: self._set_up_tree())

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "removeFile nofile",
                                         self.handler.removeFile, "nofile"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "removeFile nofile",
                                         self.handler.removeFile, "nofile"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "removeFile nodir/file",
                                         self.handler.removeFile, "nodir/file"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "removefile ''",
                                         self.handler.removeFile, ""))
            
        # removing a directory should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "removeFile tiny_lit_dir",
                                         self.handler.removeFile, "tiny_lit_dir"))

        # removing a file should succeed
        d.addCallback(lambda ign: self.root.get(u"gro\u00DF"))
        d.addCallback(lambda ign: self.handler.removeFile(u"gro\u00DF".encode('utf-8')))
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "removeFile gross", "gro\\xdf",
                                      self.root.get, u"gro\u00DF"))

        # removing an unknown should succeed
        d.addCallback(lambda ign: self.root.get(u"unknown"))
        d.addCallback(lambda ign: self.handler.removeFile("unknown"))
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "removeFile unknown", "unknown",
                                      self.root.get, u"unknown"))

        # removing a link to an open file should not prevent it from being read
        d.addCallback(lambda ign: self.handler.openFile("small", sftp.FXF_READ, {}))
        def _remove_and_read_small(rf):
            d2 = self.handler.removeFile("small")
            d2.addCallback(lambda ign:
                           self.shouldFail(NoSuchChildError, "removeFile small", "small",
                                           self.root.get, u"small"))
            d2.addCallback(lambda ign: rf.readChunk(0, 10))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))
            d2.addCallback(lambda ign: rf.close())
            return d2
        d.addCallback(_remove_and_read_small)

        # removing a link to a created file should prevent it from being created
        d.addCallback(lambda ign: self.handler.openFile("tempfile", sftp.FXF_READ | sftp.FXF_WRITE |
                                                                    sftp.FXF_CREAT, {}))
        def _write_remove(rwf):
            d2 = rwf.writeChunk(0, "0123456789")
            d2.addCallback(lambda ign: self.handler.removeFile("tempfile"))
            d2.addCallback(lambda ign: rwf.readChunk(0, 10))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))
            d2.addCallback(lambda ign: rwf.close())
            return d2
        d.addCallback(_write_remove)
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "removeFile tempfile", "tempfile",
                                      self.root.get, u"tempfile"))

        # ... even if the link is renamed while open
        d.addCallback(lambda ign: self.handler.openFile("tempfile2", sftp.FXF_READ | sftp.FXF_WRITE |
                                                                     sftp.FXF_CREAT, {}))
        def _write_rename_remove(rwf):
            d2 = rwf.writeChunk(0, "0123456789")
            d2.addCallback(lambda ign: self.handler.renameFile("tempfile2", "tempfile3"))
            d2.addCallback(lambda ign: self.handler.removeFile("tempfile3"))
            d2.addCallback(lambda ign: rwf.readChunk(0, 10))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))
            d2.addCallback(lambda ign: rwf.close())
            return d2
        d.addCallback(_write_rename_remove)
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "removeFile tempfile2", "tempfile2",
                                      self.root.get, u"tempfile2"))
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "removeFile tempfile3", "tempfile3",
                                      self.root.get, u"tempfile3"))

        d.addCallback(lambda ign: self.failUnlessEqual(sftpd.all_heisenfiles, {}))
        d.addCallback(lambda ign: self.failUnlessEqual(self.handler._heisenfiles, {}))
        return d

    def test_removeDirectory(self):
        d = self._set_up("removeDirectory")
        d.addCallback(lambda ign: self._set_up_tree())

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "removeDirectory nodir",
                                         self.handler.removeDirectory, "nodir"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "removeDirectory nodir/nodir",
                                         self.handler.removeDirectory, "nodir/nodir"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "removeDirectory ''",
                                         self.handler.removeDirectory, ""))

        # removing a file should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "removeDirectory gross",
                                         self.handler.removeDirectory, u"gro\u00DF".encode('utf-8')))

        # removing a directory should succeed
        d.addCallback(lambda ign: self.root.get(u"tiny_lit_dir"))
        d.addCallback(lambda ign: self.handler.removeDirectory("tiny_lit_dir"))
        d.addCallback(lambda ign:
                      self.shouldFail(NoSuchChildError, "removeDirectory tiny_lit_dir", "tiny_lit_dir",
                                      self.root.get, u"tiny_lit_dir"))

        # removing an unknown should succeed
        d.addCallback(lambda ign: self.root.get(u"unknown"))
        d.addCallback(lambda ign: self.handler.removeDirectory("unknown"))
        d.addCallback(lambda err:
                      self.shouldFail(NoSuchChildError, "removeDirectory unknown", "unknown",
                                      self.root.get, u"unknown"))

        d.addCallback(lambda ign: self.failUnlessEqual(sftpd.all_heisenfiles, {}))
        d.addCallback(lambda ign: self.failUnlessEqual(self.handler._heisenfiles, {}))
        return d

    def test_renameFile(self):
        d = self._set_up("renameFile")
        d.addCallback(lambda ign: self._set_up_tree())

        # renaming a non-existent file should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile nofile newfile",
                                         self.handler.renameFile, "nofile", "newfile"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile '' newfile",
                                         self.handler.renameFile, "", "newfile"))

        # renaming a file to a non-existent path should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile small nodir/small",
                                         self.handler.renameFile, "small", "nodir/small"))

        # renaming a file to an invalid UTF-8 name should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile small invalid",
                                         self.handler.renameFile, "small", "\xFF"))

        # renaming a file to or from an URI should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile small from uri",
                                         self.handler.renameFile, "uri/"+self.small_uri, "new"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile small to uri",
                                         self.handler.renameFile, "small", "uri/fake_uri"))

        # renaming a file onto an existing file, directory or unknown should fail
        # The SFTP spec isn't clear about what error should be returned, but sshfs depends on
        # it being FX_PERMISSION_DENIED.
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "renameFile small small2",
                                         self.handler.renameFile, "small", "small2"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "renameFile small tiny_lit_dir",
                                         self.handler.renameFile, "small", "tiny_lit_dir"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "renameFile small unknown",
                                         self.handler.renameFile, "small", "unknown"))

        # renaming a file onto a heisenfile should fail, even if the open hasn't completed
        def _rename_onto_heisenfile_race(wf):
            slow_open = defer.Deferred()
            reactor.callLater(1, slow_open.callback, None)

            d2 = self.handler.openFile("heisenfile", sftp.FXF_WRITE | sftp.FXF_CREAT, {}, delay=slow_open)

            # deliberate race between openFile and renameFile
            d3 = self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "renameFile small heisenfile",
                                              self.handler.renameFile, "small", "heisenfile")
            d2.addCallback(lambda wf: wf.close())
            return deferredutil.gatherResults([d2, d3])
        d.addCallback(_rename_onto_heisenfile_race)

        # renaming a file to a correct path should succeed
        d.addCallback(lambda ign: self.handler.renameFile("small", "new_small"))
        d.addCallback(lambda ign: self.root.get(u"new_small"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.small_uri))

        # renaming a file into a subdirectory should succeed (also tests Unicode names)
        d.addCallback(lambda ign: self.handler.renameFile(u"gro\u00DF".encode('utf-8'),
                                                          u"loop/neue_gro\u00DF".encode('utf-8')))
        d.addCallback(lambda ign: self.root.get(u"neue_gro\u00DF"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.gross_uri))

        # renaming a directory to a correct path should succeed
        d.addCallback(lambda ign: self.handler.renameFile("tiny_lit_dir", "new_tiny_lit_dir"))
        d.addCallback(lambda ign: self.root.get(u"new_tiny_lit_dir"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.tiny_lit_dir_uri))

        # renaming an unknown to a correct path should succeed
        d.addCallback(lambda ign: self.handler.renameFile("unknown", "new_unknown"))
        d.addCallback(lambda ign: self.root.get(u"new_unknown"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.unknown_uri))

        d.addCallback(lambda ign: self.failUnlessEqual(sftpd.all_heisenfiles, {}))
        d.addCallback(lambda ign: self.failUnlessEqual(self.handler._heisenfiles, {}))
        return d

    def test_renameFile_posix(self):
        def _renameFile(fromPathstring, toPathstring):
            extData = (struct.pack('>L', len(fromPathstring)) + fromPathstring +
                       struct.pack('>L', len(toPathstring))   + toPathstring)

            d2 = self.handler.extendedRequest('posix-rename@openssh.com', extData)
            def _check(res):
                res.trap(sftp.SFTPError)
                if res.value.code == sftp.FX_OK:
                    return None
                return res
            d2.addCallbacks(lambda res: self.fail("posix-rename request was supposed to "
                                                  "raise an SFTPError, not get '%r'" % (res,)),
                            _check)
            return d2

        d = self._set_up("renameFile_posix")
        d.addCallback(lambda ign: self._set_up_tree())

        d.addCallback(lambda ign: self.root.set_node(u"loop2", self.root))
        d.addCallback(lambda ign: self.root.set_node(u"unknown2", self.unknown))

        # POSIX-renaming a non-existent file should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile_posix nofile newfile",
                                         _renameFile, "nofile", "newfile"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile_posix '' newfile",
                                         _renameFile, "", "newfile"))

        # POSIX-renaming a file to a non-existent path should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile_posix small nodir/small",
                                         _renameFile, "small", "nodir/small"))

        # POSIX-renaming a file to an invalid UTF-8 name should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile_posix small invalid",
                                         _renameFile, "small", "\xFF"))

        # POSIX-renaming a file to or from an URI should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile_posix small from uri",
                                         _renameFile, "uri/"+self.small_uri, "new"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "renameFile_posix small to uri",
                                         _renameFile, "small", "uri/fake_uri"))

        # POSIX-renaming a file onto an existing file, directory or unknown should succeed
        d.addCallback(lambda ign: _renameFile("small", "small2"))
        d.addCallback(lambda ign: self.root.get(u"small2"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.small_uri))

        d.addCallback(lambda ign: _renameFile("small2", "loop2"))
        d.addCallback(lambda ign: self.root.get(u"loop2"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.small_uri))

        d.addCallback(lambda ign: _renameFile("loop2", "unknown2"))
        d.addCallback(lambda ign: self.root.get(u"unknown2"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.small_uri))

        # POSIX-renaming a file to a correct new path should succeed
        d.addCallback(lambda ign: _renameFile("unknown2", "new_small"))
        d.addCallback(lambda ign: self.root.get(u"new_small"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.small_uri))

        # POSIX-renaming a file into a subdirectory should succeed (also tests Unicode names)
        d.addCallback(lambda ign: _renameFile(u"gro\u00DF".encode('utf-8'),
                                              u"loop/neue_gro\u00DF".encode('utf-8')))
        d.addCallback(lambda ign: self.root.get(u"neue_gro\u00DF"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.gross_uri))

        # POSIX-renaming a directory to a correct path should succeed
        d.addCallback(lambda ign: _renameFile("tiny_lit_dir", "new_tiny_lit_dir"))
        d.addCallback(lambda ign: self.root.get(u"new_tiny_lit_dir"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.tiny_lit_dir_uri))

        # POSIX-renaming an unknown to a correct path should succeed
        d.addCallback(lambda ign: _renameFile("unknown", "new_unknown"))
        d.addCallback(lambda ign: self.root.get(u"new_unknown"))
        d.addCallback(lambda node: self.failUnlessReallyEqual(node.get_uri(), self.unknown_uri))

        d.addCallback(lambda ign: self.failUnlessEqual(sftpd.all_heisenfiles, {}))
        d.addCallback(lambda ign: self.failUnlessEqual(self.handler._heisenfiles, {}))
        return d

    def test_makeDirectory(self):
        d = self._set_up("makeDirectory")
        d.addCallback(lambda ign: self._set_up_tree())

        # making a directory at a correct path should succeed
        d.addCallback(lambda ign: self.handler.makeDirectory("newdir", {'ext_foo': 'bar', 'ctime': 42}))

        d.addCallback(lambda ign: self.root.get_child_and_metadata(u"newdir"))
        def _got( (child, metadata) ):
            self.failUnless(IDirectoryNode.providedBy(child))
            self.failUnless(child.is_mutable())
            # FIXME
            #self.failUnless('ctime' in metadata, metadata)
            #self.failUnlessReallyEqual(metadata['ctime'], 42)
            #self.failUnless('ext_foo' in metadata, metadata)
            #self.failUnlessReallyEqual(metadata['ext_foo'], 'bar')
            # TODO: child should be empty
        d.addCallback(_got)

        # making intermediate directories should also succeed
        d.addCallback(lambda ign: self.handler.makeDirectory("newparent/newchild", {}))

        d.addCallback(lambda ign: self.root.get(u"newparent"))
        def _got_newparent(newparent):
            self.failUnless(IDirectoryNode.providedBy(newparent))
            self.failUnless(newparent.is_mutable())
            return newparent.get(u"newchild")
        d.addCallback(_got_newparent)

        def _got_newchild(newchild):
            self.failUnless(IDirectoryNode.providedBy(newchild))
            self.failUnless(newchild.is_mutable())
        d.addCallback(_got_newchild)

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "makeDirectory invalid UTF-8",
                                         self.handler.makeDirectory, "\xFF", {}))

        # should fail because there is an existing file "small"
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "makeDirectory small",
                                         self.handler.makeDirectory, "small", {}))

        # directories cannot be created read-only via SFTP
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "makeDirectory newdir2 permissions:0444 denied",
                                         self.handler.makeDirectory, "newdir2",
                                         {'permissions': 0444}))

        d.addCallback(lambda ign: self.failUnlessEqual(sftpd.all_heisenfiles, {}))
        d.addCallback(lambda ign: self.failUnlessEqual(self.handler._heisenfiles, {}))
        return d

    def test_execCommand_and_openShell(self):
        class FakeProtocol:
            def __init__(self):
                self.output = ""
                self.reason = None
            def write(self, data):
                self.output += data
                return defer.succeed(None)
            def processEnded(self, reason):
                self.reason = reason
                return defer.succeed(None)

        d = self._set_up("execCommand_and_openShell")

        d.addCallback(lambda ign: conch_interfaces.ISession(self.handler))
        def _exec_df(session):
            protocol = FakeProtocol()
            d2 = session.execCommand(protocol, "df -P -k /")
            d2.addCallback(lambda ign: self.failUnlessIn("1024-blocks", protocol.output))
            d2.addCallback(lambda ign: self.failUnless(isinstance(protocol.reason.value, ProcessDone)))
            d2.addCallback(lambda ign: session.eofReceived())
            d2.addCallback(lambda ign: session.closed())
            return d2
        d.addCallback(_exec_df)

        d.addCallback(lambda ign: conch_interfaces.ISession(self.handler))
        def _exec_error(session):
            protocol = FakeProtocol()
            d2 = session.execCommand(protocol, "error")
            d2.addCallback(lambda ign: session.windowChanged(None))
            d2.addCallback(lambda ign: self.failUnlessEqual("", protocol.output))
            d2.addCallback(lambda ign: self.failUnless(isinstance(protocol.reason.value, ProcessTerminated)))
            d2.addCallback(lambda ign: self.failUnlessEqual(protocol.reason.value.exitCode, 1))
            d2.addCallback(lambda ign: session.closed())
            return d2
        d.addCallback(_exec_error)

        d.addCallback(lambda ign: conch_interfaces.ISession(self.handler))
        def _openShell(session):
            protocol = FakeProtocol()
            d2 = session.openShell(protocol)
            d2.addCallback(lambda ign: self.failUnlessIn("only SFTP", protocol.output))
            d2.addCallback(lambda ign: self.failUnless(isinstance(protocol.reason.value, ProcessTerminated)))
            d2.addCallback(lambda ign: self.failUnlessEqual(protocol.reason.value.exitCode, 1))
            d2.addCallback(lambda ign: session.closed())
            return d2
        d.addCallback(_openShell)

        return d

    def test_extendedRequest(self):
        d = self._set_up("extendedRequest")

        d.addCallback(lambda ign: self.handler.extendedRequest("statvfs@openssh.com", "/"))
        def _check(res):
            self.failUnless(isinstance(res, str))
            self.failUnlessEqual(len(res), 8*11)
        d.addCallback(_check)

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_OP_UNSUPPORTED, "extendedRequest foo bar",
                                         self.handler.extendedRequest, "foo", "bar"))

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "extendedRequest posix-rename@openssh.com invalid 1",
                                         self.handler.extendedRequest, 'posix-rename@openssh.com', ''))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "extendedRequest posix-rename@openssh.com invalid 2",
                                         self.handler.extendedRequest, 'posix-rename@openssh.com', '\x00\x00\x00\x01'))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "extendedRequest posix-rename@openssh.com invalid 3",
                                         self.handler.extendedRequest, 'posix-rename@openssh.com', '\x00\x00\x00\x01_\x00\x00\x00\x01'))

        return d
