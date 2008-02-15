#!/usr/bin/env python

#-----------------------------------------------------------------------------------------------
from allmydata.uri import CHKFileURI, NewDirectoryURI, LiteralFileURI
from allmydata.scripts.common_http import do_http as do_http_req

import base64
import sha
import os
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

if not hasattr(fuse, '__version__'):
    raise RuntimeError, \
        "your fuse-py doesn't know of fuse.__version__, probably it's too old."

fuse.fuse_python_api = (0, 2)
fuse.feature_assert('stateful_files', 'has_init')


logfile = file('tfuse.log', 'wb')

def log(msg):
    logfile.write("%s: %s\n" % (time.asctime(), msg))
    #time.sleep(0.1)
    logfile.flush()

fuse.flog = log

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

    if flags | os.O_APPEND:
        m = m.replace('w', 'a', 1)

    return m

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
                    log('TFF: [%s] open(%s) for write: no such file, creating new File' % (self.name, self.fname, ))
                    self.fnode = File(0, None)
                    self.fnode.tmp_fname = self.fname # XXX kill this
                    self.parent.add_child(self.name, self.fnode)
                elif hasattr(self.fnode, 'tmp_fname'):
                    self.fname = self.fnode.tmp_fname
                self.file = os.fdopen(os.open(self.fname, flags, *mode), m)
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
            self.tfs.add_child(self.parent.get_uri(), self.name, file_cap)
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

class ObjFetcher(object):
    def get_tahoe_file(self, path, flags, *mode):
        log('objfetcher.get_tahoe_file(%r, %r, %r, %r)' % (self, path, flags, mode))
        return TahoeFuseFile(path, flags, *mode)
fetcher = ObjFetcher()

class TahoeFuse(fuse.Fuse):

    def __init__(self, tfs, *args, **kw):
        log("TF: __init__(%r, %r)" % (args, kw))

        self.tfs = tfs
        class MyFuseFile(TahoeFuseFile):
            tfs = tfs
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
        return -errno.EOPNOTSUPP

    @logexc
    def unlink(self, path):
        self.log("unlink(%r)" % (path,))
        return -errno.EOPNOTSUPP

    @logexc
    def rmdir(self, path):
        self.log("rmdir(%r)" % (path,))
        return -errno.EOPNOTSUPP

    @logexc
    def symlink(self, path, path1):
        self.log("symlink(%r, %r)" % (path, path1))
        return -errno.EOPNOTSUPP

    @logexc
    def rename(self, path, path1):
        self.log("rename(%r, %r)" % (path, path1))
        self.tfs.rename(path, path1)

    @logexc
    def link(self, path, path1):
        self.log("link(%r, %r)" % (path, path1))
        return -errno.EOPNOTSUPP

    @logexc
    def chmod(self, path, mode):
        self.log("chmod(%r, %r)" % (path, mode))
        return -errno.EOPNOTSUPP

    @logexc
    def chown(self, path, user, group):
        self.log("chown(%r, %r, %r)" % (path, user, group))
        return -errno.EOPNOTSUPP

    @logexc
    def truncate(self, path, len):
        self.log("truncate(%r, %r)" % (path, len))
        return -errno.EOPNOTSUPP

    @logexc
    def utime(self, path, times):
        self.log("utime(%r, %r)" % (path, times))
        return -errno.EOPNOTSUPP

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

        return os.statvfs(".")

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
        node = self.tfs.get_path(path)
        if node is None:
            return -errno.ENOENT
        return node.get_stat()

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

def main(tfs):

    usage = "Userspace tahoe fs: cache a tahoe tree and present via fuse\n" + fuse.Fuse.fusage

    server = TahoeFuse(tfs, version="%prog " + fuse.__version__,
                       usage=usage,
                       dash_s_do='setsingle')
    server.parse(errex=1)
    server.main()


def getbasedir():
    f = file(os.path.expanduser("~/.tahoe/private/root_dir.cap"), 'rb')
    bd = f.read().strip()
    f.close()
    return bd

def getnodeurl():
    f = file(os.path.expanduser("~/.tahoe/node.url"), 'rb')
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
    def __init__(self, **kwargs):
        fuse.Stat.__init__(self, **kwargs)

    def __repr__(self):
        return "<Stat%r" % {
            'st_mode': self.st_mode,
            'st_ino': self.st_ino,
            'st_dev': self.st_dev,
            'st_nlink': self.st_nlink,
            'st_uid': self.st_uid,
            'st_gid': self.st_gid,
            'st_size': self.st_size,
            'st_atime': self.st_atime,
            'st_mtime': self.st_mtime,
            'st_ctime': self.st_ctime,
            }

class Directory(object):
    def __init__(self, ro_uri, rw_uri):
        self.ro_uri = ro_uri
        self.rw_uri = rw_uri
        assert (rw_uri or ro_uri)
        self.children = {}

    def __repr__(self):
        return "<Directory %s>" % (fingerprint(self.get_uri()),)

    def get_children(self):
        return self.children.keys()

    def get_child(self, name):
        return self.children[name]

    def add_child(self, name, file_node):
        self.children[name] = file_node

    def remove_child(self, name):
        del self.children[name]

    def get_uri(self):
        return self.rw_uri or self.ro_uri

    def writable(self):
        return self.rw_uri and self.rw_uri != self.ro_uri

    def pprint(self, prefix='', printed=None):
        ret = []
        if printed is None:
            printed = set()
        writable = self.writable() and '+' or ' '
        if self in printed:
            ret.append("         %s/%s ... <%s>" % (prefix, writable, fingerprint(self.get_uri()), ))
        else:
            ret.append("[%s] %s/%s" % (fingerprint(self.get_uri()), prefix, writable, ))
            printed.add(self)
            for name,f in sorted(self.children.items()):
                ret.append(f.pprint(' ' * (len(prefix)+1)+name, printed))
        return '\n'.join(ret)

    def get_stat(self):
        s = TStat(st_mode = stat.S_IFDIR | 0755, st_nlink = 2)
        log("%s.get_stat()->%s" % (self, s))
        return s

class File(object):
    def __init__(self, size, ro_uri):
        self.size = size
        if ro_uri:
            ro_uri = str(ro_uri)
        self.ro_uri = ro_uri

    def __repr__(self):
        return "<File %s>" % (fingerprint(self.ro_uri) or [self.tmp_fname],)

    def pprint(self, prefix='', printed=None):
        return "         %s (%s)" % (prefix, self.size, )

    def get_stat(self):
        if hasattr(self, 'tmp_fname'):
            s = os.stat(self.tmp_fname)
            log("%s.get_stat()->%s" % (self, s))
        else:
            s = TStat(st_size=self.size, st_mode = stat.S_IFREG | 0444, st_nlink = 1)
            log("%s.get_stat()->%s" % (self, s))
        return s

    def get_uri(self):
        return self.ro_uri

    def writable(self):
        #return not self.ro_uri
        return True

class TFS(object):
    def __init__(self, nodeurl, root_uri):
        self.nodeurl = nodeurl
        self.root_uri = root_uri
        self.dirs = {}

        self.cache = FileCache(nodeurl, os.path.expanduser('~/.tahoe/_cache'))
        ro_uri = NewDirectoryURI.init_from_string(self.root_uri).get_readonly()
        self.root = Directory(ro_uri, self.root_uri)
        self.load_dir('<root>', self.root)

    def log(self, msg):
        log("<TFS> %s" % (msg, ))

    def pprint(self):
        return self.root.pprint()

    def get_parent_name_and_child(self, path):
        dirname, name = os.path.split(path)
        parent = self.get_path(dirname)
        try:
            child = parent.get_child(name)
            return parent, name, child
        except KeyError:
            return parent, name, None
        
    def get_path(self, path):
        comps = path.strip('/').split('/')
        if comps == ['']:
            comps = []
        cursor = self.root
        for comp in comps:
            if not isinstance(cursor, Directory):
                self.log('path "%s" is not a dir' % (path,))
                return None
            try:
                cursor = cursor.children[comp]
            except KeyError:
                self.log('path "%s" not found' % (path,))
                return None
        return cursor

    def load_dir(self, name, dirobj):
        print 'loading', name, dirobj
        url = self.nodeurl + "uri/%s?t=json" % urllib.quote(dirobj.get_uri())
        data = urllib.urlopen(url).read()
        parsed = simplejson.loads(data)
        nodetype, d = parsed
        assert nodetype == 'dirnode'
        for name,details in d['children'].items():
            name = str(name)
            ctype, cattrs = details
            if ctype == 'dirnode':
                cobj = self.dir_for(name, cattrs.get('ro_uri'), cattrs.get('rw_uri'))
            else:
                assert ctype == "filenode"
                cobj = File(cattrs.get('size'), cattrs.get('ro_uri'))
            dirobj.children[name] = cobj

    def dir_for(self, name, ro_uri, rw_uri):
        if ro_uri:
            ro_uri = str(ro_uri)
        if rw_uri:
            rw_uri = str(rw_uri)
        uri = rw_uri or ro_uri
        assert uri
        dirobj = self.dirs.get(uri)
        if not dirobj:
            dirobj = Directory(ro_uri, rw_uri)
            self.dirs[uri] = dirobj
            self.load_dir(name, dirobj)
        return dirobj

    def upload(self, fname):
        self.log('upload(%r)' % (fname,))
        fh = file(fname, 'rb')
        url = self.nodeurl + "uri"
        file_cap = do_http('PUT', url, fh)
        self.log('uploaded to: %r' % (file_cap,))
        return file_cap

    def add_child(self, parent_dir_uri, child_name, child_uri):
        self.log('add_child(%r, %r, %r)' % (parent_dir_uri, child_name, child_uri,))
        url = self.nodeurl + "uri/%s/%s?t=uri" % (urllib.quote(parent_dir_uri), urllib.quote(child_name), )
        child_cap = do_http('PUT', url, child_uri)
        assert child_cap == child_uri
        self.log('added child %r with %r to %r' % (child_name, child_uri, parent_dir_uri))
        return child_uri

    def remove_child(self, parent_uri, child_name):
        self.log('remove_child(%r, %r)' % (parent_uri, child_name, ))
        url = self.nodeurl + "uri/%s/%s" % (urllib.quote(parent_uri), urllib.quote(child_name))
        resp = do_http('DELETE', url)
        self.log('child removal yielded %r' % (resp,))

    def mkdir(self, path):
        self.log('mkdir(%r)' % (path,))
        url = self.nodeurl + "uri?t=mkdir"
        new_dir_cap = do_http('PUT', url)
        parent_path, name = os.path.split(path)
        self.log('parent_path, name = %s, %s' % (parent_path, name,))
        parent = self.get_path(parent_path)
        self.log('parent = %s' % (parent, ))
        self.log('new_dir_cap = %s' % (new_dir_cap, ))
        child_uri = self.add_child(parent.get_uri(), name, new_dir_cap)
        ro_uri = NewDirectoryURI.init_from_string(child_uri).get_readonly()
        child = Directory(ro_uri, child_uri)
        parent.add_child(name, child)

    def rename(self, path, path1):
        self.log('rename(%s, %s)' % (path, path1))
        parent, name, child = self.get_parent_name_and_child(path)
        child_uri = child.get_uri()
        new_parent_path, new_child_name = os.path.split(path1)
        new_parent = self.get_path(new_parent_path)
        self.add_child(new_parent.get_uri(), new_child_name, child_uri)
        self.remove_child(parent.get_uri(), name)
        parent.remove_child(name)
        new_parent.add_child(new_child_name, child)

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

def print_tree():
    log('tree:\n' + _tfs.pprint())

if __name__ == '__main__':
    log("\n\nmain()")
    tfs = TFS(getnodeurl(), getbasedir())
    print tfs.pprint()

    # make tfs instance accesible to print_tree() for dbg
    global _tfs
    _tfs = tfs

    main(tfs)

