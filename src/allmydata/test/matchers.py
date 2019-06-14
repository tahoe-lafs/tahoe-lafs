"""
Testtools-style matchers useful to the Tahoe-LAFS test suite.
"""

from testtools.matchers import (
    AfterPreprocessing,
    MatchesStructure,
    MatchesDict,
    Always,
    Equals,
)

from foolscap.furl import (
    decode_furl,
)

from allmydata.util import (
    base32,
)


def matches_anonymous_storage_announcement():
    """
    Match an anonymous storage announcement.
    """
    return MatchesStructure(
        # Has each of these keys with associated values that match
        service_name=Equals("storage"),
        ann=MatchesDict({
            "anonymous-storage-FURL": matches_furl(),
            "permutation-seed-base32": matches_base32(),
        }),
        # Not sure what kind of assertion to make against the key
        signing_key=Always(),
    )


def matches_furl():
    """
    Match any Foolscap fURL byte string.
    """
    return AfterPreprocessing(decode_furl, Always())


def matches_base32():
    """
    Match any base32 encoded byte string.
    """
    return AfterPreprocessing(base32.a2b, Always())
