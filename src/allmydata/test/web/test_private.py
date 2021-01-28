"""
Tests for ``allmydata.web.private``.

Ported to Python 3.
"""

from __future__ import (
    print_function,
    unicode_literals,
    absolute_import,
    division,
)

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from testtools.matchers import (
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)

from twisted.web.http import (
    UNAUTHORIZED,
    NOT_FOUND,
)
from twisted.web.http_headers import (
    Headers,
)

from treq.client import (
    HTTPClient,
)
from treq.testing import (
    RequestTraversalAgent,
)

from ..common import (
    SyncTestCase,
)

from ...web.private import (
    SCHEME,
    create_private_tree,
)

from .matchers import (
    has_response_code,
)

class PrivacyTests(SyncTestCase):
    """
    Tests for the privacy features of the resources created by ``create_private_tree``.
    """
    def setUp(self):
        self.token = b"abcdef"
        self.resource = create_private_tree(lambda: self.token)
        self.agent = RequestTraversalAgent(self.resource)
        self.client =  HTTPClient(self.agent)
        return super(PrivacyTests, self).setUp()

    def _authorization(self, scheme, value):
        value = str(value, "utf-8")
        return Headers({
            u"authorization": [u"{} {}".format(scheme, value)],
        })

    def test_unauthorized(self):
        """
        A request without an *Authorization* header receives an *Unauthorized* response.
        """
        self.assertThat(
            self.client.head(b"http:///foo/bar"),
            succeeded(has_response_code(Equals(UNAUTHORIZED))),
        )

    def test_wrong_scheme(self):
        """
        A request with an *Authorization* header not containing the Tahoe-LAFS
        scheme receives an *Unauthorized* response.
        """
        self.assertThat(
            self.client.head(
                b"http:///foo/bar",
                headers=self._authorization(u"basic", self.token),
            ),
            succeeded(has_response_code(Equals(UNAUTHORIZED))),
        )

    def test_wrong_token(self):
        """
        A request with an *Authorization* header not containing the expected token
        receives an *Unauthorized* response.
        """
        self.assertThat(
            self.client.head(
                b"http:///foo/bar",
                headers=self._authorization(str(SCHEME, "utf-8"), b"foo bar"),
            ),
            succeeded(has_response_code(Equals(UNAUTHORIZED))),
        )

    def test_authorized(self):
        """
        A request with an *Authorization* header containing the expected scheme
        and token does not receive an *Unauthorized* response.
        """
        self.assertThat(
            self.client.head(
                b"http:///foo/bar",
                headers=self._authorization(str(SCHEME, "utf-8"), self.token),
            ),
            # It's a made up URL so we don't get a 200, either, but a 404.
            succeeded(has_response_code(Equals(NOT_FOUND))),
        )
