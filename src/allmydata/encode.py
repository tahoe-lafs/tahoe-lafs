# -*- test-case-name: allmydata.test.test_encode_share -*-

from zope.interface import implements
from twisted.internet import defer
import sha
from allmydata.util import idlib, mathutil
from allmydata.interfaces import IEncoder, IDecoder
from allmydata.py_ecc import rs_code

def netstring(s):
    return "%d:%s," % (len(s), s)

class ReplicatingEncoder(object):
    implements(IEncoder)
    ENCODER_TYPE = 0

    def set_params(self, data_size, required_shares, total_shares):
        self.data_size = data_size
        self.required_shares = required_shares
        self.total_shares = total_shares

    def get_encoder_type(self):
        return self.ENCODER_TYPE

    def get_serialized_params(self):
        return "%d" % self.required_shares

    def get_share_size(self):
        return self.data_size

    def encode(self, data):
        shares = [(i,data) for i in range(self.total_shares)]
        return defer.succeed(shares)

class ReplicatingDecoder(object):
    implements(IDecoder)

    def set_serialized_params(self, params):
        self.required_shares = int(params)

    def decode(self, some_shares):
        assert len(some_shares) >= self.required_shares
        data = some_shares[0][1]
        return defer.succeed(data)


class Encoder(object):
    def __init__(self, infile, m):
        self.infile = infile
        self.k = 2
        self.m = m

    def do_upload(self, landlords):
        dl = []
        data = self.infile.read()
        for (peerid, bucket_num, remotebucket) in landlords:
            dl.append(remotebucket.callRemote('write', data))
            dl.append(remotebucket.callRemote('close'))

        return defer.DeferredList(dl)

class Decoder(object):
    def __init__(self, outfile, k, m, verifierid):
        self.outfile = outfile
        self.k = 2
        self.m = m
        self._verifierid = verifierid

    def start(self, buckets):
        assert len(buckets) >= self.k
        dl = []
        for bucketnum, bucket in buckets[:self.k]:
            d = bucket.callRemote("read")
            dl.append(d)
        d2 = defer.DeferredList(dl)
        d2.addCallback(self._got_all_data)
        return d2

    def _got_all_data(self, resultslist):
        shares = [results for success,results in resultslist if success]
        assert len(shares) >= self.k
        # here's where the Reed-Solomon magic takes place
        self.outfile.write(shares[0])
        hasher = sha.new(netstring("allmydata_v1_verifierid"))
        hasher.update(shares[0])
        vid = hasher.digest()
        if self._verifierid:
            assert self._verifierid == vid, "%s != %s" % (idlib.b2a(self._verifierid), idlib.b2a(vid))


class PyRSEncoder(object):
    ENCODER_TYPE = 1

    # we will break the data into vectors in which each element is a single
    # byte (i.e. a single number from 0 to 255), and the length of the vector
    # is equal to the number of required_shares. We use padding to make the
    # last chunk of data long enough to match, and we record the data_size in
    # the serialized parameters to strip this padding out on the receiving
    # end.

    # TODO: this will write a 733kB file called 'ffield.lut.8' in the current
    # directory the first time it is run, to cache the lookup table for later
    # use. It appears to take about 15 seconds to create this the first time,
    # and about 0.5s to load it in each time afterwards. Make sure this file
    # winds up somewhere reasonable.

    # TODO: the encoder/decoder RSCode object depends upon the number of
    # required/total shares, but not upon the data. We could probably save a
    # lot of initialization time by caching a single instance and using it
    # any time we use the same required/total share numbers (which will
    # probably be always).

    # on my workstation (fluxx, a 3.5GHz Athlon), this encodes data at a rate
    # of 6.7kBps. Zooko's mom's 1.8GHz G5 got 2.2kBps . slave3 took 40s to
    # construct the LUT and encodes at 1.5kBps, and for some reason took more
    # than 20 minutes to run the test_encode_share tests, so I disabled most
    # of them.

    def set_params(self, data_size, required_shares, total_shares):
        assert required_shares <= total_shares
        self.data_size = data_size
        self.required_shares = required_shares
        self.total_shares = total_shares
        self.chunk_size = required_shares
        self.num_chunks = mathutil.div_ceil(data_size, self.chunk_size)
        self.last_chunk_padding = mathutil.pad_size(data_size, required_shares)
        self.share_size = self.num_chunks
        self.encoder = rs_code.RSCode(total_shares, required_shares, 8)

    def get_encoder_type(self):
        return self.ENCODER_TYPE

    def get_serialized_params(self):
        return "%d:%d:%d" % (self.data_size, self.required_shares,
                             self.total_shares)

    def get_share_size(self):
        return self.share_size

    def encode(self, data):
        share_data = [ [] for i in range(self.total_shares)]
        for i in range(self.num_chunks):
            # we take self.chunk_size bytes from the input string, and
            # turn it into self.total_shares bytes.
            offset = i*self.chunk_size
            # Note string slices aren't an efficient way to use memory, so
            # when we upgrade from the unusably slow py_ecc prototype to a
            # fast ECC we should also fix up this memory usage (by using the
            # array module).
            chunk = data[offset:offset+self.chunk_size]
            if i == self.num_chunks-1:
                chunk = chunk + "\x00"*self.last_chunk_padding
            assert len(chunk) == self.chunk_size
            input_vector = [ord(x) for x in chunk]
            assert len(input_vector) == self.required_shares
            output_vector = self.encoder.Encode(input_vector)
            assert len(output_vector) == self.total_shares
            for i2,out in enumerate(output_vector):
                share_data[i2].append(chr(out))

        shares = [ (i, "".join(share_data[i]))
                   for i in range(self.total_shares) ]
        return defer.succeed(shares)

class PyRSDecoder(object):

    def set_serialized_params(self, params):
        pieces = params.split(":")
        self.data_size = int(pieces[0])
        self.required_shares = int(pieces[1])
        self.total_shares = int(pieces[2])

        self.chunk_size = self.required_shares
        self.num_chunks = mathutil.div_ceil(self.data_size, self.chunk_size)
        self.last_chunk_padding = mathutil.pad_size(self.data_size,
                                                    self.required_shares)
        self.share_size = self.num_chunks
        self.encoder = rs_code.RSCode(self.total_shares, self.required_shares,
                                      8)
        if False:
            print "chunk_size: %d" % self.chunk_size
            print "num_chunks: %d" % self.num_chunks
            print "last_chunk_padding: %d" % self.last_chunk_padding
            print "share_size: %d" % self.share_size
            print "total_shares: %d" % self.total_shares
            print "required_shares: %d" % self.required_shares

    def decode(self, some_shares):
        chunk_size = self.chunk_size
        assert len(some_shares) >= self.required_shares
        chunks = []
        have_shares = {}
        for share_num, share_data in some_shares:
            have_shares[share_num] = share_data
        for i in range(self.share_size):
            # this takes one byte from each share, and turns the combination
            # into a single chunk
            received_vector = []
            for j in range(self.total_shares):
                share = have_shares.get(j)
                if share is not None:
                    received_vector.append(ord(share[i]))
                else:
                    received_vector.append(None)
            decoded_vector = self.encoder.DecodeImmediate(received_vector)
            assert len(decoded_vector) == self.chunk_size
            chunk = "".join([chr(x) for x in decoded_vector])
            chunks.append(chunk)
        data = "".join(chunks)
        if self.last_chunk_padding:
            data = data[:-self.last_chunk_padding]
        assert len(data) == self.data_size
        return defer.succeed(data)


all_encoders = {
    ReplicatingEncoder.ENCODER_TYPE: (ReplicatingEncoder, ReplicatingDecoder),
    PyRSEncoder.ENCODER_TYPE: (PyRSEncoder, PyRSDecoder),
    }
