
"""
This is a reference implementation of the lease renewal secret derivation
protocol in use by Tahoe-LAFS clients as of 1.16.0.
"""

from allmydata.util.base32 import (
    a2b as b32decode,
    b2a as b32encode,
)
from allmydata.util.hashutil import (
    tagged_hash,
    tagged_pair_hash,
)


def derive_renewal_secret(lease_secret: bytes, storage_index: bytes, tubid: bytes) -> bytes:
    assert len(lease_secret) == 32
    assert len(storage_index) == 16
    assert len(tubid) == 20

    bucket_renewal_tag = b"allmydata_bucket_renewal_secret_v1"
    file_renewal_tag = b"allmydata_file_renewal_secret_v1"
    client_renewal_tag = b"allmydata_client_renewal_secret_v1"

    client_renewal_secret = tagged_hash(lease_secret, client_renewal_tag)
    file_renewal_secret = tagged_pair_hash(
        file_renewal_tag,
        client_renewal_secret,
        storage_index,
    )
    peer_id = tubid

    return tagged_pair_hash(bucket_renewal_tag, file_renewal_secret, peer_id)

def demo():
    secret = b32encode(derive_renewal_secret(
        b"lease secretxxxxxxxxxxxxxxxxxxxx",
        b"storage indexxxx",
        b"tub idxxxxxxxxxxxxxx",
    )).decode("ascii")
    print("An example renewal secret: {}".format(secret))

def test():
    # These test vectors created by intrumenting Tahoe-LAFS
    # bb57fcfb50d4e01bbc4de2e23dbbf7a60c004031 to emit `self.renew_secret` in
    # allmydata.immutable.upload.ServerTracker.query and then uploading a
    # couple files to a couple different storage servers.
    test_vector = [
        dict(lease_secret=b"boity2cdh7jvl3ltaeebuiobbspjmbuopnwbde2yeh4k6x7jioga",
             storage_index=b"vrttmwlicrzbt7gh5qsooogr7u",
             tubid=b"v67jiisoty6ooyxlql5fuucitqiok2ic",
             expected=b"osd6wmc5vz4g3ukg64sitmzlfiaaordutrez7oxdp5kkze7zp5zq",
        ),
        dict(lease_secret=b"boity2cdh7jvl3ltaeebuiobbspjmbuopnwbde2yeh4k6x7jioga",
             storage_index=b"75gmmfts772ww4beiewc234o5e",
             tubid=b"v67jiisoty6ooyxlql5fuucitqiok2ic",
             expected=b"35itmusj7qm2pfimh62snbyxp3imreofhx4djr7i2fweta75szda",
        ),
        dict(lease_secret=b"boity2cdh7jvl3ltaeebuiobbspjmbuopnwbde2yeh4k6x7jioga",
             storage_index=b"75gmmfts772ww4beiewc234o5e",
             tubid=b"lh5fhobkjrmkqjmkxhy3yaonoociggpz",
             expected=b"srrlruge47ws3lm53vgdxprgqb6bz7cdblnuovdgtfkqrygrjm4q",
        ),
        dict(lease_secret=b"vacviff4xfqxsbp64tdr3frg3xnkcsuwt5jpyat2qxcm44bwu75a",
             storage_index=b"75gmmfts772ww4beiewc234o5e",
             tubid=b"lh5fhobkjrmkqjmkxhy3yaonoociggpz",
             expected=b"b4jledjiqjqekbm2erekzqumqzblegxi23i5ojva7g7xmqqnl5pq",
        ),
    ]

    for n, item in enumerate(test_vector):
        derived = b32encode(derive_renewal_secret(
            b32decode(item["lease_secret"]),
            b32decode(item["storage_index"]),
            b32decode(item["tubid"]),
        ))
        assert derived == item["expected"] , \
            "Test vector {} failed: {} (expected) != {} (derived)".format(
                n,
                item["expected"],
                derived,
            )
    print("{} test vectors validated".format(len(test_vector)))

test()
demo()
