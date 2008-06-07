from kernel import *
from handler import Handler
import stat, time, os, weakref, errno
from cStringIO import StringIO


class MemoryFS(object):
    INFINITE = 86400.0


    class Dir(object):
        type = TYPE_DIR
        def __init__(self, attr):
            self.attr = attr
            self.contents = {}    # { 'filename': Dir()/File()/SymLink() }

    class File(object):
        type = TYPE_REG
        def __init__(self, attr):
            self.attr = attr
            self.data = StringIO()

    class SymLink(object):
        type = TYPE_LNK
        def __init__(self, attr, target):
            self.attr = attr
            self.target = target


    def __init__(self, root=None):
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.umask = os.umask(0); os.umask(self.umask)
        self.root = root or self.Dir(self.newattr(stat.S_IFDIR))
        self.root.id = FUSE_ROOT_ID
        self.nodes = weakref.WeakValueDictionary()
        self.nodes[FUSE_ROOT_ID] = self.root
        self.nextid = FUSE_ROOT_ID + 1

    def newattr(self, s, ino=None, mode=0666):
        now = time.time()
        attr = fuse_attr(size  = 0,
                         mode  = s | (mode & ~self.umask),
                         nlink = 1,  # even on dirs! this confuses 'find' in
                                     # a good way :-)
                         atime = now,
                         mtime = now,
                         ctime = now,
                         uid   = self.uid,
                         gid   = self.gid)
        if ino is None:
            ino = id(attr)
        if ino < 0:
            ino = ~ino
        attr.ino = ino
        return attr

    def getnode(self, id):
        return self.nodes[id]

    def modified(self, node):
        node.attr.mtime = node.attr.atime = time.time()
        if isinstance(node, self.File):
            node.data.seek(0, 2)
            node.attr.size = node.data.tell()

    def getattr(self, node):
        return node.attr, self.INFINITE

    def setattr(self, node, mode, uid, gid, size, atime, mtime):
        if mode is not None:
            node.attr.mode = (node.attr.mode & ~0777) | (mode & 0777)
        if uid is not None:
            node.attr.uid = uid
        if gid is not None:
            node.attr.gid = gid
        if size is not None:
            assert isinstance(node, self.File)
            node.data.seek(0, 2)
            oldsize = node.data.tell()
            if size < oldsize:
                node.data.seek(size)
                node.data.truncate()
                self.modified(node)
            elif size > oldsize:
                node.data.write('\x00' * (size - oldsize))
                self.modified(node)
        if atime is not None:
            node.attr.atime = atime
        if mtime is not None:
            node.attr.mtime = mtime

    def listdir(self, node):
        assert isinstance(node, self.Dir)
        for name, subobj in node.contents.items():
            yield name, subobj.type

    def lookup(self, dirnode, filename):
        try:
            return dirnode.contents[filename].id, self.INFINITE
        except KeyError:
            raise IOError(errno.ENOENT, filename)

    def open(self, filenode, flags):
        return filenode.data

    def newnodeid(self, newnode):
        id = self.nextid
        self.nextid += 1
        newnode.id = id
        self.nodes[id] = newnode
        return id

    def mknod(self, dirnode, filename, mode):
        node = self.File(self.newattr(stat.S_IFREG, mode=mode))
        dirnode.contents[filename] = node
        return self.newnodeid(node), self.INFINITE

    def mkdir(self, dirnode, subdirname, mode):
        node = self.Dir(self.newattr(stat.S_IFDIR, mode=mode))
        dirnode.contents[subdirname] = node
        return self.newnodeid(node), self.INFINITE

    def symlink(self, dirnode, linkname, target):
        node = self.SymLink(self.newattr(stat.S_IFLNK), target)
        dirnode.contents[linkname] = node
        return self.newnodeid(node), self.INFINITE

    def unlink(self, dirnode, filename):
        del dirnode.contents[filename]

    rmdir = unlink

    def readlink(self, symlinknode):
        return symlinknode.target

    def rename(self, olddirnode, oldname, newdirnode, newname):
        node = olddirnode.contents[oldname]
        newdirnode.contents[newname] = node
        del olddirnode.contents[oldname]

    def getxattrs(self, node):
        try:
            return node.xattrs
        except AttributeError:
            node.xattrs = {}
            return node.xattrs


if __name__ == '__main__':
    import sys
    mountpoint = sys.argv[1]
    memoryfs = MemoryFS()
    handler = Handler(mountpoint, memoryfs)
    handler.loop_forever()
