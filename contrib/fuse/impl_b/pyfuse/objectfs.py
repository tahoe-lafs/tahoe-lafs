from kernel import *
import stat, errno, os, time
from cStringIO import StringIO
from OrderedDict import OrderedDict


class ObjectFs:
    """A simple read-only file system based on Python objects.

    Interface of Directory objects:
      * join(name)   returns a file or subdirectory object
      * listdir()    returns a list of names, or a list of (name, object)

    join() is optional if listdir() returns a list of (name, object).
    Alternatively, Directory objects can be plain dictionaries {name: object}.

    Interface of File objects:
      * size()       returns the size
      * read()       returns the data

    Alternatively, File objects can be plain strings.

    Interface of SymLink objects:
      * readlink()   returns the symlink's target, as a string
    """

    INFINITE = 86400.0
    USE_DIR_CACHE = True

    def __init__(self, rootnode):
        self.nodes = {FUSE_ROOT_ID: rootnode}
        if self.USE_DIR_CACHE:
            self.dircache = {}
        self.starttime = time.time()
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.umask = os.umask(0); os.umask(self.umask)

    def newattr(self, s, ino, mode=0666):
        if ino < 0:
            ino = ~ino
        return fuse_attr(ino   = ino,
                         size  = 0,
                         mode  = s | (mode & ~self.umask),
                         nlink = 1,  # even on dirs! this confuses 'find' in
                                     # a good way :-)
                         atime = self.starttime,
                         mtime = self.starttime,
                         ctime = self.starttime,
                         uid   = self.uid,
                         gid   = self.gid)

    def getnode(self, nodeid):
        try:
            return self.nodes[nodeid]
        except KeyError:
            raise IOError(errno.ESTALE, nodeid)

    def getattr(self, node):
        timeout = self.INFINITE
        if isinstance(node, str):
            attr = self.newattr(stat.S_IFREG, id(node))
            attr.size = len(node)
        elif hasattr(node, 'readlink'):
            target = node.readlink()
            attr = self.newattr(stat.S_IFLNK, id(node))
            attr.size = len(target)
            attr.mode |= 0777
        elif hasattr(node, 'size'):
            sz = node.size()
            attr = self.newattr(stat.S_IFREG, id(node))
            if sz is None:
                timeout = 0
            else:
                attr.size = sz
        else:
            attr = self.newattr(stat.S_IFDIR, id(node), mode=0777)
        #print 'getattr(%s) -> %s, %s' % (node, attr, timeout)
        return attr, timeout

    def getentries(self, node):
        if isinstance(node, dict):
            return node
        try:
            if not self.USE_DIR_CACHE:
                raise KeyError
            return self.dircache[node]
        except KeyError:
            entries = OrderedDict()
            if hasattr(node, 'listdir'):
                for name in node.listdir():
                    if isinstance(name, tuple):
                        name, subnode = name
                    else:
                        subnode = None
                    entries[name] = subnode
            if self.USE_DIR_CACHE:
                self.dircache[node] = entries
            return entries

    def listdir(self, node):
        entries = self.getentries(node)
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
        entries = self.getentries(node)
        try:
            subnode = entries.get(name)
            if subnode is None:
                if hasattr(node, 'join'):
                    subnode = node.join(name)
                    entries[name] = subnode
                else:
                    raise KeyError
        except KeyError:
            raise IOError(errno.ENOENT, name)
        else:
            return self.reply(subnode)

    def reply(self, node):
        res = uid(node)
        self.nodes[res] = node
        return res, self.INFINITE

    def open(self, node, mode):
        if not isinstance(node, str):
            node = node.read()
        if not hasattr(node, 'read'):
            node = StringIO(node)
        return node

    def readlink(self, node):
        return node.readlink()

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

class SymLink(object):
    def __init__(self, target):
        self.target = target
    def readlink(self):
        return self.target
