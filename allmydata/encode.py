from twisted.internet import defer
import sha
from allmydata.util import idlib

def netstring(s):
    return "%d:%s," % (len(s), s)

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

