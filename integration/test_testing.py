

from allmydata.testing.web import (
    create_fake_tahoe_root,
    deterministic_key_generator,
)

import pytest
import pytest_twisted


@pytest_twisted.inlineCallbacks
def test_retrieve_cap():
    """
    WebUI Fake can serve a read-capability back
    """

    keys = deterministic_key_generator()
    root = yield create_fake_tahoe_root()
    dummy_readcap = yield root.add_data(
        next(keys),
        "some dummy content\n"*20
    )
    print("readcap: {}".format(dummy_readcap))

    assert dummy_readcap.to_string() == "URI:CHK:ifaucqkbifaucqkbifaucqkbie:qg3n5r4q36wvwt33fobghsyqacc5ymnadk7wf6hafamsh6amybza:1:1:380"
