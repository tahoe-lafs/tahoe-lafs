from kernel import *
import stat, errno, os, time
from cStringIO import StringIO
from OrderedDict import OrderedDict

INFINITE = 86400.0


class Wrapper(object):
    def __init__(self, obj):
        self.obj = obj

    def getuid(self):
        return uid(self.obj)

    def __hash__(self):
        return hash(self.obj)

    def __eq__(self, other):
        return self.obj == other

    def __ne__(self, other):
        return self.obj != other


class BaseDir(object):

    def join(self, name):
        "Return a file or subdirectory object"
        for item in self.listdir():
            if isinstance(item, tuple):
                subname, subnode = item
                if subname == name:
                    return subnode
        raise KeyError(name)

    def listdir(self):
        "Return a list of names, or a list of (name, object)"
        raise NotImplementedError

    def create(self, name):
        "Create a file"
        raise NotImplementedError

    def mkdir(self, name):
        "Create a subdirectory"
        raise NotImplementedError

    def symlink(self, name, target):
        "Create a symbolic link"
        raise NotImplementedError

    def unlink(self, name):
        "Remove a file or subdirectory."
        raise NotImplementedError

    def rename(self, newname, olddirnode, oldname):
        "Move another node into this directory."
        raise NotImplementedError

    def getuid(self):
        return uid(self)

    def getattr(self, fs):
        return fs.newattr(stat.S_IFDIR, self.getuid(), mode=0777), INFINITE

    def setattr(self, **kwds):
        pass

    def getentries(self):
        entries = OrderedDict()
        for name in self.listdir():
            if isinstance(name, tuple):
                name, subnode = name
            else:
                subnode = None
            entries[name] = subnode
        return entries


class BaseFile(object):

    def size(self):
        "Return the size of the file, or None if not known yet"
        f = self.open()
        if isinstance(f, str):
            return len(f)
        f.seek(0, 2)
        return f.tell()

    def open(self):
        "Return the content as a string or a file-like object"
        raise NotImplementedError

    def getuid(self):
        return uid(self)

    def getattr(self, fs):
        sz = self.size()
        attr = fs.newattr(stat.S_IFREG, self.getuid())
        if sz is None:
            timeout = 0
        else:
            attr.size = sz
            timeout = INFINITE
        return attr, timeout

    def setattr(self, size, **kwds):
        f = self.open()
        if self.size() == size:
            return
        if isinstance(f, str):
            raise IOError(errno.EPERM)
        f.seek(size)
        f.truncate()


class BaseSymLink(object):

    def readlink(self):
        "Return the symlink's target, as a string"
        raise NotImplementedError

    def getuid(self):
        return uid(self)

    def getattr(self, fs):
        target = self.readlink()
        attr = fs.newattr(stat.S_IFLNK, self.getuid())
        attr.size = len(target)
        attr.mode |= 0777
        return attr, INFINITE

    def setattr(self, **kwds):
        pass

# ____________________________________________________________

class Dir(BaseDir):
    def __init__(self, **contents):
        self.contents = contents
    def listdir(self):
        return self.contents.items()
    def join(self, name):
        return self.contents[name]
    def create(self, fs, name):
        node = fs.File()
        self.contents[name] = node
        return node
    def mkdir(self, fs, name):
        node = fs.Dir()
        self.contents[name] = node
        return node
    def symlink(self, fs, name, target):
        node = fs.SymLink(target)
        self.contents[name] = node
        return node
    def unlink(self, name):
        del self.contents[name]
    def rename(self, newname, olddirnode, oldname):
        oldnode = olddirnode.join(oldname)
        olddirnode.unlink(oldname)
        self.contents[newname] = oldnode

class File(BaseFile):
    def __init__(self):
        self.data = StringIO()
    def size(self):
        self.data.seek(0, 2)
        return self.data.tell()
    def open(self):
        return self.data

class SymLink(BaseFile):
    def __init__(self, target):
        self.target = target
    def readlink(self):
        return self.target

# ____________________________________________________________


class RWObjectFs(object):
    """A simple read-write file system based on Python objects."""

    UID = os.getuid()
    GID = os.getgid()
    UMASK = os.umask(0); os.umask(UMASK)

    Dir = Dir
    File = File
    SymLink = SymLink

    def __init__(self, rootnode):
        self.nodes = {FUSE_ROOT_ID: rootnode}
        self.starttime = time.time()

    def newattr(self, s, ino, mode=0666):
        return fuse_attr(ino   = ino,
                         size  = 0,
                         mode  = s | (mode & ~self.UMASK),
                         nlink = 1,  # even on dirs! this confuses 'find' in
                                     # a good way :-)
                         atime = self.starttime,
                         mtime = self.starttime,
                         ctime = self.starttime,
                         uid   = self.UID,
                         gid   = self.GID)

    def getnode(self, nodeid):
        try:
            return self.nodes[nodeid]
        except KeyError:
            raise IOError(errno.ESTALE, nodeid)

    def getattr(self, node):
        return node.getattr(self)

    def setattr(self, node, mode, uid, gid, size, atime, mtime):
        node.setattr(mode=mode, uid=uid, gid=gid, size=size,
                     atime=atime, mtime=mtime)

    def listdir(self, node):
        entries = node.getentries()
        for name, subnode in entries.items():
            if subnode is None:
                subnode = node.join(name)
                self.nodes[uid(subnode)] = subnode
                entries[name] = subnode
            if isinstance(subnode, str):
                yield name, TYPE_REG
            elif hasattr(subnode, 'readlink'):
                yield name, TYPE_LNK
            elif hasattr(subnode, 'size'):
                yield name, TYPE_REG
            else:
                yield name, TYPE_DIR

    def lookup(self, node, name):
        try:
            subnode = node.join(name)
        except KeyError:
            raise IOError(errno.ENOENT, name)
        else:
            res = uid(subnode)
            self.nodes[res] = subnode
            return res, INFINITE

    def mknod(self, dirnode, filename, mode):
        node = dirnode.create(filename)
        return self.newnodeid(node), INFINITE

    def mkdir(self, dirnode, subdirname, mode):
        node = dirnode.mkdir(subdirname)
        return self.newnodeid(node), INFINITE

    def symlink(self, dirnode, linkname, target):
        node = dirnode.symlink(linkname, target)
        return self.newnodeid(node), INFINITE

    def unlink(self, dirnode, filename):
        try:
            dirnode.unlink(filename)
        except KeyError:
            raise IOError(errno.ENOENT, filename)

    rmdir = unlink

    def open(self, node, mode):
        f = node.open()
        if isinstance(f, str):
            f = StringIO(f)
        return f

    def readlink(self, node):
        return node.readlink()

    def rename(self, olddirnode, oldname, newdirnode, newname):
        try:
            newdirnode.rename(newname, olddirnode, oldname)
        except KeyError:
            raise IOError(errno.ENOENT, oldname)

    def getxattrs(self, node):
        return getattr(node, '__dict__', {})

# ____________________________________________________________

import struct
try:
    HUGEVAL = 256 ** struct.calcsize('P')
except struct.error:
    HUGEVAL = 0

def fixid(result):
    if result < 0:
        result += HUGEVAL
    return result

def uid(obj):
    """
    Return the id of an object as an unsigned number so that its hex
    representation makes sense
    """
    return fixid(id(obj))
