"""
Track the port to Python 3.

The two easiest ways to run the part of the test suite which is expected to
pass on Python 3 are::

    $ tox -e py36

and::

    $ trial allmydata.test.python3_tests

This module has been ported to Python 3.
"""

from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

# Keep these sorted alphabetically, to reduce merge conflicts:
PORTED_MODULES = [
    "allmydata._monkeypatch",
    "allmydata.codec",
    "allmydata.crypto",
    "allmydata.crypto.aes",
    "allmydata.crypto.ed25519",
    "allmydata.crypto.error",
    "allmydata.crypto.rsa",
    "allmydata.crypto.util",
    "allmydata.hashtree",
    "allmydata.immutable.downloader",
    "allmydata.immutable.downloader.common",
    "allmydata.immutable.downloader.fetcher",
    "allmydata.immutable.downloader.finder",
    "allmydata.immutable.downloader.node",
    "allmydata.immutable.downloader.segmentation",
    "allmydata.immutable.downloader.share",
    "allmydata.immutable.downloader.status",
    "allmydata.immutable.encode",
    "allmydata.immutable.filenode",
    "allmydata.immutable.happiness_upload",
    "allmydata.immutable.layout",
    "allmydata.immutable.literal",
    "allmydata.immutable.upload",
    "allmydata.interfaces",
    "allmydata.introducer.interfaces",
    "allmydata.monitor",
    "allmydata.storage.common",
    "allmydata.storage.crawler",
    "allmydata.storage.expirer",
    "allmydata.storage.immutable",
    "allmydata.storage.lease",
    "allmydata.storage.mutable",
    "allmydata.storage.server",
    "allmydata.storage.shares",
    "allmydata.test.no_network",
    "allmydata.uri",
    "allmydata.util._python3",
    "allmydata.util.abbreviate",
    "allmydata.util.assertutil",
    "allmydata.util.base32",
    "allmydata.util.base62",
    "allmydata.util.configutil",
    "allmydata.util.connection_status",
    "allmydata.util.deferredutil",
    "allmydata.util.fileutil",
    "allmydata.util.dictutil",
    "allmydata.util.encodingutil",
    "allmydata.util.gcutil",
    "allmydata.util.happinessutil",
    "allmydata.util.hashutil",
    "allmydata.util.humanreadable",
    "allmydata.util.iputil",
    "allmydata.util.jsonbytes",
    "allmydata.util.log",
    "allmydata.util.mathutil",
    "allmydata.util.namespace",
    "allmydata.util.netstring",
    "allmydata.util.observer",
    "allmydata.util.pipeline",
    "allmydata.util.pollmixin",
    "allmydata.util.spans",
    "allmydata.util.statistics",
    "allmydata.util.time_format",
]

PORTED_TEST_MODULES = [
    "allmydata.test.mutable.test_datahandle",
    "allmydata.test.mutable.test_different_encoding",
    "allmydata.test.mutable.test_filehandle",
    "allmydata.test.test_abbreviate",
    "allmydata.test.test_base32",
    "allmydata.test.test_base62",
    "allmydata.test.test_checker",
    "allmydata.test.test_codec",
    "allmydata.test.test_common_util",
    "allmydata.test.test_configutil",
    "allmydata.test.test_connection_status",
    "allmydata.test.test_crawler",
    "allmydata.test.test_crypto",
    "allmydata.test.test_deferredutil",
    "allmydata.test.test_dictutil",
    "allmydata.test.test_download",
    "allmydata.test.test_encode",
    "allmydata.test.test_encodingutil",
    "allmydata.test.test_filenode",
    "allmydata.test.test_happiness",
    "allmydata.test.test_hashtree",
    "allmydata.test.test_hashutil",
    "allmydata.test.test_helper",
    "allmydata.test.test_humanreadable",
    "allmydata.test.test_immutable",
    "allmydata.test.test_iputil",
    "allmydata.test.test_log",
    "allmydata.test.test_monitor",
    "allmydata.test.test_netstring",
    "allmydata.test.test_no_network",
    "allmydata.test.test_observer",
    "allmydata.test.test_pipeline",
    "allmydata.test.test_python3",
    "allmydata.test.test_spans",
    "allmydata.test.test_statistics",
    "allmydata.test.test_storage",
    "allmydata.test.test_storage_web",
    "allmydata.test.test_time_format",
    "allmydata.test.test_upload",
    "allmydata.test.test_uri",
    "allmydata.test.test_util",
    "allmydata.test.test_version",
]
