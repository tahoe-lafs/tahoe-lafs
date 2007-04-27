import easyfec, zfec
from util import fileutil
from util.mathutil import log_ceil

import array, os, re, struct, traceback

CHUNKSIZE = 4096

class InsufficientShareFilesError(zfec.Error):
    def __init__(self, k, kb, *args, **kwargs):
        zfec.Error.__init__(self, *args, **kwargs)
        self.k = k
        self.kb = kb

    def __repr__(self):
        return "Insufficient share files -- %d share files are required to recover this file, but only %d were given" % (self.k, self.kb,)

    def __str__(self):
        return self.__repr__()

class CorruptedShareFilesError(zfec.Error):
    pass

def _build_header(m, k, pad, sh):
    """
    @param m: the total number of shares; 3 <= m <= 256
    @param k: the number of shares required to reconstruct; 2 <= k < m
    @param pad: the number of bytes of padding added to the file before encoding; 0 <= pad < k
    @param sh: the shnum of this share; 0 <= k < m

    @return: a string (which is hopefully short) encoding m, k, sh, and pad
    """
    assert m >= 3
    assert m <= 2**8
    assert k >= 2
    assert k < m
    assert pad >= 0
    assert pad < k

    assert sh >= 0
    assert sh < m

    bitsused = 0
    val = 0

    val |= (m - 3)
    bitsused += 8 # the first 8 bits always encode m

    kbits = log_ceil(m-2, 2) # num bits needed to store all possible values of k
    val <<= kbits
    bitsused += kbits

    val |= (k - 2)

    padbits = log_ceil(k, 2) # num bits needed to store all possible values of pad
    val <<= padbits
    bitsused += padbits

    val |= pad

    shnumbits = log_ceil(m, 2) # num bits needed to store all possible values of shnum
    val <<= shnumbits
    bitsused += shnumbits

    val |= sh

    assert bitsused >= 11
    assert bitsused <= 32

    if bitsused <= 16:
        val <<= (16-bitsused)
        cs = struct.pack('>H', val)
        assert cs[:-2] == '\x00' * (len(cs)-2)
        return cs[-2:]
    if bitsused <= 24:
        val <<= (24-bitsused)
        cs = struct.pack('>I', val)
        assert cs[:-3] == '\x00' * (len(cs)-3)
        return cs[-3:]
    else:
        val <<= (32-bitsused)
        cs = struct.pack('>I', val)
        assert cs[:-4] == '\x00' * (len(cs)-4)
        return cs[-4:]

def MASK(bits):
    return (1<<bits)-1

def _parse_header(inf):
    """
    @param inf: an object which I can call read(1) on to get another byte

    @return: tuple of (m, k, pad, sh,); side-effect: the first one to four
        bytes of inf will be read
    """
    # The first 8 bits always encode m.
    ch = inf.read(1)
    if not ch:
        raise CorruptedShareFilesError("Share files were corrupted -- share file %r didn't have a complete metadata header at the front.  Perhaps the file was truncated." % (inf.name,))
    byte = ord(ch)
    m = byte + 3

    # The next few bits encode k.
    kbits = log_ceil(m-2, 2) # num bits needed to store all possible values of k
    b2_bits_left = 8-kbits
    kbitmask = MASK(kbits) << b2_bits_left
    ch = inf.read(1)
    if not ch:
        raise CorruptedShareFilesError("Share files were corrupted -- share file %r didn't have a complete metadata header at the front.  Perhaps the file was truncated." % (inf.name,))
    byte = ord(ch)
    k = ((byte & kbitmask) >> b2_bits_left) + 2

    shbits = log_ceil(m, 2) # num bits needed to store all possible values of shnum
    padbits = log_ceil(k, 2) # num bits needed to store all possible values of pad

    val = byte & (~kbitmask)

    needed_padbits = padbits - b2_bits_left
    if needed_padbits > 0:
        ch = inf.read(1)
        if not ch:
            raise CorruptedShareFilesError("Share files were corrupted -- share file %r didn't have a complete metadata header at the front.  Perhaps the file was truncated." % (inf.name,))
        byte = struct.unpack(">B", ch)[0]
        val <<= 8
        val |= byte 
        needed_padbits -= 8
    assert needed_padbits <= 0
    extrabits = -needed_padbits
    pad = val >> extrabits
    val &= MASK(extrabits)

    needed_shbits = shbits - extrabits
    if needed_shbits > 0:
        ch = inf.read(1)
        if not ch:
            raise CorruptedShareFilesError("Share files were corrupted -- share file %r didn't have a complete metadata header at the front.  Perhaps the file was truncated." % (inf.name,))
        byte = struct.unpack(">B", ch)[0]
        val <<= 8
        val |= byte 
        needed_shbits -= 8
    assert needed_shbits <= 0

    gotshbits = -needed_shbits

    sh = val >> gotshbits

    return (m, k, pad, sh,)

FORMAT_FORMAT = "%%s.%%0%dd_%%0%dd%%s"
RE_FORMAT = "%s.[0-9]+_[0-9]+%s"
def encode_to_files(inf, fsize, dirname, prefix, k, m, suffix=".fec", overwrite=False, verbose=False):
    """
    Encode inf, writing the shares to specially named, newly created files.

    @param fsize: calling read() on inf must yield fsize bytes of data and 
        then raise an EOFError
    @param dirname: the name of the directory into which the sharefiles will
        be written
    """
    mlen = len(str(m))
    format = FORMAT_FORMAT % (mlen, mlen,)

    padbytes = zfec.util.mathutil.pad_size(fsize, k)

    fns = []
    fs = []
    try:
        for shnum in range(m):
            hdr = _build_header(m, k, padbytes, shnum)

            fn = os.path.join(dirname, format % (prefix, shnum, m, suffix,))
            if verbose:
                print "Creating share file %r..." % (fn,)
            if overwrite:
                f = open(fn, "wb")
            else:
                flags = os.O_WRONLY|os.O_CREAT|os.O_EXCL | (hasattr(os, 'O_BINARY') and os.O_BINARY)
                fd = os.open(fn, flags)
                f = os.fdopen(fd, "wb")
            f.write(hdr)
            fs.append(f)
            fns.append(fn)
        sumlen = [0]
        def cb(blocks, length):
            assert len(blocks) == len(fs)
            oldsumlen = sumlen[0]
            sumlen[0] += length
            if verbose:
                if int((float(oldsumlen) / fsize) * 10) != int((float(sumlen[0]) / fsize) * 10):
                    print str(int((float(sumlen[0]) / fsize) * 10) * 10) + "% ...",
            
            if sumlen[0] > fsize:
                raise IOError("Wrong file size -- possibly the size of the file changed during encoding.  Original size: %d, observed size at least: %s" % (fsize, sumlen[0],))
            for i in range(len(blocks)):
                data = blocks[i]
                fs[i].write(data)
                length -= len(data)

        encode_file_stringy_easyfec(inf, cb, k, m, chunksize=4096)
    except EnvironmentError, le:
        print "Cannot complete because of exception: "
        print le
        print "Cleaning up..."
        # clean up
        while fs:
            f = fs.pop()
            f.close() ; del f
            fn = fns.pop()
            if verbose:
                print "Cleaning up: trying to remove %r..." % (fn,)
            fileutil.remove_if_possible(fn)
        return 1
    if verbose:
        print 
        print "Done!"
    return 0

# Note: if you really prefer base-2 and you change this code, then please
# denote 2^20 as "MiB" instead of "MB" in order to avoid ambiguity.
# Thanks.
# http://en.wikipedia.org/wiki/Megabyte
MILLION_BYTES=10**6

def decode_from_files(outf, infiles, verbose=False):
    """
    Decode from the first k files in infiles, writing the results to outf.
    """
    assert len(infiles) >= 2
    infs = []
    shnums = []
    m = None
    k = None
    padlen = None

    byteswritten = 0
    for f in infiles:
        (nm, nk, npadlen, shnum,) = _parse_header(f)
        if not (m is None or m == nm):
            raise CorruptedShareFilesError("Share files were corrupted -- share file %r said that m was %s but another share file previously said that m was %s" % (f.name, nm, m,))
        m = nm
        if not (k is None or k == nk):
            raise CorruptedShareFilesError("Share files were corrupted -- share file %r said that k was %s but another share file previously said that k was %s" % (f.name, nk, k,))
        if k > len(infiles):
            raise InsufficientShareFilesError(k, len(infiles))
        k = nk
        if not (padlen is None or padlen == npadlen):
            raise CorruptedShareFilesError("Share files were corrupted -- share file %r said that pad length was %s but another share file previously said that pad length was %s" % (f.name, npadlen, padlen,))
        padlen = npadlen

        infs.append(f)
        shnums.append(shnum)

        if len(infs) == k:
            break

    dec = easyfec.Decoder(k, m)

    while True:
        chunks = [ inf.read(CHUNKSIZE) for inf in infs ]
        if [ch for ch in chunks if len(ch) != len(chunks[-1])]:
            raise CorruptedShareFilesError("Share files were corrupted -- all share files are required to be the same length, but they weren't.")

        if len(chunks[-1]) == CHUNKSIZE:
            # Then this was a full read, so we're still in the sharefiles.
            resultdata = dec.decode(chunks, shnums, padlen=0)
            outf.write(resultdata)
            byteswritten += len(resultdata)
            if verbose:
                if ((byteswritten - len(resultdata)) / (10*MILLION_BYTES)) != (byteswritten / (10*MILLION_BYTES)):
                    print str(byteswritten / MILLION_BYTES) + " MB ...",
        else:
            # Then this was a short read, so we've reached the end of the sharefiles.
            resultdata = dec.decode(chunks, shnums, padlen)
            outf.write(resultdata)
            return # Done.
    if verbose:
        print
        print "Done!"

def encode_file(inf, cb, k, m, chunksize=4096):
    """
    Read in the contents of inf, encode, and call cb with the results.

    First, k "input blocks" will be read from inf, each input block being of 
    size chunksize.  Then these k blocks will be encoded into m "result 
    blocks".  Then cb will be invoked, passing a list of the m result blocks 
    as its first argument, and the length of the encoded data as its second 
    argument.  (The length of the encoded data is always equal to k*chunksize, 
    until the last iteration, when the end of the file has been reached and 
    less than k*chunksize bytes could be read from the file.)  This procedure 
    is iterated until the end of the file is reached, in which case the space 
    of the input blocks that is unused is filled with zeroes before encoding.

    Note that the sequence passed in calls to cb() contains mutable array
    objects in its first k elements whose contents will be overwritten when 
    the next segment is read from the input file.  Therefore the 
    implementation of cb() has to either be finished with those first k arrays 
    before returning, or if it wants to keep the contents of those arrays for 
    subsequent use after it has returned then it must make a copy of them to 
    keep.

    @param inf the file object from which to read the data
    @param cb the callback to be invoked with the results
    @param k the number of shares required to reconstruct the file
    @param m the total number of shares created
    @param chunksize how much data to read from inf for each of the k input 
        blocks
    """
    enc = zfec.Encoder(k, m)
    l = tuple([ array.array('c') for i in range(k) ])
    indatasize = k*chunksize # will be reset to shorter upon EOF
    eof = False
    ZEROES=array.array('c', ['\x00'])*chunksize
    while not eof:
        # This loop body executes once per segment.
        i = 0
        while (i<len(l)):
            # This loop body executes once per chunk.
            a = l[i]
            del a[:]
            try:
                a.fromfile(inf, chunksize)
                i += 1
            except EOFError:
                eof = True
                indatasize = i*chunksize + len(a)
                
                # padding
                a.fromstring("\x00" * (chunksize-len(a)))
                i += 1
                while (i<len(l)):
                    a = l[i]
                    a[:] = ZEROES
                    i += 1

        res = enc.encode(l)
        cb(res, indatasize)

def encode_file_stringy(inf, cb, k, m, chunksize=4096):
    """
    Read in the contents of inf, encode, and call cb with the results.

    First, k "input blocks" will be read from inf, each input block being of 
    size chunksize.  Then these k blocks will be encoded into m "result 
    blocks".  Then cb will be invoked, passing a list of the m result blocks 
    as its first argument, and the length of the encoded data as its second 
    argument.  (The length of the encoded data is always equal to k*chunksize, 
    until the last iteration, when the end of the file has been reached and 
    less than k*chunksize bytes could be read from the file.)  This procedure 
    is iterated until the end of the file is reached, in which case the part 
    of the input shares that is unused is filled with zeroes before encoding.

    @param inf the file object from which to read the data
    @param cb the callback to be invoked with the results
    @param k the number of shares required to reconstruct the file
    @param m the total number of shares created
    @param chunksize how much data to read from inf for each of the k input 
        blocks
    """
    enc = zfec.Encoder(k, m)
    indatasize = k*chunksize # will be reset to shorter upon EOF
    while indatasize == k*chunksize:
        # This loop body executes once per segment.
        i = 0
        l = []
        ZEROES = '\x00'*chunksize
        while i<k:
            # This loop body executes once per chunk.
            i += 1
            l.append(inf.read(chunksize))
            if len(l[-1]) < chunksize:
                indatasize = i*chunksize + len(l[-1])
                
                # padding
                l[-1] = l[-1] + "\x00" * (chunksize-len(l[-1]))
                while i<k:
                    l.append(ZEROES)
                    i += 1

        res = enc.encode(l)
        cb(res, indatasize)

def encode_file_stringy_easyfec(inf, cb, k, m, chunksize=4096):
    """
    Read in the contents of inf, encode, and call cb with the results.

    First, chunksize*k bytes will be read from inf, then encoded into m
    "result blocks".  Then cb will be invoked, passing a list of the m result
    blocks as its first argument, and the length of the encoded data as its
    second argument.  (The length of the encoded data is always equal to
    k*chunksize, until the last iteration, when the end of the file has been
    reached and less than k*chunksize bytes could be read from the file.)
    This procedure is iterated until the end of the file is reached, in which
    case the space of the input that is unused is filled with zeroes before
    encoding.

    @param inf the file object from which to read the data
    @param cb the callback to be invoked with the results
    @param k the number of shares required to reconstruct the file
    @param m the total number of shares created
    @param chunksize how much data to read from inf for each of the k input 
        blocks
    """
    enc = easyfec.Encoder(k, m)

    readsize = k*chunksize
    indata = inf.read(readsize)
    while indata:
        res = enc.encode(indata)
        cb(res, len(indata))
        indata = inf.read(readsize)

# zfec -- fast forward error correction library with Python interface
# 
# Copyright (C) 2007 Allmydata, Inc.
# Author: Zooko Wilcox-O'Hearn
# 
# This file is part of zfec.
# 
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version, with the added permission that, if you become obligated
# to release a derived work under this licence (as per section 2.b), you may
# delay the fulfillment of this obligation for up to 12 months.  See the file
# COPYING for details.
#
# If you would like to inquire about a commercial relationship with Allmydata,
# Inc., please contact partnerships@allmydata.com and visit
# http://allmydata.com/.
# 
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
