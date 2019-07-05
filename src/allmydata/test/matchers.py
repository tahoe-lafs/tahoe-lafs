"""
Testtools-style matchers useful to the Tahoe-LAFS test suite.
"""

import attr

from testtools.matchers import (
    Mismatch,
    AfterPreprocessing,
    MatchesStructure,
    MatchesDict,
    MatchesListwise,
    Always,
    Equals,
)

from foolscap.furl import (
    decode_furl,
)

from allmydata.util import (
    base32,
    keyutil,
)
from allmydata.node import (
    read_config,
)

@attr.s
class MatchesNodePublicKey(object):
    """
    Match an object representing the node's private key.

    To verify, the private key is loaded from the node's private config
    directory at the time the match is checked.
    """
    basedir = attr.ib()

    def match(self, other):
        config = read_config(self.basedir, u"tub.port")
        privkey = config.get_private_config("node.privkey")
        other_signature = other.sign(b"")
        expected_signature = keyutil.parse_privkey(privkey)[0].sign(b"")
        if other_signature != expected_signature:
            return Mismatch("{} != {}".format(
                privkey,
                base32.b2a(other.get_verifying_key_bytes()),
            ))



def matches_storage_announcement(basedir, anonymous=True, options=None):
    """
    Match a storage announcement.

    :param bytes basedir: The path to the node base directory which is
        expected to emit the announcement.  This is used to determine the key
        which is meant to sign the announcement.

    :param bool anonymous: If True, matches a storage announcement containing
        an anonymous access fURL.  Otherwise, fails to match such an
        announcement.

    :param list[matcher]|NoneType options: If a list, matches a storage
        announcement containing a list of storage plugin options matching the
        elements of the list.  If None, fails to match an announcement with
        storage plugin options.

    :return: A matcher with the requested behavior.
    """
    announcement = {
        u"permutation-seed-base32": matches_base32(),
    }
    if anonymous:
        announcement[u"anonymous-storage-FURL"] = matches_furl()
    if options:
        announcement[u"storage-options"] = MatchesListwise(options)
    return MatchesStructure(
        # Has each of these keys with associated values that match
        service_name=Equals(u"storage"),
        ann=MatchesDict(announcement),
        signing_key=MatchesNodePublicKey(basedir),
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



class MatchesSameElements(object):
    """
    Match if the two-tuple value given contains two elements that are equal to
    each other.
    """
    def match(self, value):
        left, right = value
        return Equals(left).match(right)
