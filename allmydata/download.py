
import os
from zope.interface import Interface, implements
from twisted.python import failure, log
from twisted.internet import defer
from twisted.application import service

from allmydata.util import idlib
from allmydata import encode

class NotEnoughPeersError(Exception):
    pass

class HaveAllPeersError(Exception):
    # we use this to jump out of the loop
    pass

class FileDownloader:
    debug = False

    def __init__(self, peer, verifierid):
        self._peer = peer
        assert isinstance(verifierid, str)
        self._verifierid = verifierid

    def set_download_target(self, target):
        self._target = target
        self._target.register_canceller(self._cancel)

    def _cancel(self):
        pass

    def make_decoder(self):
        n = self._shares = 4
        k = self._desired_shares = 2
        self._target.open()
        self._decoder = encode.Decoder(self._target, k, n,
                                       self._verifierid)

    def start(self):
        log.msg("starting download")
        if self.debug:
            print "starting download"
        # first step: who should we download from?

        # maybe limit max_peers to 2*len(self.shares), to reduce memory
        # footprint
        max_peers = None

        self.permuted = self._peer.permute_peerids(self._verifierid, max_peers)
        for p in self.permuted:
            assert isinstance(p, str)
        self.landlords = [] # list of (peerid, bucket_num, remotebucket)

        d = defer.maybeDeferred(self._check_next_peer)
        d.addCallback(self._got_all_peers)
        return d

    def _check_next_peer(self):
        if len(self.permuted) == 0:
            # there are no more to check
            raise NotEnoughPeersError
        peerid = self.permuted.pop(0)

        d = self._peer.get_remote_service(peerid, "storageserver")
        def _got_peer(service):
            bucket_num = len(self.landlords)
            if self.debug: print "asking %s" % idlib.b2a(peerid)
            d2 = service.callRemote("get_buckets", verifierid=self._verifierid)
            def _got_response(buckets):
                if buckets:
                    bucket_nums = [num for (num,bucket) in buckets]
                    if self.debug:
                        print " peerid %s has buckets %s" % (idlib.b2a(peerid),
                                                             bucket_nums)

                    self.landlords.append( (peerid, buckets) )
                if len(self.landlords) >= self._desired_shares:
                    if self.debug: print " we're done!"
                    raise HaveAllPeersError
                # otherwise we fall through to search more peers
            d2.addCallback(_got_response)
            return d2
        d.addCallback(_got_peer)

        def _done_with_peer(res):
            if self.debug: print "done with peer %s:" % idlib.b2a(peerid)
            if isinstance(res, failure.Failure):
                if res.check(HaveAllPeersError):
                    if self.debug: print " all done"
                    # we're done!
                    return
                if res.check(IndexError):
                    if self.debug: print " no connection"
                else:
                    if self.debug: print " other error:", res
            else:
                if self.debug: print " they had data for us"
            # we get here for either good peers (when we still need more), or
            # after checking a bad peer (and thus still need more). So now we
            # need to grab a new peer.
            return self._check_next_peer()
        d.addBoth(_done_with_peer)
        return d

    def _got_all_peers(self, res):
        all_buckets = []
        for peerid, buckets in self.landlords:
            all_buckets.extend(buckets)
        d = self._decoder.start(all_buckets)
        def _done(res):
            self._target.close()
            return self._target.finish()
        def _fail(res):
            self._target.fail()
            return res
        d.addCallbacks(_done, _fail)
        return d

def netstring(s):
    return "%d:%s," % (len(s), s)

class IDownloadTarget(Interface):
    def open():
        """Called before any calls to write() or close()."""
    def write(data):
        pass
    def close():
        pass
    def fail():
        """fail() is called to indicate that the download has failed. No
        further methods will be invoked on the IDownloadTarget after fail()."""
    def register_canceller(cb):
        """The FileDownloader uses this to register a no-argument function
        that the target can call to cancel the download. Once this canceller
        is invoked, no further calls to write() or close() will be made."""
    def finish(self):
        """When the FileDownloader is done, this finish() function will be
        called. Whatever it returns will be returned to the invoker of
        Downloader.download.
        """

class FileName:
    implements(IDownloadTarget)
    def __init__(self, filename):
        self._filename = filename
    def open(self):
        self.f = open(self._filename, "wb")
        return self.f
    def write(self, data):
        self.f.write(data)
    def close(self):
        self.f.close()
    def fail(self):
        self.f.close()
        os.unlink(self._filename)
    def register_canceller(self, cb):
        pass # we won't use it
    def finish(self):
        pass

class Data:
    implements(IDownloadTarget)
    def __init__(self):
        self._data = []
    def open(self):
        pass
    def write(self, data):
        self._data.append(data)
    def close(self):
        self.data = "".join(self._data)
        del self._data
    def fail(self):
        del self._data
    def register_canceller(self, cb):
        pass # we won't use it
    def finish(self):
        return self.data

class FileHandle:
    implements(IDownloadTarget)
    def __init__(self, filehandle):
        self._filehandle = filehandle
    def open(self):
        pass
    def write(self, data):
        self._filehandle.write(data)
    def close(self):
        # the originator of the filehandle reserves the right to close it
        pass
    def fail(self):
        pass
    def register_canceller(self, cb):
        pass
    def finish(self):
        pass


class Downloader(service.MultiService):
    """I am a service that allows file downloading.
    """
    name = "downloader"

    def download(self, verifierid, t):
        assert self.parent
        assert self.running
        assert isinstance(verifierid, str)
        t = IDownloadTarget(t)
        assert t.write
        assert t.close
        dl = FileDownloader(self.parent, verifierid)
        dl.set_download_target(t)
        dl.make_decoder()
        d = dl.start()
        return d

    # utility functions
    def download_to_data(self, verifierid):
        return self.download(verifierid, Data())
    def download_to_filename(self, verifierid, filename):
        return self.download(verifierid, FileName(filename))
    def download_to_filehandle(self, verifierid, filehandle):
        return self.download(verifierid, FileHandle(filehandle))


