
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
    already, writers = storage_server.allocate_buckets(
        storage_index,
        renew_secret,
        cancel_secret,
        shares.keys(),
        len(next(iter(shares.values()))),
    )
    for shnum, writer in writers.items():
        writer.write(0, shares[shnum])
        writer.close()


def upload_mutable(storage_server, storage_index, secrets, shares):
    """
    Synchronously upload some mutable shares to a ``StorageServer``.

    :param allmydata.storage.server.StorageServer storage_server: The storage
        server object to use to perform the upload.

    :param bytes storage_index: The storage index for the immutable shares.

    :param secrets: A three-tuple of a write enabler, renew secret, and cancel
        secret.

    :param dict[int, bytes] shares: A mapping from share numbers to share data
        to upload.

    :return: ``None``
    """
    test_and_write_vectors = {
        sharenum: ([], [(0, data)], None)
        for sharenum, data
        in shares.items()
    }
    read_vector = []

    storage_server.slot_testv_and_readv_and_writev(
        storage_index,
        secrets,
        test_and_write_vectors,
        read_vector,
    )
