
import tempfile
from zope.interface import implements
from twisted.python import components
from twisted.application import service, strports
from twisted.internet import defer
from twisted.conch.ssh import factory, keys, session
from twisted.conch.interfaces import ISFTPServer, ISFTPFile, IConchUser
from twisted.conch.avatar import ConchUser
from twisted.conch.openssh_compat import primes
from twisted.conch import ls
from twisted.cred import portal

from allmydata.interfaces import IDirectoryNode, ExistingChildError, \
     NoSuchChildError
from allmydata.immutable.upload import FileHandle
from allmydata.util.consumer import download_to_data

class ReadFile:
    implements(ISFTPFile)
    def __init__(self, node):
        self.node = node
    def readChunk(self, offset, length):
        d = download_to_data(self.node, offset, length)
        def _got(data):
            return data
        d.addCallback(_got)
        return d
    def close(self):
        pass
    def getAttrs(self):
        print "GETATTRS(file)"
        raise NotImplementedError
    def setAttrs(self, attrs):
        print "SETATTRS(file)", attrs
        raise NotImplementedError

class WriteFile:
    implements(ISFTPFile)

    def __init__(self, parent, childname, convergence):
        self.parent = parent
        self.childname = childname
        self.convergence = convergence
        self.f = tempfile.TemporaryFile()
    def writeChunk(self, offset, data):
        self.f.seek(offset)
        self.f.write(data)

    def close(self):
        u = FileHandle(self.f, self.convergence)
        d = self.parent.add_file(self.childname, u)
        return d

    def getAttrs(self):
        print "GETATTRS(file)"
        raise NotImplementedError
    def setAttrs(self, attrs):
        print "SETATTRS(file)", attrs
        raise NotImplementedError


class NoParentError(Exception):
    pass

class PermissionError(Exception):
    pass

from twisted.conch.ssh.filetransfer import FileTransferServer, SFTPError, \
     FX_NO_SUCH_FILE, FX_FILE_ALREADY_EXISTS, FX_OP_UNSUPPORTED, \
     FX_PERMISSION_DENIED
from twisted.conch.ssh.filetransfer import FXF_READ, FXF_WRITE, FXF_APPEND, FXF_CREAT, FXF_TRUNC, FXF_EXCL

class SFTPUser(ConchUser):
    def __init__(self, client, rootnode, username, convergence):
        ConchUser.__init__(self)
        self.channelLookup["session"] = session.SSHSession
        self.subsystemLookup["sftp"] = FileTransferServer

        self.client = client
        self.root = rootnode
        self.username = username
        self.convergence = convergence

class StoppableList:
    def __init__(self, items):
        self.items = items
    def __iter__(self):
        for i in self.items:
            yield i
    def close(self):
        pass

class FakeStat:
    pass

class BadRemoveRequest(Exception):
    pass

class SFTPHandler:
    implements(ISFTPServer)
    def __init__(self, user):
        print "Creating SFTPHandler from", user
        self.client = user.client
        self.root = user.root
        self.username = user.username
        self.convergence = user.convergence

    def gotVersion(self, otherVersion, extData):
        return {}

    def openFile(self, filename, flags, attrs):
        f = "|".join([f for f in
                      [(flags & FXF_READ) and "FXF_READ" or None,
                       (flags & FXF_WRITE) and "FXF_WRITE" or None,
                       (flags & FXF_APPEND) and "FXF_APPEND" or None,
                       (flags & FXF_CREAT) and "FXF_CREAT" or None,
                       (flags & FXF_TRUNC) and "FXF_TRUNC" or None,
                       (flags & FXF_EXCL) and "FXF_EXCL" or None,
                      ]
                      if f])
        print "OPENFILE", filename, flags, f, attrs
        # this is used for both reading and writing.

#        createPlease = False
#        exclusive = False
#        openFlags = 0
#
#        if flags & FXF_READ == FXF_READ and flags & FXF_WRITE == 0:
#            openFlags = os.O_RDONLY
#        if flags & FXF_WRITE == FXF_WRITE and flags & FXF_READ == 0:
#            createPlease = True
#            openFlags = os.O_WRONLY
#        if flags & FXF_WRITE == FXF_WRITE and flags & FXF_READ == FXF_READ:
#            createPlease = True
#            openFlags = os.O_RDWR
#        if flags & FXF_APPEND == FXF_APPEND:
#            createPlease = True
#            openFlags |= os.O_APPEND
#        if flags & FXF_CREAT == FXF_CREAT:
#            createPlease = True
#            openFlags |= os.O_CREAT
#        if flags & FXF_TRUNC == FXF_TRUNC:
#            openFlags |= os.O_TRUNC
#        if flags & FXF_EXCL == FXF_EXCL:
#            exclusive = True

        # /usr/bin/sftp 'get' gives us FXF_READ, while 'put' on a new file
        # gives FXF_WRITE,FXF_CREAT,FXF_TRUNC . I'm guessing that 'put' on an
        # existing file gives the same.

        path = self._convert_sftp_path(filename)

        if flags & FXF_READ:
            if flags & FXF_WRITE:
                raise NotImplementedError
            d = self._get_node_and_metadata_for_path(path)
            d.addCallback(lambda (node,metadata): ReadFile(node))
            d.addErrback(self._convert_error)
            return d

        if flags & FXF_WRITE:
            if not (flags & FXF_CREAT) or not (flags & FXF_TRUNC):
                raise NotImplementedError
            if not path:
                raise PermissionError("cannot STOR to root directory")
            childname = path[-1]
            d = self._get_root(path)
            def _got_root((root, path)):
                if not path:
                    raise PermissionError("cannot STOR to root directory")
                return root.get_child_at_path(path[:-1])
            d.addCallback(_got_root)
            def _got_parent(parent):
                return WriteFile(parent, childname, self.convergence)
            d.addCallback(_got_parent)
            return d
        raise NotImplementedError

    def removeFile(self, path):
        print "REMOVEFILE", path
        path = self._convert_sftp_path(path)
        return self._remove_thing(path, must_be_file=True)

    def renameFile(self, oldpath, newpath):
        print "RENAMEFILE", oldpath, newpath
        fromPath = self._convert_sftp_path(oldpath)
        toPath = self._convert_sftp_path(newpath)
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

    def makeDirectory(self, path, attrs):
        print "MAKEDIRECTORY", path, attrs
        # TODO: extract attrs["mtime"], use it to set the parent metadata.
        # Maybe also copy attrs["ext_*"] .
        path = self._convert_sftp_path(path)
        d = self._get_root(path)
        d.addCallback(lambda (root,path):
                      self._get_or_create_directories(root, path))
        return d

    def _get_or_create_directories(self, node, path):
        if not IDirectoryNode.providedBy(node):
            # unfortunately it is too late to provide the name of the
            # blocking directory in the error message.
            raise ExistingChildError("cannot create directory because there "
                                     "is a file in the way") # close enough
        if not path:
            return defer.succeed(node)
        d = node.get(path[0])
        def _maybe_create(f):
            f.trap(NoSuchChildError)
            return node.create_subdirectory(path[0])
        d.addErrback(_maybe_create)
        d.addCallback(self._get_or_create_directories, path[1:])
        return d

    def removeDirectory(self, path):
        print "REMOVEDIRECTORY", path
        path = self._convert_sftp_path(path)
        return self._remove_thing(path, must_be_directory=True)

    def _remove_thing(self, path, must_be_directory=False, must_be_file=False):
        d = defer.maybeDeferred(self._get_parent, path)
        def _convert_error(f):
            f.trap(NoParentError)
            raise PermissionError("cannot delete root directory")
        d.addErrback(_convert_error)
        def _got_parent( (parent, childname) ):
            d = parent.get(childname)
            def _got_child(child):
                if must_be_directory and not IDirectoryNode.providedBy(child):
                    raise BadRemoveRequest("rmdir called on a file")
                if must_be_file and IDirectoryNode.providedBy(child):
                    raise BadRemoveRequest("rmfile called on a directory")
                return parent.delete(childname)
            d.addCallback(_got_child)
            d.addErrback(self._convert_error)
            return d
        d.addCallback(_got_parent)
        return d


    def openDirectory(self, path):
        print "OPENDIRECTORY", path
        path = self._convert_sftp_path(path)
        d = self._get_node_and_metadata_for_path(path)
        d.addCallback(lambda (dirnode,metadata): dirnode.list())
        def _render(children):
            results = []
            for filename, (node, metadata) in children.iteritems():
                s = FakeStat()
                if IDirectoryNode.providedBy(node):
                    s.st_mode = 040700
                    s.st_size = 0
                else:
                    s.st_mode = 0100600
                    s.st_size = node.get_size()
                s.st_nlink = 1
                s.st_uid = 0
                s.st_gid = 0
                s.st_mtime = int(metadata.get("mtime", 0))
                longname = ls.lsLine(filename.encode("utf-8"), s)
                attrs = self._populate_attrs(node, metadata)
                results.append( (filename.encode("utf-8"), longname, attrs) )
            return StoppableList(results)
        d.addCallback(_render)
        return d

    def getAttrs(self, path, followLinks):
        print "GETATTRS", path, followLinks
        # from ftp.stat
        d = self._get_node_and_metadata_for_path(self._convert_sftp_path(path))
        def _render((node,metadata)):
            return self._populate_attrs(node, metadata)
        d.addCallback(_render)
        d.addErrback(self._convert_error)
        def _done(res):
            print " DONE", res
            return res
        d.addBoth(_done)
        return d

    def _convert_sftp_path(self, pathstring):
        assert pathstring[0] == "/"
        pathstring = pathstring.strip("/")
        if pathstring == "":
            path = []
        else:
            path = pathstring.split("/")
        print "CONVERT", pathstring, path
        path = [unicode(p) for p in path]
        return path

    def _get_node_and_metadata_for_path(self, path):
        d = self._get_root(path)
        def _got_root((root,path)):
            print "ROOT", root
            print "PATH", path
            if path:
                return root.get_child_and_metadata_at_path(path)
            else:
                return (root,{})
        d.addCallback(_got_root)
        return d

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

    def _populate_attrs(self, childnode, metadata):
        attrs = {}
        attrs["uid"] = 1000
        attrs["gid"] = 1000
        attrs["atime"] = 0
        attrs["mtime"] = int(metadata.get("mtime", 0))
        isdir = bool(IDirectoryNode.providedBy(childnode))
        if isdir:
            attrs["size"] = 1
            # the permissions must have the extra bits (040000 or 0100000),
            # otherwise the client will not call openDirectory
            attrs["permissions"] = 040700 # S_IFDIR
        else:
            attrs["size"] = childnode.get_size()
            attrs["permissions"] = 0100600 # S_IFREG
        return attrs

    def _convert_error(self, f):
        if f.check(NoSuchChildError):
            childname = f.value.args[0].encode("utf-8")
            raise SFTPError(FX_NO_SUCH_FILE, childname)
        if f.check(ExistingChildError):
            msg = f.value.args[0].encode("utf-8")
            raise SFTPError(FX_FILE_ALREADY_EXISTS, msg)
        if f.check(PermissionError):
            raise SFTPError(FX_PERMISSION_DENIED, str(f.value))
        if f.check(NotImplementedError):
            raise SFTPError(FX_OP_UNSUPPORTED, str(f.value))
        return f


    def setAttrs(self, path, attrs):
        print "SETATTRS", path, attrs
        # ignored
        return None

    def readLink(self, path):
        print "READLINK", path
        raise NotImplementedError

    def makeLink(self, linkPath, targetPath):
        print "MAKELINK", linkPath, targetPath
        raise NotImplementedError

    def extendedRequest(self, extendedName, extendedData):
        print "EXTENDEDREQUEST", extendedName, extendedData
        # client 'df' command requires 'statvfs@openssh.com' extension
        raise NotImplementedError
    def realPath(self, path):
        print "REALPATH", path
        if path == ".":
            return "/"
        return path


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


# if you have an SFTPUser, and you want something that provides ISFTPServer,
# then you get SFTPHandler(user)
components.registerAdapter(SFTPHandler, SFTPUser, ISFTPServer)

from allmydata.frontends.auth import AccountURLChecker, AccountFileChecker, NeedRootcapLookupScheme

class Dispatcher:
    implements(portal.IRealm)
    def __init__(self, client):
        self.client = client

    def requestAvatar(self, avatarID, mind, interface):
        assert interface == IConchUser
        rootnode = self.client.create_node_from_uri(avatarID.rootcap)
        convergence = self.client.convergence
        s = SFTPUser(self.client, rootnode, avatarID.username, convergence)
        def logout(): pass
        return (interface, s, logout)

class SFTPServer(service.MultiService):
    def __init__(self, client, accountfile, accounturl,
                 sftp_portstr, pubkey_file, privkey_file):
        service.MultiService.__init__(self)

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

        pubkey = keys.Key.fromFile(pubkey_file)
        privkey = keys.Key.fromFile(privkey_file)
        class SSHFactory(factory.SSHFactory):
            publicKeys = {pubkey.sshType(): pubkey}
            privateKeys = {privkey.sshType(): privkey}
            def getPrimes(self):
                try:
                    # if present, this enables diffie-hellman-group-exchange
                    return primes.parseModuliFile("/etc/ssh/moduli")
                except IOError:
                    return None

        f = SSHFactory()
        f.portal = p

        s = strports.service(sftp_portstr, f)
        s.setServiceParent(self)

