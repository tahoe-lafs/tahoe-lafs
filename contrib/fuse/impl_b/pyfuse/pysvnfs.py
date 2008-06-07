from kernel import *
import errno, posixpath, weakref
from time import time as now
from stat import S_IFDIR, S_IFREG, S_IFMT
from cStringIO import StringIO
from handler import Handler
from pathfs import PathFs
from pysvn.ra_filesystem import SvnRepositoryFilesystem
import pysvn.date


class SvnFS(PathFs):

    def __init__(self, svnurl, root=''):
        super(SvnFS, self).__init__(root)
        self.svnurl = svnurl
        self.openfiles = weakref.WeakValueDictionary()
        self.creationtimes = {}
        self.do_open()

    def do_open(self, rev='HEAD'):
        self.fs = SvnRepositoryFilesystem(svnurl, rev)

    def do_commit(self, msg):
        rev = self.fs.commit(msg)
        if rev is None:
            print '* no changes.'
        else:
            print '* checked in revision %d.' % (rev,)
        self.do_open()

    def do_status(self, path=''):
        print '* status'
        result = []
        if path and not path.endswith('/'):
            path += '/'
        for delta in self.fs._compute_deltas():
            if delta.path.startswith(path):
                if delta.oldrev is None:
                    c = 'A'
                elif delta.newrev is None:
                    c = 'D'
                else:
                    c = 'M'
                result.append('    %s  %s\n' % (c, delta.path[len(path):]))
        return ''.join(result)

    def getattr(self, path):
        stat = self.fs.stat(path)
        if stat['svn:entry:kind'] == 'dir':
            s = S_IFDIR
            mode = 0777
        else:
            s = S_IFREG
            mode = 0666
        try:
            time = pysvn.date.decode(stat['svn:entry:committed-date'])
        except KeyError:
            try:
                time = self.creationtimes[path]
            except KeyError:
                time = self.creationtimes[path] = now()
        return self.mkattr(path,
                           size    = stat.get('svn:entry:size', 0),
                           st_kind = s,
                           mode    = mode,
                           time    = time)

    def setattr(self, path, mode, uid, gid, size, atime, mtime):
        if size is not None:
            data = self.fs.read(path)
            if size < len(data):
                self.fs.write(path, data[:size])
            elif size > len(data):
                self.fs.write(path, data + '\x00' * (size - len(data)))

    def listdir(self, path):
        for name in self.fs.listdir(path):
            kind = self.fs.check_path(posixpath.join(path, name))
            if kind == 'dir':
                yield name, TYPE_DIR
            else:
                yield name, TYPE_REG

    def check_path(self, path):
        kind = self.fs.check_path(path)
        return kind is not None

    def open(self, path, mode):
        try:
            of = self.openfiles[path]
        except KeyError:
            of = self.openfiles[path] = OpenFile(self.fs.read(path))
        return of, FOPEN_KEEP_CACHE

    def modified(self, path):
        try:
            of = self.openfiles[path]
        except KeyError:
            pass
        else:
            self.fs.write(path, of.f.getvalue())

    def mknod_path(self, path, mode):
        self.fs.add(path)

    def mkdir_path(self, path, mode):
        self.fs.mkdir(path)

    def unlink_path(self, path):
        self.fs.unlink(path)

    def rmdir_path(self, path):
        self.fs.rmdir(path)

    def rename_path(self, oldpath, newpath):
        kind = self.fs.check_path(oldpath)
        if kind is None:
            return False
        self.fs.move(oldpath, newpath, kind)
        return True

    def getxattrs(self, path):
        return XAttrs(self, path)


class OpenFile:
    def __init__(self, data=''):
        self.f = StringIO()
        self.f.write(data)
        self.f.seek(0)

    def seek(self, pos):
        self.f.seek(pos)

    def read(self, sz):
        return self.f.read(sz)

    def write(self, buf):
        self.f.write(buf)


class XAttrs:
    def __init__(self, svnfs, path):
        self.svnfs = svnfs
        self.path = path

    def keys(self):
        return []

    def __getitem__(self, key):
        if key == 'status':
            return self.svnfs.do_status(self.path)
        raise KeyError(key)

    def __setitem__(self, key, value):
        if key == 'commit' and self.path == '':
            self.svnfs.do_commit(value)
        elif key == 'update' and self.path == '':
            if self.svnfs.fs.modified():
                raise IOError(errno.EPERM, "there are local changes")
            if value == '':
                rev = 'HEAD'
            else:
                try:
                    rev = int(value)
                except ValueError:
                    raise IOError(errno.EPERM, "invalid revision number")
            self.svnfs.do_open(rev)
        else:
            raise KeyError(key)

    def __delitem__(self, key):
        raise KeyError(key)


if __name__ == '__main__':
    import sys
    svnurl, mountpoint = sys.argv[1:]
    handler = Handler(mountpoint, SvnFS(svnurl))
    handler.loop_forever()
