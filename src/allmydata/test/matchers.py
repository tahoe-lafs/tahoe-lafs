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
)
from allmydata.node import (
    read_config,
)
from allmydata.crypto import (
    ed25519,
    error,
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
        privkey_bytes = config.get_private_config("node.privkey")
        private_key = ed25519.signing_keypair_from_string(privkey_bytes)[0]
        signature = ed25519.sign_data(private_key, b"")
        other_public_key = ed25519.verifying_key_from_signing_key(other)
        try:
            ed25519.verify_signature(other_public_key, signature, b"")
        except error.BadSignature:
            return Mismatch("The signature did not verify.")


def matches_storage_announcement(basedir, options=None):
    """
    Match an anonymous storage announcement.
    """
    announcement = {
        u"anonymous-storage-FURL": matches_furl(),
        u"permutation-seed-base32": matches_base32(),
    }
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
