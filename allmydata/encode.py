from twisted.internet import defer

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
