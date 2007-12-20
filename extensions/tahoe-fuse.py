#! /usr/bin/env python
'''
Tahoe thin-client fuse module.

Goals:
- Delegate to Tahoe webapi as much as possible.
- Thin as possible.
- This is a proof-of-concept, not a usable product.
'''


#import bindann
#bindann.install_exception_handler()

import sys, stat, os, errno, urllib

import simplejson

# FIXME: Currently uses the old, silly path-based (non-stateful) interface:
import fuse
fuse.fuse_python_api = (0, 1) # Use the silly path-based api for now.


### Config:
TahoeConfigDir = '~/.tahoe'
MagicDevNumber = 42


def main(args = sys.argv[1:]):
    fs = TahoeFS(os.path.expanduser(TahoeConfigDir))
    fs.main()


### Utilities just for debug:
def debugdeco(m):
    def dbmeth(self, *a, **kw):
        pid = self.GetContext()['pid']
        print '[%d %r]\n%s%r%r' % (pid, get_cmdline(pid), m.__name__, a, kw)
        try:
            r = m(self, *a, **kw)
            if (type(r) is int) and (r < 0):
                print '-> -%s\n' % (errno.errorcode[-r],)
            else:
                repstr = repr(r)[:256]
                print '-> %s\n' % (repstr,)
            return r
        except:
            sys.excepthook(*sys.exc_info())
            
    return dbmeth


def get_cmdline(pid):
    f = open('/proc/%d/cmdline' % pid, 'r')
    args = f.read().split('\0')
    f.close()
    assert args[-1] == ''
    return args[:-1]


class ErrnoExc (Exception):
    def __init__(self, eno):
        self.eno = eno
        Exception.__init__(self, errno.errorcode[eno])

    @staticmethod
    def wrapped(meth):
        def wrapper(*args, **kw):
            try:
                return meth(*args, **kw)
            except ErrnoExc, e:
                return -e.eno
        wrapper.__name__ = meth.__name__
        return wrapper


### Heart of the Matter:
class TahoeFS (fuse.Fuse):
    def __init__(self, confdir):
        fuse.Fuse.__init__(self)
        self.confdir = confdir
        
        self.flags = 0 # FIXME: What goes here?
        self.multithreaded = 0

        # silly path-based file handles.
        self.filecontents = {} # {path -> contents}

        self._init_url()
        self._init_bookmarks()

    def _init_url(self):
        f = open(os.path.join(self.confdir, 'webport'), 'r')
        contents = f.read()
        f.close()

        fields = contents.split(':')
        proto, port = fields[:2]
        assert proto == 'tcp'
        port = int(port)
        self.url = 'http://localhost:%d' % (port,)

    def _init_bookmarks(self):
        f = open(os.path.join(self.confdir, 'fuse-bookmarks.uri'), 'r')
        uri = f.read().strip()
        f.close()
        
        self.bookmarks = TahoeDir(self.url, uri)

    def _get_node(self, path):
        assert path.startswith('/')
        if path == '/':
            return self.bookmarks.resolve_path([])
        else:
            parts = path.split('/')[1:]
            return self.bookmarks.resolve_path(parts)
    
    def _get_contents(self, path):
        node = self._get_node(path)
        contents = node.open().read()
        self.filecontents[path] = contents
        return contents
    
    @debugdeco
    @ErrnoExc.wrapped
    def getattr(self, path):
        node = self._get_node(path)
        return node.getattr()
                
    @debugdeco
    @ErrnoExc.wrapped
    def getdir(self, path):
        """
        return: [(name, typeflag), ... ]
        """
        node = self._get_node(path)
        return node.getdir()

    @debugdeco
    @ErrnoExc.wrapped
    def mythread(self):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def chmod(self, path, mode):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def chown(self, path, uid, gid):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def fsync(self, path, isFsyncFile):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def link(self, target, link):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def mkdir(self, path, mode):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def mknod(self, path, mode, dev_ignored):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def open(self, path, mode):
        IgnoredFlags = os.O_RDONLY | os.O_NONBLOCK | os.O_SYNC | os.O_LARGEFILE 
        # Note: IgnoredFlags are all ignored!
        for fname in dir(os):
            if fname.startswith('O_'):
                flag = getattr(os, fname)
                if flag & IgnoredFlags:
                    continue
                elif mode & flag:
                    print 'Flag not supported:', fname
                    raise ErrnoExc(errno.ENOSYS)

        self._get_contents(path)
        return 0

    @debugdeco
    @ErrnoExc.wrapped
    def read(self, path, length, offset):
        return self._get_contents(path)[offset:length]

    @debugdeco
    @ErrnoExc.wrapped
    def release(self, path):
        del self.filecontents[path]
        return 0

    @debugdeco
    @ErrnoExc.wrapped
    def readlink(self, path):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def rename(self, oldpath, newpath):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def rmdir(self, path):
        return -errno.ENOSYS

    #@debugdeco
    @ErrnoExc.wrapped
    def statfs(self):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def symlink ( self, targetPath, linkPath ):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def truncate(self, path, size):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def unlink(self, path):
        return -errno.ENOSYS

    @debugdeco
    @ErrnoExc.wrapped
    def utime(self, path, times):
        return -errno.ENOSYS


class TahoeNode (object):
    NextInode = 0
    
    @staticmethod
    def make(baseurl, uri):
        typefield = uri.split(':', 2)[1]
        if typefield.startswith('DIR'):
            return TahoeDir(baseurl, uri)
        else:
            return TahoeFile(baseurl, uri)
        
    def __init__(self, baseurl, uri):
        self.burl = baseurl
        self.uri = uri
        self.fullurl = '%s/uri/%s' % (self.burl, self.uri)
        self.inode = TahoeNode.NextInode
        TahoeNode.NextInode += 1

    def getattr(self):
        """
        - st_mode (protection bits)
        - st_ino (inode number)
        - st_dev (device)
        - st_nlink (number of hard links)
        - st_uid (user ID of owner)
        - st_gid (group ID of owner)
        - st_size (size of file, in bytes)
        - st_atime (time of most recent access)
        - st_mtime (time of most recent content modification)
        - st_ctime (platform dependent; time of most recent metadata change on Unix,
                    or the time of creation on Windows).
        """
        # FIXME: Return metadata that isn't completely fabricated.
        return (self.get_mode(),
                self.inode,
                MagicDevNumber,
                self.get_linkcount(),
                os.getuid(),
                os.getgid(),
                self.get_size(),
                0,
                0,
                0)

    def get_metadata(self):
        f = self.open('?t=json')
        json = f.read()
        f.close()
        return simplejson.loads(json)
        
    def open(self, postfix=''):
        url = self.fullurl + postfix
        print '*** Fetching:', `url`
        return urllib.urlopen(url)


class TahoeFile (TahoeNode):
    def __init__(self, baseurl, uri):
        assert uri.split(':', 2)[1] in ('CHK', 'LIT'), `uri`
        TahoeNode.__init__(self, baseurl, uri)

    # nonfuse:
    def get_mode(self):
        return stat.S_IFREG | 0400 # Read only regular file.

    def get_linkcount(self):
        return 1
    
    def get_size(self):
        return self.get_metadata()[1]['size']
    
    def resolve_path(self, path):
        assert type(path) is list
        assert path == []
        return self
    

class TahoeDir (TahoeNode):
    def __init__(self, baseurl, uri):
        assert uri.split(':', 2)[1] in ('DIR', 'DIR-RO'), `uri`
        TahoeNode.__init__(self, baseurl, uri)

        self.mode = stat.S_IFDIR | 0500 # Read only directory.

    # FUSE:
    def getdir(self):
        d = [('.', self.get_mode()), ('..', self.get_mode())]
        for name, child in self.get_children().items():
            if name: # Just ignore this crazy case!
                d.append((name, child.get_mode()))
        return d

    # nonfuse:
    def get_mode(self):
        return stat.S_IFDIR | 0500 # Read only directory.

    def get_linkcount(self):
        return len(self.getdir())
    
    def get_size(self):
        return 2 ** 12 # FIXME: What do we return here?  len(self.get_metadata())
    
    def resolve_path(self, path):
        assert type(path) is list

        if path:
            head = path[0]
            child = self.get_child(head)
            return child.resolve_path(path[1:])
        else:
            return self
        
    def get_child(self, name):
        c = self.get_children()
        return c[name]

    def get_children(self):
        flag, md = self.get_metadata()
        assert flag == 'dirnode'

        c = {}
        for name, (childflag, childmd) in md['children'].items():
            if childflag == 'dirnode':
                cls = TahoeDir
            else:
                cls = TahoeFile

            c[str(name)] = cls(self.burl, childmd['ro_uri'])
        return c
        
        

if __name__ == '__main__':
    main()

