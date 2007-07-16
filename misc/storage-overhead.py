#! /usr/bin/python

import sys, math
from cStringIO import StringIO
from allmydata import upload, uri, encode, storage
from allmydata.util import mathutil

def roundup(size, blocksize=4096):
    return blocksize * mathutil.div_ceil(size, blocksize)


class BigFakeString:
    def __init__(self, length):
        self.length = length
        self.fp = 0
    def seek(self, offset, whence=0):
        if whence == 0:
            self.fp = offset
        elif whence == 1:
            self.fp += offset
        elif whence == 2:
            self.fp = self.length - offset
    def tell(self):
        return self.fp

def calc(filesize, params=(3,7,10), segsize=encode.Encoder.MAX_SEGMENT_SIZE):
    num_shares = params[2]
    if filesize <= upload.Uploader.URI_LIT_SIZE_THRESHOLD:
        urisize = len(uri.pack_lit("A"*filesize))
        sharesize = 0
        sharespace = 0
    else:
        u = upload.FileUploader(None)
        u.set_params(params)
        # unfortunately, Encoder doesn't currently lend itself to answering
        # this question without measuring a filesize, so we have to give it a
        # fake one
        data = BigFakeString(filesize)
        u.set_filehandle(data)
        u.set_encryption_key("a"*16)
        sharesize, blocksize = u.setup_encoder()
        # how much overhead?
        #  0x20 bytes of offsets
        #  0x04 bytes of extension length
        #  0x1ad bytes of extension (=429)
        # total is 465 bytes
        num_segments = mathutil.div_ceil(filesize, segsize)
        num_share_hashes = int(math.log(mathutil.next_power_of_k(num_shares, 2),
                                    2)) + 1
        sharesize = storage.allocated_size(sharesize, num_segments,
                                           num_share_hashes,
                                           429)
        sharespace = num_shares * roundup(sharesize)
        urisize = len(uri.pack_uri(storage_index="a"*32,
                                   key="a"*16,
                                   uri_extension_hash="a"*32,
                                   needed_shares=params[0],
                                   total_shares=params[2],
                                   size=filesize))

    return urisize, sharesize, sharespace

def main():
    filesize = int(sys.argv[1])
    urisize, sharesize, sharespace = calc(filesize)
    print "urisize:", urisize
    print "sharesize:  %10d" % sharesize
    print "sharespace: %10d" % sharespace
    print "desired expansion: %1.1f" % (1.0 * 10 / 3)
    print "effective expansion: %1.1f" % (1.0 * sharespace / filesize)

def chart():
    filesize = 2
    while filesize < 2**20:
        urisize, sharesize, sharespace = calc(int(filesize))
        expansion = 1.0 * sharespace / int(filesize)
        print "%d,%d,%d,%1.2f" % (int(filesize), urisize, sharespace, expansion)
        filesize  = filesize * 2**0.5

if __name__ == '__main__':
    if sys.argv[1] == "chart":
        chart()
    else:
        main()

