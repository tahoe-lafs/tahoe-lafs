#!/usr/bin/env python

#-----------------------------------------------------------------------------------------------
from allmydata.uri import CHKFileURI, NewDirectoryURI, LiteralFileURI
from allmydata.scripts.common_http import do_http as do_http_req
from allmydata.util.hashutil import tagged_hash
from allmydata.util.assertutil import precondition
from allmydata.util import base32
from allmydata.scripts.common import get_aliases

from twisted.python import usage

import base64
import sha
import sys
import os
#import pprint
import errno
import stat
# pull in some spaghetti to make this stuff work without fuse-py being installed
try:
    import _find_fuse_parts
    junk = _find_fuse_parts
    del junk
except ImportError:
    pass
import fuse

import time
import traceback
import simplejson
import urllib

VERSIONSTR="0.6"

USAGE = 'usage: tahoe fuse [dir_cap_name] [fuse_options] mountpoint'
DEFAULT_DIRECTORY_VALIDITY=26

if not hasattr(fuse, '__version__'):
    raise RuntimeError, \
        "your fuse-py doesn't know of fuse.__version__, probably it's too old."

fuse.fuse_python_api = (0, 2)
fuse.feature_assert('stateful_files', 'has_init')

class TahoeFuseOptions(usage.Options):
    optParameters = [
        ["node-directory", None, "~/.tahoe",
         "Look here to find out which Tahoe node should be used for all "
         "operations. The directory should either contain a full Tahoe node, "
         "or a file named node.url which points to some other Tahoe node. "
         "It should also contain a file named private/aliases which contains "
         "the mapping from alias name to root dirnode URI."
         ],
        ["node-url", None, None,
         "URL of the tahoe node to use, a URL like \"http://127.0.0.1:8123\". "
         "This overrides the URL found in the --node-directory ."],
        ["alias", None, None,
         "Which alias should be mounted."],
        ["root-uri", None, None,
         "Which root directory uri should be mounted."],
        ["cache-timeout", None, 20,
         "Time, in seconds, to cache directory data."],
        ]

    def __init__(self):
        usage.Options.__init__(self)
        self.fuse_options = []
        self.mountpoint = None

    def opt_option(self, fuse_option):
        """
        Pass mount options directly to fuse.  See below.
        """
        self.fuse_options.append(fuse_option)
        
    opt_o = opt_option

    def parseArgs(self, mountpoint=''):
        self.mountpoint = mountpoint

    def getSynopsis(self):
        return "%s [options] mountpoint" % (os.path.basename(sys.argv[0]),)

logfile = file('tfuse.log', 'ab')

def log(msg):
    logfile.write("%s: %s\n" % (time.asctime(), msg))
    #time.sleep(0.1)
    logfile.flush()

fuse.flog = log

def unicode_to_utf8(u):
    precondition(isinstance(u, unicode), repr(u))
    return u.encode('utf-8')

def do_http(method, url, body=''):
    resp = do_http_req(method, url, body)
    log('do_http(%s, %s) -> %s, %s' % (method, url, resp.status, resp.reason))
    if resp.status not in (200, 201):
        raise RuntimeError('http response (%s, %s)' % (resp.status, resp.reason))
    else:
        return resp.read()

def flag2mode(flags):
    log('flag2mode(%r)' % (flags,))
    #md = {os.O_RDONLY: 'r', os.O_WRONLY: 'w', os.O_RDWR: 'w+'}
    md = {os.O_RDONLY: 'rb', os.O_WRONLY: 'wb', os.O_RDWR: 'w+b'}
    m = md[flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR)]

    if flags & os.O_APPEND:
        m = m.replace('w', 'a', 1)

    return m

class TFSIOError(IOError):
    pass

class ENOENT(TFSIOError):
    def __init__(self, msg):
        TFSIOError.__init__(self, errno.ENOENT, msg)

class EINVAL(TFSIOError):
    def __init__(self, msg):
        TFSIOError.__init__(self, errno.EINVAL, msg)

class EACCESS(TFSIOError):
    def __init__(self, msg):
        TFSIOError.__init__(self, errno.EACCESS, msg)

class EEXIST(TFSIOError):
    def __init__(self, msg):
        TFSIOError.__init__(self, errno.EEXIST, msg)

def logargsretexc(meth):
    def inner(self, *args, **kwargs):
        log("%s(%r, %r)" % (meth, args, kwargs))
        try:
            ret = meth(self, *args, **kwargs)
        except:
            log('exception:\n%s' % (traceback.format_exc(),))
            raise
        log("ret: %r" % (ret, ))
        return ret
    inner.__name__ = '<logwrap(%s)>' % (meth,)
    return inner

def logexc(meth):
    def inner(self, *args, **kwargs):
        try:
            ret = meth(self, *args, **kwargs)
        except TFSIOError, tie:
            log('error: %s' % (tie,))
            raise
        except:
            log('exception:\n%s' % (traceback.format_exc(),))
            raise
        return ret
    inner.__name__ = '<logwrap(%s)>' % (meth,)
    return inner

def log_exc():
    log('exception:\n%s' % (traceback.format_exc(),))

class TahoeFuseFile(object):

    def __init__(self, path, flags, *mode):
        log("TFF: __init__(%r, %r, %r)" % (path, flags, mode))

        self._path = path # for tahoe put
        try:
            self.parent, self.name, self.fnode = self.tfs.get_parent_name_and_child(path)
            m = flag2mode(flags)
            log('TFF: flags2(mode) -> %s' % (m,))
            if m[0] in 'wa':
                # write
                self.fname = self.tfs.cache.tmp_file(os.urandom(20))
                if self.fnode is None:
                    log('TFF: [%s] open() for write: no file node, creating new File %s' % (self.name, self.fname, ))
                    self.fnode = File(0, 'URI:LIT:')
                    self.fnode.tmp_fname = self.fname # XXX kill this
                    self.parent.add_child(self.name, self.fnode, {})
                elif hasattr(self.fnode, 'tmp_fname'):
                    self.fname = self.fnode.tmp_fname
                    log('TFF: [%s] open() for write: existing file node lists %s' % (self.name, self.fname, ))
                else:
                    log('TFF: [%s] open() for write: existing file node lists no tmp_file, using new %s' % (self.name, self.fname, ))
                self.file = os.fdopen(os.open(self.fname, flags|os.O_CREAT, *mode), m)
                self.fd = self.file.fileno()
                log('TFF: opened(%s) for write' % self.fname)
                self.open_for_write = True
            else:
                # read
                assert self.fnode is not None
                uri = self.fnode.get_uri()

                # XXX make this go away
                if hasattr(self.fnode, 'tmp_fname'):
                    self.fname = self.fnode.tmp_fname
                    log('TFF: reopening(%s) for reading' % self.fname)
                else:
                    log('TFF: fetching file from cache for reading')
                    self.fname = self.tfs.cache.get_file(uri)

                self.file = os.fdopen(os.open(self.fname, flags, *mode), m)
                self.fd = self.file.fileno()
                self.open_for_write = False
                log('TFF: opened(%s) for read' % self.fname)
        except:
            log_exc()
            raise

    def log(self, msg):
        log("<TFF(%s:%s)> %s" % (os.path.basename(self.fname), self.name, msg))

    @logexc
    def read(self, size, offset):
        self.log('read(%r, %r)' % (size, offset, ))
        self.file.seek(offset)
        return self.file.read(size)

    @logexc
    def write(self, buf, offset):
        self.log("write(-%s-, %r)" % (len(buf), offset))
        if not self.open_for_write:
            return -errno.EACCES
        self.file.seek(offset)
        self.file.write(buf)
        return len(buf)

    @logexc
    def release(self, flags):
        self.log("release(%r)" % (flags,))
        self.file.close()
        if self.open_for_write:
            size = os.path.getsize(self.fname)
            self.fnode.size = size
            file_cap = self.tfs.upload(self.fname)
            self.fnode.ro_uri = file_cap
            # XXX [ ] TODO: set metadata
            # write new uri into parent dir entry
            self.parent.add_child(self.name, self.fnode, {})
            self.log("uploaded: %s" % (file_cap,))

        # dbg
        print_tree()

    def _fflush(self):
        if 'w' in self.file.mode or 'a' in self.file.mode:
            self.file.flush()

    @logexc
    def fsync(self, isfsyncfile):
        self.log("fsync(%r)" % (isfsyncfile,))
        self._fflush()
        if isfsyncfile and hasattr(os, 'fdatasync'):
            os.fdatasync(self.fd)
        else:
            os.fsync(self.fd)

    @logexc
    def flush(self):
        self.log("flush()")
        self._fflush()
        # cf. xmp_flush() in fusexmp_fh.c
        os.close(os.dup(self.fd))

    @logexc
    def fgetattr(self):
        self.log("fgetattr()")
        s = os.fstat(self.fd)
        self.log("fgetattr() -> %r" % (s,))
        return s

    @logexc
    def ftruncate(self, len):
        self.log("ftruncate(%r)" % (len,))
        self.file.truncate(len)

class TahoeFuse(fuse.Fuse):

    def __init__(self, tfs, *args, **kw):
        log("TF: __init__(%r, %r)" % (args, kw))

        self.tfs = tfs
        _tfs_ = tfs
        class MyFuseFile(TahoeFuseFile):
            tfs = _tfs_
        self.file_class = MyFuseFile
        log("TF: file_class: %r" % (self.file_class,))

        fuse.Fuse.__init__(self, *args, **kw)

        #import thread
        #thread.start_new_thread(self.launch_reactor, ())

    def log(self, msg):
        log("<TF> %s" % (msg, ))

    @logexc
    def readlink(self, path):
        self.log("readlink(%r)" % (path,))
        node = self.tfs.get_path(path)
        if node:
            raise EINVAL('Not a symlink') # nothing in tahoe is a symlink
        else:
            raise ENOENT('Invalid argument')

    @logexc
    def unlink(self, path):
        self.log("unlink(%r)" % (path,))
        self.tfs.unlink(path)

    @logexc
    def rmdir(self, path):
        self.log("rmdir(%r)" % (path,))
        self.tfs.unlink(path)

    @logexc
    def symlink(self, path, path1):
        self.log("symlink(%r, %r)" % (path, path1))
        self.tfs.link(path, path1)

    @logexc
    def rename(self, path, path1):
        self.log("rename(%r, %r)" % (path, path1))
        self.tfs.rename(path, path1)

    @logexc
    def link(self, path, path1):
        self.log("link(%r, %r)" % (path, path1))
        self.tfs.link(path, path1)

    @logexc
    def chmod(self, path, mode):
        self.log("XX chmod(%r, %r)" % (path, mode))
        #return -errno.EOPNOTSUPP

    @logexc
    def chown(self, path, user, group):
        self.log("XX chown(%r, %r, %r)" % (path, user, group))
        #return -errno.EOPNOTSUPP

    @logexc
    def truncate(self, path, len):
        self.log("XX truncate(%r, %r)" % (path, len))
        #return -errno.EOPNOTSUPP

    @logexc
    def utime(self, path, times):
        self.log("XX utime(%r, %r)" % (path, times))
        #return -errno.EOPNOTSUPP

    @logexc
    def statfs(self):
        self.log("statfs()")
        """
        Should return an object with statvfs attributes (f_bsize, f_frsize...).
        Eg., the return value of os.statvfs() is such a thing (since py 2.2).
        If you are not reusing an existing statvfs object, start with
        fuse.StatVFS(), and define the attributes.

        To provide usable information (ie., you want sensible df(1)
        output, you are suggested to specify the following attributes:

            - f_bsize - preferred size of file blocks, in bytes
            - f_frsize - fundamental size of file blcoks, in bytes
                [if you have no idea, use the same as blocksize]
            - f_blocks - total number of blocks in the filesystem
            - f_bfree - number of free blocks
            - f_files - total number of file inodes
            - f_ffree - nunber of free file inodes
        """

        block_size = 4096 # 4k
        preferred_block_size = 131072 # 128k, c.f. seg_size
        fs_size = 8*2**40 # 8Tb
        fs_free = 2*2**40 # 2Tb

        s = fuse.StatVfs(f_bsize = preferred_block_size,
                         f_frsize = block_size,
                         f_blocks = fs_size / block_size,
                         f_bfree = fs_free / block_size,
                         f_bavail = fs_free / block_size,
                         f_files = 2**30, # total files
                         f_ffree = 2**20, # available files
                         f_favail = 2**20, # available files (root)
                         f_flag = 2, # no suid
                         f_namemax = 255) # max name length
        return s

    def fsinit(self):
        self.log("fsinit()")

    def main(self, *a, **kw):
        self.log("main(%r, %r)" % (a, kw))

        return fuse.Fuse.main(self, *a, **kw)

    ##################################################################

    @logexc
    def readdir(self, path, offset):
        log('readdir(%r, %r)' % (path, offset))
        node = self.tfs.get_path(path)
        if node is None:
            return -errno.ENOENT
        dirlist = ['.', '..'] + node.children.keys()
        log('dirlist = %r' % (dirlist,))
        return [fuse.Direntry(d) for d in dirlist]

    @logexc
    def getattr(self, path):
        log('getattr(%r)' % (path,))

        if path == '/':
            # we don't have any metadata for the root (no edge leading to it)
            mode = (stat.S_IFDIR | 755)
            mtime = self.tfs.root.mtime
            s = TStat({}, st_mode=mode, st_nlink=1, st_mtime=mtime)
            log('getattr(%r) -> %r' % (path, s))
            return s
            
        parent, name, child = self.tfs.get_parent_name_and_child(path)
        if not child: # implicitly 'or not parent'
            raise ENOENT('No such file or directory')
        return parent.get_stat(name)

    @logexc
    def access(self, path, mode):
        self.log("access(%r, %r)" % (path, mode))
        node = self.tfs.get_path(path)
        if not node:
            return -errno.ENOENT
        accmode = os.O_RDONLY | os.O_WRONLY | os.O_RDWR
        if (mode & 0222):
            if not node.writable():
                log('write access denied for %s (req:%o)' % (path, mode, ))
                return -errno.EACCES
        #else:
            #log('access granted for %s' % (path, ))

    @logexc
    def mkdir(self, path, mode):
        self.log("mkdir(%r, %r)" % (path, mode))
        self.tfs.mkdir(path)

def launch_tahoe_fuse(tfs, argv):
    sys.argv = ['tahoe fuse'] + list(argv)
    log('setting sys.argv=%r' % (sys.argv,))
    config = TahoeFuseOptions()
    server = TahoeFuse(tfs, version="%prog " +VERSIONSTR+", fuse "+ fuse.__version__,
                       usage=config.getSynopsis(),
                       dash_s_do='setsingle')
    server.parse(errex=1)
    server.main()

def getnodeurl(nodedir):
    f = file(os.path.expanduser(os.path.join(nodedir, "node.url")), 'rb')
    nu = f.read().strip()
    f.close()
    if nu[-1] != "/":
        nu += "/"
    return nu

def fingerprint(uri):
    if uri is None:
        return None
    return base64.b32encode(sha.new(uri).digest()).lower()[:6]

class TStat(fuse.Stat):
    # in fuse 0.2, these are set by fuse.Stat.__init__
    # in fuse 0.2-pre3 (hardy) they are not. badness unsues if they're missing
    st_mode  = None
    st_ino   = 0
    st_dev   = 0
    st_nlink = None
    st_uid   = 0
    st_gid   = 0
    st_size  = 0
    st_atime = 0
    st_mtime = 0
    st_ctime = 0

    fields = [ 'st_mode', 'st_ino', 'st_dev', 'st_nlink', 'st_uid', 'st_gid', 'st_size',
               'st_atime', 'st_mtime', 'st_ctime', ]
    def __init__(self, metadata, **kwargs):
        # first load any stat fields present in 'metadata'
        for st in [ 'mtime', 'ctime' ]:
            if st in metadata:
                setattr(self, "st_%s" % st, metadata[st])
        for st in self.fields:
            if st in metadata:
                setattr(self, st, metadata[st])

        # then set any values passed in as kwargs
        fuse.Stat.__init__(self, **kwargs)

    def __repr__(self):
        d = {}
        for f in self.fields:
            d[f] = getattr(self, f, None)
        return "<Stat%r>" % (d,)

class Directory(object):
    def __init__(self, tfs, ro_uri, rw_uri):
        self.tfs = tfs
        self.ro_uri = ro_uri
        self.rw_uri = rw_uri
        assert (rw_uri or ro_uri)
        self.children = {}
        self.last_load = None
        self.last_data = None
        self.mtime = 0

    def __repr__(self):
        return "<Directory %s>" % (fingerprint(self.get_uri()),)

    def maybe_refresh(self, name=None):
        """
        if the previously cached data was retrieved within the cache
        validity period, does nothing. otherwise refetches the data
        for this directory and reloads itself
        """
        now = time.time()
        if self.last_load is None or (now - self.last_load) > self.tfs.cache_validity:
            self.load(name)

    def load(self, name=None):
        now = time.time()
        print 'loading', name or self
        log('%s.loading(%s)' % (self, name))
        url = self.tfs.compose_url("uri/%s?t=json", self.get_uri())
        data = urllib.urlopen(url).read()
        h = tagged_hash('cache_hash', data)
        if h == self.last_data:
            self.last_load = now
            log('%s.load() : no change h(data)=%s' % (self, base32.b2a(h), ))
            return
        try:
            parsed = simplejson.loads(data)
        except ValueError:
            log('%s.load(): unable to parse json data for dir:\n%r' % (self, data))
            return
        nodetype, d = parsed
        assert nodetype == 'dirnode'
        self.children.clear()
        for cname,details in d['children'].items():
            cname = unicode_to_utf8(cname)
            ctype, cattrs = details
            metadata = cattrs.get('metadata', {})
            if ctype == 'dirnode':
                cobj = self.tfs.dir_for(cname, cattrs.get('ro_uri'), cattrs.get('rw_uri'))
            else:
                assert ctype == "filenode"
                cobj = File(cattrs.get('size'), cattrs.get('ro_uri'))
            self.children[cname] = cobj, metadata
        self.last_load = now
        self.last_data = h
        self.mtime = now
        log('%s.load() loaded: \n%s' % (self, self.pprint(),))

    def get_children(self):
        return self.children.keys()

    def get_child(self, name):
        return self.children[name][0]

    def add_child(self, name, child, metadata):
        log('%s.add_child(%r, %r, %r)' % (self, name, child, metadata, ))
        self.children[name] = child, metadata
        url = self.tfs.compose_url("uri/%s/%s?t=uri", self.get_uri(), name)
        child_cap = do_http('PUT', url, child.get_uri())
        # XXX [ ] TODO: push metadata to tahoe node
        assert child_cap == child.get_uri()
        self.mtime = time.time()
        log('added child %r with %r to %r' % (name, child_cap, self))

    def remove_child(self, name):
        log('%s.remove_child(%r)' % (self, name, ))
        del self.children[name]
        url = self.tfs.compose_url("uri/%s/%s", self.get_uri(), name)
        resp = do_http('DELETE', url)
        self.mtime = time.time()
        log('child (%s) removal yielded %r' % (name, resp,))

    def get_uri(self):
        return self.rw_uri or self.ro_uri

    def writable(self):
        return self.rw_uri and self.rw_uri != self.ro_uri

    def pprint(self, prefix='', printed=None, suffix=''):
        ret = []
        if printed is None:
            printed = set()
        writable = self.writable() and '+' or ' '
        if self in printed:
            ret.append("         %s/%s ... <%s> : %s" % (prefix, writable, fingerprint(self.get_uri()), suffix, ))
        else:
            ret.append("[%s] %s/%s : %s" % (fingerprint(self.get_uri()), prefix, writable, suffix, ))
            printed.add(self)
            for name,(child,metadata) in sorted(self.children.items()):
                ret.append(child.pprint(' ' * (len(prefix)+1)+name, printed, repr(metadata)))
        return '\n'.join(ret)

    def get_metadata(self, name):
        return self.children[name][1]

    def get_stat(self, name):
        child,metadata = self.children[name]
        log("%s.get_stat(%s) md: %r" % (self, name, metadata))

        if isinstance(child, Directory):
            child.maybe_refresh(name)
            mode = metadata.get('st_mode') or (stat.S_IFDIR | 0755)
            s = TStat(metadata, st_mode=mode, st_nlink=1, st_mtime=child.mtime)
        else:
            if hasattr(child, 'tmp_fname'):
                s = os.stat(child.tmp_fname)
                log("%s.get_stat(%s) returning local stat of tmp file" % (self, name, ))
            else:
                s = TStat(metadata,
                          st_nlink = 1,
                          st_size = child.size,
                          st_mode = metadata.get('st_mode') or (stat.S_IFREG | 0444),
                          st_mtime = metadata.get('mtime') or self.mtime,
                          )
            return s

        log("%s.get_stat(%s)->%s" % (self, name, s))
        return s

class File(object):
    def __init__(self, size, ro_uri):
        self.size = size
        if ro_uri:
            ro_uri = str(ro_uri)
        self.ro_uri = ro_uri

    def __repr__(self):
        return "<File %s>" % (fingerprint(self.ro_uri) or [self.tmp_fname],)

    def pprint(self, prefix='', printed=None, suffix=''):
        return "         %s (%s) : %s" % (prefix, self.size, suffix, )

    def get_uri(self):
        return self.ro_uri

    def writable(self):
        return True

class TFS(object):
    def __init__(self, nodedir, nodeurl, root_uri, 
                       cache_validity_period=DEFAULT_DIRECTORY_VALIDITY):
        self.cache_validity = cache_validity_period
        self.nodeurl = nodeurl
        self.root_uri = root_uri
        self.dirs = {}

        cachedir = os.path.expanduser(os.path.join(nodedir, '_cache'))
        self.cache = FileCache(nodeurl, cachedir)
        ro_uri = NewDirectoryURI.init_from_string(self.root_uri).get_readonly()
        self.root = Directory(self, ro_uri, self.root_uri)
        self.root.maybe_refresh('<root>')

    def log(self, msg):
        log("<TFS> %s" % (msg, ))

    def pprint(self):
        return self.root.pprint()

    def compose_url(self, fmt, *args):
        return self.nodeurl + (fmt % tuple(map(urllib.quote, args)))

    def get_parent_name_and_child(self, path):
        """
        find the parent dir node, name of child relative to that parent, and
        child node within the TFS object space.
        @returns: (parent, name, child) if the child is found
                  (parent, name, None) if the child is missing from the parent
                  (None, name, None) if the parent is not found
        """
        if path == '/':
            return 
        dirname, name = os.path.split(path)
        parent = self.get_path(dirname)
        if parent:
            try:
                child = parent.get_child(name)
                return parent, name, child
            except KeyError:
                return parent, name, None
        else:
            return None, name, None

    def get_path(self, path):
        comps = path.strip('/').split('/')
        if comps == ['']:
            comps = []
        cursor = self.root
        c_name = '<root>'
        for comp in comps:
            if not isinstance(cursor, Directory):
                self.log('path "%s" is not a dir' % (path,))
                return None
            cursor.maybe_refresh(c_name)
            try:
                cursor = cursor.get_child(comp)
                c_name = comp
            except KeyError:
                self.log('path "%s" not found' % (path,))
                return None
        if isinstance(cursor, Directory):
            cursor.maybe_refresh(c_name)
        return cursor

    def dir_for(self, name, ro_uri, rw_uri):
        #self.log('dir_for(%s) [%s/%s]' % (name, fingerprint(ro_uri), fingerprint(rw_uri)))
        if ro_uri:
            ro_uri = str(ro_uri)
        if rw_uri:
            rw_uri = str(rw_uri)
        uri = rw_uri or ro_uri
        assert uri
        dirobj = self.dirs.get(uri)
        if not dirobj:
            self.log('dir_for(%s) creating new Directory' % (name, ))
            dirobj = Directory(self, ro_uri, rw_uri)
            self.dirs[uri] = dirobj
        return dirobj

    def upload(self, fname):
        self.log('upload(%r)' % (fname,))
        fh = file(fname, 'rb')
        url = self.compose_url("uri")
        file_cap = do_http('PUT', url, fh)
        self.log('uploaded to: %r' % (file_cap,))
        return file_cap

    def mkdir(self, path):
        self.log('mkdir(%r)' % (path,))
        parent, name, child = self.get_parent_name_and_child(path)

        if child:
            raise EEXIST('File exists: %s' % (name,))
        if not parent:
            raise ENOENT('No such file or directory: %s' % (path,))

        url = self.compose_url("uri?t=mkdir")
        new_dir_cap = do_http('PUT', url)

        ro_uri = NewDirectoryURI.init_from_string(new_dir_cap).get_readonly()
        child = Directory(self, ro_uri, new_dir_cap)
        parent.add_child(name, child, {})

    def rename(self, path, path1):
        self.log('rename(%s, %s)' % (path, path1))
        src_parent, src_name, src_child = self.get_parent_name_and_child(path)
        dst_parent, dst_name, dst_child = self.get_parent_name_and_child(path1)

        if not src_child or not dst_parent:
            raise ENOENT('No such file or directory')

        dst_parent.add_child(dst_name, src_child, {})
        src_parent.remove_child(src_name)

    def unlink(self, path):
        parent, name, child = self.get_parent_name_and_child(path)

        if child is None: # parent or child is missing
            raise ENOENT('No such file or directory')
        if not parent.writable():
            raise EACCESS('Permission denied')

        parent.remove_child(name)

    def link(self, path, path1):
        src = self.get_path(path)
        dst_parent, dst_name, dst_child = self.get_parent_name_and_child(path1)

        if not src:
            raise ENOENT('No such file or directory')
        if dst_parent is None:
            raise ENOENT('No such file or directory')
        if not dst_parent.writable():
            raise EACCESS('Permission denied')

        dst_parent.add_child(dst_name, src, {})

class FileCache(object):
    def __init__(self, nodeurl, cachedir):
        self.nodeurl = nodeurl
        self.cachedir = cachedir
        if not os.path.exists(self.cachedir):
            os.makedirs(self.cachedir)
        self.tmpdir = os.path.join(self.cachedir, 'tmp')
        if not os.path.exists(self.tmpdir):
            os.makedirs(self.tmpdir)

    def log(self, msg):
        log("<FC> %s" % (msg, ))

    def get_file(self, uri):
        self.log('get_file(%s)' % (uri,))
        if uri.startswith("URI:LIT"):
            return self.get_literal(uri)
        else:
            return self.get_chk(uri)

    def get_literal(self, uri):
        h = sha.new(uri).digest()
        u = LiteralFileURI.init_from_string(uri)
        fname = os.path.join(self.cachedir, '__'+base64.b32encode(h).lower())
        size = len(u.data)
        self.log('writing literal file %s (%s)' % (fname, size, ))
        fh = open(fname, 'wb')
        fh.write(u.data)
        fh.close()
        return fname

    def get_chk(self, uri):
        u = CHKFileURI.init_from_string(str(uri))
        storage_index = u.storage_index
        size = u.size
        fname = os.path.join(self.cachedir, base64.b32encode(storage_index).lower())
        if os.path.exists(fname):
            fsize = os.path.getsize(fname)
            if fsize == size:
                return fname
            else:
                self.log('warning file "%s" is too short %s < %s' % (fname, fsize, size))
        self.log('downloading file %s (%s)' % (fname, size, ))
        fh = open(fname, 'wb')
        url = "%suri/%s" % (self.nodeurl, uri)
        download = urllib.urlopen(''.join([ self.nodeurl, "uri/", uri ]))
        while True:
            chunk = download.read(4096)
            if not chunk:
                break
            fh.write(chunk)
        fh.close()
        return fname

    def tmp_file(self, id):
        fname = os.path.join(self.tmpdir, base64.b32encode(id).lower())
        return fname

_tfs = None # to appease pyflakes; is set in main()
def print_tree():
    log('tree:\n' + _tfs.pprint())

def main(argv):
    log("\n\nmain(%s)" % (argv,))

    if not argv:
        argv = ['--help']
    if len(argv) == 1 and argv[0] in ['-h', '--help', '--version']:
        config = TahoeFuseOptions()
        print >> sys.stderr, config
        print >> sys.stderr, 'fuse usage follows:'
        launch_tahoe_fuse(None, argv)
        return -2

    config = TahoeFuseOptions()
    try:
        #print 'parsing', argv
        config.parseOptions(argv)
    except usage.error, e:
        print config
        print e
        return -1

    if config['alias']:
        alias = config['alias']
        #print 'looking for aliases in', config['node-directory']
        aliases = get_aliases(os.path.expanduser(config['node-directory']))
        if alias not in aliases:
            raise usage.error('Alias %r not found' % (alias,))
        root_uri = aliases[alias]
    elif config['root-uri']:
        root_uri = config['root-uri']
        alias = 'root-uri'
        # test the uri for structural validity:
        try:
            NewDirectoryURI.init_from_string(root_uri)
        except:
            raise usage.error('root-uri must be a valid directory uri (not %r)' % (root_uri,))
    else:
        raise usage.error('At least one of --alias or --root-uri must be specified')


    nodedir = config['node-directory']
    nodeurl = config['node-url']
    if not nodeurl:
        nodeurl = getnodeurl(nodedir)

    # switch to named log file.
    global logfile
    fname = 'tfuse.%s.log' % (alias,)
    log('switching to %s' % (fname,))
    logfile.close()
    logfile = file(fname, 'ab')
    log('\n'+(24*'_')+'init'+(24*'_')+'\n')

    if not os.path.exists(config.mountpoint):
        raise OSError(2, 'No such file or directory: "%s"' % (config.mountpoint,))

    cache_timeout = float(config['cache-timeout'])
    tfs = TFS(nodedir, nodeurl, root_uri, cache_timeout)
    print tfs.pprint()

    # make tfs instance accesible to print_tree() for dbg
    global _tfs
    _tfs = tfs

    args = [ '-o'+opt for opt in config.fuse_options ] + [config.mountpoint]
    launch_tahoe_fuse(tfs, args)

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
