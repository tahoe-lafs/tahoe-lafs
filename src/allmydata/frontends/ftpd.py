
import tempfile
from zope.interface import implements
from twisted.application import service, strports
from twisted.internet import defer
from twisted.internet.interfaces import IConsumer
from twisted.cred import portal
from twisted.protocols import ftp

from allmydata.interfaces import IDirectoryNode, ExistingChildError, \
     NoSuchChildError
from allmydata.immutable.upload import FileHandle

class ReadFile:
    implements(ftp.IReadFile)
    def __init__(self, node):
        self.node = node
    def send(self, consumer):
        d = self.node.read(consumer)
        return d # when consumed

class FileWriter:
    implements(IConsumer)

    def registerProducer(self, producer, streaming):
        if not streaming:
            raise NotImplementedError("Non-streaming producer not supported.")
        # we write the data to a temporary file, since Tahoe can't do
        # streaming upload yet.
        self.f = tempfile.TemporaryFile()
        return None

    def unregisterProducer(self):
        # the upload actually happens in WriteFile.close()
        pass

    def write(self, data):
        self.f.write(data)

class WriteFile:
    implements(ftp.IWriteFile)

    def __init__(self, parent, childname, convergence):
        self.parent = parent
        self.childname = childname
        self.convergence = convergence

    def receive(self):
        self.c = FileWriter()
        return defer.succeed(self.c)

    def close(self):
        u = FileHandle(self.c.f, self.convergence)
        d = self.parent.add_file(self.childname, u)
        return d


class NoParentError(Exception):
    pass

class Handler:
    implements(ftp.IFTPShell)
    def __init__(self, client, rootnode, username, convergence):
        self.client = client
        self.root = rootnode
        self.username = username
        self.convergence = convergence

    def makeDirectory(self, path):
        d = self._get_root(path)
        d.addCallback(lambda (root,path):
                      self._get_or_create_directories(root, path))
        return d

    def _get_or_create_directories(self, node, path):
        if not IDirectoryNode.providedBy(node):
            # unfortunately it is too late to provide the name of the
            # blocking directory in the error message.
            raise ftp.FileExistsError("cannot create directory because there "
                                      "is a file in the way")
        if not path:
            return defer.succeed(node)
        d = node.get(path[0])
        def _maybe_create(f):
            f.trap(NoSuchChildError)
            return node.create_subdirectory(path[0])
        d.addErrback(_maybe_create)
        d.addCallback(self._get_or_create_directories, path[1:])
        return d

    def _get_parent(self, path):
        # fire with (parentnode, childname)
        path = [unicode(p) for p in path]
        if not path:
            raise NoParentError
        childname = path[-1]
        d = self._get_root(path)
        def _got_root((root, path)):
            if not path:
                raise NoParentError
            return root.get_child_at_path(path[:-1])
        d.addCallback(_got_root)
        def _got_parent(parent):
            return (parent, childname)
        d.addCallback(_got_parent)
        return d

    def _remove_thing(self, path, must_be_directory=False, must_be_file=False):
        d = defer.maybeDeferred(self._get_parent, path)
        def _convert_error(f):
            f.trap(NoParentError)
            raise ftp.PermissionDeniedError("cannot delete root directory")
        d.addErrback(_convert_error)
        def _got_parent( (parent, childname) ):
            d = parent.get(childname)
            def _got_child(child):
                if must_be_directory and not IDirectoryNode.providedBy(child):
                    raise ftp.IsNotADirectoryError("rmdir called on a file")
                if must_be_file and IDirectoryNode.providedBy(child):
                    raise ftp.IsADirectoryError("rmfile called on a directory")
                return parent.delete(childname)
            d.addCallback(_got_child)
            d.addErrback(self._convert_error)
            return d
        d.addCallback(_got_parent)
        return d

    def removeDirectory(self, path):
        return self._remove_thing(path, must_be_directory=True)

    def removeFile(self, path):
        return self._remove_thing(path, must_be_file=True)

    def rename(self, fromPath, toPath):
        # the target directory must already exist
        d = self._get_parent(fromPath)
        def _got_from_parent( (fromparent, childname) ):
            d = self._get_parent(toPath)
            d.addCallback(lambda (toparent, tochildname):
                          fromparent.move_child_to(childname,
                                                   toparent, tochildname,
                                                   overwrite=False))
            return d
        d.addCallback(_got_from_parent)
        d.addErrback(self._convert_error)
        return d

    def access(self, path):
        # we allow access to everything that exists. We are required to raise
        # an error for paths that don't exist: FTP clients (at least ncftp)
        # uses this to decide whether to mkdir or not.
        d = self._get_node_and_metadata_for_path(path)
        d.addErrback(self._convert_error)
        d.addCallback(lambda res: None)
        return d

    def _convert_error(self, f):
        if f.check(NoSuchChildError):
            childname = f.value.args[0].encode("utf-8")
            msg = "'%s' doesn't exist" % childname
            raise ftp.FileNotFoundError(msg)
        if f.check(ExistingChildError):
            msg = f.value.args[0].encode("utf-8")
            raise ftp.FileExistsError(msg)
        return f

    def _get_root(self, path):
        # return (root, remaining_path)
        path = [unicode(p) for p in path]
        if path and path[0] == "uri":
            d = defer.maybeDeferred(self.client.create_node_from_uri,
                                    str(path[1]))
            d.addCallback(lambda root: (root, path[2:]))
        else:
            d = defer.succeed((self.root,path))
        return d

    def _get_node_and_metadata_for_path(self, path):
        d = self._get_root(path)
        def _got_root((root,path)):
            if path:
                return root.get_child_and_metadata_at_path(path)
            else:
                return (root,{})
        d.addCallback(_got_root)
        return d

    def _populate_row(self, keys, (childnode, metadata)):
        values = []
        isdir = bool(IDirectoryNode.providedBy(childnode))
        for key in keys:
            if key == "size":
                if isdir:
                    value = 0
                else:
                    value = childnode.get_size()
            elif key == "directory":
                value = isdir
            elif key == "permissions":
                value = 0600
            elif key == "hardlinks":
                value = 1
            elif key == "modified":
                value = metadata.get("mtime", 0)
            elif key == "owner":
                value = self.username
            elif key == "group":
                value = self.username
            else:
                value = "??"
            values.append(value)
        return values

    def stat(self, path, keys=()):
        # for files only, I think
        d = self._get_node_and_metadata_for_path(path)
        def _render((node,metadata)):
            assert not IDirectoryNode.providedBy(node)
            return self._populate_row(keys, (node,metadata))
        d.addCallback(_render)
        d.addErrback(self._convert_error)
        return d

    def list(self, path, keys=()):
        # the interface claims that path is a list of unicodes, but in
        # practice it is not
        d = self._get_node_and_metadata_for_path(path)
        def _list((node, metadata)):
            if IDirectoryNode.providedBy(node):
                return node.list()
            return { path[-1]: (node, metadata) } # need last-edge metadata
        d.addCallback(_list)
        def _render(children):
            results = []
            for (name, childnode) in children.iteritems():
                # the interface claims that the result should have a unicode
                # object as the name, but it fails unless you give it a
                # bytestring
                results.append( (name.encode("utf-8"),
                                 self._populate_row(keys, childnode) ) )
            return results
        d.addCallback(_render)
        d.addErrback(self._convert_error)
        return d

    def openForReading(self, path):
        d = self._get_node_and_metadata_for_path(path)
        d.addCallback(lambda (node,metadata): ReadFile(node))
        d.addErrback(self._convert_error)
        return d

    def openForWriting(self, path):
        path = [unicode(p) for p in path]
        if not path:
            raise ftp.PermissionDeniedError("cannot STOR to root directory")
        childname = path[-1]
        d = self._get_root(path)
        def _got_root((root, path)):
            if not path:
                raise ftp.PermissionDeniedError("cannot STOR to root directory")
            return root.get_child_at_path(path[:-1])
        d.addCallback(_got_root)
        def _got_parent(parent):
            return WriteFile(parent, childname, self.convergence)
        d.addCallback(_got_parent)
        return d

from auth import AccountURLChecker, AccountFileChecker, NeedRootcapLookupScheme


class Dispatcher:
    implements(portal.IRealm)
    def __init__(self, client):
        self.client = client

    def requestAvatar(self, avatarID, mind, interface):
        assert interface == ftp.IFTPShell
        rootnode = self.client.create_node_from_uri(avatarID.rootcap)
        convergence = self.client.convergence
        s = Handler(self.client, rootnode, avatarID.username, convergence)
        def logout(): pass
        return (interface, s, None)


class FTPServer(service.MultiService):
    def __init__(self, client, accountfile, accounturl, ftp_portstr):
        service.MultiService.__init__(self)

        # make sure we're using a patched Twisted that uses IWriteFile.close:
        # see docs/frontends/FTP-and-SFTP.txt and
        # http://twistedmatrix.com/trac/ticket/3462 for details.
        if "close" not in ftp.IWriteFile.names():
            raise AssertionError("your twisted is lacking a vital patch, see docs/frontends/FTP-and-SFTP.txt")

        r = Dispatcher(client)
        p = portal.Portal(r)

        if accountfile:
            c = AccountFileChecker(self, accountfile)
            p.registerChecker(c)
        if accounturl:
            c = AccountURLChecker(self, accounturl)
            p.registerChecker(c)
        if not accountfile and not accounturl:
            # we could leave this anonymous, with just the /uri/CAP form
            raise NeedRootcapLookupScheme("must provide some translation")

        f = ftp.FTPFactory(p)
        s = strports.service(ftp_portstr, f)
        s.setServiceParent(self)
