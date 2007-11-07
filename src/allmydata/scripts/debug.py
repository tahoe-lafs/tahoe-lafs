
# do not import any allmydata modules at this level. Do that from inside
# individual functions instead.
import os, sys, struct, time
from twisted.python import usage
from allmydata.scripts.common import BasedirMixin

class DumpOptions(usage.Options):
    optParameters = [
        ["filename", "f", None, "which file to dump"],
        ]

    def parseArgs(self, filename=None):
        if filename:
            self['filename'] = filename

    def postOptions(self):
        if not self['filename']:
            raise usage.UsageError("<filename> parameter is required")

class DumpRootDirnodeOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["basedir", "C", None, "the vdrive-server's base directory"],
        ]

class DumpDirnodeOptions(BasedirMixin, usage.Options):
    optParameters = [
        ["uri", "u", None, "the URI of the dirnode to dump."],
        ["basedir", "C", None, "which directory to create the introducer in"],
        ]
    optFlags = [
        ["verbose", "v", "be extra noisy (show encrypted data)"],
        ]
    def parseArgs(self, *args):
        if len(args) == 1:
            self['uri'] = args[-1]
            args = args[:-1]
        BasedirMixin.parseArgs(self, *args)

    def postOptions(self):
        BasedirMixin.postOptions(self)
        if not self['uri']:
            raise usage.UsageError("<uri> parameter is required")

def dump_share(config, out=sys.stdout, err=sys.stderr):
    from allmydata import uri, storage

    # check the version, to see if we have a mutable or immutable share
    f = open(config['filename'], "rb")
    prefix = f.read(32)
    f.close()
    if prefix == storage.MutableShareFile.MAGIC:
        return dump_mutable_share(config, out, err)
    # otherwise assume it's immutable
    f = storage.ShareFile(config['filename'])
    # use a ReadBucketProxy to parse the bucket and find the uri extension
    bp = storage.ReadBucketProxy(None)
    offsets = bp._parse_offsets(f.read_share_data(0, 0x24))
    seek = offsets['uri_extension']
    length = struct.unpack(">L", f.read_share_data(seek, 4))[0]
    seek += 4
    data = f.read_share_data(seek, length)

    unpacked = uri.unpack_extension_readable(data)
    keys1 = ("size", "num_segments", "segment_size",
             "needed_shares", "total_shares")
    keys2 = ("codec_name", "codec_params", "tail_codec_params")
    keys3 = ("plaintext_hash", "plaintext_root_hash",
             "crypttext_hash", "crypttext_root_hash",
             "share_root_hash")
    display_keys = {"size": "file_size"}
    for k in keys1:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print >>out, "%19s: %s" % (dk, unpacked[k])
    print >>out
    for k in keys2:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print >>out, "%19s: %s" % (dk, unpacked[k])
    print >>out
    for k in keys3:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print >>out, "%19s: %s" % (dk, unpacked[k])

    leftover = set(unpacked.keys()) - set(keys1 + keys2 + keys3)
    if leftover:
        print >>out
        print >>out, "LEFTOVER:"
        for k in sorted(leftover):
            print >>out, "%s: %s" % (k, unpacked[k])

    sizes = {}
    sizes['data'] = bp._data_size
    sizes['validation'] = (offsets['uri_extension'] -
                           offsets['plaintext_hash_tree'])
    sizes['uri-extension'] = len(data)
    print >>out
    print >>out, "Size of data within the share:"
    for k in sorted(sizes):
        print >>out, "%19s: %s" % (k, sizes[k])

    # display lease information too
    leases = list(f.iter_leases())
    if leases:
        for i,lease in enumerate(leases):
            (owner_num, renew_secret, cancel_secret, expiration_time) = lease
            when = format_expiration_time(expiration_time)
            print >>out, "Lease #%d: owner=%d, expire in %s" % (i, owner_num,
                                                                when)
    else:
        print >>out, "No leases."

    print >>out
    return 0

def format_expiration_time(expiration_time):
    now = time.time()
    remains = expiration_time - now
    when = "%ds" % remains
    if remains > 24*3600:
        when += " (%d days)" % (remains / (24*3600))
    elif remains > 3600:
        when += " (%d hours)" % (remains / 3600)
    return when


def dump_mutable_share(config, out, err):
    from allmydata import storage
    from allmydata.util import idlib
    m = storage.MutableShareFile(config['filename'])
    f = open(config['filename'], "rb")
    WE, nodeid = m._read_write_enabler_and_nodeid(f)
    num_extra_leases = m._read_num_extra_leases(f)
    data_length = m._read_data_length(f)
    extra_lease_offset = m._read_extra_lease_offset(f)
    leases = list(m._enumerate_leases(f))
    f.close()

    print >>out
    print >>out, "Mutable slot found:"
    print >>out, " write_enabler: %s" % idlib.b2a(WE)
    print >>out, " WE for nodeid: %s" % idlib.nodeid_b2a(nodeid)
    print >>out, " num_extra_leases: %d" % num_extra_leases
    print >>out, " data_length: %d" % data_length
    if leases:
        for (leasenum, (oid,et,rs,cs,anid)) in leases:
            print >>out, " Lease #%d:" % leasenum
            print >>out, "  ownerid: %d" % oid
            when = format_expiration_time(et)
            print >>out, "  expires in %s" % when
            print >>out, "  renew_secret: %s" % idlib.b2a(rs)
            print >>out, "  cancel_secret: %s" % idlib.b2a(cs)
            print >>out, "  secrets are for nodeid: %s" % idlib.nodeid_b2a(anid)
    else:
        print >>out, "No leases."
    print >>out
    return 0


def dump_root_dirnode(config, out=sys.stdout, err=sys.stderr):
    from allmydata import uri

    basedir = config['basedirs'][0]
    root_dirnode_file = os.path.join(basedir, "vdrive", "root")
    try:
        f = open(root_dirnode_file, "rb")
        key = f.read()
        rooturi = uri.DirnodeURI("fakeFURL", key)
        print >>out, rooturi.to_string()
        return 0
    except EnvironmentError:
        print >>out,  "unable to read root dirnode file from %s" % \
              root_dirnode_file
        return 1

def dump_directory_node(config, out=sys.stdout, err=sys.stderr):
    from allmydata import dirnode
    from allmydata.util import hashutil, idlib
    from allmydata.interfaces import IDirnodeURI
    basedir = config['basedirs'][0]
    dir_uri = IDirnodeURI(config['uri'])
    verbose = config['verbose']

    if dir_uri.is_readonly():
        wk, we, rk, index = \
            hashutil.generate_dirnode_keys_from_readkey(dir_uri.readkey)
    else:
        wk, we, rk, index = \
            hashutil.generate_dirnode_keys_from_writekey(dir_uri.writekey)

    filename = os.path.join(basedir, "vdrive", idlib.b2a(index))

    print >>out
    print >>out, "dirnode uri: %s" % dir_uri.to_string()
    print >>out, "filename : %s" % filename
    print >>out, "index        : %s" % idlib.b2a(index)
    if wk:
        print >>out, "writekey     : %s" % idlib.b2a(wk)
        print >>out, "write_enabler: %s" % idlib.b2a(we)
    else:
        print >>out, "writekey     : None"
        print >>out, "write_enabler: None"
    print >>out, "readkey      : %s" % idlib.b2a(rk)

    print >>out

    vds = dirnode.VirtualDriveServer(os.path.join(basedir, "vdrive"), False)
    data = vds._read_from_file(index)
    if we:
        if we != data[0]:
            print >>out, "ERROR: write_enabler does not match"

    for (H_key, E_key, E_write, E_read) in data[1]:
        if verbose:
            print >>out, " H_key %s" % idlib.b2a(H_key)
            print >>out, " E_key %s" % idlib.b2a(E_key)
            print >>out, " E_write %s" % idlib.b2a(E_write)
            print >>out, " E_read %s" % idlib.b2a(E_read)
        key = dirnode.decrypt(rk, E_key)
        print >>out, " key %s" % key
        if hashutil.dir_name_hash(rk, key) != H_key:
            print >>out, "  ERROR: H_key does not match"
        if wk and E_write:
            if len(E_write) < 14:
                print >>out, "  ERROR: write data is short:", idlib.b2a(E_write)
            write = dirnode.decrypt(wk, E_write)
            print >>out, "   write: %s" % write
        read = dirnode.decrypt(rk, E_read)
        print >>out, "   read: %s" % read
        print >>out

    return 0


subCommands = [
    ["dump-share", None, DumpOptions,
     "Unpack and display the contents of a share (uri_extension and leases)."],
    ["dump-root-dirnode", None, DumpRootDirnodeOptions,
     "Compute most of the URI for the vdrive server's root dirnode."],
    ["dump-dirnode", None, DumpDirnodeOptions,
     "Unpack and display the contents of a vdrive DirectoryNode."],
    ]

dispatch = {
    "dump-share": dump_share,
    "dump-root-dirnode": dump_root_dirnode,
    "dump-dirnode": dump_directory_node,
    }
