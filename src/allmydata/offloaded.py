
from foolscap import RemoteInterface
from foolscap.schema import DictOf, ChoiceOf, ListOf
from allmydata.interfaces import StorageIndex, Hash

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

