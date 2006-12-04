
"""This is the client-side facility to manipulate virtual drives."""

from twisted.application import service
from twisted.internet import defer
from allmydata.upload import Data, FileHandle, FileName

class VDrive(service.MultiService):
    name = "vdrive"

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
                        return subdir
                else:
                    raise KeyError("no such directory '%s' in '%s'" %
                                   (subdir, [t[0] for t in table]))
            d.addCallback(_find, piece)
        def _check(subdir):
            assert not isinstance(subdir, str)
            return subdir
        d.addCallback(_check)
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

        u = self.parent.getServiceNamed("uploader")
        d = self.dirpath(dir_or_path)
        def _got_dir(dirnode):
            d1 = u.upload(uploadable)
            d1.addCallback(lambda vid:
                           dirnode.callRemote("add_file", name, vid))
            return d1
        d.addCallback(_got_dir)
        return d

    def put_file_by_filename(self, dir_or_path, name, filename):
        return self.put_file(dir_or_path, name, FileName(filename))
    def put_file_by_data(self, dir_or_path, name, data):
        return self.put_file(dir_or_path, name, Data(data))
    def put_file_by_filehandle(self, dir_or_path, name, filehandle):
        return self.put_file(dir_or_path, name, FileHandle(filehandle))

    def make_directory(self, dir_or_path, name):
        d = self.dirpath(dir_or_path)
        d.addCallback(lambda parent: parent.callRemote("add_directory", name))
        return d

