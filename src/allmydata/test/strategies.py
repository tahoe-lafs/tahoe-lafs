
from hypothesis.strategies import (
    text,
    binary,
    integers,
    just,
    builds,
    lists,
    fixed_dictionaries,
)

from foolscap.furl import (
    encode_furl,
)

from ..util import (
    base32,
)

def base32_text(*args, **kwargs):
    return binary(*args, **kwargs).map(
        base32.b2a,
    )

def tub_ids():
    return binary(min_size=20, max_size=20).map(
        base32.b2a,
    )

def port_numbers():
    return integers(
        min_value=1,
        max_value=65535,
    )

def location_hint():
    return builds(
        u"{hostname}:{port_number}".format,
        hostname=just(u"localhost"),
        port_number=port_numbers(),
    )

def furls():
    return builds(
        encode_furl,
        tub_ids(),
        lists(location_hint()),
        text(),
    )

def storage_announcements():
    """
    Build dictionaries like those published by storage servers.
    """
    return fixed_dictionaries({
        u"permutation-seed-base32": base32_text(),
        u"anonymous-storage-FURL": furls(),
    })
