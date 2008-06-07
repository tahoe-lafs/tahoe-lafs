from kernel import *
import errno, posixpath, os


class PathFs(object):
    """Base class for a read-write FUSE file system interface
    whose underlying content is best accessed with '/'-separated
    string paths.
    """
    uid = os.getuid()
    gid = os.getgid()
    umask = os.umask(0); os.umask(umask)
    timeout = 86400.0

    def __init__(self, root=''):
        self._paths = {FUSE_ROOT_ID: root}
        self._path2id = {root: FUSE_ROOT_ID}
        self._nextid = FUSE_ROOT_ID + 1

    def getnode(self, nodeid):
        try:
            return self._paths[nodeid]
        except KeyError:
            raise IOError(errno.ESTALE, nodeid)

    def forget(self, nodeid):
        try:
            p = self._paths.pop(nodeid)
            del self._path2id[p]
        except KeyError:
            pass

    def cachepath(self, path):
        if path in self._path2id:
            return self._path2id[path]
        id = self._nextid
        self._nextid += 1
        self._paths[id] = path
        self._path2id[path] = id
        return id

    def mkattr(self, path, size, st_kind, mode, time):
        attr = fuse_attr(ino   = self._path2id[path],
                         size  = size,
                         mode  = st_kind | (mode & ~self.umask),
                         nlink = 1,  # even on dirs! this confuses 'find' in
                                     # a good way :-)
                         atime = time,
                         mtime = time,
                         ctime = time,
                         uid   = self.uid,
                         gid   = self.gid)
        return attr, self.timeout

    def lookup(self, path, name):
        npath = posixpath.join(path, name)
        if not self.check_path(npath):
            raise IOError(errno.ENOENT, name)
        return self.cachepath(npath), self.timeout

    def mknod(self, path, name, mode):
        npath = posixpath.join(path, name)
        self.mknod_path(npath, mode)
        return self.cachepath(npath), self.timeout

    def mkdir(self, path, name, mode):
        npath = posixpath.join(path, name)
        self.mkdir_path(npath, mode)
        return self.cachepath(npath), self.timeout

    def unlink(self, path, name):
        npath = posixpath.join(path, name)
        self.unlink_path(npath)

    def rmdir(self, path, name):
        npath = posixpath.join(path, name)
        self.rmdir_path(npath)

    def rename(self, oldpath, oldname, newpath, newname):
        noldpath = posixpath.join(oldpath, oldname)
        nnewpath = posixpath.join(newpath, newname)
        if not self.rename_path(noldpath, nnewpath):
            raise IOError(errno.ENOENT, oldname)
        # fix all paths in the cache
        N = len(noldpath)
        for id, path in self._paths.items():
            if path.startswith(noldpath):
                if len(path) == N or path[N] == '/':
                    del self._path2id[path]
                    path = nnewpath + path[N:]
                    self._paths[id] = path
                    self._path2id[path] = id
