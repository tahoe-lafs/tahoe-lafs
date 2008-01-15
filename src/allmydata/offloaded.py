
from zope.interface import implements
from twisted.application import service
from twisted.internet import defer
from foolscap import Referenceable
from allmydata import upload, interfaces
from allmydata.util import idlib



class CHKUploadHelper(Referenceable, upload.CHKUploader):
    """I am the helper-server -side counterpart to AssistedUploader. I handle
    peer selection, encoding, and share pushing. I read ciphertext from the
    remote AssistedUploader.
    """
    implements(interfaces.RICHKUploadHelper)

    def __init__(self, storage_index, helper, log_number, options={}):
        self._finished = False
        self._storage_index = storage_index
        self._helper = helper
        upload_id = idlib.b2a(storage_index)[:6]
        self._log_number = log_number
        self._helper.log("CHKUploadHelper starting for SI %s" % upload_id,
                         parent=log_number)

        self._client = helper.parent
        self._options = options
        self._readers = []

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

        self._readers.append(reader)
        reader.notifyOnDisconnect(self._remove_reader, reader)
        eu = CiphertextReader(reader, self._storage_index)
        d = self.start_encrypted(eu)
        def _done(res):
            self.finished(self._storage_index)
            (uri_extension_hash, needed_shares, total_shares, size) = res
            return {'uri_extension_hash': uri_extension_hash}
        d.addCallback(_done)
        return d

    def _remove_reader(self, reader):
        # NEEDS MORE
        self._readers.remove(reader)
        if not self._readers:
            if not self._finished:
                self.finished(None)

    def finished(self, res):
        self._finished = True
        self._helper.upload_finished(self._storage_index)

class CiphertextReader:
    implements(interfaces.IEncryptedUploadable)

    def __init__(self, remote_reader, storage_index):
        self.rr = remote_reader
        self.storage_index = storage_index
        self._offset = 0

    def get_size(self):
        return self.rr.callRemote("get_size")
    def get_storage_index(self):
        return defer.succeed(self.storage_index)
    def set_segment_size(self, segment_size):
        return self.rr.callRemote("set_segment_size", segment_size)
    def set_serialized_encoding_parameters(self, params):
        pass # ??
    def read_encrypted(self, length):
        d = self.rr.callRemote("read_encrypted", self._offset, length)
        def _done(strings):
            self._offset += sum([len(data) for data in strings])
            return strings
        d.addCallback(_done)
        return d
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
