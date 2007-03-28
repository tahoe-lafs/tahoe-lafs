# -*- test-case-name: allmydata.test.test_encode_share -*-

from zope.interface import implements
from twisted.internet import defer
import sha
from allmydata.util import idlib, mathutil
from allmydata.util.assertutil import precondition
from allmydata.interfaces import ICodecEncoder, ICodecDecoder
import fec

def netstring(s):
    return "%d:%s," % (len(s), s)

from base64 import b32encode
def ab(x): # debuggery
    if len(x) >= 3:
        return "%s:%s" % (len(x), b32encode(x[-3:]),)
    elif len(x) == 2:
        return "%s:%s" % (len(x), b32encode(x[-2:]),)
    elif len(x) == 1:
        return "%s:%s" % (len(x), b32encode(x[-1:]),)
    elif len(x) == 0:
        return "%s:%s" % (len(x), "--empty--",)


class ReplicatingEncoder(object):
    implements(ICodecEncoder)
    ENCODER_TYPE = "rep"

    def set_params(self, data_size, required_shares, max_shares):
        assert data_size % required_shares == 0
        assert required_shares <= max_shares
        self.data_size = data_size
        self.required_shares = required_shares
        self.max_shares = max_shares

    def get_encoder_type(self):
        return self.ENCODER_TYPE

    def get_serialized_params(self):
        return "%d" % self.required_shares

    def get_share_size(self):
        return self.data_size

    def encode(self, inshares, desired_shareids=None):
        assert isinstance(inshares, list)
        for inshare in inshares:
            assert isinstance(inshare, str)
            assert self.required_shares * len(inshare) == self.data_size
        data = "".join(inshares)
        if desired_shareids is None:
            desired_shareids = range(self.max_shares)
        shares = [data for i in desired_shareids]
        return defer.succeed((shares, desired_shareids))

class ReplicatingDecoder(object):
    implements(ICodecDecoder)

    def set_serialized_params(self, params):
        self.required_shares = int(params)

    def get_required_shares(self):
        return self.required_shares

    def decode(self, some_shares, their_shareids):
        assert len(some_shares) == self.required_shares
        assert len(some_shares) == len(their_shareids)
        data = some_shares[0]

        chunksize = mathutil.div_ceil(len(data), self.required_shares)
        numchunks = mathutil.div_ceil(len(data), chunksize)
        l = [ data[i:i+chunksize] for i in range(0, len(data), chunksize) ]

        return defer.succeed(l)


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
        self.k = k
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


class CRSEncoder(object):
    implements(ICodecEncoder)
    ENCODER_TYPE = "crs"

    def set_params(self, data_size, required_shares, max_shares):
        assert required_shares <= max_shares
        self.data_size = data_size
        self.required_shares = required_shares
        self.max_shares = max_shares
        self.share_size = mathutil.div_ceil(data_size, required_shares)
        self.last_share_padding = mathutil.pad_size(self.share_size, required_shares)
        self.encoder = fec.Encoder(required_shares, max_shares)

    def get_encoder_type(self):
        return self.ENCODER_TYPE

    def get_serialized_params(self):
        return "%d-%d-%d" % (self.data_size, self.required_shares,
                             self.max_shares)

    def get_share_size(self):
        return self.share_size

    def encode(self, inshares, desired_share_ids=None):
        precondition(desired_share_ids is None or len(desired_share_ids) <= self.max_shares, desired_share_ids, self.max_shares)

        if desired_share_ids is None:
            desired_share_ids = range(self.max_shares)

        for inshare in inshares:
            assert len(inshare) == self.share_size, (len(inshare), self.share_size, self.data_size, self.required_shares)
        shares = self.encoder.encode(inshares, desired_share_ids)

        return defer.succeed((shares, desired_share_ids))

class CRSDecoder(object):
    implements(ICodecDecoder)

    def set_serialized_params(self, params):
        pieces = params.split("-")
        self.data_size = int(pieces[0])
        self.required_shares = int(pieces[1])
        self.max_shares = int(pieces[2])

        self.chunk_size = self.required_shares
        self.num_chunks = mathutil.div_ceil(self.data_size, self.chunk_size)
        self.share_size = self.num_chunks
        self.decoder = fec.Decoder(self.required_shares, self.max_shares)
        if False:
            print "chunk_size: %d" % self.chunk_size
            print "num_chunks: %d" % self.num_chunks
            print "share_size: %d" % self.share_size
            print "max_shares: %d" % self.max_shares
            print "required_shares: %d" % self.required_shares

    def get_required_shares(self):
        return self.required_shares

    def decode(self, some_shares, their_shareids):
        precondition(len(some_shares) == len(their_shareids), len(some_shares), len(their_shareids))
        precondition(len(some_shares) == self.required_shares, len(some_shares), self.required_shares)
        return defer.succeed(self.decoder.decode(some_shares, [int(s) for s in their_shareids]))


all_encoders = {
    ReplicatingEncoder.ENCODER_TYPE: (ReplicatingEncoder, ReplicatingDecoder),
    CRSEncoder.ENCODER_TYPE: (CRSEncoder, CRSDecoder),
    }

def get_decoder_by_name(name):
    decoder_class = all_encoders[name][1]
    return decoder_class()

