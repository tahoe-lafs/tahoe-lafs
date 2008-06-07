"""
For reading and caching from slow file system (e.g. DVDs or network).

    python mirrorfs.py <sourcedir> <cachedir> <mountpoint>

Makes <mountpoint> show a read-only copy of the files in <sourcedir>,
caching all data ever read in the <cachedir> to avoid reading it
twice.  This script also features optimistic read-ahead: once a
file is accessed, and as long as no other file is accessed, the
whole file is read and cached as fast as the <sourcedir> will
provide it.

You have to clean up <cachedir> manually before mounting a modified
or different <sourcedir>.
"""
import sys, os, posixpath, stat

try:
    __file__
except NameError:
    __file__ = sys.argv[0]
this_dir = os.path.dirname(os.path.abspath(__file__))

# ____________________________________________________________

sys.path.append(os.path.dirname(this_dir))
from blockfs import valuetree
from handler import Handler
import greenhandler, greensock
from objectfs import ObjectFs

BLOCKSIZE = 65536

class MirrorFS(ObjectFs):
    rawfd = None

    def __init__(self, srcdir, cachedir):
        self.srcdir = srcdir
        self.cachedir = cachedir
        self.table = valuetree.ValueTree(os.path.join(cachedir, 'table'), 'q')
        if '' not in self.table:
            self.initial_read_dir('')
            self.table[''] = -1,
        try:
            self.rawfile = open(os.path.join(cachedir, 'raw'), 'r+b')
        except IOError:
            self.rawfile = open(os.path.join(cachedir, 'raw'), 'w+b')
        ObjectFs.__init__(self, DirNode(self, ''))
        self.readahead_at = None
        greenhandler.autogreenlet(self.readahead)

    def close(self):
        self.table.close()

    def readahead(self):
        while True:
            greensock.sleep(0.001)
            while not self.readahead_at:
                greensock.sleep(1)
            path, blocknum = self.readahead_at
            self.readahead_at = None
            try:
                self.readblock(path, blocknum, really=False)
            except EOFError:
                pass

    def initial_read_dir(self, path):
        print 'Reading initial directory structure...', path
        dirname = os.path.join(self.srcdir, path)
        for name in os.listdir(dirname):
            filename = os.path.join(dirname, name)
            st = os.stat(filename)
            if stat.S_ISDIR(st.st_mode):
                self.initial_read_dir(posixpath.join(path, name))
                q = -1
            else:
                q = st.st_size
            self.table[posixpath.join(path, name)] = q,

    def __getitem__(self, key):
        self.tablelock.acquire()
        try:
            return self.table[key]
        finally:
            self.tablelock.release()

    def readblock(self, path, blocknum, really=True):
        s = '%s/%d' % (path, blocknum)
        try:
            q, = self.table[s]
        except KeyError:
            print s
            self.readahead_at = None
            f = open(os.path.join(self.srcdir, path), 'rb')
            f.seek(blocknum * BLOCKSIZE)
            data = f.read(BLOCKSIZE)
            f.close()
            if not data:
                q = -2
            else:
                data += '\x00' * (BLOCKSIZE - len(data))
                self.rawfile.seek(0, 2)
                q = self.rawfile.tell()
                self.rawfile.write(data)
            self.table[s] = q,
            if q == -2:
                raise EOFError
        else:
            if q == -2:
                raise EOFError
            if really:
                self.rawfile.seek(q, 0)
                data = self.rawfile.read(BLOCKSIZE)
            else:
                data = None
        if self.readahead_at is None:
            self.readahead_at = path, blocknum + 1
        return data


class Node(object):

    def __init__(self, mfs, path):
        self.mfs = mfs
        self.path = path

class DirNode(Node):

    def join(self, name):
        path = posixpath.join(self.path, name)
        q, = self.mfs.table[path]
        if q == -1:
            return DirNode(self.mfs, path)
        else:
            return FileNode(self.mfs, path)

    def listdir(self):
        result = []
        for key, value in self.mfs.table.iteritemsfrom(self.path):
            if not key.startswith(self.path):
                break
            tail = key[len(self.path):].lstrip('/')
            if tail and '/' not in tail:
                result.append(tail)
        return result

class FileNode(Node):

    def size(self):
        q, = self.mfs.table[self.path]
        return q

    def read(self):
        return FileStream(self.mfs, self.path)

class FileStream(object):

    def __init__(self, mfs, path):
        self.mfs = mfs
        self.path = path
        self.pos = 0
        self.size, = self.mfs.table[path]

    def seek(self, p):
        self.pos = p

    def read(self, count):
        result = []
        end = min(self.pos + count, self.size)
        while self.pos < end:
            blocknum, offset = divmod(self.pos, BLOCKSIZE)
            data = self.mfs.readblock(self.path, blocknum)
            data = data[offset:]
            data = data[:end - self.pos]
            assert len(data) > 0
            result.append(data)
            self.pos += len(data)
        return ''.join(result)

# ____________________________________________________________

if __name__ == '__main__':
    import sys
    srcdir, cachedir, mountpoint = sys.argv[1:]
    mirrorfs = MirrorFS(srcdir, cachedir)
    try:
        handler = Handler(mountpoint, mirrorfs)
        greenhandler.add_handler(handler)
        greenhandler.mainloop()
    finally:
        mirrorfs.close()
