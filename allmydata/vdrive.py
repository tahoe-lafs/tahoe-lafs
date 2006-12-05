
"""This is the client-side facility to manipulate virtual drives."""

from twisted.application import service
from twisted.internet import defer
from twisted.python import log
from allmydata import upload, download

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

    def get_verifierid_from_parent(self, parent, filename):
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
            d1.addCallback(lambda vid:
                           dirnode.callRemote("add_file", name, vid))
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
        # first find the verifierid
        d = self.get_verifierid_from_parent(parent, name)
        def _got_verifierid(vid):
            # TODO: delete the file's shares using this
            pass
        d.addCallback(_got_verifierid)
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
                return self.get_verifierid_from_parent(dirnode, name)
            d.addCallback(_got_dir)
        else:
            rslash = dir_and_name_or_path.rfind("/")
            if rslash == -1:
                # we're looking for a file in the root directory
                dir = self.gvd_root
                name = dir_and_name_or_path
                d = self.get_verifierid_from_parent(dir, name)
            else:
                dirpath = dir_and_name_or_path[:rslash]
                name = dir_and_name_or_path[rslash+1:]
                d = self.dirpath(dirpath)
                d.addCallback(lambda dir:
                              self.get_verifierid_from_parent(dir, name))

        def _got_verifierid(verifierid):
            return dl.download(verifierid, download_target)
        d.addCallback(_got_verifierid)
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

