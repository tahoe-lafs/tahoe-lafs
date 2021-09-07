"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from six import ensure_text

import os
import tempfile
from io import BytesIO, StringIO
from os.path import join

from twisted.trial import unittest
from twisted.internet import defer

from allmydata.mutable.publish import MutableData
from allmydata.scripts.common_http import BadResponse
from allmydata.scripts.tahoe_status import _handle_response_for_fragment
from allmydata.scripts.tahoe_status import _get_request_parameters_for_fragment
from allmydata.scripts.tahoe_status import pretty_progress
from allmydata.scripts.tahoe_status import do_status
from allmydata.web.status import marshal_json

from allmydata.immutable.upload import UploadStatus
from allmydata.immutable.downloader.status import DownloadStatus
from allmydata.mutable.publish import PublishStatus
from allmydata.mutable.retrieve import RetrieveStatus
from allmydata.mutable.servermap import UpdateStatus
from allmydata.util import jsonbytes as json

from ..no_network import GridTestMixin
from ..common_web import do_http
from .common import CLITestMixin


class FakeStatus(object):
    def __init__(self):
        self.status = []

    def setServiceParent(self, p):
        pass

    def get_status(self):
        return self.status

    def get_storage_index(self):
        return None

    def get_size(self):
        return None


class ProgressBar(unittest.TestCase):

    def test_ascii0(self):
        prog = pretty_progress(80.0, size=10, output_ascii=True)
        self.assertEqual('########. ', prog)

    def test_ascii1(self):
        prog = pretty_progress(10.0, size=10, output_ascii=True)
        self.assertEqual('#.        ', prog)

    def test_ascii2(self):
        prog = pretty_progress(13.0, size=10, output_ascii=True)
        self.assertEqual('#o        ', prog)

    def test_ascii3(self):
        prog = pretty_progress(90.0, size=10, output_ascii=True)
        self.assertEqual('#########.', prog)

    def test_unicode0(self):
        self.assertEqual(
            pretty_progress(82.0, size=10, output_ascii=False),
            u'\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u258e ',
        )

    def test_unicode1(self):
        self.assertEqual(
            pretty_progress(100.0, size=10, output_ascii=False),
            u'\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588',
        )


class _FakeOptions(dict):
    def __init__(self):
        self._tmp = tempfile.mkdtemp()
        os.mkdir(join(self._tmp, 'private'), 0o777)
        with open(join(self._tmp, 'private', 'api_auth_token'), 'w') as f:
            f.write('a' * 32)
        with open(join(self._tmp, 'node.url'), 'w') as f:
            f.write('localhost:9000')

        self['node-directory'] = self._tmp
        self['verbose'] = True
        self.stdout = StringIO()
        self.stderr = StringIO()


class Integration(GridTestMixin, CLITestMixin, unittest.TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        yield super(Integration, self).setUp()
        self.basedir = "cli/status"
        self.set_up_grid()

        # upload something
        c0 = self.g.clients[0]
        data = MutableData(b"data" * 100)
        filenode = yield c0.create_mutable_file(data)
        self.uri = filenode.get_uri()

        # make sure our web-port is actually answering
        yield do_http("get", 'http://127.0.0.1:{}/status?t=json'.format(self.client_webports[0]))

    def test_simple(self):
        d = self.do_cli('status')# '--verbose')

        def _check(ign):
            code, stdout, stderr = ign
            self.assertEqual(code, 0, stderr)
            self.assertTrue('Skipped 1' in stdout)
        d.addCallback(_check)
        return d

    @defer.inlineCallbacks
    def test_help(self):
        rc, _, _ = yield self.do_cli('status', '--help')
        self.assertEqual(rc, 0)


class CommandStatus(unittest.TestCase):
    """
    These tests just exercise the renderers and ensure they don't
    catastrophically fail.
    """

    def setUp(self):
        self.options = _FakeOptions()

    def test_no_operations(self):
        values = [
            StringIO(ensure_text(json.dumps({
                "active": [],
                "recent": [],
            }))),
            StringIO(ensure_text(json.dumps({
                "counters": {
                    "bytes_downloaded": 0,
                },
                "stats": {
                    "node.uptime": 0,
                }
            }))),
        ]
        def do_http(*args, **kw):
            return values.pop(0)
        do_status(self.options, do_http)

    def test_simple(self):
        recent_items = active_items = [
            UploadStatus(),
            DownloadStatus(b"abcd", 12345),
            PublishStatus(),
            RetrieveStatus(),
            UpdateStatus(),
            FakeStatus(),
        ]
        values = [
            BytesIO(json.dumps({
                "active": list(
                    marshal_json(item)
                    for item
                    in active_items
                ),
                "recent": list(
                    marshal_json(item)
                    for item
                    in recent_items
                ),
            }).encode("utf-8")),
            BytesIO(json.dumps({
                "counters": {
                    "bytes_downloaded": 0,
                },
                "stats": {
                    "node.uptime": 0,
                }
            }).encode("utf-8")),
        ]
        def do_http(*args, **kw):
            return values.pop(0)
        do_status(self.options, do_http)

    def test_fetch_error(self):
        def do_http(*args, **kw):
            raise RuntimeError("boom")
        do_status(self.options, do_http)


class JsonHelpers(unittest.TestCase):

    def test_bad_response(self):
        def do_http(*args, **kw):
            return
        with self.assertRaises(RuntimeError) as ctx:
            _handle_response_for_fragment(
                BadResponse('the url', 'some err'),
                'http://localhost:1234',
            )
        self.assertIn(
            "Failed to get",
            str(ctx.exception),
        )

    def test_happy_path(self):
        resp = _handle_response_for_fragment(
            StringIO('{"some": "json"}'),
            'http://localhost:1234/',
        )
        self.assertEqual(resp, dict(some='json'))

    def test_happy_path_post(self):
        resp = _handle_response_for_fragment(
            StringIO('{"some": "json"}'),
            'http://localhost:1234/',
        )
        self.assertEqual(resp, dict(some='json'))

    def test_no_data_returned(self):
        with self.assertRaises(RuntimeError) as ctx:
            _handle_response_for_fragment(StringIO('null'), 'http://localhost:1234')
        self.assertIn('No data from', str(ctx.exception))

    def test_no_post_args(self):
        with self.assertRaises(ValueError) as ctx:
            _get_request_parameters_for_fragment(
                {'node-url': 'http://localhost:1234'},
                '/fragment',
                method='POST',
                post_args=None,
            )
        self.assertIn(
            "Must pass post_args",
            str(ctx.exception),
        )

    def test_post_args_for_get(self):
        with self.assertRaises(ValueError) as ctx:
            _get_request_parameters_for_fragment(
                {'node-url': 'http://localhost:1234'},
                '/fragment',
                method='GET',
                post_args={'foo': 'bar'}
            )
        self.assertIn(
            "only valid for POST",
            str(ctx.exception),
        )
