"""
Tests for ``allmydata.web.logs``.
"""

from __future__ import (
    print_function,
    unicode_literals,
    absolute_import,
    division,
)

from testtools.matchers import (
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)

from twisted.web.http import (
    OK,
)

from treq.client import (
    HTTPClient,
)
from treq.testing import (
    RequestTraversalAgent,
)

from .matchers import (
    has_response_code,
)

from ..common import (
    SyncTestCase,
)

from ...web.logs import (
    create_log_resources,
)

class StreamingEliotLogsTests(SyncTestCase):
    """
    Tests for the log streaming resources created by ``create_log_resources``.
    """
    def setUp(self):
        self.resource = create_log_resources()
        self.agent = RequestTraversalAgent(self.resource)
        self.client =  HTTPClient(self.agent)
        return super(StreamingEliotLogsTests, self).setUp()

    def test_v1(self):
        """
        There is a resource at *v1*.
        """
        self.assertThat(
            self.client.get(b"http:///v1"),
            succeeded(has_response_code(Equals(OK))),
        )
