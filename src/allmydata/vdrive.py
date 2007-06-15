
"""This is the client-side facility to manipulate virtual drives."""

from twisted.application import service
from twisted.internet import defer
from twisted.python import log
from allmydata import upload, download
from foolscap import Copyable, RemoteCopy

class VDrive(service.MultiService):
    name = "vdrive"

    def set_server(self, vdrive_server):
        self.gvd_server = vdrive_server
    def set_root(self, root):
        self.gvd_root = root

    def dirpath(self, dir_or_path):
        if isinstance(dir_or_path, str):
            return self.get_dir(dir_or_path)
        return defer.succeed(dir_or_path)

    def get_dir(self, path):
        """Return a Deferred that fires with a RemoteReference to a
        MutableDirectoryNode at the given /-delimited path."""
        d = defer.succeed(self.gvd_root)
        if path.startswith("/"):
            path = path[1:]
        if path == "":
            return d
        for piece in path.split("/"):
            d.addCallback(lambda parent: parent.callRemote("list"))
            def _find(table, subdir):
                for name,target in table:
                    if name == subdir:
                        return target
                else:
                    raise KeyError("no such directory '%s' in '%s'" %
                                   (subdir, [t[0] for t in table]))
            d.addCallback(_find, piece)
        def _check(subdir):
            assert not isinstance(subdir, str), "Hey, %s shouldn't be a string" % subdir
            return subdir
        d.addCallback(_check)
        return d

    def get_uri_from_parent(self, parent, filename):
        assert not isinstance(parent, str), "'%s' isn't a directory node" % (parent,)
        d = parent.callRemote("list")
        def _find(table):
            for name,target in table:
                if name == filename:
                    assert isinstance(target, str), "Hey, %s isn't a file" % filename
                    return target
            else:
                raise KeyError("no such file '%s' in '%s'" %
                               (filename, [t[0] for t in table]))
        d.addCallback(_find)
        return d

    def get_root(self):
        return self.gvd_root

    def listdir(self, dir_or_path):
        d = self.dirpath(dir_or_path)
        d.addCallback(lambda parent: parent.callRemote("list"))
        def _list(table):
            return [t[0] for t in table]
        d.addCallback(_list)
        return d

    def put_file(self, dir_or_path, name, uploadable):
        """Upload an IUploadable and add it to the virtual drive (as an entry
        called 'name', in 'dir_or_path') 'dir_or_path' must either be a
        string like 'root/subdir1/subdir2', or a directory node (either the
        root directory node returned by get_root(), or a subdirectory
        returned by list() ).

        The uploadable can be an instance of allmydata.upload.Data,
        FileHandle, or FileName.

        I return a deferred that will fire when the operation is complete.
        """

        log.msg("putting file to '%s'" % name)
        ul = self.parent.getServiceNamed("uploader")
        d = self.dirpath(dir_or_path)
        def _got_dir(dirnode):
            d1 = ul.upload(uploadable)
            def _add(uri):
                d2 = dirnode.callRemote("add_file", name, uri)
                d2.addCallback(lambda res: uri)
                return d2
            d1.addCallback(_add)
            return d1
        d.addCallback(_got_dir)
        def _done(res):
            log.msg("finished putting file to '%s'" % name)
            return res
        d.addCallback(_done)
        return d

    def put_file_by_filename(self, dir_or_path, name, filename):
        return self.put_file(dir_or_path, name, upload.FileName(filename))
    def put_file_by_data(self, dir_or_path, name, data):
        return self.put_file(dir_or_path, name, upload.Data(data))
    def put_file_by_filehandle(self, dir_or_path, name, filehandle):
        return self.put_file(dir_or_path, name, upload.FileHandle(filehandle))

    def make_directory(self, dir_or_path, name):
        d = self.dirpath(dir_or_path)
        d.addCallback(lambda parent: parent.callRemote("add_directory", name))
        return d

    def remove(self, parent, name):
        assert not isinstance(parent, str)
        log.msg("vdrive removing %s" % name)
        # first find the uri
        d = self.get_uri_from_parent(parent, name)
        def _got_uri(vid):
            # TODO: delete the file's shares using this
            pass
        d.addCallback(_got_uri)
        def _delete_from_parent(res):
            return parent.callRemote("remove", name)
        d.addCallback(_delete_from_parent)
        def _done(res):
            log.msg("vdrive done removing %s" % name)
        d.addCallback(_done)
        return d


    def get_file(self, dir_and_name_or_path, download_target):
        """Retrieve a file from the virtual drive and put it somewhere.

        The file to be retrieved may either be specified as a (dir, name)
        tuple or as a full /-delimited pathname. In the former case, 'dir'
        can be either a DirectoryNode or a pathname.

        The download target must be an IDownloadTarget instance like
        allmydata.download.Data, .FileName, or .FileHandle .
        """

        log.msg("getting file from %s" % (dir_and_name_or_path,))
        dl = self.parent.getServiceNamed("downloader")

        if isinstance(dir_and_name_or_path, tuple):
            dir_or_path, name = dir_and_name_or_path
            d = self.dirpath(dir_or_path)
            def _got_dir(dirnode):
                return self.get_uri_from_parent(dirnode, name)
            d.addCallback(_got_dir)
        else:
            rslash = dir_and_name_or_path.rfind("/")
            if rslash == -1:
                # we're looking for a file in the root directory
                dir = self.gvd_root
                name = dir_and_name_or_path
                d = self.get_uri_from_parent(dir, name)
            else:
                dirpath = dir_and_name_or_path[:rslash]
                name = dir_and_name_or_path[rslash+1:]
                d = self.dirpath(dirpath)
                d.addCallback(lambda dir:
                              self.get_uri_from_parent(dir, name))

        def _got_uri(uri):
            return dl.download(uri, download_target)
        d.addCallback(_got_uri)
        def _done(res):
            log.msg("finished getting file")
            return res
        d.addCallback(_done)
        return d

    def get_file_to_filename(self, from_where, filename):
        return self.get_file(from_where, download.FileName(filename))
    def get_file_to_data(self, from_where):
        return self.get_file(from_where, download.Data())
    def get_file_to_filehandle(self, from_where, filehandle):
        return self.get_file(from_where, download.FileHandle(filehandle))


class DirectoryNode(Copyable, RemoteCopy):
    """I have either a .furl attribute or a .get(tub) method."""
    typeToCopy = "allmydata.com/tahoe/interfaces/DirectoryNode/v1"
    copytype = typeToCopy
    def __init__(self, furl=None, client=None):
        # RemoteCopy subclasses are always called without arguments
        self.furl = furl
        self._set_client(client)
    def _set_client(self, client):
        self._client = client
        return self
    def getStateToCopy(self):
        return {"furl": self.furl }
    def setCopyableState(self, state):
        self.furl = state['furl']
    def __hash__(self):
        return hash((self.__class__, self.furl))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.furl, them.furl)

    def list(self):
        d = self._client.tub.getReference(self.furl)
        d.addCallback(lambda node: node.callRemote("list"))
        d.addCallback(lambda children:
                      [(name,child._set_client(self._client))
                       for name,child in children])
        return d

    def get(self, name):
        d = self._client.tub.getReference(self.furl)
        d.addCallback(lambda node: node.callRemote("get", name))
        d.addCallback(lambda child: child._set_client(self._client))
        return d

    def add(self, name, child):
        d = self._client.tub.getReference(self.furl)
        d.addCallback(lambda node: node.callRemote("add", name, child))
        d.addCallback(lambda newnode: newnode._set_client(self._client))
        return d

    def add_file(self, name, uploadable):
        uploader = self._client.getServiceNamed("uploader")
        d = uploader.upload(uploadable)
        d.addCallback(lambda uri: self.add(name, FileNode(uri, self._client)))
        return d

    def remove(self, name):
        d = self._client.tub.getReference(self.furl)
        d.addCallback(lambda node: node.callRemote("remove", name))
        d.addCallback(lambda newnode: newnode._set_client(self._client))
        return d

    def create_empty_directory(self, name):
        vdrive_server = self._client._vdrive_server
        d = vdrive_server.callRemote("create_directory")
        d.addCallback(lambda node: self.add(name, node))
        return d

    def attach_shared_directory(self, name, furl):
        d = self.add(name, DirectoryNode(furl))
        return d

    def get_shared_directory_furl(self):
        return defer.succeed(self.furl)

    def move_child_to(self, current_child_name,
                      new_parent, new_child_name=None):
        if new_child_name is None:
            new_child_name = current_child_name
        d = self.get(current_child_name)
        d.addCallback(lambda child: new_parent.add(new_child_name, child))
        d.addCallback(lambda child: self.remove(current_child_name))
        return d

class FileNode(Copyable, RemoteCopy):
    """I have a .uri attribute."""
    typeToCopy = "allmydata.com/tahoe/interfaces/FileNode/v1"
    copytype = typeToCopy
    def __init__(self, uri=None, client=None):
        # RemoteCopy subclasses are always called without arguments
        self.uri = uri
        self._set_client(client)
    def _set_client(self, client):
        self._client = client
        return self
    def getStateToCopy(self):
        return {"uri": self.uri }
    def setCopyableState(self, state):
        self.uri = state['uri']
    def __hash__(self):
        return hash((self.__class__, self.uri))
    def __cmp__(self, them):
        if cmp(type(self), type(them)):
            return cmp(type(self), type(them))
        if cmp(self.__class__, them.__class__):
            return cmp(self.__class__, them.__class__)
        return cmp(self.uri, them.uri)

    def download(self, target):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download(self.uri, target)

    def download_to_data(self):
        downloader = self._client.getServiceNamed("downloader")
        return downloader.download_to_data(self.uri)

