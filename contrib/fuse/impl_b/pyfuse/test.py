from handler import Handler
import stat, errno, os, time
from cStringIO import StringIO
from kernel import *


UID = os.getuid()
GID = os.getgid()
UMASK = os.umask(0); os.umask(UMASK)
INFINITE = 86400.0


class Node(object):
    __slots__ = ['attr', 'data']

    def __init__(self, attr, data=None):
        self.attr = attr
        self.data = data

    def type(self):
        return mode2type(self.attr.mode)

    def modified(self):
        self.attr.mtime = self.attr.atime = time.time()
        t = self.type()
        if t == TYPE_REG:
            f = self.data
            pos = f.tell()
            f.seek(0, 2)
            self.attr.size = f.tell()
            f.seek(pos)
        elif t == TYPE_DIR:
            nsubdirs = 0
            for nodeid in self.data.values():
                nsubdirs += nodeid & 1
            self.attr.nlink = 2 + nsubdirs


def newattr(s, mode=0666):
    now = time.time()
    return fuse_attr(ino   = INVALID_INO,
                     size  = 0,
                     mode  = s | (mode & ~UMASK),
                     nlink = 1 + (s == stat.S_IFDIR),
                     atime = now,
                     mtime = now,
                     ctime = now,
                     uid   = UID,
                     gid   = GID)

# ____________________________________________________________

class Filesystem:

    def __init__(self, rootnode):
        self.nodes = {FUSE_ROOT_ID: rootnode}
        self.nextid = 2
        assert self.nextid > FUSE_ROOT_ID

    def getnode(self, nodeid):
        try:
            return self.nodes[nodeid]
        except KeyError:
            raise IOError(errno.ESTALE, nodeid)

    def forget(self, nodeid):
        pass

    def cachenode(self, node):
        id = self.nextid
        self.nextid += 2
        if node.type() == TYPE_DIR:
            id += 1
        self.nodes[id] = node
        return id

    def getattr(self, node):
        return node.attr, INFINITE

    def setattr(self, node, mode=None, uid=None, gid=None,
                size=None, atime=None, mtime=None):
        if mode  is not None:  node.attr.mode  = (node.attr.mode&~0777) | mode
        if uid   is not None:  node.attr.uid   = uid
        if gid   is not None:  node.attr.gid   = gid
        if atime is not None:  node.attr.atime = atime
        if mtime is not None:  node.attr.mtime = mtime
        if size is not None and node.type() == TYPE_REG:
            node.data.seek(size)
            node.data.truncate()

    def listdir(self, node):
        for name, subnodeid in node.data.items():
            subnode = self.nodes[subnodeid]
            yield name, subnode.type()

    def lookup(self, node, name):
        try:
            return node.data[name], INFINITE
        except KeyError:
            pass
        if hasattr(node, 'findnode'):
            try:
                subnode = node.findnode(name)
            except KeyError:
                pass
            else:
                id = self.cachenode(subnode)
                node.data[name] = id
                return  id, INFINITE
        raise IOError(errno.ENOENT, name)

    def open(self, node, mode):
        return node.data

    def mknod(self, node, name, mode):
        subnode = Node(newattr(mode & 0170000, mode & 0777))
        if subnode.type() == TYPE_REG:
            subnode.data = StringIO()
        else:
            raise NotImplementedError
        id = self.cachenode(subnode)
        node.data[name] = id
        node.modified()
        return id, INFINITE

    def mkdir(self, node, name, mode):
        subnode = Node(newattr(stat.S_IFDIR, mode & 0777), {})
        id = self.cachenode(subnode)
        node.data[name] = id
        node.modified()
        return id, INFINITE

    def symlink(self, node, linkname, target):
        subnode = Node(newattr(stat.S_IFLNK, 0777), target)
        id = self.cachenode(subnode)
        node.data[linkname] = id
        node.modified()
        return id, INFINITE

    def readlink(self, node):
        assert node.type() == TYPE_LNK
        return node.data

    def unlink(self, node, name):
        try:
            del node.data[name]
        except KeyError:
            raise IOError(errno.ENOENT, name)
        node.modified()

    rmdir = unlink

    def rename(self, oldnode, oldname, newnode, newname):
        if newnode.type() != TYPE_DIR:
            raise IOError(errno.ENOTDIR, newnode)
        try:
            nodeid = oldnode.data.pop(oldname)
        except KeyError:
            raise IOError(errno.ENOENT, oldname)
        oldnode.modified()
        newnode.data[newname] = nodeid
        newnode.modified()

    def modified(self, node):
        node.modified()

# ____________________________________________________________

if __name__ == '__main__':
    root = Node(newattr(stat.S_IFDIR), {})
    handler = Handler('/home/arigo/mnt', Filesystem(root))
    handler.loop_forever()
