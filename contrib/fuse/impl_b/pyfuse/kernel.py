from struct import pack, unpack, calcsize
import stat

class Struct(object):
    __slots__ = []

    def __init__(self, data=None, truncate=False, **fields):
        if data is not None:
            if truncate:
                data = data[:self.calcsize()]
            self.unpack(data)
        for key, value in fields.items():
            setattr(self, key, value)

    def unpack(self, data):
        data = unpack(self.__types__, data)
        for key, value in zip(self.__slots__, data):
            setattr(self, key, value)

    def pack(self):
        return pack(self.__types__, *[getattr(self, k, 0)
                                      for k in self.__slots__])

    def calcsize(cls):
        return calcsize(cls.__types__)
    calcsize = classmethod(calcsize)

    def __repr__(self):
        result = ['%s=%r' % (name, getattr(self, name, None))
                  for name in self.__slots__]
        return '<%s %s>' % (self.__class__.__name__, ', '.join(result))

    def from_param(cls, msg):
        limit = cls.calcsize()
        zero = msg.find('\x00', limit)
        assert zero >= 0
        return cls(msg[:limit]), msg[limit:zero]
    from_param = classmethod(from_param)

    def from_param2(cls, msg):
        limit = cls.calcsize()
        zero1 = msg.find('\x00', limit)
        assert zero1 >= 0
        zero2 = msg.find('\x00', zero1+1)
        assert zero2 >= 0
        return cls(msg[:limit]), msg[limit:zero1], msg[zero1+1:zero2]
    from_param2 = classmethod(from_param2)

    def from_head(cls, msg):
        limit = cls.calcsize()
        return cls(msg[:limit]), msg[limit:]
    from_head = classmethod(from_head)

    def from_param_head(cls, msg):
        limit = cls.calcsize()
        zero = msg.find('\x00', limit)
        assert zero >= 0
        return cls(msg[:limit]), msg[limit:zero], msg[zero+1:]
    from_param_head = classmethod(from_param_head)

class StructWithAttr(Struct):

    def unpack(self, data):
        limit = -fuse_attr.calcsize()
        super(StructWithAttr, self).unpack(data[:limit])
        self.attr = fuse_attr(data[limit:])

    def pack(self):
        return super(StructWithAttr, self).pack() + self.attr.pack()

    def calcsize(cls):
        return super(StructWithAttr, cls).calcsize() + fuse_attr.calcsize()
    calcsize = classmethod(calcsize)


def _mkstruct(name, c, base=Struct):
    typ2code = {
        '__u32': 'I',
        '__s32': 'i',
        '__u64': 'Q',
        '__s64': 'q'}
    slots = []
    types = ['=']
    for line in c.split('\n'):
        line = line.strip()
        if line:
            line, tail = line.split(';', 1)
            typ, nam = line.split()
            slots.append(nam)
            types.append(typ2code[typ])
    cls = type(name, (base,), {'__slots__': slots,
                                 '__types__': ''.join(types)})
    globals()[name] = cls

class timeval(object):

    def __init__(self, attr1, attr2):
        self.sec = attr1
        self.nsec = attr2

    def __get__(self, obj, typ=None):
        if obj is None:
            return self
        else:
            return (getattr(obj, self.sec) +
                    getattr(obj, self.nsec) * 0.000000001)

    def __set__(self, obj, val):
        val = int(val * 1000000000)
        sec, nsec = divmod(val, 1000000000)
        setattr(obj, self.sec, sec)
        setattr(obj, self.nsec, nsec)

    def __delete__(self, obj):
        delattr(obj, self.sec)
        delattr(obj, self.nsec)

def _mktimeval(cls, attr1, attr2):
    assert attr1.startswith('_')
    assert attr2.startswith('_')
    tv = timeval(attr1, attr2)
    setattr(cls, attr1[1:], tv)

INVALID_INO = 0xFFFFFFFFFFFFFFFF

def mode2type(mode):
    return (mode & 0170000) >> 12

TYPE_REG = mode2type(stat.S_IFREG)
TYPE_DIR = mode2type(stat.S_IFDIR)
TYPE_LNK = mode2type(stat.S_IFLNK)

def c2pystr(s):
    n = s.find('\x00')
    assert n >= 0
    return s[:n]

def c2pystr2(s):
    first = c2pystr(s)
    second = c2pystr(s[len(first)+1:])
    return first, second

# ____________________________________________________________

# Version number of this interface
FUSE_KERNEL_VERSION = 7

# Minor version number of this interface
FUSE_KERNEL_MINOR_VERSION = 2

# The node ID of the root inode
FUSE_ROOT_ID = 1

# The major number of the fuse character device
FUSE_MAJOR = 10

# The minor number of the fuse character device
FUSE_MINOR = 229

# Make sure all structures are padded to 64bit boundary, so 32bit
# userspace works under 64bit kernels

_mkstruct('fuse_attr', '''
	__u64	ino;
	__u64	size;
	__u64	blocks;
	__u64	_atime;
	__u64	_mtime;
	__u64	_ctime;
	__u32	_atimensec;
	__u32	_mtimensec;
	__u32	_ctimensec;
	__u32	mode;
	__u32	nlink;
	__u32	uid;
	__u32	gid;
	__u32	rdev;
''')
_mktimeval(fuse_attr, '_atime', '_atimensec')
_mktimeval(fuse_attr, '_mtime', '_mtimensec')
_mktimeval(fuse_attr, '_ctime', '_ctimensec')

_mkstruct('fuse_kstatfs', '''
	__u64	blocks;
	__u64	bfree;
	__u64	bavail;
	__u64	files;
	__u64	ffree;
	__u32	bsize;
	__u32	namelen;
''')

FATTR_MODE	= 1 << 0
FATTR_UID	= 1 << 1
FATTR_GID	= 1 << 2
FATTR_SIZE	= 1 << 3
FATTR_ATIME	= 1 << 4
FATTR_MTIME	= 1 << 5

#
# Flags returned by the OPEN request
#
# FOPEN_DIRECT_IO: bypass page cache for this open file
# FOPEN_KEEP_CACHE: don't invalidate the data cache on open
#
FOPEN_DIRECT_IO		= 1 << 0
FOPEN_KEEP_CACHE	= 1 << 1

fuse_opcode = {
    'FUSE_LOOKUP'        : 1,
    'FUSE_FORGET'        : 2,  # no reply
    'FUSE_GETATTR'       : 3,
    'FUSE_SETATTR'       : 4,
    'FUSE_READLINK'      : 5,
    'FUSE_SYMLINK'       : 6,
    'FUSE_MKNOD'         : 8,
    'FUSE_MKDIR'         : 9,
    'FUSE_UNLINK'        : 10,
    'FUSE_RMDIR'         : 11,
    'FUSE_RENAME'        : 12,
    'FUSE_LINK'          : 13,
    'FUSE_OPEN'          : 14,
    'FUSE_READ'          : 15,
    'FUSE_WRITE'         : 16,
    'FUSE_STATFS'        : 17,
    'FUSE_RELEASE'       : 18,
    'FUSE_FSYNC'         : 20,
    'FUSE_SETXATTR'      : 21,
    'FUSE_GETXATTR'      : 22,
    'FUSE_LISTXATTR'     : 23,
    'FUSE_REMOVEXATTR'   : 24,
    'FUSE_FLUSH'         : 25,
    'FUSE_INIT'          : 26,
    'FUSE_OPENDIR'       : 27,
    'FUSE_READDIR'       : 28,
    'FUSE_RELEASEDIR'    : 29,
    'FUSE_FSYNCDIR'      : 30,
}

fuse_opcode2name = []
def setup():
    for key, value in fuse_opcode.items():
        fuse_opcode2name.extend([None] * (value+1 - len(fuse_opcode2name)))
        fuse_opcode2name[value] = key
setup()
del setup

# Conservative buffer size for the client
FUSE_MAX_IN = 8192

FUSE_NAME_MAX = 1024
FUSE_SYMLINK_MAX = 4096
FUSE_XATTR_SIZE_MAX = 4096

_mkstruct('fuse_entry_out', """
	__u64	nodeid;		/* Inode ID */
	__u64	generation;	/* Inode generation: nodeid:gen must \
				   be unique for the fs's lifetime */
	__u64	_entry_valid;	/* Cache timeout for the name */
	__u64	_attr_valid;	/* Cache timeout for the attributes */
	__u32	_entry_valid_nsec;
	__u32	_attr_valid_nsec;
""", base=StructWithAttr)
_mktimeval(fuse_entry_out, '_entry_valid', '_entry_valid_nsec')
_mktimeval(fuse_entry_out, '_attr_valid', '_attr_valid_nsec')

_mkstruct('fuse_forget_in', '''
	__u64	nlookup;
''')

_mkstruct('fuse_attr_out', '''
	__u64	_attr_valid;	/* Cache timeout for the attributes */
	__u32	_attr_valid_nsec;
	__u32	dummy;
''', base=StructWithAttr)
_mktimeval(fuse_attr_out, '_attr_valid', '_attr_valid_nsec')

_mkstruct('fuse_mknod_in', '''
	__u32	mode;
	__u32	rdev;
''')

_mkstruct('fuse_mkdir_in', '''
	__u32	mode;
	__u32	padding;
''')

_mkstruct('fuse_rename_in', '''
	__u64	newdir;
''')

_mkstruct('fuse_link_in', '''
	__u64	oldnodeid;
''')

_mkstruct('fuse_setattr_in', '''
	__u32	valid;
	__u32	padding;
''', base=StructWithAttr)

_mkstruct('fuse_open_in', '''
	__u32	flags;
	__u32	padding;
''')

_mkstruct('fuse_open_out', '''
	__u64	fh;
	__u32	open_flags;
	__u32	padding;
''')

_mkstruct('fuse_release_in', '''
	__u64	fh;
	__u32	flags;
	__u32	padding;
''')

_mkstruct('fuse_flush_in', '''
	__u64	fh;
	__u32	flush_flags;
	__u32	padding;
''')

_mkstruct('fuse_read_in', '''
	__u64	fh;
	__u64	offset;
	__u32	size;
	__u32	padding;
''')

_mkstruct('fuse_write_in', '''
	__u64	fh;
	__u64	offset;
	__u32	size;
	__u32	write_flags;
''')

_mkstruct('fuse_write_out', '''
	__u32	size;
	__u32	padding;
''')

fuse_statfs_out = fuse_kstatfs

_mkstruct('fuse_fsync_in', '''
	__u64	fh;
	__u32	fsync_flags;
	__u32	padding;
''')

_mkstruct('fuse_setxattr_in', '''
	__u32	size;
	__u32	flags;
''')

_mkstruct('fuse_getxattr_in', '''
	__u32	size;
	__u32	padding;
''')

_mkstruct('fuse_getxattr_out', '''
	__u32	size;
	__u32	padding;
''')

_mkstruct('fuse_init_in_out', '''
	__u32	major;
	__u32	minor;
''')

_mkstruct('fuse_in_header', '''
	__u32	len;
	__u32	opcode;
	__u64	unique;
	__u64	nodeid;
	__u32	uid;
	__u32	gid;
	__u32	pid;
	__u32	padding;
''')

_mkstruct('fuse_out_header', '''
	__u32	len;
	__s32	error;
	__u64	unique;
''')

class fuse_dirent(Struct):
    __slots__ = ['ino', 'off', 'type', 'name']

    def unpack(self, data):
        self.ino, self.off, namelen, self.type = struct.unpack('QQII',
                                                               data[:24])
        self.name = data[24:24+namelen]
        assert len(self.name) == namelen

    def pack(self):
        namelen = len(self.name)
        return pack('QQII%ds' % ((namelen+7)&~7,),
                    self.ino, getattr(self, 'off', 0), namelen,
                    self.type, self.name)

    def calcsize(cls, namelen):
        return 24 + ((namelen+7)&~7)
    calcsize = classmethod(calcsize)
