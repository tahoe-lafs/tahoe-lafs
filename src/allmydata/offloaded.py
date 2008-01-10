
from zope.interface import implements
from twisted.application import service
from twisted.internet import defer
from foolscap import Referenceable
from allmydata.util import hashutil
from allmydata import upload, interfaces



class CHKUploadHelper(Referenceable, upload.CHKUploader):
    """I am the helper-server -side counterpart to AssistedUploader. I handle
    peer selection, encoding, and share pushing. I read ciphertext from the
    remote AssistedUploader.
    """
    implements(interfaces.RIUploadHelper)

    def __init__(self, storage_index, helper):
        self._finished = False
        self._storage_index = storage_index
        self._helper = helper
        self._log_number = self._helper.log("CHKUploadHelper starting")

        self._client = helper.parent
        self._wait_for_numpeers = None
        self._options = {}

        self.set_params( (3,7,10) ) # GACK

    def start(self):
        # determine if we need to upload the file. If so, return ({},self) .
        # If not, return (UploadResults,None) .
        return ({'uri_extension_hash': hashutil.uri_extension_hash("")},self)

    def remote_upload(self, reader):
        # reader is an RIEncryptedUploadable. I am specified to return an
        # UploadResults dictionary.

        eu = CiphertextReader(reader, self._storage_index)
        d = self.start_encrypted(eu)
        def _done(res):
            (uri_extension_hash, needed_shares, total_shares, size) = res
            return {'uri_extension_hash': uri_extension_hash}
        d.addCallback(_done)
        return d

    def finished(self, res):
        self._finished = True
        self._helper.upload_finished(self._storage_index)

class CiphertextReader:
    implements(interfaces.IEncryptedUploadable)

    def __init__(self, remote_reader, storage_index):
        self.rr = remote_reader
        self.storage_index = storage_index

    def get_size(self):
        return self.rr.callRemote("get_size")
    def get_storage_index(self):
        return defer.succeed(self.storage_index)
    def set_segment_size(self, segment_size):
        return self.rr.callRemote("set_segment_size", segment_size)
    def set_serialized_encoding_parameters(self, params):
        pass # ??
    def read_encrypted(self, length):
        return self.rr.callRemote("read_encrypted", length)
    def get_plaintext_hashtree_leaves(self, first, last, num_segments):
        return self.rr.callRemote("get_plaintext_hashtree_leaves",
                                  first, last, num_segments)
    def get_plaintext_hash(self):
        return self.rr.callRemote("get_plaintext_hash")
    def close(self):
        # ??
        return self.rr.callRemote("close")


class Helper(Referenceable, service.MultiService):
    implements(interfaces.RIHelper)
    # this is the non-distributed version. When we need to have multiple
    # helpers, this object will query the farm to see if anyone has the
    # storage_index of interest, and send the request off to them.

    chk_upload_helper_class = CHKUploadHelper

    def __init__(self, basedir):
        self._basedir = basedir
        self._active_uploads = {}
        service.MultiService.__init__(self)

    def log(self, msg, **kwargs):
        if 'facility' not in kwargs:
            kwargs['facility'] = "helper"
        return self.parent.log(msg, **kwargs)

    def remote_upload(self, storage_index):
        # TODO: look on disk
        if storage_index in self._active_uploads:
            uh = self._active_uploads[storage_index]
        else:
            uh = self.chk_upload_helper_class(storage_index, self)
            self._active_uploads[storage_index] = uh
        return uh.start()

    def upload_finished(self, storage_index):
        del self._active_uploads[storage_index]
