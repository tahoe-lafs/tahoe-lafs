
from zope.interface import implements
from twisted.application import service
from twisted.internet import defer
from foolscap import Referenceable
from allmydata import upload, interfaces
from allmydata.util import idlib, log, observer


class NotEnoughWritersError(Exception):
    pass


class CHKUploadHelper(Referenceable, upload.CHKUploader):
    """I am the helper-server -side counterpart to AssistedUploader. I handle
    peer selection, encoding, and share pushing. I read ciphertext from the
    remote AssistedUploader.
    """
    implements(interfaces.RICHKUploadHelper)

    def __init__(self, storage_index, helper, log_number, options={}):
        self._started = False
        self._storage_index = storage_index
        self._helper = helper
        upload_id = idlib.b2a(storage_index)[:6]
        self._log_number = log_number
        self._helper.log("CHKUploadHelper starting for SI %s" % upload_id,
                         parent=log_number)

        self._client = helper.parent
        self._options = options
        self._reader = CiphertextReader(storage_index, self)
        self._finished_observers = observer.OneShotObserverList()

        self.set_params( (3,7,10) ) # GACK

    def log(self, *args, **kwargs):
        if 'facility' not in kwargs:
            kwargs['facility'] = "tahoe.helper"
        return upload.CHKUploader.log(self, *args, **kwargs)

    def start(self):
        # determine if we need to upload the file. If so, return ({},self) .
        # If not, return (UploadResults,None) .
        #return ({'uri_extension_hash': hashutil.uri_extension_hash("")},self)
        return ({}, self)

    def remote_upload(self, reader):
        # reader is an RIEncryptedUploadable. I am specified to return an
        # UploadResults dictionary.

        self._reader.add_reader(reader)

            # there is already an upload in progress, and a second uploader
            # has joined in. We will notify the second client when the upload
            # is complete, but we will not request any data from them unless
            # the first one breaks. TODO: fetch data from both clients to
            # speed the upload

        if not self._started:
            self._started = True
            d = self.start_encrypted(self._reader)
            d.addCallbacks(self._finished, self._failed)
        return self._finished_observers.when_fired()

    def _finished(self, res):
        (uri_extension_hash, needed_shares, total_shares, size) = res
        upload_results = {'uri_extension_hash': uri_extension_hash}
        self._finished_observers.fire(upload_results)
        self._helper.upload_finished(self._storage_index)

    def _failed(self, f):
        self._finished_observers.fire(f)
        self._helper.upload_finished(self._storage_index)

class CiphertextReader:
    implements(interfaces.IEncryptedUploadable)

    def __init__(self, storage_index, upload_helper):
        self._readers = []
        self.storage_index = storage_index
        self._offset = 0
        self._upload_helper = upload_helper

    def add_reader(self, reader):
        # for now, we stick to the first uploader
        self._readers.append(reader)

    def call(self, *args, **kwargs):
        if not self._readers:
            raise NotEnoughWritersError("ran out of assisted uploaders")
        rr = self._readers[0]
        d = rr.callRemote(*args, **kwargs)
        def _err(f):
            if rr in self._readers:
                self._readers.remove(rr)
            self._upload_helper.log("call to assisted uploader %s failed" % rr,
                                    failure=f, level=log.UNUSUAL)
            # we can try again with someone else who's left
            return self.call(*args, **kwargs)
        d.addErrback(_err)
        return d

    def get_size(self):
        return self.call("get_size")
    def get_storage_index(self):
        return defer.succeed(self.storage_index)
    def set_segment_size(self, segment_size):
        return self.call("set_segment_size", segment_size)
    def set_serialized_encoding_parameters(self, params):
        pass # ??
    def read_encrypted(self, length):
        d = self.call("read_encrypted", self._offset, length)
        def _done(strings):
            self._offset += sum([len(data) for data in strings])
            return strings
        d.addCallback(_done)
        return d
    def get_plaintext_hashtree_leaves(self, first, last, num_segments):
        return self.call("get_plaintext_hashtree_leaves", first, last,
                         num_segments)
    def get_plaintext_hash(self):
        return self.call("get_plaintext_hash")
    def close(self):
        # ??
        return self.call("close")


class Helper(Referenceable, service.MultiService):
    implements(interfaces.RIHelper)
    # this is the non-distributed version. When we need to have multiple
    # helpers, this object will become the HelperCoordinator, and will query
    # the farm of Helpers to see if anyone has the storage_index of interest,
    # and send the request off to them. If nobody has it, we'll choose a
    # helper at random.

    name = "helper"
    chk_upload_helper_class = CHKUploadHelper

    def __init__(self, basedir):
        self._basedir = basedir
        self._chk_options = {}
        self._active_uploads = {}
        service.MultiService.__init__(self)

    def log(self, *args, **kwargs):
        if 'facility' not in kwargs:
            kwargs['facility'] = "tahoe.helper"
        return self.parent.log(*args, **kwargs)

    def remote_upload_chk(self, storage_index):
        lp = self.log(format="helper: upload_chk query for SI %(si)s",
                      si=idlib.b2a(storage_index))
        # TODO: look on disk
        if storage_index in self._active_uploads:
            self.log("upload is currently active", parent=lp)
            uh = self._active_uploads[storage_index]
        else:
            self.log("creating new upload helper", parent=lp)
            uh = self.chk_upload_helper_class(storage_index, self, lp,
                                              self._chk_options)
            self._active_uploads[storage_index] = uh
        return uh.start()

    def upload_finished(self, storage_index):
        del self._active_uploads[storage_index]
