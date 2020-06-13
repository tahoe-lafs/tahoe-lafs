# -*- coding: utf-8 -*-
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2020 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

"""
Tests for the allmydata.testing helpers
"""

from twisted.internet.defer import (
    inlineCallbacks,
)

from allmydata.uri import (
    from_string,
    CHKFileURI,
)
from allmydata.testing.web import (
    create_tahoe_treq_client,
)

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
)

from testtools import (
    TestCase,
)
from testtools.matchers import (
    Always,
)
from testtools.twistedsupport import (
    succeeded,
)


class FakeWebTest(TestCase):
    """
    Test the WebUI verified-fakes infrastucture
    """

    def setUp(self):
        super(FakeWebTest, self).setUp()
        self.http_client = create_tahoe_treq_client()

    @given(
        content=binary(),
    )
    def test_create_and_download(self, content):
        """
        Upload some content (via 'PUT /uri') and then download it (via
        'GET /uri?uri=...')
        """

        @inlineCallbacks
        def do_test():
            resp = yield self.http_client.put("http://example.com/uri", content)
            self.assertEqual(resp.code, 201)

            cap_raw = yield resp.content()
            cap = from_string(cap_raw)
            self.assertIsInstance(cap, CHKFileURI)

            resp = yield self.http_client.get(
                "http://example.com/uri?uri={}".format(cap.to_string())
            )
            self.assertEqual(resp.code, 200)

            round_trip_content = yield resp.content()
            self.assertEqual(content, round_trip_content)
        self.assertThat(
            do_test(),
            succeeded(Always()),
        )
