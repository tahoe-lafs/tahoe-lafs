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

Ported to Python 3.

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from twisted.internet.defer import (
    inlineCallbacks,
)

from allmydata.uri import (
    from_string,
    CHKFileURI,
)
from allmydata.testing.web import (
    create_tahoe_treq_client,
    capability_generator,
)

from hyperlink import (
    DecodedURL,
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
    Equals,
    IsInstance,
    MatchesStructure,
    AfterPreprocessing,
)
from testtools.twistedsupport import (
    succeeded,
)


class FakeWebTest(TestCase):
    """
    Test the WebUI verified-fakes infrastucture
    """

    # Note: do NOT use setUp() because Hypothesis doesn't work
    # properly with it. You must instead do all fixture-type work
    # yourself in each test.

    @given(
        content=binary(),
    )
    def test_create_and_download(self, content):
        """
        Upload some content (via 'PUT /uri') and then download it (via
        'GET /uri?uri=...')
        """
        http_client = create_tahoe_treq_client()

        @inlineCallbacks
        def do_test():
            resp = yield http_client.put("http://example.com/uri", content)
            self.assertThat(resp.code, Equals(201))

            cap_raw = yield resp.content()
            cap = from_string(cap_raw)
            self.assertThat(cap, IsInstance(CHKFileURI))

            resp = yield http_client.get(
                b"http://example.com/uri?uri=" + cap.to_string()
            )
            self.assertThat(resp.code, Equals(200))

            round_trip_content = yield resp.content()

            # using the form "/uri/<cap>" is also valid

            resp = yield http_client.get(
                b"http://example.com/uri?uri=" + cap.to_string()
            )
            self.assertEqual(resp.code, 200)

            round_trip_content = yield resp.content()
            self.assertEqual(content, round_trip_content)
        self.assertThat(
            do_test(),
            succeeded(Always()),
        )

    @given(
        content=binary(),
    )
    def test_duplicate_upload(self, content):
        """
        Upload the same content (via 'PUT /uri') twice
        """

        http_client = create_tahoe_treq_client()

        @inlineCallbacks
        def do_test():
            resp = yield http_client.put("http://example.com/uri", content)
            self.assertEqual(resp.code, 201)

            cap_raw = yield resp.content()
            self.assertThat(
                cap_raw,
                AfterPreprocessing(
                    from_string,
                    IsInstance(CHKFileURI)
                )
            )

            resp = yield http_client.put("http://example.com/uri", content)
            self.assertThat(resp.code, Equals(200))
        self.assertThat(
            do_test(),
            succeeded(Always()),
        )

    def test_download_missing(self):
        """
        Error if we download a capability that doesn't exist
        """

        http_client = create_tahoe_treq_client()
        cap_gen = capability_generator(b"URI:CHK:")
        cap = next(cap_gen).decode('ascii')
        uri = DecodedURL.from_text(u"http://example.com/uri?uri={}".format(cap))
        resp = http_client.get(uri.to_uri().to_text())

        self.assertThat(
            resp,
            succeeded(
                MatchesStructure(
                    code=Equals(500)
                )
            )
        )

    def test_download_no_arg(self):
        """
        Error if we GET from "/uri" with no ?uri= query-arg
        """

        http_client = create_tahoe_treq_client()

        uri = DecodedURL.from_text(u"http://example.com/uri/")
        resp = http_client.get(uri.to_uri().to_text())

        self.assertThat(
            resp,
            succeeded(
                MatchesStructure(
                    code=Equals(400)
                )
            )
        )
