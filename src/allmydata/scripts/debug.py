
# do not import any allmydata modules at this level. Do that from inside
# individual functions instead.
import struct, time, os, sys
from collections import deque

from twisted.python import usage, failure
from twisted.internet import defer
from twisted.scripts import trial as twisted_trial

from foolscap.logging import cli as foolscap_cli

from allmydata.util.assertutil import _assert
from allmydata.scripts.common import BaseOptions


class ChunkedShare(object):
    def __init__(self, filename, preferred_chunksize):
        self._filename = filename
        self._position = 0
        self._chunksize = os.stat(filename).st_size
        self._total_size = self._chunksize
        chunknum = 1
        while True:
            chunk_filename = self._get_chunk_filename(chunknum)
            if not os.path.exists(chunk_filename):
                break
            size = os.stat(chunk_filename).st_size
            _assert(size <= self._chunksize, size=size, chunksize=self._chunksize)
            self._total_size += size
            chunknum += 1

        if self._chunksize == self._total_size:
            # There is only one chunk, so we are at liberty to make the chunksize larger
            # than that chunk, but not smaller.
            self._chunksize = max(self._chunksize, preferred_chunksize)

    def __repr__(self):
        return "<ChunkedShare at %r>" % (self._filename,)

    def seek(self, offset):
        self._position = offset

    def read(self, length):
        data = self.pread(self._position, length)
        self._position += len(data)
        return data

    def write(self, data):
        self.pwrite(self._position, data)
        self._position += len(data)

    def pread(self, offset, length):
        if offset + length > self._total_size:
            length = max(0, self._total_size - offset)

        pieces = deque()
        chunknum    = offset / self._chunksize
        read_offset = offset % self._chunksize
        remaining   = length
        while remaining > 0:
            read_length = min(remaining, self._chunksize - read_offset)
            _assert(read_length > 0, read_length=read_length)
            pieces.append(self.read_from_chunk(chunknum, read_offset, read_length))
            remaining -= read_length
            read_offset = 0
            chunknum += 1
        return ''.join(pieces)

    def _get_chunk_filename(self, chunknum):
        if chunknum == 0:
            return self._filename
        else:
            return "%s.%d" % (self._filename, chunknum)

    def read_from_chunk(self, chunknum, offset, length):
        f = open(self._get_chunk_filename(chunknum), "rb")
        try:
            f.seek(offset)
            data = f.read(length)
            _assert(len(data) == length, len_data = len(data), length=length)
            return data
        finally:
            f.close()

    def pwrite(self, offset, data):
        if offset > self._total_size:
            # fill the gap with zeroes
            data = "\x00"*(offset + len(data) - self._total_size) + data
            offset = self._total_size

        self._total_size = max(self._total_size, offset + len(data))
        chunknum     = offset / self._chunksize
        write_offset = offset % self._chunksize
        data_offset  = 0
        remaining = len(data)
        while remaining > 0:
            write_length = min(remaining, self._chunksize - write_offset)
            _assert(write_length > 0, write_length=write_length)
            self.write_to_chunk(chunknum, write_offset, data[data_offset : data_offset + write_length])
            remaining -= write_length
            data_offset += write_length
            write_offset = 0
            chunknum += 1

    def write_to_chunk(self, chunknum, offset, data):
        f = open(self._get_chunk_filename(chunknum), "rw+b")
        try:
            f.seek(offset)
            f.write(data)
        finally:
            f.close()


class DumpOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-opts] debug dump-share SHARE_FILENAME"

    optFlags = [
        ["offsets", None, "Display a table of section offsets."],
        ]

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Print lots of information about the given share, by parsing the share's
contents. This includes share type, lease information, encoding parameters,
hash-tree roots, public keys, and segment sizes. This command also emits a
verify-cap for the file that uses the share.

 tahoe debug dump-share testgrid/node-3/storage/shares/4v/4vozh77tsrw7mdhnj7qvp5ky74/0

"""
        return t

    def parseArgs(self, filename):
        from allmydata.util.encodingutil import argv_to_abspath
        self['filename'] = argv_to_abspath(filename)


def dump_share(options):
    from allmydata.util.encodingutil import quote_output
    from allmydata.mutable.layout import MUTABLE_MAGIC, MAX_MUTABLE_SHARE_SIZE

    out = options.stdout
    filename = options['filename']

    # check the version, to see if we have a mutable or immutable share
    print >>out, "share filename: %s" % quote_output(filename)

    share = ChunkedShare(filename, MAX_MUTABLE_SHARE_SIZE)
    prefix = share.pread(0, len(MUTABLE_MAGIC))

    if prefix == MUTABLE_MAGIC:
        return dump_mutable_share(options, share)
    else:
        return dump_immutable_share(options, share)


def dump_immutable_share(options, share):
    from allmydata.storage.backends.disk.immutable import ImmutableDiskShare

    share.DATA_OFFSET = ImmutableDiskShare.DATA_OFFSET
    out = options.stdout
    dump_immutable_chk_share(share, out, options)
    print >>out
    return 0


def dump_immutable_chk_share(share, out, options):
    from allmydata import uri
    from allmydata.util import base32
    from allmydata.immutable.layout import ReadBucketProxy
    from allmydata.util.encodingutil import quote_output, to_str

    # use a ReadBucketProxy to parse the bucket and find the uri extension
    bp = ReadBucketProxy(None, None, '')

    def read_share_data(offset, length):
        return share.pread(share.DATA_OFFSET + offset, length)

    offsets = bp._parse_offsets(read_share_data(0, 0x44))
    print >>out, "%20s: %d" % ("version", bp._version)
    seek = offsets['uri_extension']
    length = struct.unpack(bp._fieldstruct,
                           read_share_data(seek, bp._fieldsize))[0]
    seek += bp._fieldsize
    UEB_data = read_share_data(seek, length)

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
    if len(pieces) >= 2:
        piece = to_str(pieces[-2])
        if base32.could_be_base32_encoded(piece):
            storage_index = base32.a2b(piece)
            uri_extension_hash = base32.a2b(unpacked["UEB_hash"])
            u = uri.CHKFileVerifierURI(storage_index, uri_extension_hash,
                                      unpacked["needed_shares"],
                                      unpacked["total_shares"], unpacked["size"])
            verify_cap = u.to_string()
            print >>out, "%20s: %s" % ("verify-cap", quote_output(verify_cap, quotemarks=False))

    sizes = {}
    sizes['data'] = (offsets['plaintext_hash_tree'] -
                           offsets['data'])
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
        print >>out, "%20s: %s" % ("share data", share.DATA_OFFSET)
        for k in ["data", "plaintext_hash_tree", "crypttext_hash_tree",
                  "block_hashes", "share_hashes", "uri_extension"]:
            name = {"data": "block data"}.get(k,k)
            offset = share.DATA_OFFSET + offsets[k]
            print >>out, "  %20s: %s   (0x%x)" % (name, offset, offset)


def format_expiration_time(expiration_time):
    now = time.time()
    remains = expiration_time - now
    when = "%ds" % remains
    if remains > 24*3600:
        when += " (%d days)" % (remains / (24*3600))
    elif remains > 3600:
        when += " (%d hours)" % (remains / 3600)
    return when


def dump_mutable_share(options, m):
    from allmydata.util import base32, idlib
    from allmydata.storage.backends.disk.mutable import MutableDiskShare

    out = options.stdout

    m.DATA_OFFSET = MutableDiskShare.DATA_OFFSET
    WE, nodeid = MutableDiskShare._read_write_enabler_and_nodeid(m)
    data_length = MutableDiskShare._read_data_length(m)
    container_size = MutableDiskShare._read_container_size(m)

    share_type = "unknown"
    version = m.pread(m.DATA_OFFSET, 1)
    if version == "\x00":
        # this slot contains an SMDF share
        share_type = "SDMF"
    elif version == "\x01":
        share_type = "MDMF"

    print >>out
    print >>out, "Mutable slot found:"
    print >>out, " share_type: %s" % share_type
    print >>out, " write_enabler: %s" % base32.b2a(WE)
    print >>out, " WE for nodeid: %s" % idlib.nodeid_b2a(nodeid)
    print >>out, " container_size: %d" % container_size
    print >>out, " data_length: %d" % data_length
    print >>out

    if share_type == "SDMF":
        dump_SDMF_share(m, data_length, options)
    elif share_type == "MDMF":
        dump_MDMF_share(m, data_length, options)

    return 0


def dump_SDMF_share(m, length, options):
    from allmydata.mutable.layout import unpack_share, unpack_header
    from allmydata.mutable.common import NeedMoreDataError
    from allmydata.util import base32, hashutil
    from allmydata.uri import SSKVerifierURI
    from allmydata.util.encodingutil import quote_output, to_str

    out = options.stdout

    data = m.pread(m.DATA_OFFSET, min(length, 2000))

    try:
        pieces = unpack_share(data)
    except NeedMoreDataError, e:
        # retry once with the larger size
        size = e.needed_bytes
        data = m.pread(m.DATA_OFFSET, min(length, size))
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
    if len(pieces) >= 2:
        piece = to_str(pieces[-2])
        if base32.could_be_base32_encoded(piece):
            storage_index = base32.a2b(piece)
            fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey)
            u = SSKVerifierURI(storage_index, fingerprint)
            verify_cap = u.to_string()
            print >>out, "  verify-cap:", quote_output(verify_cap, quotemarks=False)

    if options['offsets']:
        # NOTE: this offset-calculation code is fragile, and needs to be
        # merged with MutableDiskShare's internals.
        print >>out
        print >>out, " Section Offsets:"
        def printoffset(name, value, shift=0):
            print >>out, "%s%20s: %s   (0x%x)" % (" "*shift, name, value, value)
        printoffset("end of header", m.DATA_OFFSET)
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

    print >>out


def dump_MDMF_share(m, length, options):
    from allmydata.mutable.layout import MDMFSlotReadProxy
    from allmydata.util import base32, hashutil
    from allmydata.uri import MDMFVerifierURI
    from allmydata.util.encodingutil import quote_output, to_str
    from allmydata.storage.backends.disk.mutable import MutableDiskShare
    DATA_OFFSET = MutableDiskShare.DATA_OFFSET

    out = options.stdout

    storage_index = None; shnum = 0

    class ShareDumper(MDMFSlotReadProxy):
        def _read(self, readvs, force_remote=False, queue=False):
            data = []
            for (where,length) in readvs:
                data.append(m.pread(DATA_OFFSET + where, length))
            return defer.succeed({shnum: data})

    p = ShareDumper(None, storage_index, shnum)
    def extract(func):
        stash = []
        # these methods return Deferreds, but we happen to know that they run
        # synchronously when not actually talking to a remote server
        d = func()
        d.addCallback(stash.append)
        return stash[0]

    verinfo = extract(p.get_verinfo)
    encprivkey = extract(p.get_encprivkey)
    signature = extract(p.get_signature)
    pubkey = extract(p.get_verification_key)
    block_hash_tree = extract(p.get_blockhashes)
    share_hash_chain = extract(p.get_sharehashes)

    (seqnum, root_hash, salt_to_use, segsize, datalen, k, N, prefix,
     offsets) = verinfo

    print >>out, " MDMF contents:"
    print >>out, "  seqnum: %d" % seqnum
    print >>out, "  root_hash: %s" % base32.b2a(root_hash)
    #print >>out, "  IV: %s" % base32.b2a(IV)
    print >>out, "  required_shares: %d" % k
    print >>out, "  total_shares: %d" % N
    print >>out, "  segsize: %d" % segsize
    print >>out, "  datalen: %d" % datalen
    print >>out, "  enc_privkey: %d bytes" % len(encprivkey)
    print >>out, "  pubkey: %d bytes" % len(pubkey)
    print >>out, "  signature: %d bytes" % len(signature)
    share_hash_ids = ",".join([str(hid)
                               for hid in sorted(share_hash_chain.keys())])
    print >>out, "  share_hash_chain: %s" % share_hash_ids
    print >>out, "  block_hash_tree: %d nodes" % len(block_hash_tree)

    # the storage index isn't stored in the share itself, so we depend upon
    # knowing the parent directory name to get it
    pieces = options['filename'].split(os.sep)
    if len(pieces) >= 2:
        piece = to_str(pieces[-2])
        if base32.could_be_base32_encoded(piece):
            storage_index = base32.a2b(piece)
            fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey)
            u = MDMFVerifierURI(storage_index, fingerprint)
            verify_cap = u.to_string()
            print >>out, "  verify-cap:", quote_output(verify_cap, quotemarks=False)

    if options['offsets']:
        # NOTE: this offset-calculation code is fragile, and needs to be
        # merged with MutableDiskShare's internals.

        print >>out
        print >>out, " Section Offsets:"
        def printoffset(name, value, shift=0):
            print >>out, "%s%.20s: %s   (0x%x)" % (" "*shift, name, value, value)
        printoffset("end of header", m.DATA_OFFSET, 2)
        printoffset("share data", m.DATA_OFFSET, 2)
        o_seqnum = m.DATA_OFFSET + struct.calcsize(">B")
        printoffset("seqnum", o_seqnum, 4)
        o_root_hash = m.DATA_OFFSET + struct.calcsize(">BQ")
        printoffset("root_hash", o_root_hash, 4)
        for k in ["enc_privkey", "share_hash_chain", "signature",
                  "verification_key", "verification_key_end",
                  "share_data", "block_hash_tree", "EOF"]:
            name = {"share_data": "block data",
                    "verification_key": "pubkey",
                    "verification_key_end": "end of pubkey",
                    "EOF": "end of share data"}.get(k,k)
            offset = m.DATA_OFFSET + offsets[k]
            printoffset(name, offset, 4)

    print >>out


class DumpCapOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-opts] debug dump-cap [options] FILECAP"
    optParameters = [
        ["nodeid", "n",
         None, "Specify the storage server nodeid (ASCII), to construct the write enabler."],
        ]
    def parseArgs(self, cap):
        self.cap = cap

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Print information about the given cap-string (aka: URI, file-cap, dir-cap,
read-cap, write-cap). The URI string is parsed and unpacked. This prints the
type of the cap, its storage index, and any derived keys.

 tahoe debug dump-cap URI:SSK-Verifier:4vozh77tsrw7mdhnj7qvp5ky74:q7f3dwz76sjys4kqfdt3ocur2pay3a6rftnkqmi2uxu3vqsdsofq

This may be useful to determine if a read-cap and a write-cap refer to the
same time, or to extract the storage-index from a file-cap (to then use with
find-shares)

For mutable write-caps, if the storage server nodeid is provided, this command
will compute the write enabler.
"""
        return t


def dump_cap(options):
    from allmydata import uri
    from base64 import b32decode
    import urlparse, urllib

    out = options.stdout
    cap = options.cap
    nodeid = None
    if options['nodeid']:
        nodeid = b32decode(options['nodeid'].upper())

    if cap.startswith("http"):
        scheme, netloc, path, params, query, fragment = urlparse.urlparse(cap)
        _assert(path.startswith("/uri/"), path=path)
        cap = urllib.unquote(path[len("/uri/"):])

    u = uri.from_string(cap)

    print >>out
    dump_uri_instance(u, nodeid, out)

def dump_uri_instance(u, nodeid, out, show_header=True):
    from allmydata import uri
    from allmydata.storage.server import si_b2a
    from allmydata.util import base32, hashutil
    from allmydata.util.encodingutil import quote_output

    if isinstance(u, uri.CHKFileURI):
        if show_header:
            print >>out, "CHK File:"
        print >>out, " key:", base32.b2a(u.key)
        print >>out, " UEB hash:", base32.b2a(u.uri_extension_hash)
        print >>out, " size:", u.size
        print >>out, " k/N: %d/%d" % (u.needed_shares, u.total_shares)
        print >>out, " storage index:", si_b2a(u.get_storage_index())
    elif isinstance(u, uri.CHKFileVerifierURI):
        if show_header:
            print >>out, "CHK Verifier URI:"
        print >>out, " UEB hash:", base32.b2a(u.uri_extension_hash)
        print >>out, " size:", u.size
        print >>out, " k/N: %d/%d" % (u.needed_shares, u.total_shares)
        print >>out, " storage index:", si_b2a(u.get_storage_index())

    elif isinstance(u, uri.LiteralFileURI):
        if show_header:
            print >>out, "Literal File URI:"
        print >>out, " data:", quote_output(u.data)

    elif isinstance(u, uri.WriteableSSKFileURI): # SDMF
        if show_header:
            print >>out, "SDMF Writeable URI:"
        print >>out, " writekey:", base32.b2a(u.writekey)
        print >>out, " readkey:", base32.b2a(u.readkey)
        print >>out, " storage index:", si_b2a(u.get_storage_index())
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)
        print >>out
        if nodeid:
            we = hashutil.ssk_write_enabler_hash(u.writekey, nodeid)
            print >>out, " write_enabler:", base32.b2a(we)
            print >>out
    elif isinstance(u, uri.ReadonlySSKFileURI):
        if show_header:
            print >>out, "SDMF Read-only URI:"
        print >>out, " readkey:", base32.b2a(u.readkey)
        print >>out, " storage index:", si_b2a(u.get_storage_index())
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)
    elif isinstance(u, uri.SSKVerifierURI):
        if show_header:
            print >>out, "SDMF Verifier URI:"
        print >>out, " storage index:", si_b2a(u.get_storage_index())
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)

    elif isinstance(u, uri.WriteableMDMFFileURI): # MDMF
        if show_header:
            print >>out, "MDMF Writeable URI:"
        print >>out, " writekey:", base32.b2a(u.writekey)
        print >>out, " readkey:", base32.b2a(u.readkey)
        print >>out, " storage index:", si_b2a(u.get_storage_index())
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)
        print >>out
        if nodeid:
            we = hashutil.ssk_write_enabler_hash(u.writekey, nodeid)
            print >>out, " write_enabler:", base32.b2a(we)
            print >>out
    elif isinstance(u, uri.ReadonlyMDMFFileURI):
        if show_header:
            print >>out, "MDMF Read-only URI:"
        print >>out, " readkey:", base32.b2a(u.readkey)
        print >>out, " storage index:", si_b2a(u.get_storage_index())
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)
    elif isinstance(u, uri.MDMFVerifierURI):
        if show_header:
            print >>out, "MDMF Verifier URI:"
        print >>out, " storage index:", si_b2a(u.get_storage_index())
        print >>out, " fingerprint:", base32.b2a(u.fingerprint)

    elif isinstance(u, uri.ImmutableDirectoryURI): # CHK-based directory
        if show_header:
            print >>out, "CHK Directory URI:"
        dump_uri_instance(u._filenode_uri, nodeid, out, False)
    elif isinstance(u, uri.ImmutableDirectoryURIVerifier):
        if show_header:
            print >>out, "CHK Directory Verifier URI:"
        dump_uri_instance(u._filenode_uri, nodeid, out, False)

    elif isinstance(u, uri.DirectoryURI): # SDMF-based directory
        if show_header:
            print >>out, "Directory Writeable URI:"
        dump_uri_instance(u._filenode_uri, nodeid, out, False)
    elif isinstance(u, uri.ReadonlyDirectoryURI):
        if show_header:
            print >>out, "Directory Read-only URI:"
        dump_uri_instance(u._filenode_uri, nodeid, out, False)
    elif isinstance(u, uri.DirectoryURIVerifier):
        if show_header:
            print >>out, "Directory Verifier URI:"
        dump_uri_instance(u._filenode_uri, nodeid, out, False)

    elif isinstance(u, uri.MDMFDirectoryURI): # MDMF-based directory
        if show_header:
            print >>out, "Directory Writeable URI:"
        dump_uri_instance(u._filenode_uri, nodeid, out, False)
    elif isinstance(u, uri.ReadonlyMDMFDirectoryURI):
        if show_header:
            print >>out, "Directory Read-only URI:"
        dump_uri_instance(u._filenode_uri, nodeid, out, False)
    elif isinstance(u, uri.MDMFDirectoryURIVerifier):
        if show_header:
            print >>out, "Directory Verifier URI:"
        dump_uri_instance(u._filenode_uri, nodeid, out, False)

    else:
        print >>out, "unknown cap type"


class FindSharesOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-opts] debug find-shares STORAGE_INDEX NODEDIRS.."

    def parseArgs(self, storage_index_s, *nodedirs):
        from allmydata.util.encodingutil import argv_to_abspath
        self.si_s = storage_index_s
        self.nodedirs = map(argv_to_abspath, nodedirs)

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Locate all shares for the given storage index. This command looks through one
or more node directories to find the shares. It returns a list of filenames,
one per line, for the initial chunk of each share found.

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
    from allmydata.storage.common import si_a2b, NUM_RE
    from allmydata.storage.backends.disk.disk_backend import si_si2dir
    from allmydata.util import fileutil
    from allmydata.util.encodingutil import quote_output

    out = options.stdout
    si = si_a2b(options.si_s)
    for nodedir in options.nodedirs:
        sharedir = si_si2dir(os.path.join(nodedir, "storage", "shares"), si)
        for shnumstr in fileutil.listdir(sharedir, filter=NUM_RE):
            sharefile = os.path.join(sharedir, shnumstr)
            print >>out, quote_output(sharefile, quotemarks=False)

    return 0


class CatalogSharesOptions(BaseOptions):
    def parseArgs(self, *nodedirs):
        from allmydata.util.encodingutil import argv_to_abspath
        self.nodedirs = map(argv_to_abspath, nodedirs)
        if not nodedirs:
            raise usage.UsageError("must specify at least one node directory")

    def getSynopsis(self):
        return "Usage: tahoe [global-opts] debug catalog-shares NODEDIRS.."

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """
Locate all shares in the given node directories, and emit a one-line summary
of each share. Run it like this:

 tahoe debug catalog-shares testgrid/node-* >allshares.txt

The lines it emits will look like the following:

 CHK $SI $k/$N $filesize $UEB_hash - $abspath_sharefile
 SDMF $SI $k/$N $filesize $seqnum/$roothash - $abspath_sharefile
 MDMF $SI $k/$N $filesize $seqnum/$roothash - $abspath_sharefile
 UNKNOWN $abspath_sharefile

This command can be used to build up a catalog of shares from many storage
servers and then sort the results to compare all shares for the same file. If
you see shares with the same SI but different parameters/filesize/UEB_hash,
then something is wrong. The misc/find-share/anomalies.py script may be
useful for that purpose.
"""
        return t


def call(c, *args, **kwargs):
    # take advantage of the fact that ImmediateReadBucketProxy returns
    # Deferreds that are already fired
    results = []
    failures = []
    d = defer.maybeDeferred(c, *args, **kwargs)
    d.addCallbacks(results.append, failures.append)
    if failures:
        failures[0].raiseException()
    return results[0]


def describe_share(abs_sharefile, si_s, shnum_s, now, out):
    from allmydata import uri
    from allmydata.storage.backends.disk.immutable import ImmutableDiskShare
    from allmydata.storage.backends.disk.mutable import MutableDiskShare
    from allmydata.mutable.layout import unpack_share, MUTABLE_MAGIC, MAX_MUTABLE_SHARE_SIZE
    from allmydata.mutable.common import NeedMoreDataError
    from allmydata.immutable.layout import ReadBucketProxy
    from allmydata.util import base32
    from allmydata.util.encodingutil import quote_output

    share = ChunkedShare(abs_sharefile, MAX_MUTABLE_SHARE_SIZE)
    prefix = share.pread(0, len(MUTABLE_MAGIC))

    if prefix == MUTABLE_MAGIC:
        share.DATA_OFFSET = MutableDiskShare.DATA_OFFSET
        WE, nodeid = MutableDiskShare._read_write_enabler_and_nodeid(share)
        data_length = MutableDiskShare._read_data_length(share)

        share_type = "unknown"
        version = share.pread(share.DATA_OFFSET, 1)
        if version == "\x00":
            # this slot contains an SMDF share
            share_type = "SDMF"
        elif version == "\x01":
            share_type = "MDMF"

        if share_type == "SDMF":
            data = share.pread(share.DATA_OFFSET, min(data_length, 2000))

            try:
                pieces = unpack_share(data)
            except NeedMoreDataError, e:
                # retry once with the larger size
                size = e.needed_bytes
                data = share.pread(share.DATA_OFFSET, min(data_length, size))
                pieces = unpack_share(data)
            (seqnum, root_hash, IV, k, N, segsize, datalen,
             pubkey, signature, share_hash_chain, block_hash_tree,
             share_data, enc_privkey) = pieces

            print >>out, "SDMF %s %d/%d %d #%d:%s - %s" % \
                  (si_s, k, N, datalen,
                   seqnum, base32.b2a(root_hash),
                   quote_output(abs_sharefile))
        elif share_type == "MDMF":
            from allmydata.mutable.layout import MDMFSlotReadProxy
            fake_shnum = 0
            # TODO: factor this out with dump_MDMF_share()
            class ShareDumper(MDMFSlotReadProxy):
                def _read(self, readvs, force_remote=False, queue=False):
                    data = []
                    for (where,length) in readvs:
                        data.append(share.pread(share.DATA_OFFSET + where, length))
                    return defer.succeed({fake_shnum: data})

            p = ShareDumper(None, "fake-si", fake_shnum)
            def extract(func):
                stash = []
                # these methods return Deferreds, but we happen to know that
                # they run synchronously when not actually talking to a
                # remote server
                d = func()
                d.addCallback(stash.append)
                return stash[0]

            verinfo = extract(p.get_verinfo)
            (seqnum, root_hash, salt_to_use, segsize, datalen, k, N, prefix,
             offsets) = verinfo
            print >>out, "MDMF %s %d/%d %d #%d:%s - %s" % \
                  (si_s, k, N, datalen,
                   seqnum, base32.b2a(root_hash),
                   quote_output(abs_sharefile))
        else:
            print >>out, "UNKNOWN mutable %s" % quote_output(abs_sharefile)

    else:
        # immutable
        share.DATA_OFFSET = ImmutableDiskShare.DATA_OFFSET

        #version = struct.unpack(">L", share.pread(0, struct.calcsize(">L")))
        #if version != 1:
        #    print >>out, "UNKNOWN really-unknown %s" % quote_output(abs_sharefile)
        #    return

        class ImmediateReadBucketProxy(ReadBucketProxy):
            def __init__(self, share):
                self.share = share
                ReadBucketProxy.__init__(self, None, None, "")
            def __repr__(self):
                return "<ImmediateReadBucketProxy>"
            def _read(self, offset, size):
                return defer.maybeDeferred(self.share.pread, share.DATA_OFFSET + offset, size)

        # use a ReadBucketProxy to parse the bucket and find the uri extension
        bp = ImmediateReadBucketProxy(share)

        UEB_data = call(bp.get_uri_extension)
        unpacked = uri.unpack_extension_readable(UEB_data)

        k = unpacked["needed_shares"]
        N = unpacked["total_shares"]
        filesize = unpacked["size"]
        ueb_hash = unpacked["UEB_hash"]

        print >>out, "CHK %s %d/%d %d %s - %s" % (si_s, k, N, filesize, ueb_hash,
                                                  quote_output(abs_sharefile))


def catalog_shares(options):
    from allmydata.util import fileutil
    from allmydata.util.encodingutil import quote_output

    out = options.stdout
    err = options.stderr
    now = time.time()
    for node_dir in options.nodedirs:
        shares_dir = os.path.join(node_dir, "storage", "shares")
        try:
            prefixes = fileutil.listdir(shares_dir)
        except EnvironmentError:
            # ignore nodes that have storage turned off altogether
            pass
        else:
            for prefix in sorted(prefixes):
                if prefix == "incoming":
                    continue
                prefix_dir = os.path.join(shares_dir, prefix)
                # this tool may get run against bad disks, so we can't assume
                # that fileutil.listdir will always succeed. Try to catalog as much
                # as possible.
                try:
                    share_dirs = fileutil.listdir(prefix_dir)
                    for si_s in sorted(share_dirs):
                        si_dir = os.path.join(prefix_dir, si_s)
                        catalog_shareset(si_s, si_dir, now, out, err)
                except:
                    print >>err, "Error processing %s" % quote_output(prefix_dir)
                    failure.Failure().printTraceback(err)

    return 0

def catalog_shareset(si_s, si_dir, now, out, err):
    from allmydata.storage.common import NUM_RE
    from allmydata.util import fileutil
    from allmydata.util.encodingutil import quote_output

    try:
        for shnum_s in sorted(fileutil.listdir(si_dir, filter=NUM_RE), key=int):
            abs_sharefile = os.path.join(si_dir, shnum_s)
            _assert(os.path.isfile(abs_sharefile), "%r is not a file" % (abs_sharefile,))
            try:
                describe_share(abs_sharefile, si_s, shnum_s, now, out)
            except:
                print >>err, "Error processing %s" % quote_output(abs_sharefile)
                failure.Failure().printTraceback(err)
    except:
        print >>err, "Error processing %s" % quote_output(si_dir)
        failure.Failure().printTraceback(err)


class CorruptShareOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-opts] debug corrupt-share SHARE_FILENAME"

    optParameters = [
        ["offset", "o", "block-random", "Specify which bit to flip."],
        ]

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
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
    do_corrupt_share(options.stdout, options['filename'], options['offset'])

def do_corrupt_share(out, filename, offset="block-random"):
    import random
    from allmydata.storage.backends.disk.immutable import ImmutableDiskShare
    from allmydata.storage.backends.disk.mutable import MutableDiskShare
    from allmydata.mutable.layout import unpack_header, MUTABLE_MAGIC, MAX_MUTABLE_SHARE_SIZE
    from allmydata.immutable.layout import ReadBucketProxy

    _assert(offset == "block-random", "other offsets not implemented")

    def flip_bit(start, end):
        offset = random.randrange(start, end)
        bit = random.randrange(0, 8)
        print >>out, "[%d..%d):  %d.b%d" % (start, end, offset, bit)
        f = open(filename, "rb+")
        try:
            f.seek(offset)
            d = f.read(1)
            d = chr(ord(d) ^ 0x01)
            f.seek(offset)
            f.write(d)
        finally:
            f.close()

    # what kind of share is it?

    share = ChunkedShare(filename, MAX_MUTABLE_SHARE_SIZE)
    prefix = share.pread(0, len(MUTABLE_MAGIC))

    if prefix == MUTABLE_MAGIC:
        data = share.pread(MutableDiskShare.DATA_OFFSET, 2000)
        # make sure this slot contains an SMDF share
        _assert(data[0] == "\x00", "non-SDMF mutable shares not supported")

        (version, ig_seqnum, ig_roothash, ig_IV, ig_k, ig_N, ig_segsize,
         ig_datalen, offsets) = unpack_header(data)

        _assert(version == 0, "we only handle v0 SDMF files")
        start = MutableDiskShare.DATA_OFFSET + offsets["share_data"]
        end = MutableDiskShare.DATA_OFFSET + offsets["enc_privkey"]
        flip_bit(start, end)
    else:
        # otherwise assume it's immutable
        bp = ReadBucketProxy(None, None, '')
        header = share.pread(ImmutableDiskShare.DATA_OFFSET, 0x24)
        offsets = bp._parse_offsets(header)
        start = ImmutableDiskShare.DATA_OFFSET + offsets["data"]
        end = ImmutableDiskShare.DATA_OFFSET + offsets["plaintext_hash_tree"]
        flip_bit(start, end)


class ReplOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-opts] debug repl"

def repl(options):
    import code
    return code.interact()


DEFAULT_TESTSUITE = 'allmydata'

class TrialOptions(twisted_trial.Options):
    def getSynopsis(self):
        return "Usage: tahoe [global-opts] debug trial [options] [[file|package|module|TestCase|testmethod]...]"

    def parseOptions(self, all_subargs, *a, **kw):
        self.trial_args = list(all_subargs)

        # any output from the option parsing will be printed twice, but that's harmless
        return twisted_trial.Options.parseOptions(self, all_subargs, *a, **kw)

    def parseArgs(self, *nonoption_args):
        if not nonoption_args:
            self.trial_args.append(DEFAULT_TESTSUITE)

    def getUsage(self, width=None):
        t = twisted_trial.Options.getUsage(self, width)
        t += """
The 'tahoe debug trial' command uses the correct imports for this instance of
Tahoe-LAFS. The default test suite is '%s'.
""" % (DEFAULT_TESTSUITE,)
        return t

def trial(config):
    sys.argv = ['trial'] + config.trial_args

    from allmydata._version import full_version
    if full_version.endswith("-dirty"):
        print >>sys.stderr
        print >>sys.stderr, "WARNING: the source tree has been modified since the last commit."
        print >>sys.stderr, "(It is usually preferable to commit, then test, then amend the commit(s)"
        print >>sys.stderr, "if the tests fail.)"
        print >>sys.stderr

    # This does not return.
    twisted_trial.run()


def fixOptionsClass( (subcmd, shortcut, OptionsClass, desc) ):
    class FixedOptionsClass(OptionsClass):
        def getSynopsis(self):
            t = OptionsClass.getSynopsis(self)
            i = t.find("Usage: flogtool ")
            if i >= 0:
                return "Usage: tahoe [global-opts] debug flogtool " + t[i+len("Usage: flogtool "):]
            else:
                return "Usage: tahoe [global-opts] debug flogtool %s [options]" % (subcmd,)
    return (subcmd, shortcut, FixedOptionsClass, desc)

class FlogtoolOptions(foolscap_cli.Options):
    def __init__(self):
        super(FlogtoolOptions, self).__init__()
        self.subCommands = map(fixOptionsClass, self.subCommands)

    def getSynopsis(self):
        return "Usage: tahoe [global-opts] debug flogtool (%s) [command options]" % ("|".join([x[0] for x in self.subCommands]))

    def parseOptions(self, all_subargs, *a, **kw):
        self.flogtool_args = list(all_subargs)
        return super(FlogtoolOptions, self).parseOptions(self.flogtool_args, *a, **kw)

    def getUsage(self, width=None):
        t = super(FlogtoolOptions, self).getUsage(width)
        t += """
The 'tahoe debug flogtool' command uses the correct imports for this instance
of Tahoe-LAFS.

Please run 'tahoe debug flogtool SUBCOMMAND --help' for more details on each
subcommand.
"""
        return t

    def opt_help(self):
        print str(self)
        sys.exit(0)

def flogtool(config):
    sys.argv = ['flogtool'] + config.flogtool_args
    return foolscap_cli.run_flogtool()


class DebugCommand(BaseOptions):
    subCommands = [
        ["dump-share", None, DumpOptions,
         "Unpack and display the contents of a share (uri_extension and leases)."],
        ["dump-cap", None, DumpCapOptions, "Unpack a read-cap or write-cap."],
        ["find-shares", None, FindSharesOptions, "Locate sharefiles in node dirs."],
        ["catalog-shares", None, CatalogSharesOptions, "Describe all shares in node dirs."],
        ["corrupt-share", None, CorruptShareOptions, "Corrupt a share by flipping a bit."],
        ["repl", None, ReplOptions, "Open a Python interpreter."],
        ["trial", None, TrialOptions, "Run tests using Twisted Trial with the right imports."],
        ["flogtool", None, FlogtoolOptions, "Utilities to access log files."],
        ]
    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    def getSynopsis(self):
        return ""
    def getUsage(self, width=None):
        #t = BaseOptions.getUsage(self, width)
        t = """Usage: tahoe debug SUBCOMMAND
Subcommands:
    tahoe debug dump-share      Unpack and display the contents of a share.
    tahoe debug dump-cap        Unpack a read-cap or write-cap.
    tahoe debug find-shares     Locate sharefiles in node directories.
    tahoe debug catalog-shares  Describe all shares in node dirs.
    tahoe debug corrupt-share   Corrupt a share by flipping a bit.
    tahoe debug repl            Open a Python interpreter.
    tahoe debug trial           Run tests using Twisted Trial with the right imports.
    tahoe debug flogtool        Utilities to access log files.

Please run e.g. 'tahoe debug dump-share --help' for more details on each
subcommand.
"""
        # See ticket #1441 for why we print different information when
        # run via /usr/bin/tahoe. Note that argv[0] is the full path.
        if sys.argv[0] == '/usr/bin/tahoe':
            t += """
To get branch coverage for the Tahoe test suite (on the installed copy of
Tahoe), install the 'python-coverage' package and then use:

    python-coverage run --branch /usr/bin/tahoe debug trial
"""
        else:
            t += """
Another debugging feature is that bin%stahoe allows executing an arbitrary
"runner" command (typically an installed Python script, such as 'coverage'),
with the Tahoe libraries on the PYTHONPATH. The runner command name is
prefixed with '@', and any occurrences of '@tahoe' in its arguments are
replaced by the full path to the tahoe script.

For example, if 'coverage' is installed and on the PATH, you can use:

    bin%stahoe @coverage run --branch @tahoe debug trial

to get branch coverage for the Tahoe test suite. Or, to run python with
the -3 option that warns about Python 3 incompatibilities:

    bin%stahoe @python -3 @tahoe command [options]
""" % (os.sep, os.sep, os.sep)
        return t

subDispatch = {
    "dump-share": dump_share,
    "dump-cap": dump_cap,
    "find-shares": find_shares,
    "catalog-shares": catalog_shares,
    "corrupt-share": corrupt_share,
    "repl": repl,
    "trial": trial,
    "flogtool": flogtool,
    }


def do_debug(options):
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    return f(so)


subCommands = [
    ["debug", None, DebugCommand, "debug subcommands: use 'tahoe debug' for a list."],
    ]

dispatch = {
    "debug": do_debug,
    }
