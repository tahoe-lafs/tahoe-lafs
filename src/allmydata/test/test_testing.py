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

from allmydata.testing.web import (
    create_tahoe_treq_client,
)


class FakeWebTest(TestCase):
    """
    Test the WebUI verified-fakes infrastucture
    """

    def setUp(self):
        super(FakeWebTest, self).setUp()
        self.http_client = create_tahoe_treq_client()

    @inlineCallbacks
    def test_create_and_download(self):
        """
        Upload some content and then download it
        """
        content = "fake data\n" * 100
        resp = yield self.http_client.put("http://example.com/uri", content)
        cap = yield resp.content()

        self.assertTrue(cap.startswith("URI:CHK:"))

        resp = yield self.http_client.get(
            "http://example.com/uri?uri={}".format(cap)
        )
        round_trip_content = yield resp.content()

        self.assertEqual(content, round_trip_content)
