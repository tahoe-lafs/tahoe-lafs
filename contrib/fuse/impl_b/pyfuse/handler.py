from kernel import *
import os, errno, sys

def fuse_mount(mountpoint, opts=None):
    if not isinstance(mountpoint, str):
        raise TypeError
    if opts is not None and not isinstance(opts, str):
        raise TypeError
    import dl
    try:
        fuse = dl.open('libfuse.so')
    except dl.error:
        fuse = dl.open('libfuse.so.2')
    if fuse.sym('fuse_mount_compat22'):
        fnname = 'fuse_mount_compat22'
    else:
        fnname = 'fuse_mount'     # older versions of libfuse.so
    return fuse.call(fnname, mountpoint, opts)

class Handler(object):
    __system = os.system
    mountpoint = fd = None
    __in_header_size  = fuse_in_header.calcsize()
    __out_header_size = fuse_out_header.calcsize()
    MAX_READ = FUSE_MAX_IN

    def __init__(self, mountpoint, filesystem, logfile='STDERR', **opts1):
        opts = getattr(filesystem, 'MOUNT_OPTIONS', {}).copy()
        opts.update(opts1)
        if opts:
            opts = opts.items()
            opts.sort()
            opts = ' '.join(['%s=%s' % item for item in opts])
        else:
            opts = None
        fd = fuse_mount(mountpoint, opts)
        if fd < 0:
            raise IOError("mount failed")
        self.fd = fd
        if logfile == 'STDERR':
            logfile = sys.stderr
        self.logfile = logfile
        if self.logfile:
            print >> self.logfile, '* mounted at', mountpoint
        self.mountpoint = mountpoint
        self.filesystem = filesystem
        self.handles = {}
        self.nexth = 1

    def __del__(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.mountpoint:
            cmd = "fusermount -u '%s'" % self.mountpoint.replace("'", r"'\''")
            self.mountpoint = None
            if self.logfile:
                print >> self.logfile, '*', cmd
            self.__system(cmd)

    close = __del__

    def loop_forever(self):
        while True:
            try:
                msg = os.read(self.fd, FUSE_MAX_IN)
            except OSError, ose:
                if ose.errno == errno.ENODEV:
                    # on hardy, at least, this is what happens upon fusermount -u
                    #raise EOFError("out-kernel connection closed")
                    return
            if not msg:
                #raise EOFError("out-kernel connection closed")
                return
            self.handle_message(msg)

    def handle_message(self, msg):
        headersize = self.__in_header_size
        req = fuse_in_header(msg[:headersize])
        assert req.len == len(msg)
        name = req.opcode
        try:
            try:
                name = fuse_opcode2name[req.opcode]
                meth = getattr(self, name)
            except (IndexError, AttributeError):
                raise NotImplementedError
            #if self.logfile:
            #    print >> self.logfile, '%s(%d)' % (name, req.nodeid)
            reply = meth(req, msg[headersize:])
            #if self.logfile:
            #    print >> self.logfile, '   >>', repr(reply)
        except NotImplementedError:
            if self.logfile:
                print >> self.logfile, '%s: not implemented' % (name,)
            self.send_reply(req, err=errno.ENOSYS)
        except EnvironmentError, e:
            if self.logfile:
                print >> self.logfile, '%s: %s' % (name, e)
            self.send_reply(req, err = e.errno or errno.ESTALE)
        except NoReply:
            pass
        else:
            self.send_reply(req, reply)

    def send_reply(self, req, reply=None, err=0):
        assert 0 <= err < 1000
        if reply is None:
            reply = ''
        elif not isinstance(reply, str):
            reply = reply.pack()
        f = fuse_out_header(unique = req.unique,
                            error  = -err,
                            len    = self.__out_header_size + len(reply))
        data = f.pack() + reply
        while data:
            count = os.write(self.fd, data)
            if not count:
                raise EOFError("in-kernel connection closed")
            data = data[count:]

    def notsupp_or_ro(self):
        if hasattr(self.filesystem, "modified"):
            raise IOError(errno.ENOSYS, "not supported")
        else:
            raise IOError(errno.EROFS, "read-only file system")

    # ____________________________________________________________

    def FUSE_INIT(self, req, msg):
        msg = fuse_init_in_out(msg[:8])
        if self.logfile:
            print >> self.logfile, 'INIT: %d.%d' % (msg.major, msg.minor)
        return fuse_init_in_out(major = FUSE_KERNEL_VERSION,
                                minor = FUSE_KERNEL_MINOR_VERSION)

    def FUSE_GETATTR(self, req, msg):
        node = self.filesystem.getnode(req.nodeid)
        attr, valid = self.filesystem.getattr(node)
        return fuse_attr_out(attr_valid = valid,
                             attr = attr)

    def FUSE_SETATTR(self, req, msg):
        if not hasattr(self.filesystem, 'setattr'):
            self.notsupp_or_ro()
        msg = fuse_setattr_in(msg)
        if msg.valid & FATTR_MODE:  mode = msg.attr.mode & 0777
        else:                       mode = None
        if msg.valid & FATTR_UID:   uid = msg.attr.uid
        else:                       uid = None
        if msg.valid & FATTR_GID:   gid = msg.attr.gid
        else:                       gid = None
        if msg.valid & FATTR_SIZE:  size = msg.attr.size
        else:                       size = None
        if msg.valid & FATTR_ATIME: atime = msg.attr.atime
        else:                       atime = None
        if msg.valid & FATTR_MTIME: mtime = msg.attr.mtime
        else:                       mtime = None
        node = self.filesystem.getnode(req.nodeid)
        self.filesystem.setattr(node, mode, uid, gid,
                                size, atime, mtime)
        attr, valid = self.filesystem.getattr(node)
        return fuse_attr_out(attr_valid = valid,
                             attr = attr)

    def FUSE_RELEASE(self, req, msg):
        msg = fuse_release_in(msg, truncate=True)
        try:
            del self.handles[msg.fh]
        except KeyError:
            raise IOError(errno.EBADF, msg.fh)
    FUSE_RELEASEDIR = FUSE_RELEASE

    def FUSE_OPENDIR(self, req, msg):
        #msg = fuse_open_in(msg)
        node = self.filesystem.getnode(req.nodeid)
        attr, valid = self.filesystem.getattr(node)
        if mode2type(attr.mode) != TYPE_DIR:
            raise IOError(errno.ENOTDIR, node)
        fh = self.nexth
        self.nexth += 1
        self.handles[fh] = True, '', node
        return fuse_open_out(fh = fh)

    def FUSE_READDIR(self, req, msg):
        msg = fuse_read_in(msg)
        try:
            isdir, data, node = self.handles[msg.fh]
            if not isdir:
                raise KeyError    # not a dir handle
        except KeyError:
            raise IOError(errno.EBADF, msg.fh)
        if msg.offset == 0:
            # start or rewind
            d_entries = []
            off = 0
            for name, type in self.filesystem.listdir(node):
                off += fuse_dirent.calcsize(len(name))
                d_entry = fuse_dirent(ino  = INVALID_INO,
                                      off  = off,
                                      type = type,
                                      name = name)
                d_entries.append(d_entry)
            data = ''.join([d.pack() for d in d_entries])
            self.handles[msg.fh] = True, data, node
        return data[msg.offset:msg.offset+msg.size]

    def replyentry(self, (subnodeid, valid1)):
        subnode = self.filesystem.getnode(subnodeid)
        attr, valid2 = self.filesystem.getattr(subnode)
        return fuse_entry_out(nodeid = subnodeid,
                              entry_valid = valid1,
                              attr_valid = valid2,
                              attr = attr)

    def FUSE_LOOKUP(self, req, msg):
        filename = c2pystr(msg)
        dirnode = self.filesystem.getnode(req.nodeid)
        return self.replyentry(self.filesystem.lookup(dirnode, filename))

    def FUSE_OPEN(self, req, msg, mask=os.O_RDONLY|os.O_WRONLY|os.O_RDWR):
        msg = fuse_open_in(msg)
        node = self.filesystem.getnode(req.nodeid)
        attr, valid = self.filesystem.getattr(node)
        if mode2type(attr.mode) != TYPE_REG:
            raise IOError(errno.EPERM, node)
        f = self.filesystem.open(node, msg.flags & mask)
        if isinstance(f, tuple):
            f, open_flags = f
        else:
            open_flags = 0
        fh = self.nexth
        self.nexth += 1
        self.handles[fh] = False, f, node
        return fuse_open_out(fh = fh, open_flags = open_flags)

    def FUSE_READ(self, req, msg):
        msg = fuse_read_in(msg)
        try:
            isdir, f, node = self.handles[msg.fh]
            if isdir:
                raise KeyError
        except KeyError:
            raise IOError(errno.EBADF, msg.fh)
        f.seek(msg.offset)
        return f.read(msg.size)

    def FUSE_WRITE(self, req, msg):
        if not hasattr(self.filesystem, 'modified'):
            raise IOError(errno.EROFS, "read-only file system")
        msg, data = fuse_write_in.from_head(msg)
        try:
            isdir, f, node = self.handles[msg.fh]
            if isdir:
                raise KeyError
        except KeyError:
            raise IOError(errno.EBADF, msg.fh)
        f.seek(msg.offset)
        f.write(data)
        self.filesystem.modified(node)
        return fuse_write_out(size = len(data))

    def FUSE_MKNOD(self, req, msg):
        if not hasattr(self.filesystem, 'mknod'):
            self.notsupp_or_ro()
        msg, filename = fuse_mknod_in.from_param(msg)
        node = self.filesystem.getnode(req.nodeid)
        return self.replyentry(self.filesystem.mknod(node, filename, msg.mode))

    def FUSE_MKDIR(self, req, msg):
        if not hasattr(self.filesystem, 'mkdir'):
            self.notsupp_or_ro()
        msg, filename = fuse_mkdir_in.from_param(msg)
        node = self.filesystem.getnode(req.nodeid)
        return self.replyentry(self.filesystem.mkdir(node, filename, msg.mode))

    def FUSE_SYMLINK(self, req, msg):
        if not hasattr(self.filesystem, 'symlink'):
            self.notsupp_or_ro()
        linkname, target = c2pystr2(msg)
        node = self.filesystem.getnode(req.nodeid)
        return self.replyentry(self.filesystem.symlink(node, linkname, target))

    #def FUSE_LINK(self, req, msg):
    #    ...

    def FUSE_UNLINK(self, req, msg):
        if not hasattr(self.filesystem, 'unlink'):
            self.notsupp_or_ro()
        filename = c2pystr(msg)
        node = self.filesystem.getnode(req.nodeid)
        self.filesystem.unlink(node, filename)

    def FUSE_RMDIR(self, req, msg):
        if not hasattr(self.filesystem, 'rmdir'):
            self.notsupp_or_ro()
        dirname = c2pystr(msg)
        node = self.filesystem.getnode(req.nodeid)
        self.filesystem.rmdir(node, dirname)

    def FUSE_FORGET(self, req, msg):
        if hasattr(self.filesystem, 'forget'):
            self.filesystem.forget(req.nodeid)
        raise NoReply

    def FUSE_READLINK(self, req, msg):
        if not hasattr(self.filesystem, 'readlink'):
            raise IOError(errno.ENOSYS, "readlink not supported")
        node = self.filesystem.getnode(req.nodeid)
        target = self.filesystem.readlink(node)
        return target

    def FUSE_RENAME(self, req, msg):
        if not hasattr(self.filesystem, 'rename'):
            self.notsupp_or_ro()
        msg, oldname, newname = fuse_rename_in.from_param2(msg)
        oldnode = self.filesystem.getnode(req.nodeid)
        newnode = self.filesystem.getnode(msg.newdir)
        self.filesystem.rename(oldnode, oldname, newnode, newname)

    def getxattrs(self, nodeid):
        if not hasattr(self.filesystem, 'getxattrs'):
            raise IOError(errno.ENOSYS, "xattrs not supported")
        node = self.filesystem.getnode(nodeid)
        return self.filesystem.getxattrs(node)

    def FUSE_LISTXATTR(self, req, msg):
        names = self.getxattrs(req.nodeid).keys()
        names = ['user.' + name for name in names]
        totalsize = 0
        for name in names:
            totalsize += len(name)+1
        msg = fuse_getxattr_in(msg)
        if msg.size > 0:
            if msg.size < totalsize:
                raise IOError(errno.ERANGE, "buffer too small")
            names.append('')
            return '\x00'.join(names)
        else:
            return fuse_getxattr_out(size=totalsize)

    def FUSE_GETXATTR(self, req, msg):
        xattrs = self.getxattrs(req.nodeid)
        msg, name = fuse_getxattr_in.from_param(msg)
        if not name.startswith('user.'):    # ENODATA == ENOATTR
            raise IOError(errno.ENODATA, "only supports 'user.' xattrs, "
                                         "got %r" % (name,))
        name = name[5:]
        try:
            value = xattrs[name]
        except KeyError:
            raise IOError(errno.ENODATA, "no such xattr")    # == ENOATTR
        value = str(value)
        if msg.size > 0:
            if msg.size < len(value):
                raise IOError(errno.ERANGE, "buffer too small")
            return value
        else:
            return fuse_getxattr_out(size=len(value))

    def FUSE_SETXATTR(self, req, msg):
        xattrs = self.getxattrs(req.nodeid)
        msg, name, value = fuse_setxattr_in.from_param_head(msg)
        assert len(value) == msg.size
        # XXX msg.flags ignored
        if not name.startswith('user.'):    # ENODATA == ENOATTR
            raise IOError(errno.ENODATA, "only supports 'user.' xattrs")
        name = name[5:]
        try:
            xattrs[name] = value
        except KeyError:
            raise IOError(errno.ENODATA, "cannot set xattr")    # == ENOATTR

    def FUSE_REMOVEXATTR(self, req, msg):
        xattrs = self.getxattrs(req.nodeid)
        name = c2pystr(msg)
        if not name.startswith('user.'):    # ENODATA == ENOATTR
            raise IOError(errno.ENODATA, "only supports 'user.' xattrs")
        name = name[5:]
        try:
            del xattrs[name]
        except KeyError:
            raise IOError(errno.ENODATA, "cannot delete xattr")   # == ENOATTR


class NoReply(Exception):
    pass
