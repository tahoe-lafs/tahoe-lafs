#!/usr/bin/env python

#-----------------------------------------------------------------------------------------------
from allmydata.uri import CHKFileURI, NewDirectoryURI, LiteralFileURI
from allmydata.scripts.common_http import do_http as do_http_req
from allmydata.util.hashutil import tagged_hash
from allmydata.util.assertutil import precondition
from allmydata.util import base32, fileutil, observer
from allmydata.scripts.common import get_aliases

from twisted.python import usage
from twisted.python.failure import Failure
from twisted.internet.protocol import Factory, Protocol
from twisted.internet import reactor, defer, task
from twisted.web import client

import base64
import errno
import heapq
import sha
import socket
import stat
import subprocess
import sys
import os
import weakref
#import pprint

# one needs either python-fuse to have been installed in sys.path, or
# suitable affordances to be made in the build or runtime environment
import fuse

import time
import traceback
import simplejson
import urllib

VERSIONSTR="0.7"

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
         "URL of the tahoe node to use, a URL like \"http://127.0.0.1:3456\". "
         "This overrides the URL found in the --node-directory ."],
        ["alias", None, None,
         "Which alias should be mounted."],
        ["root-uri", None, None,
         "Which root directory uri should be mounted."],
        ["cache-timeout", None, 20,
         "Time, in seconds, to cache directory data."],
        ]
    optFlags = [
        ['no-split', None,
         'run stand-alone; no splitting into client and server'],
        ['server', None,
         'server mode (should not be used by end users)'],
        ['server-shutdown', None,
         'shutdown server (should not be used by end users)'],
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

def reopen_logfile(fname):
    global logfile
    log('switching to %s' % (fname,))
    logfile.close()
    logfile = file(fname, 'ab')

def log(msg):
    logfile.write("%s: %s\n" % (time.asctime(), msg))
    #time.sleep(0.1)
    logfile.flush()

fuse.flog = log

def unicode_to_utf8_or_str(u):
    if isinstance(u, unicode):
        return u.encode('utf-8')
    else:
        precondition(isinstance(u, str), repr(u))
        return u

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

class EIO(TFSIOError):
    def __init__(self, msg):
        TFSIOError.__init__(self, errno.EIO, msg)

def logargsretexc(meth):
    def inner_logargsretexc(self, *args, **kwargs):
        log("%s(%r, %r)" % (meth, args, kwargs))
        try:
            ret = meth(self, *args, **kwargs)
        except:
            log('exception:\n%s' % (traceback.format_exc(),))
            raise
        log("ret: %r" % (ret, ))
        return ret
    inner_logargsretexc.__name__ = '<logwrap(%s)>' % (meth,)
    return inner_logargsretexc

def logexc(meth):
    def inner_logexc(self, *args, **kwargs):
        try:
            ret = meth(self, *args, **kwargs)
        except TFSIOError, tie:
            log('error: %s' % (tie,))
            raise
        except:
            log('exception:\n%s' % (traceback.format_exc(),))
            raise
        return ret
    inner_logexc.__name__ = '<logwrap(%s)>' % (meth,)
    return inner_logexc

def log_exc():
    log('exception:\n%s' % (traceback.format_exc(),))

def repr_mode(mode=None):
    if mode is None:
        return 'none'
    fields = ['S_ENFMT', 'S_IFBLK', 'S_IFCHR', 'S_IFDIR', 'S_IFIFO', 'S_IFLNK', 'S_IFREG', 'S_IFSOCK', 'S_IRGRP', 'S_IROTH', 'S_IRUSR', 'S_IRWXG', 'S_IRWXO', 'S_IRWXU', 'S_ISGID', 'S_ISUID', 'S_ISVTX', 'S_IWGRP', 'S_IWOTH', 'S_IWUSR', 'S_IXGRP', 'S_IXOTH', 'S_IXUSR']
    ret = []
    for field in fields:
        fval = getattr(stat, field)
        if (mode & fval) == fval:
            ret.append(field)
    return '|'.join(ret)

def repr_flags(flags=None):
    if flags is None:
        return 'none'
    fields = [ 'O_APPEND', 'O_CREAT', 'O_DIRECT', 'O_DIRECTORY', 'O_EXCL', 'O_EXLOCK',
               'O_LARGEFILE', 'O_NDELAY', 'O_NOCTTY', 'O_NOFOLLOW', 'O_NONBLOCK', 'O_RDWR',
               'O_SHLOCK', 'O_SYNC', 'O_TRUNC', 'O_WRONLY', ]
    ret = []
    for field in fields:
        fval = getattr(os, field, None)
        if fval is not None and (flags & fval) == fval:
            ret.append(field)
    if not ret:
        ret = ['O_RDONLY']
    return '|'.join(ret)

class DownloaderWithReadQueue(object):
    def __init__(self):
        self.read_heap = []
        self.dest_file_name = None
        self.running = False
        self.done_observer = observer.OneShotObserverList()

    def __repr__(self):
        name = self.dest_file_name is None and '<none>' or os.path.basename(self.dest_file_name)
        return "<DWRQ(%s)> q(%s)" % (name, len(self.read_heap or []))

    def log(self, msg):
        log("%r: %s" % (self, msg))

    @logexc
    def start(self, url, dest_file_name, target_size, interval=0.5):
        self.log('start(%s, %s, %s)' % (url, dest_file_name, target_size, ))
        self.dest_file_name = dest_file_name
        file(self.dest_file_name, 'wb').close() # touch
        self.target_size = target_size
        self.log('start()')
        self.loop = task.LoopingCall(self._check_file_size)
        self.loop.start(interval)
        self.running = True
        d = client.downloadPage(url, self.dest_file_name)
        d.addCallbacks(self.done, self.fail)
        return d

    def when_done(self):
        return self.done_observer.when_fired()

    def get_size(self):
        if os.path.exists(self.dest_file_name):
            return os.path.getsize(self.dest_file_name)
        else:
            return 0

    @logexc
    def _read(self, posn, size):
        #self.log('_read(%s, %s)' % (posn, size))
        f = file(self.dest_file_name, 'rb')
        f.seek(posn)
        data = f.read(size)
        f.close()
        return data

    @logexc
    def read(self, posn, size):
        self.log('read(%s, %s)' % (posn, size))
        if self.read_heap is None:
            raise ValueError('read() called when already shut down')
        if posn+size > self.target_size:
            size -= self.target_size - posn
        fsize = self.get_size()
        if posn+size < fsize:
            return defer.succeed(self._read(posn, size))
        else:
            d = defer.Deferred()
            dread = (posn+size, posn, d)
            heapq.heappush(self.read_heap, dread)
        return d

    @logexc
    def _check_file_size(self):
        #self.log('_check_file_size()')
        if self.read_heap:
            try:
                size = self.get_size()
                while self.read_heap and self.read_heap[0][0] <= size:
                    end, start, d = heapq.heappop(self.read_heap)
                    data = self._read(start, end-start)
                    d.callback(data)
            except Exception, e:
                log_exc()
                failure = Failure()

    @logexc
    def fail(self, failure):
        self.log('fail(%s)' % (failure,))
        self.running = False
        if self.loop.running:
            self.loop.stop()
        # fail any reads still pending
        for end, start, d in self.read_heap:
            reactor.callLater(0, d.errback, failure)
        self.read_heap = None
        self.done_observer.fire_if_not_fired(failure)
        return failure

    @logexc
    def done(self, result):
        self.log('done()')
        self.running = False
        if self.loop.running:
            self.loop.stop()
        precondition(self.get_size() == self.target_size, self.get_size(), self.target_size)
        self._check_file_size() # process anything left pending in heap
        precondition(not self.read_heap, self.read_heap, self.target_size, self.get_size())
        self.read_heap = None
        self.done_observer.fire_if_not_fired(self)
        return result


class TahoeFuseFile(object):

    #def __init__(self, path, flags, *mode):
    def __init__(self, tfs, path, flags, *mode):
        log("TFF: __init__(%r, %r:%s, %r:%s)" % (path, flags, repr_flags(flags), mode, repr_mode(*mode)))
        self.tfs = tfs
        self.downloader = None

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
                if mode != (0600,):
                    log('TFF: [%s] changing mode %s(%s) to 0600' % (self.name, repr_mode(*mode), mode))
                    mode = (0600,)
                log('TFF: [%s] opening(%s) with flags %s(%s), mode %s(%s)' % (self.name, self.fname, repr_flags(flags|os.O_CREAT), flags|os.O_CREAT, repr_mode(*mode), mode))
                #self.file = os.fdopen(os.open(self.fname, flags|os.O_CREAT, *mode), m)
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
                    if uri.startswith("URI:LIT") or not self.tfs.async:
                        log('TFF: synchronously fetching file from cache for reading')
                        self.fname = self.tfs.cache.get_file(uri)
                    else:
                        log('TFF: asynchronously fetching file from cache for reading')
                        self.fname, self.downloader = self.tfs.cache.async_get_file(uri)
                        # downloader is None if the cache already contains the file
                        if self.downloader is not None:
                            d = self.downloader.when_done()
                            def download_complete(junk):
                                # once the download is complete, revert to non-async behaviour
                                self.downloader = None
                            d.addCallback(download_complete)

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
        if self.downloader:
            # then we're busy doing an async download
            # (and hence implicitly, we're in an environment that supports twisted)
            #self.log('passing read() to %s' % (self.downloader, ))
            d = self.downloader.read(offset, size)
            def thunk(failure):
                raise EIO(str(failure))
            d.addErrback(thunk)
            return d
        else:
            self.log('servicing read() from %s' % (self.file, ))
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
        d = stat_to_dict(s)
        if self.downloader:
            size = self.downloader.target_size
            self.log("fgetattr() during async download, cache file: %s, size=%s" % (s, size))
            d['st_size'] = size
        self.log("fgetattr() -> %r" % (d,))
        return d

    @logexc
    def ftruncate(self, len):
        self.log("ftruncate(%r)" % (len,))
        self.file.truncate(len)

class TahoeFuseBase(object):

    def __init__(self, tfs):
        log("TFB: __init__()")
        self.tfs = tfs
        self.files = {}

    def log(self, msg):
        log("<TFB> %s" % (msg, ))

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

        #s = fuse.StatVfs(f_bsize = preferred_block_size,
        s = dict(f_bsize = preferred_block_size,
                         f_frsize = block_size,
                         f_blocks = fs_size / block_size,
                         f_bfree = fs_free / block_size,
                         f_bavail = fs_free / block_size,
                         f_files = 2**30, # total files
                         f_ffree = 2**20, # available files
                         f_favail = 2**20, # available files (root)
                         f_flag = 2, # no suid
                         f_namemax = 255) # max name length
        #self.log('statfs(): %r' % (s,))
        return s

    def fsinit(self):
        self.log("fsinit()")

    ##################################################################

    @logexc
    def readdir(self, path, offset):
        self.log('readdir(%r, %r)' % (path, offset))
        node = self.tfs.get_path(path)
        if node is None:
            return -errno.ENOENT
        dirlist = ['.', '..'] + node.children.keys()
        self.log('dirlist = %r' % (dirlist,))
        #return [fuse.Direntry(d) for d in dirlist]
        return dirlist

    @logexc
    def getattr(self, path):
        self.log('getattr(%r)' % (path,))

        if path == '/':
            # we don't have any metadata for the root (no edge leading to it)
            mode = (stat.S_IFDIR | 755)
            mtime = self.tfs.root.mtime
            s = TStat({}, st_mode=mode, st_nlink=1, st_mtime=mtime)
            self.log('getattr(%r) -> %r' % (path, s))
            #return s
            return stat_to_dict(s)
            
        parent, name, child = self.tfs.get_parent_name_and_child(path)
        if not child: # implicitly 'or not parent'
            raise ENOENT('No such file or directory')
        return stat_to_dict(parent.get_stat(name))

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

    ##################################################################
    # file methods

    def open(self, path, flags):
        self.log('open(%r, %r)' % (path, flags, ))
        if path in self.files:
            # XXX todo [ ] should consider concurrent open files of differing modes
            return
        else:
            tffobj = TahoeFuseFile(self.tfs, path, flags)
            self.files[path] = tffobj

    def create(self, path, flags, mode):
        self.log('create(%r, %r, %r)' % (path, flags, mode))
        if path in self.files:
            # XXX todo [ ] should consider concurrent open files of differing modes
            return
        else:
            tffobj = TahoeFuseFile(self.tfs, path, flags, mode)
            self.files[path] = tffobj

    def _get_file(self, path):
        if not path in self.files:
            raise ENOENT('No such file or directory: %s' % (path,))
        return self.files[path]

    ##

    def read(self, path, size, offset):
        self.log('read(%r, %r, %r)' % (path, size, offset, ))
        return self._get_file(path).read(size, offset)

    @logexc
    def write(self, path, buf, offset):
        self.log("write(%r, -%s-, %r)" % (path, len(buf), offset))
        return self._get_file(path).write(buf, offset)

    @logexc
    def release(self, path, flags):
        self.log("release(%r, %r)" % (path, flags,))
        self._get_file(path).release(flags)
        del self.files[path]

    @logexc
    def fsync(self, path, isfsyncfile):
        self.log("fsync(%r, %r)" % (path, isfsyncfile,))
        return self._get_file(path).fsync(isfsyncfile)

    @logexc
    def flush(self, path):
        self.log("flush(%r)" % (path,))
        return self._get_file(path).flush()

    @logexc
    def fgetattr(self, path):
        self.log("fgetattr(%r)" % (path,))
        return self._get_file(path).fgetattr()

    @logexc
    def ftruncate(self, path, len):
        self.log("ftruncate(%r, %r)" % (path, len,))
        return self._get_file(path).ftruncate(len)

class TahoeFuseLocal(TahoeFuseBase, fuse.Fuse):
    def __init__(self, tfs, *args, **kw):
        log("TFL: __init__(%r, %r)" % (args, kw))
        TahoeFuseBase.__init__(self, tfs)
        fuse.Fuse.__init__(self, *args, **kw)

    def log(self, msg):
        log("<TFL> %s" % (msg, ))

    def main(self, *a, **kw):
        self.log("main(%r, %r)" % (a, kw))
        return fuse.Fuse.main(self, *a, **kw)

    # overrides for those methods which return objects not marshalled
    def fgetattr(self, path):
        return TStat({}, **(TahoeFuseBase.fgetattr(self, path)))

    def getattr(self, path):
        return TStat({}, **(TahoeFuseBase.getattr(self, path)))

    def statfs(self):
        return fuse.StatVfs(**(TahoeFuseBase.statfs(self)))
        #self.log('statfs()')
        #ret = fuse.StatVfs(**(TahoeFuseBase.statfs(self)))
        #self.log('statfs(): %r' % (ret,))
        #return ret

    @logexc
    def readdir(self, path, offset):
        return [ fuse.Direntry(d) for d in TahoeFuseBase.readdir(self, path, offset) ]

class TahoeFuseShim(fuse.Fuse):
    def __init__(self, trpc, *args, **kw):
        log("TF: __init__(%r, %r)" % (args, kw))
        self.trpc = trpc
        fuse.Fuse.__init__(self, *args, **kw)

    def log(self, msg):
        log("<TFs> %s" % (msg, ))

    @logexc
    def readlink(self, path):
        self.log("readlink(%r)" % (path,))
        return self.trpc.call('readlink', path)

    @logexc
    def unlink(self, path):
        self.log("unlink(%r)" % (path,))
        return self.trpc.call('unlink', path)

    @logexc
    def rmdir(self, path):
        self.log("rmdir(%r)" % (path,))
        return self.trpc.call('unlink', path)

    @logexc
    def symlink(self, path, path1):
        self.log("symlink(%r, %r)" % (path, path1))
        return self.trpc.call('link', path, path1)

    @logexc
    def rename(self, path, path1):
        self.log("rename(%r, %r)" % (path, path1))
        return self.trpc.call('rename', path, path1)

    @logexc
    def link(self, path, path1):
        self.log("link(%r, %r)" % (path, path1))
        return self.trpc.call('link', path, path1)

    @logexc
    def chmod(self, path, mode):
        self.log("XX chmod(%r, %r)" % (path, mode))
        return self.trpc.call('chmod', path, mode)

    @logexc
    def chown(self, path, user, group):
        self.log("XX chown(%r, %r, %r)" % (path, user, group))
        return self.trpc.call('chown', path, user, group)

    @logexc
    def truncate(self, path, len):
        self.log("XX truncate(%r, %r)" % (path, len))
        return self.trpc.call('truncate', path, len)

    @logexc
    def utime(self, path, times):
        self.log("XX utime(%r, %r)" % (path, times))
        return self.trpc.call('utime', path, times)

    @logexc
    def statfs(self):
        self.log("statfs()")
        response = self.trpc.call('statfs')
        #self.log("statfs(): %r" % (response,))
        kwargs = dict([ (str(k),v) for k,v in response.items() ])
        return fuse.StatVfs(**kwargs)

    def fsinit(self):
        self.log("fsinit()")

    def main(self, *a, **kw):
        self.log("main(%r, %r)" % (a, kw))

        return fuse.Fuse.main(self, *a, **kw)

    ##################################################################

    @logexc
    def readdir(self, path, offset):
        self.log('readdir(%r, %r)' % (path, offset))
        return [ fuse.Direntry(d) for d in self.trpc.call('readdir', path, offset) ]

    @logexc
    def getattr(self, path):
        self.log('getattr(%r)' % (path,))
        response = self.trpc.call('getattr', path)
        kwargs = dict([ (str(k),v) for k,v in response.items() ])
        s = TStat({}, **kwargs)
        self.log('getattr(%r) -> %r' % (path, s))
        return s

    @logexc
    def access(self, path, mode):
        self.log("access(%r, %r)" % (path, mode))
        return self.trpc.call('access', path, mode)

    @logexc
    def mkdir(self, path, mode):
        self.log("mkdir(%r, %r)" % (path, mode))
        return self.trpc.call('mkdir', path, mode)

    ##################################################################
    # file methods

    def open(self, path, flags):
        self.log('open(%r, %r)' % (path, flags, ))
        return self.trpc.call('open', path, flags)

    def create(self, path, flags, mode):
        self.log('create(%r, %r, %r)' % (path, flags, mode))
        return self.trpc.call('create', path, flags, mode)

    ##

    def read(self, path, size, offset):
        self.log('read(%r, %r, %r)' % (path, size, offset, ))
        return self.trpc.call('read', path, size, offset)

    @logexc
    def write(self, path, buf, offset):
        self.log("write(%r, -%s-, %r)" % (path, len(buf), offset))
        return self.trpc.call('write', path, buf, offset)

    @logexc
    def release(self, path, flags):
        self.log("release(%r, %r)" % (path, flags,))
        return self.trpc.call('release', path, flags)

    @logexc
    def fsync(self, path, isfsyncfile):
        self.log("fsync(%r, %r)" % (path, isfsyncfile,))
        return self.trpc.call('fsync', path, isfsyncfile)

    @logexc
    def flush(self, path):
        self.log("flush(%r)" % (path,))
        return self.trpc.call('flush', path)

    @logexc
    def fgetattr(self, path):
        self.log("fgetattr(%r)" % (path,))
        #return self.trpc.call('fgetattr', path)
        response = self.trpc.call('fgetattr', path)
        kwargs = dict([ (str(k),v) for k,v in response.items() ])
        s = TStat({}, **kwargs)
        self.log('getattr(%r) -> %r' % (path, s))
        return s

    @logexc
    def ftruncate(self, path, len):
        self.log("ftruncate(%r, %r)" % (path, len,))
        return self.trpc.call('ftruncate', path, len)


def launch_tahoe_fuse(tf_class, tobj, argv):
    sys.argv = ['tahoe fuse'] + list(argv)
    log('setting sys.argv=%r' % (sys.argv,))
    config = TahoeFuseOptions()
    version = "%prog " +VERSIONSTR+", fuse "+ fuse.__version__
    server = tf_class(tobj, version=version, usage=config.getSynopsis(), dash_s_do='setsingle')
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

stat_fields = [ 'st_mode', 'st_ino', 'st_dev', 'st_nlink', 'st_uid', 'st_gid', 'st_size',
                'st_atime', 'st_mtime', 'st_ctime', ]
def stat_to_dict(statobj, fields=None):
    if fields is None:
        fields = stat_fields
    d = {}
    for f in fields:
        d[f] = getattr(statobj, f, None)
    return d

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
        return "<Stat%r>" % (stat_to_dict(self),)

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
            cname = unicode_to_utf8_or_str(cname)
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
                       cache_validity_period=DEFAULT_DIRECTORY_VALIDITY, async=False):
        self.cache_validity = cache_validity_period
        self.nodeurl = nodeurl
        self.root_uri = root_uri
        self.async = async
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
        self.downloaders = weakref.WeakValueDictionary()

    def log(self, msg):
        log("<FC> %s" % (msg, ))

    def get_file(self, uri):
        self.log('get_file(%s)' % (uri,))
        if uri.startswith("URI:LIT"):
            return self.get_literal(uri)
        else:
            return self.get_chk(uri, async=False)

    def async_get_file(self, uri):
        self.log('get_file(%s)' % (uri,))
        return self.get_chk(uri, async=True)

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

    def get_chk(self, uri, async=False):
        u = CHKFileURI.init_from_string(str(uri))
        storage_index = u.storage_index
        size = u.size
        fname = os.path.join(self.cachedir, base64.b32encode(storage_index).lower())
        if os.path.exists(fname):
            fsize = os.path.getsize(fname)
            if fsize == size:
                if async:
                    return fname, None
                else:
                    return fname
            else:
                self.log('warning file "%s" is too short %s < %s' % (fname, fsize, size))
        self.log('downloading file %s (%s)' % (fname, size, ))
        url = "%suri/%s" % (self.nodeurl, uri)
        if async:
            if fname in self.downloaders and self.downloaders[fname].running:
                downloader = self.downloaders[fname]
            else:
                downloader = DownloaderWithReadQueue()
                self.downloaders[fname] = downloader
                d = downloader.start(url, fname, target_size=u.size)
                def clear_downloader(result, fname):
                    self.log('clearing %s from downloaders: %r' % (fname, result))
                    self.downloaders.pop(fname, None)
                d.addBoth(clear_downloader, fname)
            return fname, downloader
        else:
            fh = open(fname, 'wb')
            download = urllib.urlopen(url)
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


def unmarshal(obj):
    if obj is None or isinstance(obj, int) or isinstance(obj, long) or isinstance(obj, float):
        return obj
    elif isinstance(obj, unicode) or isinstance(obj, str):
        #log('unmarshal(%r)' % (obj,))
        return base64.b64decode(obj)
    elif isinstance(obj, list):
        return map(unmarshal, obj)
    elif isinstance(obj, dict):
        return dict([ (k,unmarshal(v)) for k,v in obj.items() ])
    else:
        raise ValueError('object type not int,str,list,dict,none (%s) (%r)' % (type(obj), obj))

def marshal(obj):
    if obj is None or isinstance(obj, int) or isinstance(obj, long) or isinstance(obj, float):
        return obj
    elif isinstance(obj, str):
        return base64.b64encode(obj)
    elif isinstance(obj, list) or isinstance(obj, tuple):
        return map(marshal, obj)
    elif isinstance(obj, dict):
        return dict([ (k,marshal(v)) for k,v in obj.items() ])
    else:
        raise ValueError('object type not int,str,list,dict,none (%s)' % type(obj))


class TRPCProtocol(Protocol):
    compute_response_sha1 = True
    log_all_requests = False

    def connectionMade(self):
        self.buf = []

    def dataReceived(self, data):
        if data == 'keepalive\n':
            log('keepalive connection on %r' % (self.transport,))
            self.keepalive = True
            return

        if not data.endswith('\n'):
            self.buf.append(data)
            return
        if self.buf:
            self.buf.append(data)
            reqstr = ''.join(self.buf)
            self.buf = []
            self.dispatch_request(reqstr)
        else:
            self.dispatch_request(data)

    def dispatch_request(self, reqstr):
        try:
            req = simplejson.loads(reqstr)
        except ValueError, ve:
            log(ve)
            return

        d = defer.maybeDeferred(self.handle_request, req)
        d.addCallback(self.send_response)
        d.addErrback(self.send_error)

    def send_error(self, failure):
        log('failure: %s' % (failure,))
        if failure.check(TFSIOError):
            e = failure.value
            self.send_response(['error', 'errno', e.args[0], e.args[1]])
        else:
            self.send_response(['error', 'failure', str(failure)])

    def send_response(self, result):
        response = simplejson.dumps(result)
        header = { 'len': len(response), }
        if self.compute_response_sha1:
            header['sha1'] = base64.b64encode(sha.new(response).digest())
        hdr = simplejson.dumps(header)
        self.transport.write(hdr)
        self.transport.write('\n')
        self.transport.write(response)
        self.transport.loseConnection()

    def connectionLost(self, reason):
        if hasattr(self, 'keepalive'):
            log('keepalive connection %r lost, shutting down' % (self.transport,))
            reactor.callLater(0, reactor.stop)

    def handle_request(self, req):
        if type(req) is not list or not req or len(req) < 1:
            return ['error', 'malformed request']
        if req[0] == 'call':
            if len(req) < 3:
                return ['error', 'malformed request']
            methname = req[1]
            try:
                args = unmarshal(req[2])
            except ValueError, ve:
                return ['error', 'malformed arguments', str(ve)]

            try:
                meth = getattr(self.factory.server, methname)
            except AttributeError, ae:
                return ['error', 'no such method', str(ae)]

            if self.log_all_requests:
                log('call %s(%s)' % (methname, ', '.join(map(repr, args))))
            try:
                result = meth(*args)
            except TFSIOError, e:
                log('errno: %s; %s' % e.args)
                return ['error', 'errno', e.args[0], e.args[1]]
            except Exception, e:
                log('exception: ' + traceback.format_exc())
                return ['error', 'exception', str(e)]
            d = defer.succeed(None)
            d.addCallback(lambda junk: result) # result may be Deferred
            d.addCallback(lambda res: ['result', marshal(res)]) # only applies if not errback
            return d

class TFSServer(object):
    def __init__(self, socket_path, server=None):
        self.socket_path = socket_path
        log('TFSServer init socket: %s' % (socket_path,))

        self.factory = Factory()
        self.factory.protocol = TRPCProtocol
        if server:
            self.factory.server = server
        else:
            self.factory.server = self

    def get_service(self):
        if not hasattr(self, 'svc'):
            from twisted.application import strports
            self.svc = strports.service('unix:'+self.socket_path, self.factory)
        return self.svc

    def run(self):
        svc = self.get_service()
        def ss():
            try:
                svc.startService()
            except:
                reactor.callLater(0, reactor.stop)
                raise
        reactor.callLater(0, ss)
        reactor.run()

    def hello(self):
        return 'pleased to meet you'

    def echo(self, arg):
        return arg

    def failex(self):
        raise ValueError('expected')

    def fail(self):
        return defer.maybeDeferred(self.failex)

class RPCError(RuntimeError):
    pass

class TRPC(object):
    def __init__(self, socket_fname):
        self.socket_fname = socket_fname
        self.keepalive = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.keepalive.connect(self.socket_fname)
        self.keepalive.send('keepalive\n')
        log('requested keepalive on %s' % (self.keepalive,))

    def req(self, req):
        # open conenction to trpc server
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.socket_fname)
        # send request
        s.send(simplejson.dumps(req))
        s.send('\n')
        # read response header
        hdr_data = s.recv(8192)
        first_newline = hdr_data.index('\n')
        header = hdr_data[:first_newline]
        data = hdr_data[first_newline+1:]
        hdr = simplejson.loads(header)
        hdr_len = hdr['len']
        if hdr.has_key('sha1'):
            hdr_sha1 = base64.b64decode(hdr['sha1'])
            spool = [data]
            spool_sha = sha.new(data)
            # spool response
            while True:
                data = s.recv(8192)
                if data:
                    spool.append(data)
                    spool_sha.update(data)
                else:
                    break
        else:
            spool = [data]
            # spool response
            while True:
                data = s.recv(8192)
                if data:
                    spool.append(data)
                else:
                    break
        s.close()
        # decode response
        resp = ''.join(spool)
        spool = None
        assert hdr_len == len(resp), str((hdr_len, len(resp), repr(resp)))
        if hdr.has_key('sha1'):
            data_sha1 = spool_sha.digest()
            spool = spool_sha = None
            assert hdr_sha1 == data_sha1, str((base32.b2a(hdr_sha1), base32.b2a(data_sha1)))
        #else:
            #print 'warning, server provided no sha1 to check'
        return resp

    def call(self, methodname, *args):
        res = self.req(['call', methodname, marshal(args)])

        result = simplejson.loads(res)
        if not result or len(result) < 2:
            raise TypeError('malformed response %r' % (result,))
        if result[0] == 'error':
            if result[1] == 'errno':
                raise TFSIOError(result[2], result[3])
            else:
                raise RPCError(*(result[1:])) # error, exception / error, failure
        elif result[0] == 'result':
            return unmarshal(result[1])
        else:
            raise TypeError('unknown response type %r' % (result[0],))

    def shutdown(self):
        log('shutdown() closing keepalive %s' % (self.keepalive,))
        self.keepalive.close()

# (cut-n-pasted here due to an ImportError / some py2app linkage issues)
#from twisted.scripts._twistd_unix import daemonize
def daemonize():
    # See http://www.erlenstar.demon.co.uk/unix/faq_toc.html#TOC16
    if os.fork():   # launch child and...
        os._exit(0) # kill off parent
    os.setsid()
    if os.fork():   # launch child and...
        os._exit(0) # kill off parent again.
    os.umask(077)
    null=os.open('/dev/null', os.O_RDWR)
    for i in range(3):
        try:
            os.dup2(null, i)
        except OSError, e:
            if e.errno != errno.EBADF:
                raise
    os.close(null)

def main(argv):
    log("main(%s)" % (argv,))

    # check for version or help options (no args == help)
    if not argv:
        argv = ['--help']
    if len(argv) == 1 and argv[0] in ['-h', '--help']:
        config = TahoeFuseOptions()
        print >> sys.stderr, config
        print >> sys.stderr, 'fuse usage follows:'
    if len(argv) == 1 and argv[0] in ['-h', '--help', '--version']:
        launch_tahoe_fuse(TahoeFuseLocal, None, argv)
        return -2

    # parse command line options
    config = TahoeFuseOptions()
    try:
        #print 'parsing', argv
        config.parseOptions(argv)
    except usage.error, e:
        print config
        print e
        return -1

    # check for which alias or uri is specified
    if config['alias']:
        alias = config['alias']
        #print 'looking for aliases in', config['node-directory']
        aliases = get_aliases(os.path.expanduser(config['node-directory']))
        if alias not in aliases:
            raise usage.error('Alias %r not found' % (alias,))
        root_uri = aliases[alias]
        root_name = alias
    elif config['root-uri']:
        root_uri = config['root-uri']
        root_name = 'uri_' + base32.b2a(tagged_hash('root_name', root_uri))[:12]
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

    # allocate socket
    socket_dir = os.path.join(os.path.expanduser(nodedir), "tfuse.sockets")
    socket_path = os.path.join(socket_dir, root_name)
    if len(socket_path) > 103:
        # try googling AF_UNIX and sun_len for some taste of why this oddity exists.
        raise OSError(errno.ENAMETOOLONG, 'socket path too long (%s)' % (socket_path,))

    fileutil.make_dirs(socket_dir, 0700)
    if os.path.exists(socket_path):
        log('socket exists')
        if config['server-shutdown']:
            log('calling shutdown')
            trpc = TRPC(socket_path)
            result = trpc.shutdown()
            log('result: %r' % (result,))
            log('called shutdown')
            return
        else:
            raise OSError(errno.EEXIST, 'fuse already running (%r exists)' % (socket_path,))
    elif config['server-shutdown']:
        raise OSError(errno.ENOTCONN, '--server-shutdown specified, but server not running')

    if not os.path.exists(config.mountpoint):
        raise OSError(errno.ENOENT, 'No such file or directory: "%s"' % (config.mountpoint,))

    global _tfs
    #
    # Standalone ("no-split")
    #
    if config['no-split']:
        reopen_logfile('tfuse.%s.unsplit.log' % (root_name,))
        log('\n'+(24*'_')+'init (unsplit)'+(24*'_')+'\n')

        cache_timeout = float(config['cache-timeout'])
        tfs = TFS(nodedir, nodeurl, root_uri, cache_timeout, async=False)
        #print tfs.pprint()

        # make tfs instance accesible to print_tree() for dbg
        _tfs = tfs

        args = [ '-o'+opt for opt in config.fuse_options ] + [config.mountpoint]
        launch_tahoe_fuse(TahoeFuseLocal, tfs, args)

    #
    # Server
    #
    elif config['server']:
        reopen_logfile('tfuse.%s.server.log' % (root_name,))
        log('\n'+(24*'_')+'init (server)'+(24*'_')+'\n')

        log('daemonizing')
        daemonize()

        try:
            cache_timeout = float(config['cache-timeout'])
            tfs = TFS(nodedir, nodeurl, root_uri, cache_timeout, async=True)
            #print tfs.pprint()

            # make tfs instance accesible to print_tree() for dbg
            _tfs = tfs

            log('launching tfs server')
            tfuse = TahoeFuseBase(tfs)
            tfs_server = TFSServer(socket_path, tfuse)
            tfs_server.run()
            log('tfs server ran, exiting')
        except:
            log('exception: ' + traceback.format_exc())

    #
    # Client
    #
    else:
        reopen_logfile('tfuse.%s.client.log' % (root_name,))
        log('\n'+(24*'_')+'init (client)'+(24*'_')+'\n')

        server_args = [sys.executable, sys.argv[0], '--server'] + argv
        if 'Allmydata.app/Contents/MacOS' in sys.executable:
            # in this case blackmatch is the 'fuse' subcommand of the 'tahoe' executable
            # otherwise we assume blackmatch is being run from source
            server_args.insert(2, 'fuse')
        #print 'launching server:', server_args
        server = subprocess.Popen(server_args)
        waiting_since = time.time()
        wait_at_most = 8
        while not os.path.exists(socket_path):
            log('waiting for appearance of %r' % (socket_path,))
            time.sleep(1)
            if time.time() - waiting_since > wait_at_most:
                log('%r did not appear within %ss' % (socket_path, wait_at_most))
                raise IOError(2, 'no socket %s' % (socket_path,))
        #print 'launched server'
        trpc = TRPC(socket_path)


        args = [ '-o'+opt for opt in config.fuse_options ] + [config.mountpoint]
        launch_tahoe_fuse(TahoeFuseShim, trpc, args)

        
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
