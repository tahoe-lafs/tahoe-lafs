"""
Tests for allmydata.util.iputil.

Ported to Python 3.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2, native_str
if PY2:
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os, socket
import gc

from testtools.matchers import (
    MatchesAll,
    IsInstance,
    AllMatch,
    MatchesPredicate,
)

from twisted.trial import unittest

from tenacity import retry, stop_after_attempt

from foolscap.api import Tub

from allmydata.util import iputil, gcutil

from ..util.iputil import (
    get_local_addresses_sync,
)

from .common import (
    SyncTestCase,
)

class ListenOnUsed(unittest.TestCase):
    """Tests for listenOnUnused."""

    def create_tub(self, basedir):
        os.makedirs(basedir)
        tubfile = os.path.join(basedir, "tub.pem")
        tub = Tub(certFile=tubfile)
        tub.setOption("expose-remote-exception-types", False)
        tub.startService()
        self.addCleanup(tub.stopService)
        return tub

    @retry(stop=stop_after_attempt(7))
    def test_random_port(self):
        """A random port is selected if none is given."""
        tub = self.create_tub("utils/ListenOnUsed/test_randomport")
        self.assertEqual(len(tub.getListeners()), 0)
        portnum = iputil.listenOnUnused(tub)
        # We can connect to this port:
        s = socket.socket()
        s.connect(("127.0.0.1", portnum))
        s.close()
        self.assertEqual(len(tub.getListeners()), 1)

        # Listen on another port:
        tub2 = self.create_tub("utils/ListenOnUsed/test_randomport_2")
        portnum2 = iputil.listenOnUnused(tub2)
        self.assertNotEqual(portnum, portnum2)

    @retry(stop=stop_after_attempt(7))
    def test_specific_port(self):
        """The given port is used."""
        tub = self.create_tub("utils/ListenOnUsed/test_givenport")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        port2 = iputil.listenOnUnused(tub, port)
        self.assertEqual(port, port2)


class GcUtil(unittest.TestCase):
    """Tests for allmydata.util.gcutil, which is used only by listenOnUnused."""

    def test_gc_after_allocations(self):
        """The resource tracker triggers allocations every 26 allocations."""
        tracker = gcutil._ResourceTracker()
        collections = []
        self.patch(gc, "collect", lambda: collections.append(1))
        for _ in range(2):
            for _ in range(25):
                tracker.allocate()
                self.assertEqual(len(collections), 0)
            tracker.allocate()
            self.assertEqual(len(collections), 1)
            del collections[:]

    def test_release_delays_gc(self):
        """Releasing a file descriptor resource delays GC collection."""
        tracker = gcutil._ResourceTracker()
        collections = []
        self.patch(gc, "collect", lambda: collections.append(1))
        for _ in range(2):
            tracker.allocate()
        for _ in range(3):
            tracker.release()
        for _ in range(25):
            tracker.allocate()
            self.assertEqual(len(collections), 0)
        tracker.allocate()
        self.assertEqual(len(collections), 1)


class GetLocalAddressesSyncTests(SyncTestCase):
    """
    Tests for ``get_local_addresses_sync``.
    """
    def test_some_ipv4_addresses(self):
        """
        ``get_local_addresses_sync`` returns a list of IPv4 addresses as native
        strings.
        """
        self.assertThat(
            get_local_addresses_sync(),
            MatchesAll(
                IsInstance(list),
                AllMatch(
                    MatchesAll(
                        IsInstance(native_str),
                        MatchesPredicate(
                            lambda addr: socket.inet_pton(socket.AF_INET, addr),
                            "%r is not an IPv4 address.",
                        ),
                    ),
                ),
            ),
        )
