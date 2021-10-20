
from .common_util import (
    FakeCanary,
)

def upload_immutable(storage_server, storage_index, renew_secret, cancel_secret, shares):
    """
    Synchronously upload some immutable shares to a ``StorageServer``.

    :param allmydata.storage.server.StorageServer storage_server: The storage
        server object to use to perform the upload.

    :param bytes storage_index: The storage index for the immutable shares.

    :param bytes renew_secret: The renew secret for the implicitly created lease.
    :param bytes cancel_secret: The cancel secret for the implicitly created lease.

    :param dict[int, bytes] shares: A mapping from share numbers to share data
        to upload.  The data for all shares must be of the same length.

    :return: ``None``
    """
    already, writers = storage_server.remote_allocate_buckets(
        storage_index,
        renew_secret,
        cancel_secret,
        shares.keys(),
        len(next(iter(shares.values()))),
        canary=FakeCanary(),
    )
    for shnum, writer in writers.items():
        writer.remote_write(0, shares[shnum])
        writer.remote_close()
