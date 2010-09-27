from zope.interface import implements
from twisted.internet import defer
from allmydata.storage.server import si_b2a
from allmydata.util import log, consumer
from allmydata.util.assertutil import precondition
from allmydata.interfaces import IEncryptedUploadable

from allmydata.immutable import upload

class Repairer(log.PrefixingLogMixin):
    implements(IEncryptedUploadable)
    """I generate any shares which were not available and upload them to
    servers.

    Which servers? Well, I just use the normal upload process, so any servers
    that will take shares. In fact, I even believe servers if they say that
    they already have shares even if attempts to download those shares would
    fail because the shares are corrupted.

    My process of uploading replacement shares proceeds in a segment-wise
    fashion -- first I ask servers if they can hold the new shares, and wait
    until enough have agreed then I download the first segment of the file
    and upload the first block of each replacement share, and only after all
    those blocks have been uploaded do I download the second segment of the
    file and upload the second block of each replacement share to its
    respective server. (I do it this way in order to minimize the amount of
    downloading I have to do and the amount of memory I have to use at any
    one time.)

    If any of the servers to which I am uploading replacement shares fails to
    accept the blocks during this process, then I just stop using that
    server, abandon any share-uploads that were going to that server, and
    proceed to finish uploading the remaining shares to their respective
    servers. At the end of my work, I produce an object which satisfies the
    ICheckAndRepairResults interface (by firing the deferred that I returned
    from start() and passing that check-and-repair-results object).

    Before I send any new request to a server, I always ask the 'monitor'
    object that was passed into my constructor whether this task has been
    cancelled (by invoking its raise_if_cancelled() method).
    """

    def __init__(self, filenode, storage_broker, secret_holder, monitor):
        logprefix = si_b2a(filenode.get_storage_index())[:5]
        log.PrefixingLogMixin.__init__(self, "allmydata.immutable.repairer",
                                       prefix=logprefix)
        self._filenode = filenode
        self._storage_broker = storage_broker
        self._secret_holder = secret_holder
        self._monitor = monitor
        self._offset = 0

    def start(self):
        self.log("starting repair")
        d = self._filenode.get_segment_size()
        def _got_segsize(segsize):
            vcap = self._filenode.get_verify_cap()
            k = vcap.needed_shares
            N = vcap.total_shares
            # Per ticket #1212
            # (http://tahoe-lafs.org/trac/tahoe-lafs/ticket/1212)
            happy = 0
            self._encodingparams = (k, happy, N, segsize)
            ul = upload.CHKUploader(self._storage_broker, self._secret_holder)
            return ul.start(self) # I am the IEncryptedUploadable
        d.addCallback(_got_segsize)
        return d


    # methods to satisfy the IEncryptedUploader interface
    # (From the perspective of an uploader I am an IEncryptedUploadable.)
    def set_upload_status(self, upload_status):
        self.upload_status = upload_status
    def get_size(self):
        size = self._filenode.get_size()
        assert size is not None
        return defer.succeed(size)
    def get_all_encoding_parameters(self):
        return defer.succeed(self._encodingparams)
    def read_encrypted(self, length, hash_only):
        """Returns a deferred which eventually fires with the requested
        ciphertext, as a list of strings."""
        precondition(length) # please don't ask to read 0 bytes
        mc = consumer.MemoryConsumer()
        d = self._filenode.read(mc, self._offset, length)
        self._offset += length
        d.addCallback(lambda ign: mc.chunks)
        return d
    def get_storage_index(self):
        return self._filenode.get_storage_index()
    def close(self):
        pass
