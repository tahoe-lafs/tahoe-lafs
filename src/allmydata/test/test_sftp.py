
import re
from stat import S_IFREG, S_IFDIR

from twisted.trial import unittest
from twisted.internet import defer
from twisted.python.failure import Failure
from twisted.internet.error import ProcessDone, ProcessTerminated

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

import traceback

"""
import sys
def trace_exceptions(frame, event, arg):
    if event != 'exception':
        return
    co = frame.f_code
    func_name = co.co_name
    line_no = frame.f_lineno
    filename = co.co_filename
    exc_type, exc_value, exc_traceback = arg
    print 'Tracing exception: %r %r on line %r of %r in %r' % \
        (exc_type.__name__, exc_value, line_no, func_name, filename)

def trace_calls(frame, event, arg):
    if event != 'call':
        return
    return trace_exceptions

sys.settrace(trace_calls)
"""

timeout = 30

from allmydata.interfaces import IDirectoryNode, ExistingChildError, NoSuchChildError
from allmydata.mutable.common import NotWriteableError

from allmydata.util.consumer import download_to_data
from allmydata.immutable import upload
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common import ShouldFailMixin

class Handler(GridTestMixin, ShouldFailMixin, unittest.TestCase):
    """This is a no-network unit test of the SFTPHandler class."""

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
                                         "%s was supposed to raise SFTPError(%d), not SFTPError(%d): %s" %
                                         (which, expected_code, res.value.code, res))
            else:
                print '@' + '@'.join(s)
                self.fail("%s was supposed to raise SFTPError(%d), not get '%s'" %
                          (which, expected_code, res))
        d.addBoth(_done)
        return d

    def failUnlessReallyEqual(self, a, b, msg=None):
        self.failUnlessEqual(a, b, msg=msg)
        self.failUnlessEqual(type(a), type(b), msg=msg)

    def _set_up(self, basedir, num_clients=1, num_servers=10):
        self.basedir = "sftp/" + basedir
        self.set_up_grid(num_clients=num_clients, num_servers=num_servers)

        self.client = self.g.clients[0]
        self.username = "alice"

        d = self.client.create_dirnode()
        def _created_root(node):
            self.root = node
            self.root_uri = node.get_uri()
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

        d.addCallback(lambda ign: self.root.set_node(u"loop", self.root))
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

    def test_raise_error(self):
        self.failUnlessReallyEqual(sftpd._raise_error(None), None)
        
        d = defer.succeed(None)
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "_raise_error SFTPError",
                                         sftpd._raise_error, Failure(sftp.SFTPError(sftp.FX_FAILURE, "foo"))))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "_raise_error NoSuchChildError",
                                         sftpd._raise_error, Failure(NoSuchChildError("foo"))))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "_raise_error ExistingChildError",
                                         sftpd._raise_error, Failure(ExistingChildError("foo"))))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "_raise_error NotWriteableError",
                                         sftpd._raise_error, Failure(NotWriteableError("foo"))))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_OP_UNSUPPORTED, "_raise_error NotImplementedError",
                                         sftpd._raise_error, Failure(NotImplementedError("foo"))))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_EOF, "_raise_error EOFError",
                                         sftpd._raise_error, Failure(EOFError("foo"))))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_EOF, "_raise_error defer.FirstError",
                                         sftpd._raise_error, Failure(defer.FirstError(
                                                               Failure(sftp.SFTPError(sftp.FX_EOF, "foo")), 0))))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_FAILURE, "_raise_error AssertionError",
                                         sftpd._raise_error, Failure(AssertionError("foo"))))

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
           self.failUnless(re.match(expected_text_re, text), "%r does not match %r" % (text, expected_text_re))
           # it is ok for there to be extra actual attributes
           # TODO: check times
           for e in expected_attrs:
               self.failUnlessReallyEqual(attrs[e], expected_attrs[e])

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
            ('empty_lit_dir', r'drwxrwx--- .* \? .* empty_lit_dir$', {'permissions': S_IFDIR | 0770}),
            (gross,           r'-rw-rw---- .* 1010 .* '+gross+'$',   {'permissions': S_IFREG | 0660, 'size': 1010}),
            ('loop',          r'drwxrwx--- .* \? .* loop$',          {'permissions': S_IFDIR | 0770}),
            ('mutable',       r'-rw-rw---- .* \? .* mutable$',       {'permissions': S_IFREG | 0660}),
            ('readonly',      r'-r--r----- .* \? .* readonly$',      {'permissions': S_IFREG | 0440}),
            ('small',         r'-rw-rw---- .* 10 .* small$',         {'permissions': S_IFREG | 0660, 'size': 10}),
            ('small2',        r'-rw-rw---- .* 26 .* small2$',        {'permissions': S_IFREG | 0660, 'size': 26}),
            ('tiny_lit_dir',  r'drwxrwx--- .* \? .* tiny_lit_dir$',  {'permissions': S_IFDIR | 0770}),
            ('unknown',       r'\?--------- .* \? .* unknown$',      {'permissions': 0}),
        ]

        d.addCallback(lambda ign: self.handler.openDirectory(""))
        d.addCallback(lambda res: self._compareDirLists(res, expected_root))

        d.addCallback(lambda ign: self.handler.openDirectory("loop"))
        d.addCallback(lambda res: self._compareDirLists(res, expected_root))

        d.addCallback(lambda ign: self.handler.openDirectory("loop/loop"))
        d.addCallback(lambda res: self._compareDirLists(res, expected_root))

        d.addCallback(lambda ign: self.handler.openDirectory("empty_lit_dir"))
        d.addCallback(lambda res: self._compareDirLists(res, []))
        
        expected_tiny_lit = [
            ('short', r'-r--r----- .* 8 Jan 01  1970 short$', {'permissions': S_IFREG | 0440, 'size': 8}),
        ]

        d.addCallback(lambda ign: self.handler.openDirectory("tiny_lit_dir"))
        d.addCallback(lambda res: self._compareDirLists(res, expected_tiny_lit))

        d.addCallback(lambda ign: self.handler.getAttrs("small", True))
        def _check_attrs(attrs):
            self.failUnlessReallyEqual(attrs['permissions'], S_IFREG | 0440) #FIXME
            self.failUnlessReallyEqual(attrs['size'], 10)
        d.addCallback(_check_attrs)

        d.addCallback(lambda ign:
            self.failUnlessReallyEqual(self.handler.setAttrs("small", {}), None))

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_OP_UNSUPPORTED, "setAttrs size",
                                         self.handler.setAttrs, "small", {'size': 0}))

        return d

    def test_openFile_read(self):
        d = self._set_up("openFile_read")
        d.addCallback(lambda ign: self._set_up_tree())

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "openFile small 0",
                                         self.handler.openFile, "small", 0, {}))

        # attempting to open a non-existent file should fail
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openFile nofile READ",
                                         self.handler.openFile, "nofile", sftp.FXF_READ, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openFile nodir/file READ",
                                         self.handler.openFile, "nodir/file", sftp.FXF_READ, {}))

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile unknown READ denied",
                                         self.handler.openFile, "unknown", sftp.FXF_READ, {}))
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
            def _check_attrs(attrs):
                self.failUnlessReallyEqual(attrs['permissions'], S_IFREG | 0440) #FIXME
                self.failUnlessReallyEqual(attrs['size'], 10)
            d2.addCallback(_check_attrs)

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
        d.addCallback(_read_small)

        # repeat for a large file
        gross = u"gro\u00DF".encode("utf-8")
        d.addCallback(lambda ign: self.handler.openFile(gross, sftp.FXF_READ, {}))
        def _read_gross(rf):
            d2 = rf.readChunk(0, 10)
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))

            d2.addCallback(lambda ign: rf.readChunk(2, 6))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "234567"))

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
            def _check_attrs(attrs):
                self.failUnlessReallyEqual(attrs['permissions'], S_IFREG | 0440) #FIXME
                self.failUnlessReallyEqual(attrs['size'], 1010)
            d2.addCallback(_check_attrs)

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

        return d

    def test_openFile_write(self):
        d = self._set_up("openFile_write")
        d.addCallback(lambda ign: self._set_up_tree())

        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_NO_SUCH_FILE, "openFile '' WRITE|CREAT|TRUNC",
                                         self.handler.openFile, "", sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_TRUNC, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "openFile newfile WRITE|TRUNC",
                                         self.handler.openFile, "newfile", sftp.FXF_WRITE | sftp.FXF_TRUNC, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "openFile small WRITE|EXCL",
                                         self.handler.openFile, "small", sftp.FXF_WRITE | sftp.FXF_EXCL, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir WRITE",
                                         self.handler.openFile, "tiny_lit_dir", sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile unknown WRITE",
                                         self.handler.openFile, "unknown", sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir/newfile WRITE|CREAT|TRUNC",
                                         self.handler.openFile, "tiny_lit_dir/newfile",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_TRUNC, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir/short WRITE",
                                         self.handler.openFile, "tiny_lit_dir/short", sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile tiny_lit_dir/short WRITE|CREAT|EXCL",
                                         self.handler.openFile, "tiny_lit_dir/short",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile readonly WRITE",
                                         self.handler.openFile, "readonly", sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile small WRITE|CREAT|EXCL",
                                         self.handler.openFile, "small",
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile readonly uri WRITE",
                                         self.handler.openFile, "uri/"+self.readonly_uri, sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile small uri WRITE",
                                         self.handler.openFile, "uri/"+self.small_uri, sftp.FXF_WRITE, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile small uri WRITE|CREAT|TRUNC",
                                         self.handler.openFile, "uri/"+self.small_uri,
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_TRUNC, {}))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "openFile mutable uri WRITE|CREAT|EXCL",
                                         self.handler.openFile, "uri/"+self.mutable_uri,
                                         sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_EXCL, {}))

        d.addCallback(lambda ign:
                      self.handler.openFile("newfile", sftp.FXF_WRITE | sftp.FXF_CREAT | sftp.FXF_TRUNC, {}))
        def _write(wf):
            d2 = wf.writeChunk(0, "0123456789")
            d2.addCallback(lambda res: self.failUnlessReallyEqual(res, None))

            d2.addCallback(lambda ign: wf.writeChunk(8, "0123"))
            d2.addCallback(lambda ign: wf.writeChunk(13, "abc"))

            d2.addCallback(lambda ign: wf.getAttrs())
            def _check_attrs(attrs):
                self.failUnlessReallyEqual(attrs['permissions'], S_IFREG | 0440) #FIXME
                self.failUnlessReallyEqual(attrs['size'], 16)
            d2.addCallback(_check_attrs)

            d2.addCallback(lambda ign: wf.setAttrs({}))

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "setAttrs with negative size",
                                             wf.setAttrs, {'size': -1}))

            d2.addCallback(lambda ign: wf.setAttrs({'size': 14}))
            d2.addCallback(lambda ign: wf.getAttrs())
            d2.addCallback(lambda attrs: self.failUnlessReallyEqual(attrs['size'], 14))

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "readChunk on write-only handle denied",
                                             wf.readChunk, 0, 1))

            d2.addCallback(lambda ign: wf.close())

            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "writeChunk on closed file",
                                             wf.writeChunk, 0, "a"))
            d2.addCallback(lambda ign:
                self.shouldFailWithSFTPError(sftp.FX_BAD_MESSAGE, "setAttrs on closed file",
                                             wf.setAttrs, {'size': 0}))

            d2.addCallback(lambda ign: wf.close()) # should be no-op
            return d2
        d.addCallback(_write)
        d.addCallback(lambda ign: self.root.get(u"newfile"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "012345670123\x00a"))

        # test APPEND flag, and also replacing an existing file ("newfile")
        d.addCallback(lambda ign:
                      self.handler.openFile("newfile", sftp.FXF_WRITE | sftp.FXF_CREAT |
                                                       sftp.FXF_TRUNC | sftp.FXF_APPEND, {}))
        def _write_append(wf):
            d2 = wf.writeChunk(0, "0123456789")
            d2.addCallback(lambda ign: wf.writeChunk(8, "0123"))
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_append)
        d.addCallback(lambda ign: self.root.get(u"newfile"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "01234567890123"))

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
            #stash = {}
            #d2.addCallback(lambda ign: self.root.get_best_version_number())
            #d2.addCallback(lambda version: stash['version'] = version)
            d2.addCallback(lambda ign: wf.close())
            #d2.addCallback(lambda ign: self.root.get_best_version_number())
            #d2.addCallback(lambda new_version: self.failUnlessReallyEqual(new_version, stash['version'])
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

        # test WRITE | CREAT without TRUNC
        d.addCallback(lambda ign:
                      self.handler.openFile("newfile2", sftp.FXF_WRITE | sftp.FXF_CREAT, {}))
        def _write_notrunc(wf):
            d2 =  wf.writeChunk(0, "0123456789")
            d2.addCallback(lambda ign: wf.close())
            return d2
        d.addCallback(_write_notrunc)
        d.addCallback(lambda ign: self.root.get(u"newfile2"))
        d.addCallback(lambda node: download_to_data(node))
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))

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
            self.failUnlessReallyEqual(node.get_uri(), self.mutable_uri)
            return node.download_best_version()
        d.addCallback(_check_same_file)
        d.addCallback(lambda data: self.failUnlessReallyEqual(data, "mutable new! contents"))

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
            d2= self.handler.removeFile("small")
            d2.addCallback(lambda ign:
                           self.shouldFail(NoSuchChildError, "removeFile small", "small",
                                           self.root.get, u"small"))
            d2.addCallback(lambda ign: rf.readChunk(0, 10))
            d2.addCallback(lambda data: self.failUnlessReallyEqual(data, "0123456789"))
            d2.addCallback(lambda ign: rf.close())
            return d2
        d.addCallback(_remove_and_read_small)

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
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "renameFile small small2",
                                         self.handler.renameFile, "small", "small2"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "renameFile small tiny_lit_dir",
                                         self.handler.renameFile, "small", "tiny_lit_dir"))
        d.addCallback(lambda ign:
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "renameFile small unknown",
                                         self.handler.renameFile, "small", "unknown"))

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
            self.shouldFailWithSFTPError(sftp.FX_PERMISSION_DENIED, "makeDirectory small",
                                         self.handler.makeDirectory, "small", {}))
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
        d.addCallback(_exec_error)

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

        return d