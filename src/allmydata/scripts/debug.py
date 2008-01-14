
# do not import any allmydata modules at this level. Do that from inside
# individual functions instead.
import sys, struct, time, os
from twisted.python import usage

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
    container_size = extra_lease_offset - m.DATA_OFFSET
    leases = list(m._enumerate_leases(f))

    share_type = "unknown"
    f.seek(m.DATA_OFFSET)
    if f.read(1) == "\x00":
        # this slot contains an SMDF share
        share_type = "SDMF"
    f.close()

    print >>out
    print >>out, "Mutable slot found:"
    print >>out, " share_type: %s" % share_type
    print >>out, " write_enabler: %s" % idlib.b2a(WE)
    print >>out, " WE for nodeid: %s" % idlib.nodeid_b2a(nodeid)
    print >>out, " num_extra_leases: %d" % num_extra_leases
    print >>out, " container_size: %d" % container_size
    print >>out, " data_length: %d" % data_length
    if leases:
        for (leasenum, (oid,et,rs,cs,anid)) in leases:
            print >>out
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

    if share_type == "SDMF":
        dump_SDMF_share(m.DATA_OFFSET, data_length, config, out, err)

    return 0

def dump_SDMF_share(offset, length, config, out, err):
    from allmydata import mutable
    from allmydata.util import idlib

    f = open(config['filename'], "rb")
    f.seek(offset)
    data = f.read(min(length, 2000))
    f.close()

    try:
        pieces = mutable.unpack_share(data)
    except mutable.NeedMoreDataError, e:
        # retry once with the larger size
        size = e.needed_bytes
        f = open(config['filename'], "rb")
        f.seek(offset)
        data = f.read(min(length, size))
        f.close()
        pieces = mutable.unpack_share(data)

    (seqnum, root_hash, IV, k, N, segsize, datalen,
     pubkey, signature, share_hash_chain, block_hash_tree,
     share_data, enc_privkey) = pieces

    print >>out, " SDMF contents:"
    print >>out, "  seqnum: %d" % seqnum
    print >>out, "  root_hash: %s" % idlib.b2a(root_hash)
    print >>out, "  IV: %s" % idlib.b2a(IV)
    print >>out, "  required_shares: %d" % k
    print >>out, "  total_shares: %d" % N
    print >>out, "  segsize: %d" % segsize
    print >>out, "  datalen: %d" % datalen
    share_hash_ids = ",".join(sorted([str(hid)
                                      for hid in share_hash_chain.keys()]))
    print >>out, "  share_hash_chain: %s" % share_hash_ids
    print >>out, "  block_hash_tree: %d nodes" % len(block_hash_tree)

    print >>out



class DumpCapOptions(usage.Options):
    optParameters = [
        ["nodeid", "n", None, "storage server nodeid (ascii), to construct WE and secrets."],
        ["client-secret", "c", None, "client's base secret (ascii), to construct secrets"],
        ["client-dir", "d", None, "client's base directory, from which a -c secret will be read"],
        ]
    def parseArgs(self, cap):
        self.cap = cap

def dump_cap(config, out=sys.stdout, err=sys.stderr):
    from allmydata import uri
    from allmydata.util.idlib import a2b
    from base64 import b32decode
    import urlparse, urllib

    cap = config.cap
    nodeid = None
    if config['nodeid']:
        nodeid = b32decode(config['nodeid'].upper())
    secret = None
    if config['client-secret']:
        secret = a2b(config['client-secret'])
    elif config['client-dir']:
        secretfile = os.path.join(config['client-dir'], "private", "secret")
        try:
            secret = a2b(open(secretfile, "r").read().strip())
        except EnvironmentError:
            pass

    if cap.startswith("http"):
        scheme, netloc, path, params, query, fragment = urlparse.urlparse(cap)
        assert path.startswith("/uri/")
        cap = urllib.unquote(path[len("/uri/"):])

    u = uri.from_string(cap)

    print >>out
    dump_uri_instance(u, nodeid, secret, out, err)

def _dump_secrets(storage_index, secret, nodeid, out):
    from allmydata.util import hashutil
    from allmydata.util.idlib import b2a

    if secret:
        crs = hashutil.my_renewal_secret_hash(secret)
        print >>out, " client renewal secret:", b2a(crs)
        frs = hashutil.file_renewal_secret_hash(crs, storage_index)
        print >>out, " file renewal secret:", b2a(frs)
        if nodeid:
            renew = hashutil.bucket_renewal_secret_hash(frs, nodeid)
            print >>out, " lease renewal secret:", b2a(renew)
        ccs = hashutil.my_cancel_secret_hash(secret)
        print >>out, " client cancel secret:", b2a(ccs)
        fcs = hashutil.file_cancel_secret_hash(ccs, storage_index)
        print >>out, " file cancel secret:", b2a(fcs)
        if nodeid:
            cancel = hashutil.bucket_cancel_secret_hash(fcs, nodeid)
            print >>out, " lease cancel secret:", b2a(cancel)

def dump_uri_instance(u, nodeid, secret, out, err, show_header=True):
    from allmydata import uri
    from allmydata.util.idlib import b2a
    from allmydata.util import hashutil

    if isinstance(u, uri.CHKFileURI):
        if show_header:
            print >>out, "CHK File:"
        print >>out, " key:", b2a(u.key)
        print >>out, " UEB hash:", b2a(u.uri_extension_hash)
        print >>out, " size:", u.size
        print >>out, " k/N: %d/%d" % (u.needed_shares, u.total_shares)
        print >>out, " storage index:", b2a(u.storage_index)
        _dump_secrets(u.storage_index, secret, nodeid, out)
    elif isinstance(u, uri.CHKFileVerifierURI):
        if show_header:
            print >>out, "CHK Verifier URI:"
        print >>out, " UEB hash:", b2a(u.uri_extension_hash)
        print >>out, " size:", u.size
        print >>out, " k/N: %d/%d" % (u.needed_shares, u.total_shares)
        print >>out, " storage index:", b2a(u.storage_index)

    elif isinstance(u, uri.LiteralFileURI):
        if show_header:
            print >>out, "Literal File URI:"
        print >>out, " data:", u.data

    elif isinstance(u, uri.WriteableSSKFileURI):
        if show_header:
            print >>out, "SSK Writeable URI:"
        print >>out, " writekey:", b2a(u.writekey)
        print >>out, " readkey:", b2a(u.readkey)
        print >>out, " storage index:", b2a(u.storage_index)
        print >>out, " fingerprint:", b2a(u.fingerprint)
        print >>out
        if nodeid:
            we = hashutil.ssk_write_enabler_hash(u.writekey, nodeid)
            print >>out, " write_enabler:", b2a(we)
            print >>out
        _dump_secrets(u.storage_index, secret, nodeid, out)

    elif isinstance(u, uri.ReadonlySSKFileURI):
        if show_header:
            print >>out, "SSK Read-only URI:"
        print >>out, " readkey:", b2a(u.readkey)
        print >>out, " storage index:", b2a(u.storage_index)
        print >>out, " fingerprint:", b2a(u.fingerprint)
    elif isinstance(u, uri.SSKVerifierURI):
        if show_header:
            print >>out, "SSK Verifier URI:"
        print >>out, " storage index:", b2a(u.storage_index)
        print >>out, " fingerprint:", b2a(u.fingerprint)

    elif isinstance(u, uri.NewDirectoryURI):
        if show_header:
            print >>out, "Directory Writeable URI:"
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, err, False)
    elif isinstance(u, uri.ReadonlyNewDirectoryURI):
        if show_header:
            print >>out, "Directory Read-only URI:"
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, err, False)
    elif isinstance(u, uri.NewDirectoryURIVerifier):
        if show_header:
            print >>out, "Directory Verifier URI:"
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, err, False)
    else:
        print >>out, "unknown cap type"


subCommands = [
    ["dump-share", None, DumpOptions,
     "Unpack and display the contents of a share (uri_extension and leases)."],
    ["dump-cap", None, DumpCapOptions, "Unpack a read-cap or write-cap"]
    ]

dispatch = {
    "dump-share": dump_share,
    "dump-cap": dump_cap,
    }
