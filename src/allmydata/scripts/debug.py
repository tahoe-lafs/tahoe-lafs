
# do not import any allmydata modules at this level. Do that from inside
# individual functions instead.
import struct, time, os
from twisted.python import usage

class DumpOptions(usage.Options):
    def getSynopsis(self):
        return "Usage: tahoe debug dump-share SHARE_FILENAME"

    optFlags = [
        ["offsets", None, "Display a table of section offsets"],
        ]

    def getUsage(self, width=None):
        t = usage.Options.getUsage(self, width)
        t += """
Print lots of information about the given share, by parsing the share's
contents. This includes share type, lease information, encoding parameters,
hash-tree roots, public keys, and segment sizes. This command also emits a
verify-cap for the file that uses the share.

 tahoe debug dump-share testgrid/node-3/storage/shares/4v/4vozh77tsrw7mdhnj7qvp5ky74/0

"""
        return t

    def parseArgs(self, filename):
        self['filename'] = filename

def dump_share(options):
    from allmydata import storage

    out = options.stdout

    # check the version, to see if we have a mutable or immutable share
    print >>out, "share filename: %s" % options['filename']

    f = open(options['filename'], "rb")
    prefix = f.read(32)
    f.close()
    if prefix == storage.MutableShareFile.MAGIC:
        return dump_mutable_share(options)
    # otherwise assume it's immutable
    return dump_immutable_share(options)

def dump_immutable_share(options):
    from allmydata import uri, storage
    from allmydata.util import base32
    from allmydata.immutable.layout import ReadBucketProxy

    out = options.stdout
    f = storage.ShareFile(options['filename'])
    # use a ReadBucketProxy to parse the bucket and find the uri extension
    bp = ReadBucketProxy(None)
    offsets = bp._parse_offsets(f.read_share_data(0, 0x24))
    seek = offsets['uri_extension']
    length = struct.unpack(">L", f.read_share_data(seek, 4))[0]
    seek += 4
    UEB_data = f.read_share_data(seek, length)

    unpacked = uri.unpack_extension_readable(UEB_data)
    keys1 = ("size", "num_segments", "segment_size",
             "needed_shares", "total_shares")
    keys2 = ("codec_name", "codec_params", "tail_codec_params")
    keys3 = ("plaintext_hash", "plaintext_root_hash",
             "crypttext_hash", "crypttext_root_hash",
             "share_root_hash", "UEB_hash")
    display_keys = {"size": "file_size"}
    for k in keys1:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print >>out, "%20s: %s" % (dk, unpacked[k])
    print >>out
    for k in keys2:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print >>out, "%20s: %s" % (dk, unpacked[k])
    print >>out
    for k in keys3:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print >>out, "%20s: %s" % (dk, unpacked[k])

    leftover = set(unpacked.keys()) - set(keys1 + keys2 + keys3)
    if leftover:
        print >>out
        print >>out, "LEFTOVER:"
        for k in sorted(leftover):
            print >>out, "%20s: %s" % (k, unpacked[k])

    # the storage index isn't stored in the share itself, so we depend upon
    # knowing the parent directory name to get it
    pieces = options['filename'].split(os.sep)
    if len(pieces) >= 2 and base32.could_be_base32_encoded(pieces[-2]):
        storage_index = base32.a2b(pieces[-2])
        uri_extension_hash = base32.a2b(unpacked["UEB_hash"])
        u = uri.CHKFileVerifierURI(storage_index, uri_extension_hash,
                                   unpacked["needed_shares"],
                                   unpacked["total_shares"], unpacked["size"])
        verify_cap = u.to_string()
        print >>out, "%20s: %s" % ("verify-cap", verify_cap)

    sizes = {}
    sizes['data'] = bp._data_size
    sizes['validation'] = (offsets['uri_extension'] -
                           offsets['plaintext_hash_tree'])
    sizes['uri-extension'] = len(UEB_data)
    print >>out
    print >>out, " Size of data within the share:"
    for k in sorted(sizes):
        print >>out, "%20s: %s" % (k, sizes[k])

    if options['offsets']:
        print >>out
        print >>out, " Section Offsets:"
        print >>out, "%20s: %s" % ("share data", f._data_offset)
        for k in ["data", "plaintext_hash_tree", "crypttext_hash_tree",
                  "block_hashes", "share_hashes", "uri_extension"]:
            name = {"data": "block data"}.get(k,k)
            offset = f._data_offset + offsets[k]
            print >>out, "  %20s: %s   (0x%x)" % (name, offset, offset)
        print >>out, "%20s: %s" % ("leases", f._lease_offset)


    # display lease information too
    print >>out
    leases = list(f.iter_leases())
    if leases:
        for i,lease in enumerate(leases):
            when = format_expiration_time(lease.expiration_time)
            print >>out, " Lease #%d: owner=%d, expire in %s" \
                  % (i, lease.owner_num, when)
    else:
        print >>out, " No leases."

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


def dump_mutable_share(options):
    from allmydata import storage
    from allmydata.util import base32, idlib
    out = options.stdout
    m = storage.MutableShareFile(options['filename'])
    f = open(options['filename'], "rb")
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
    print >>out, " write_enabler: %s" % base32.b2a(WE)
    print >>out, " WE for nodeid: %s" % idlib.nodeid_b2a(nodeid)
    print >>out, " num_extra_leases: %d" % num_extra_leases
    print >>out, " container_size: %d" % container_size
    print >>out, " data_length: %d" % data_length
    if leases:
        for (leasenum, lease) in leases:
            print >>out
            print >>out, " Lease #%d:" % leasenum
            print >>out, "  ownerid: %d" % lease.owner_num
            when = format_expiration_time(lease.expiration_time)
            print >>out, "  expires in %s" % when
            print >>out, "  renew_secret: %s" % base32.b2a(lease.renew_secret)
            print >>out, "  cancel_secret: %s" % base32.b2a(lease.cancel_secret)
            print >>out, "  secrets are for nodeid: %s" % idlib.nodeid_b2a(lease.nodeid)
    else:
        print >>out, "No leases."
    print >>out

    if share_type == "SDMF":
        dump_SDMF_share(m, data_length, options)

    return 0

def dump_SDMF_share(m, length, options):
    from allmydata.mutable.layout import unpack_share, unpack_header
    from allmydata.mutable.common import NeedMoreDataError
    from allmydata.util import base32, hashutil
    from allmydata.uri import SSKVerifierURI

    offset = m.DATA_OFFSET

    out = options.stdout

    f = open(options['filename'], "rb")
    f.seek(offset)
    data = f.read(min(length, 2000))
    f.close()

    try:
        pieces = unpack_share(data)
    except NeedMoreDataError, e:
        # retry once with the larger size
        size = e.needed_bytes
        f = open(options['filename'], "rb")
        f.seek(offset)
        data = f.read(min(length, size))
        f.close()
        pieces = unpack_share(data)

    (seqnum, root_hash, IV, k, N, segsize, datalen,
     pubkey, signature, share_hash_chain, block_hash_tree,
     share_data, enc_privkey) = pieces
    (ig_version, ig_seqnum, ig_roothash, ig_IV, ig_k, ig_N, ig_segsize,
     ig_datalen, offsets) = unpack_header(data)

    print >>out, " SDMF contents:"
    print >>out, "  seqnum: %d" % seqnum
    print >>out, "  root_hash: %s" % base32.b2a(root_hash)
    print >>out, "  IV: %s" % base32.b2a(IV)
    print >>out, "  required_shares: %d" % k
    print >>out, "  total_shares: %d" % N
    print >>out, "  segsize: %d" % segsize
    print >>out, "  datalen: %d" % datalen
    print >>out, "  enc_privkey: %d bytes" % len(enc_privkey)
    print >>out, "  pubkey: %d bytes" % len(pubkey)
    print >>out, "  signature: %d bytes" % len(signature)
    share_hash_ids = ",".join(sorted([str(hid)
                                      for hid in share_hash_chain.keys()]))
    print >>out, "  share_hash_chain: %s" % share_hash_ids
    print >>out, "  block_hash_tree: %d nodes" % len(block_hash_tree)

    # the storage index isn't stored in the share itself, so we depend upon
    # knowing the parent directory name to get it
    pieces = options['filename'].split(os.sep)
    if len(pieces) >= 2 and base32.could_be_base32_encoded(pieces[-2]):
        storage_index = base32.a2b(pieces[-2])
        fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey)
        u = SSKVerifierURI(storage_index, fingerprint)
        verify_cap = u.to_string()
        print >>out, "  verify-cap:", verify_cap

    if options['offsets']:
        # NOTE: this offset-calculation code is fragile, and needs to be
        # merged with MutableShareFile's internals.
        print >>out
        print >>out, " Section Offsets:"
        def printoffset(name, value, shift=0):
            print >>out, "%s%20s: %s   (0x%x)" % (" "*shift, name, value, value)
        printoffset("first lease", m.HEADER_SIZE)
        printoffset("share data", m.DATA_OFFSET)
        o_seqnum = m.DATA_OFFSET + struct.calcsize(">B")
        printoffset("seqnum", o_seqnum, 2)
        o_root_hash = m.DATA_OFFSET + struct.calcsize(">BQ")
        printoffset("root_hash", o_root_hash, 2)
        for k in ["signature", "share_hash_chain", "block_hash_tree",
                  "share_data",
                  "enc_privkey", "EOF"]:
            name = {"share_data": "block data",
                    "EOF": "end of share data"}.get(k,k)
            offset = m.DATA_OFFSET + offsets[k]
            printoffset(name, offset, 2)
        f = open(options['filename'], "rb")
        printoffset("extra leases", m._read_extra_lease_offset(f) + 4)
        f.close()

    print >>out



class DumpCapOptions(usage.Options):
    def getSynopsis(self):
        return "Usage: tahoe debug dump-cap [options] FILECAP"
    optParameters = [
        ["nodeid", "n",
         None, "storage server nodeid (ascii), to construct WE and secrets."],
        ["client-secret", "c", None,
         "client's base secret (ascii), to construct secrets"],
        ["client-dir", "d", None,
         "client's base directory, from which a -c secret will be read"],
        ]
    def parseArgs(self, cap):
        self.cap = cap

    def getUsage(self, width=None):
        t = usage.Options.getUsage(self, width)
        t += """
Print information about the given cap-string (aka: URI, file-cap, dir-cap,
read-cap, write-cap). The URI string is parsed and unpacked. This prints the
type of the cap, its storage index, and any derived keys.

 tahoe debug dump-cap URI:SSK-Verifier:4vozh77tsrw7mdhnj7qvp5ky74:q7f3dwz76sjys4kqfdt3ocur2pay3a6rftnkqmi2uxu3vqsdsofq

This may be useful to determine if a read-cap and a write-cap refer to the
same time, or to extract the storage-index from a file-cap (to then use with
find-shares)

If additional information is provided (storage server nodeid and/or client
base secret), this command will compute the shared secrets used for the
write-enabler and for lease-renewal.
"""
        return t


def dump_cap(options):
    from allmydata import uri
    from allmydata.util import base32
    from base64 import b32decode
    import urlparse, urllib

    out = options.stdout
    cap = options.cap
    nodeid = None
    if options['nodeid']:
        nodeid = b32decode(options['nodeid'].upper())
    secret = None
    if options['client-secret']:
        secret = base32.a2b(options['client-secret'])
    elif options['client-dir']:
        secretfile = os.path.join(options['client-dir'], "private", "secret")
        try:
            secret = base32.a2b(open(secretfile, "r").read().strip())
        except EnvironmentError:
            pass

    if cap.startswith("http"):
        scheme, netloc, path, params, query, fragment = urlparse.urlparse(cap)
        assert path.startswith("/uri/")
        cap = urllib.unquote(path[len("/uri/"):])

    u = uri.from_string(cap)

    print >>out
    dump_uri_instance(u, nodeid, secret, out)

def _dump_secrets(storage_index, secret, nodeid, out):
    from allmydata.util import hashutil
    from allmydata.util import base32

    if secret:
        crs = hashutil.my_renewal_secret_hash(secret)
        print >>out, " client renewal secret:", base32.b2a(crs)
        frs = hashutil.file_renewal_secret_hash(crs, storage_index)
        print >>out, " file renewal secret:", base32.b2a(frs)
        if nodeid:
            renew = hashutil.bucket_renewal_secret_hash(frs, nodeid)
            print >>out, " lease renewal secret:", base32.b2a(renew)
        ccs = hashutil.my_cancel_secret_hash(secret)
        print >>out, " client cancel secret:", base32.b2a(ccs)
        fcs = hashutil.file_cancel_secret_hash(ccs, storage_index)
        print >>out, " file cancel secret:", base32.b2a(fcs)
        if nodeid:
            cancel = hashutil.bucket_cancel_secret_hash(fcs, nodeid)
            print >>out, " lease cancel secret:", base32.b2a(cancel)

def dump_uri_instance(u, nodeid, secret, out, show_header=True):
    from allmydata import storage, uri
    from allmydata.util import base32, hashutil

    if isinstance(u, uri.CHKFileURI):
        if show_header:
            print >>out, "CHK File:"
        print >>out, " key:", base32.b2a(u.key)
        print >>out, " UEB hash:", base32.b2a(u.uri_extension_hash)
        print >>out, " size:", u.size
        print >>out, " k/N: %d/%d" % (u.needed_shares, u.total_shares)
        print >>out, " storage index:", storage.si_b2a(u.storage_index)
        _dump_secrets(u.storage_index, secret, nodeid, out)
    elif isinstance(u, uri.CHKFileVerifierURI):
        if show_header:
            print >>out, "CHK Verifier URI:"
        print >>out, " UEB hash:", base32.b2a(u.uri_extension_hash)
        print >>out, " size:", u.size
        print >>out, " k/N: %d/%d" % (u.needed_shares, u.total_shares)
        print >>out, " storage index:", storage.si_b2a(u.storage_index)

    elif isinstance(u, uri.LiteralFileURI):
        if show_header:
            print >>out, "Literal File URI:"
        print >>out, " data:", u.data

    elif isinstance(u, uri.WriteableSSKFileURI):
        if show_header:
            print >>out, "SSK Writeable URI:"
        print >>out, " writekey:", base32.b2a(u.writekey)
        print >>out, " readkey:", base32.b2a(u.readkey)
        print >>out, " storage index:", storage.si_b2a(u.storage_index)
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)
        print >>out
        if nodeid:
            we = hashutil.ssk_write_enabler_hash(u.writekey, nodeid)
            print >>out, " write_enabler:", base32.b2a(we)
            print >>out
        _dump_secrets(u.storage_index, secret, nodeid, out)

    elif isinstance(u, uri.ReadonlySSKFileURI):
        if show_header:
            print >>out, "SSK Read-only URI:"
        print >>out, " readkey:", base32.b2a(u.readkey)
        print >>out, " storage index:", storage.si_b2a(u.storage_index)
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)
    elif isinstance(u, uri.SSKVerifierURI):
        if show_header:
            print >>out, "SSK Verifier URI:"
        print >>out, " storage index:", storage.si_b2a(u.storage_index)
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)

    elif isinstance(u, uri.NewDirectoryURI):
        if show_header:
            print >>out, "Directory Writeable URI:"
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)
    elif isinstance(u, uri.ReadonlyNewDirectoryURI):
        if show_header:
            print >>out, "Directory Read-only URI:"
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)
    elif isinstance(u, uri.NewDirectoryURIVerifier):
        if show_header:
            print >>out, "Directory Verifier URI:"
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)
    else:
        print >>out, "unknown cap type"

class FindSharesOptions(usage.Options):
    def getSynopsis(self):
        return "Usage: tahoe debug find-shares STORAGE_INDEX NODEDIRS.."
    def parseArgs(self, storage_index_s, *nodedirs):
        self.si_s = storage_index_s
        self.nodedirs = nodedirs
    def getUsage(self, width=None):
        t = usage.Options.getUsage(self, width)
        t += """
Locate all shares for the given storage index. This command looks through one
or more node directories to find the shares. It returns a list of filenames,
one per line, for each share file found.

 tahoe debug find-shares 4vozh77tsrw7mdhnj7qvp5ky74 testgrid/node-*

It may be useful during testing, when running a test grid in which all the
nodes are on a local disk. The share files thus located can be counted,
examined (with dump-share), or corrupted/deleted to test checker/repairer.
"""
        return t

def find_shares(options):
    """Given a storage index and a list of node directories, emit a list of
    all matching shares to stdout, one per line. For example:

     find-shares.py 44kai1tui348689nrw8fjegc8c ~/testnet/node-*

    gives:

    /home/warner/testnet/node-1/storage/shares/44k/44kai1tui348689nrw8fjegc8c/5
    /home/warner/testnet/node-1/storage/shares/44k/44kai1tui348689nrw8fjegc8c/9
    /home/warner/testnet/node-2/storage/shares/44k/44kai1tui348689nrw8fjegc8c/2
    """
    from allmydata import storage

    out = options.stdout
    sharedir = storage.storage_index_to_dir(storage.si_a2b(options.si_s))
    for d in options.nodedirs:
        d = os.path.join(os.path.expanduser(d), "storage/shares", sharedir)
        if os.path.exists(d):
            for shnum in os.listdir(d):
                print >>out, os.path.join(d, shnum)

    return 0


class CatalogSharesOptions(usage.Options):
    """

    """
    def parseArgs(self, *nodedirs):
        self.nodedirs = nodedirs
        if not nodedirs:
            raise usage.UsageError("must specify at least one node directory")

    def getSynopsis(self):
        return "Usage: tahoe debug catalog-shares NODEDIRS.."

    def getUsage(self, width=None):
        t = usage.Options.getUsage(self, width)
        t += """
Locate all shares in the given node directories, and emit a one-line summary
of each share. Run it like this:

 tahoe debug catalog-shares testgrid/node-* >allshares.txt

The lines it emits will look like the following:

 CHK $SI $k/$N $filesize $UEB_hash $expiration $abspath_sharefile
 SDMF $SI $k/$N $filesize $seqnum/$roothash $expiration $abspath_sharefile
 UNKNOWN $abspath_sharefile

This command can be used to build up a catalog of shares from many storage
servers and then sort the results to compare all shares for the same file. If
you see shares with the same SI but different parameters/filesize/UEB_hash,
then something is wrong. The misc/find-share/anomalies.py script may be
useful for purpose.
"""
        return t

def describe_share(abs_sharefile, si_s, shnum_s, now, out):
    from allmydata import uri, storage
    from allmydata.mutable.layout import unpack_share
    from allmydata.mutable.common import NeedMoreDataError
    from allmydata.immutable.layout import ReadBucketProxy
    from allmydata.util import base32
    import struct

    f = open(abs_sharefile, "rb")
    prefix = f.read(32)

    if prefix == storage.MutableShareFile.MAGIC:
        # mutable share
        m = storage.MutableShareFile(abs_sharefile)
        WE, nodeid = m._read_write_enabler_and_nodeid(f)
        num_extra_leases = m._read_num_extra_leases(f)
        data_length = m._read_data_length(f)
        extra_lease_offset = m._read_extra_lease_offset(f)
        container_size = extra_lease_offset - m.DATA_OFFSET
        leases = list(m._enumerate_leases(f))
        expiration_time = min( [lease[1].expiration_time
                                for lease in leases] )
        expiration = max(0, expiration_time - now)

        share_type = "unknown"
        f.seek(m.DATA_OFFSET)
        if f.read(1) == "\x00":
            # this slot contains an SMDF share
            share_type = "SDMF"

        if share_type == "SDMF":
            f.seek(m.DATA_OFFSET)
            data = f.read(min(data_length, 2000))

            try:
                pieces = unpack_share(data)
            except NeedMoreDataError, e:
                # retry once with the larger size
                size = e.needed_bytes
                f.seek(m.DATA_OFFSET)
                data = f.read(min(data_length, size))
                pieces = unpack_share(data)
            (seqnum, root_hash, IV, k, N, segsize, datalen,
             pubkey, signature, share_hash_chain, block_hash_tree,
             share_data, enc_privkey) = pieces

            print >>out, "SDMF %s %d/%d %d #%d:%s %d %s" % \
                  (si_s, k, N, datalen,
                   seqnum, base32.b2a(root_hash),
                   expiration, abs_sharefile)
        else:
            print >>out, "UNKNOWN mutable %s" % (abs_sharefile,)

    elif struct.unpack(">L", prefix[:4]) == (1,):
        # immutable

        sf = storage.ShareFile(abs_sharefile)
        # use a ReadBucketProxy to parse the bucket and find the uri extension
        bp = ReadBucketProxy(None)
        offsets = bp._parse_offsets(sf.read_share_data(0, 0x24))
        seek = offsets['uri_extension']
        length = struct.unpack(">L", sf.read_share_data(seek, 4))[0]
        seek += 4
        UEB_data = sf.read_share_data(seek, length)
        expiration_time = min( [lease.expiration_time
                                for lease in sf.iter_leases()] )
        expiration = max(0, expiration_time - now)

        unpacked = uri.unpack_extension_readable(UEB_data)
        k = unpacked["needed_shares"]
        N = unpacked["total_shares"]
        filesize = unpacked["size"]
        ueb_hash = unpacked["UEB_hash"]

        print >>out, "CHK %s %d/%d %d %s %d %s" % (si_s, k, N, filesize,
                                                   ueb_hash, expiration,
                                                   abs_sharefile)

    else:
        print >>out, "UNKNOWN really-unknown %s" % (abs_sharefile,)

    f.close()


def catalog_shares(options):
    out = options.stdout
    now = time.time()
    for d in options.nodedirs:
        d = os.path.join(os.path.expanduser(d), "storage/shares")
        try:
            abbrevs = os.listdir(d)
        except EnvironmentError:
            # ignore nodes that have storage turned off altogether
            pass
        else:
            for abbrevdir in abbrevs:
                if abbrevdir == "incoming":
                    continue
                abbrevdir = os.path.join(d, abbrevdir)
                for si_s in os.listdir(abbrevdir):
                    si_dir = os.path.join(abbrevdir, si_s)
                    for shnum_s in os.listdir(si_dir):
                        abs_sharefile = os.path.join(si_dir, shnum_s)
                        abs_sharefile = os.path.abspath(abs_sharefile)
                        assert os.path.isfile(abs_sharefile)
                        describe_share(abs_sharefile, si_s, shnum_s, now, out)
    return 0

class CorruptShareOptions(usage.Options):
    def getSynopsis(self):
        return "Usage: tahoe debug corrupt-share SHARE_FILENAME"

    optParameters = [
        ["offset", "o", "block-random", "Which bit to flip."],
        ]

    def getUsage(self, width=None):
        t = usage.Options.getUsage(self, width)
        t += """
Corrupt the given share by flipping a bit. This will cause a
verifying/downloading client to log an integrity-check failure incident, and
downloads will proceed with a different share.

The --offset parameter controls which bit should be flipped. The default is
to flip a single random bit of the block data.

 tahoe debug corrupt-share testgrid/node-3/storage/shares/4v/4vozh77tsrw7mdhnj7qvp5ky74/0

Obviously, this command should not be used in normal operation.
"""
        return t
    def parseArgs(self, filename):
        self['filename'] = filename

def corrupt_share(options):
    import random
    from allmydata import storage
    from allmydata.mutable.layout import unpack_header
    out = options.stdout
    fn = options['filename']
    assert options["offset"] == "block-random", "other offsets not implemented"
    # first, what kind of share is it?

    def flip_bit(start, end):
        offset = random.randrange(start, end)
        bit = random.randrange(0, 8)
        print >>out, "[%d..%d):  %d.b%d" % (start, end, offset, bit)
        f = open(fn, "rb+")
        f.seek(offset)
        d = f.read(1)
        d = chr(ord(d) ^ 0x01)
        f.seek(offset)
        f.write(d)
        f.close()

    f = open(fn, "rb")
    prefix = f.read(32)
    f.close()
    if prefix == storage.MutableShareFile.MAGIC:
        # mutable
        m = storage.MutableShareFile(fn)
        f = open(fn, "rb")
        f.seek(m.DATA_OFFSET)
        data = f.read(2000)
        # make sure this slot contains an SMDF share
        assert data[0] == "\x00", "non-SDMF mutable shares not supported"
        f.close()

        (version, ig_seqnum, ig_roothash, ig_IV, ig_k, ig_N, ig_segsize,
         ig_datalen, offsets) = unpack_header(data)

        assert version == 0, "we only handle v0 SDMF files"
        start = m.DATA_OFFSET + offsets["share_data"]
        end = m.DATA_OFFSET + offsets["enc_privkey"]
        flip_bit(start, end)
    else:
        # otherwise assume it's immutable
        f = storage.ShareFile(fn)
        bp = ReadBucketProxy(None)
        offsets = bp._parse_offsets(f.read_share_data(0, 0x24))
        start = f._data_offset + offsets["data"]
        end = f._data_offset + offsets["plaintext_hash_tree"]
        flip_bit(start, end)



class ReplOptions(usage.Options):
    pass

def repl(options):
    import code
    return code.interact()


class DebugCommand(usage.Options):
    subCommands = [
        ["dump-share", None, DumpOptions,
         "Unpack and display the contents of a share (uri_extension and leases)."],
        ["dump-cap", None, DumpCapOptions, "Unpack a read-cap or write-cap"],
        ["find-shares", None, FindSharesOptions, "Locate sharefiles in node dirs"],
        ["catalog-shares", None, CatalogSharesOptions, "Describe shares in node dirs"],
        ["corrupt-share", None, CorruptShareOptions, "Corrupt a share"],
        ["repl", None, ReplOptions, "Open a python interpreter"],
        ]
    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    def getSynopsis(self):
        return "Usage: tahoe debug SUBCOMMAND"
    def getUsage(self, width=None):
        #t = usage.Options.getUsage(self, width)
        t = """
Subcommands:
    tahoe debug dump-share      Unpack and display the contents of a share
    tahoe debug dump-cap        Unpack a read-cap or write-cap
    tahoe debug find-shares     Locate sharefiles in node directories
    tahoe debug catalog-shares  Describe all shares in node dirs
    tahoe debug corrupt-share   Corrupt a share by flipping a bit.

Please run e.g. 'tahoe debug dump-share --help' for more details on each
subcommand.
"""
        return t

subDispatch = {
    "dump-share": dump_share,
    "dump-cap": dump_cap,
    "find-shares": find_shares,
    "catalog-shares": catalog_shares,
    "corrupt-share": corrupt_share,
    "repl": repl,
    }


def do_debug(options):
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    return f(so)


subCommands = [
    ["debug", None, DebugCommand, "debug subcommands: use 'tahoe debug' for a list"],
    ]

dispatch = {
    "debug": do_debug,
    }
