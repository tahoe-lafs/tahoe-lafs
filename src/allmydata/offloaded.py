
from zope.interface import implements
from twisted.application import service
from foolscap import RemoteInterface, Referenceable
from foolscap.schema import DictOf, ChoiceOf, ListOf
from allmydata.interfaces import StorageIndex, Hash
from allmydata.util import hashutil

UploadResults = DictOf(str, str)

class RIEncryptedUploadable(RemoteInterface):
    __remote_name__ = "RIEncryptedUploadable.tahoe.allmydata.com"

    def get_size():
        return int

    def set_segment_size(segment_size=long):
        return None

    def read_encrypted(offset=long, length=long):
        return str

    def get_plaintext_hashtree_leaves(first=int, last=int):
        return ListOf(Hash)

    def get_plaintext_hash():
        return Hash


class RIUploadHelper(RemoteInterface):
    __remote_name__ = "RIUploadHelper.tahoe.allmydata.com"

    def upload(reader=RIEncryptedUploadable):
        return UploadResults


class RIHelper(RemoteInterface):
    __remote_name__ = "RIHelper.tahoe.allmydata.com"

    def upload(si=StorageIndex):
        """See if a file with a given storage index needs uploading. The
        helper will ask the appropriate storage servers to see if the file
        has already been uploaded. If so, the helper will return a set of
        'upload results' that includes whatever hashes are needed to build
        the read-cap, and perhaps a truncated sharemap.

        If the file has not yet been uploaded (or if it was only partially
        uploaded), the helper will return an empty upload-results dictionary
        and also an RIUploadHelper object that will take care of the upload
        process. The client should call upload() on this object and pass it a
        reference to an RIEncryptedUploadable object that will provide
        ciphertext. When the upload is finished, the upload() method will
        finish and return the upload results.
        """
        return (UploadResults, ChoiceOf(RIUploadHelper, None))


class CHKUploadHelper(Referenceable):
    """I am the helper-server -side counterpart to AssistedUploader. I handle
    peer selection, encoding, and share pushing. I read ciphertext from the
    remote AssistedUploader.
    """
    implements(RIUploadHelper)

    def __init__(self, storage_index, helper):
        self._finished = False
        self._storage_index = storage_index
        self._helper = helper
        self._log_number = self._helper.log("CHKUploadHelper starting")

    def log(self, msg, parent=None):
        if parent is None:
            parent = self._log_number
        return self._client.log(msg, parent=parent)

    def start(self):
        # determine if we need to upload the file. If so, return ({},self) .
        # If not, return (UploadResults,None) .
        return ({'uri_extension_hash': hashutil.uri_extension_hash("")},self)

    def remote_upload(self, reader):
        # reader is an RIEncryptedUploadable. I am specified to return an
        # UploadResults dictionary.
        return {'uri_extension_hash': hashutil.uri_extension_hash("")}

    def finished(self, res):
        self._finished = True
        self._helper.upload_finished(self._storage_index)


class Helper(Referenceable, service.MultiService):
    implements(RIHelper)
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
            uh = CHKUploadHelper(storage_index, self)
            self._active_uploads[storage_index] = uh
        return uh.start()

    def upload_finished(self, storage_index):
        del self._active_uploads[storage_index]
