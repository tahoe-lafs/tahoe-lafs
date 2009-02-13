from zope.interface import implements
from twisted.internet import defer
from allmydata import storage
from allmydata.util import log, observer
from allmydata.util.assertutil import precondition, _assert
from allmydata.uri import CHKFileVerifierURI
from allmydata.interfaces import IEncryptedUploadable, IDownloadTarget
from twisted.internet.interfaces import IConsumer

from allmydata.immutable import download, upload

import collections

class Repairer(log.PrefixingLogMixin):
    """ I generate any shares which were not available and upload them to servers.

    Which servers?  Well, I just use the normal upload process, so any servers that will take
    shares.  In fact, I even believe servers if they say that they already have shares even if
    attempts to download those shares would fail because the shares are corrupted.

    My process of uploading replacement shares proceeds in a segment-wise fashion -- first I ask
    servers if they can hold the new shares, and wait until enough have agreed then I download
    the first segment of the file and upload the first block of each replacement share, and only
    after all those blocks have been uploaded do I download the second segment of the file and
    upload the second block of each replacement share to its respective server.  (I do it this
    way in order to minimize the amount of downloading I have to do and the amount of memory I
    have to use at any one time.)

    If any of the servers to which I am uploading replacement shares fails to accept the blocks
    during this process, then I just stop using that server, abandon any share-uploads that were
    going to that server, and proceed to finish uploading the remaining shares to their
    respective servers.  At the end of my work, I produce an object which satisfies the
    ICheckAndRepairResults interface (by firing the deferred that I returned from start() and
    passing that check-and-repair-results object).

    Before I send any new request to a server, I always ask the "monitor" object that was passed
    into my constructor whether this task has been cancelled (by invoking its
    raise_if_cancelled() method).
    """
    def __init__(self, client, verifycap, monitor):
        assert precondition(isinstance(verifycap, CHKFileVerifierURI))

        logprefix = storage.si_b2a(verifycap.storage_index)[:5]
        log.PrefixingLogMixin.__init__(self, "allmydata.immutable.repairer", prefix=logprefix)

        self._client = client
        self._verifycap = verifycap
        self._monitor = monitor

    def start(self):
        self.log("starting repair")
        duc = DownUpConnector()
        dl = download.CiphertextDownloader(self._client, self._verifycap, target=duc, monitor=self._monitor)
        ul = upload.CHKUploader(self._client)

        d = defer.Deferred()

        # If the upload or the download fails or is stopped, then the repair failed.
        def _errb(f):
            d.errback(f)
            return None

        # If the upload succeeds, then the repair has succeeded.
        def _cb(res):
            d.callback(res)
        ul.start(duc).addCallbacks(_cb, _errb)

        # If the download fails or is stopped, then the repair failed.
        d2 = dl.start()
        d2.addErrback(_errb)

        # We ignore the callback from d2.  Is this right?  Ugh.

        return d

class DownUpConnector(log.PrefixingLogMixin):
    implements(IEncryptedUploadable, IDownloadTarget, IConsumer)
    """ I act like an "encrypted uploadable" -- something that a local uploader can read
    ciphertext from in order to upload the ciphertext.  However, unbeknownst to the uploader,
    I actually download the ciphertext from a CiphertextDownloader instance as it is needed.

    On the other hand, I act like a "download target" -- something that a local downloader can
    write ciphertext to as it downloads the ciphertext.  That downloader doesn't realize, of
    course, that I'm just turning around and giving the ciphertext to the uploader. """

    # The theory behind this class is nice: just satisfy two separate interfaces.  The
    # implementation is slightly horrible, because of "impedance mismatch" -- the downloader
    # expects to be able to synchronously push data in, and the uploader expects to be able to
    # read data out with a "read(THIS_SPECIFIC_LENGTH)" which returns a deferred.  The two
    # interfaces have different APIs for pausing/unpausing.  The uploader requests metadata like
    # size and encodingparams which the downloader provides either eventually or not at all
    # (okay I just now extended the downloader to provide encodingparams).  Most of this
    # slightly horrible code would disappear if CiphertextDownloader just used this object as an
    # IConsumer (plus maybe a couple of other methods) and if the Uploader simply expected to be
    # treated as an IConsumer (plus maybe a couple of other things).

    def __init__(self, buflim=2**19):
        """ If we're already holding at least buflim bytes, then tell the downloader to pause
        until we have less than buflim bytes."""
        log.PrefixingLogMixin.__init__(self, "allmydata.immutable.repairer")
        self.buflim = buflim
        self.bufs = collections.deque() # list of strings
        self.bufsiz = 0 # how many bytes total in bufs

        self.next_read_ds = collections.deque() # list of deferreds which will fire with the requested ciphertext
        self.next_read_lens = collections.deque() # how many bytes of ciphertext were requested by each deferred

        self._size_osol = observer.OneShotObserverList()
        self._encodingparams_osol = observer.OneShotObserverList()
        self._storageindex_osol = observer.OneShotObserverList()
        self._closed_to_pusher = False

        # once seg size is available, the following attribute will be created to hold it:

        # self.encodingparams # (provided by the object which is pushing data into me, required
        # by the object which is pulling data out of me)

        # open() will create the following attribute:
        # self.size # size of the whole file (provided by the object which is pushing data into
        # me, required by the object which is pulling data out of me)

        # set_upload_status() will create the following attribute:

        # self.upload_status # XXX do we need to actually update this?  Is anybody watching the
        # results during a repair?

    def _satisfy_reads_if_possible(self):
        assert bool(self.next_read_ds) == bool(self.next_read_lens)
        while self.next_read_ds and ((self.bufsiz >= self.next_read_lens[0]) or self._closed_to_pusher):
            nrd = self.next_read_ds.popleft()
            nrl = self.next_read_lens.popleft()

            # Pick out the requested number of bytes from self.bufs, turn it into a string, and
            # callback the deferred with that.
            res = []
            ressize = 0
            while ressize < nrl and self.bufs:
                nextbuf = self.bufs.popleft()
                res.append(nextbuf)
                ressize += len(nextbuf)
                if ressize > nrl:
                    extra = ressize - nrl
                    self.bufs.appendleft(nextbuf[:-extra])
                    res[-1] = nextbuf[:-extra]
            assert _assert(sum(len(x) for x in res) <= nrl, [len(x) for x in res], nrl)
            assert _assert(sum(len(x) for x in res) == nrl or self._closed_to_pusher, [len(x) for x in res], nrl)
            self.bufsiz -= nrl
            if self.bufsiz < self.buflim and self.producer:
                self.producer.resumeProducing()
            nrd.callback(res)

    # methods to satisfy the IConsumer and IDownloadTarget interfaces
    # (From the perspective of a downloader I am an IDownloadTarget and an IConsumer.)
    def registerProducer(self, producer, streaming):
        assert streaming # We know how to handle only streaming producers.
        self.producer = producer # the downloader
    def unregisterProducer(self):
        self.producer = None
    def open(self, size):
        self.size = size
        self._size_osol.fire(self.size)
    def set_encodingparams(self, encodingparams):
        self.encodingparams = encodingparams
        self._encodingparams_osol.fire(self.encodingparams)
    def set_storageindex(self, storageindex):
        self.storageindex = storageindex
        self._storageindex_osol.fire(self.storageindex)
    def write(self, data):
        precondition(data) # please don't write empty strings
        self.bufs.append(data)
        self.bufsiz += len(data)
        self._satisfy_reads_if_possible()
        if self.bufsiz >= self.buflim and self.producer:
            self.producer.pauseProducing()
    def finish(self):
        pass
    def close(self):
        self._closed_to_pusher = True
        # Any reads which haven't been satisfied by now are going to
        # have to be satisfied with short reads.
        self._satisfy_reads_if_possible()

    # methods to satisfy the IEncryptedUploader interface
    # (From the perspective of an uploader I am an IEncryptedUploadable.)
    def set_upload_status(self, upload_status):
        self.upload_status = upload_status
    def get_size(self):
        if hasattr(self, 'size'): # attribute created by self.open()
            return defer.succeed(self.size)
        else:
            return self._size_osol.when_fired()
    def get_all_encoding_parameters(self):
        # We have to learn the encoding params from pusher.
        if hasattr(self, 'encodingparams'): # attribute created by self.set_encodingparams()
            return defer.succeed(self.encodingparams)
        else:
            return self._encodingparams_osol.when_fired()
    def read_encrypted(self, length, hash_only):
        """ Returns a deferred which eventually fired with the requested ciphertext. """
        precondition(length) # please don't ask to read 0 bytes
        d = defer.Deferred()
        self.next_read_ds.append(d)
        self.next_read_lens.append(length)
        self._satisfy_reads_if_possible()
        return d
    def get_storage_index(self):
        # We have to learn the storage index from pusher.
        if hasattr(self, 'storageindex'): # attribute created by self.set_storageindex()
            return defer.succeed(self.storageindex)
        else:
            return self._storageindex.when_fired()
