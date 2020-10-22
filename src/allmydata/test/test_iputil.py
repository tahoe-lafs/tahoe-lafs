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

import re, errno, subprocess, os, socket
import gc

from twisted.trial import unittest

from tenacity import retry, stop_after_attempt

from foolscap.api import Tub

from allmydata.util import iputil, gcutil
import allmydata.test.common_util as testutil
from allmydata.util.namespace import Namespace


DOTTED_QUAD_RE=re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")

# Mock output from subprocesses should be bytes, that's what happens on both
# Python 2 and Python 3:
MOCK_IPADDR_OUTPUT = b"""\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 16436 qdisc noqueue state UNKNOWN \n\
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
    inet6 ::1/128 scope host \n\
       valid_lft forever preferred_lft forever
2: eth1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP qlen 1000
    link/ether d4:3d:7e:01:b4:3e brd ff:ff:ff:ff:ff:ff
    inet 192.168.0.6/24 brd 192.168.0.255 scope global eth1
    inet6 fe80::d63d:7eff:fe01:b43e/64 scope link \n\
       valid_lft forever preferred_lft forever
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP qlen 1000
    link/ether 90:f6:52:27:15:0a brd ff:ff:ff:ff:ff:ff
    inet 192.168.0.2/24 brd 192.168.0.255 scope global wlan0
    inet6 fe80::92f6:52ff:fe27:150a/64 scope link \n\
       valid_lft forever preferred_lft forever
"""

MOCK_IFCONFIG_OUTPUT = b"""\
eth1      Link encap:Ethernet  HWaddr d4:3d:7e:01:b4:3e  \n\
          inet addr:192.168.0.6  Bcast:192.168.0.255  Mask:255.255.255.0
          inet6 addr: fe80::d63d:7eff:fe01:b43e/64 Scope:Link
          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1
          RX packets:154242234 errors:0 dropped:0 overruns:0 frame:0
          TX packets:155461891 errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:1000 \n\
          RX bytes:84367213640 (78.5 GiB)  TX bytes:73401695329 (68.3 GiB)
          Interrupt:20 Memory:f4f00000-f4f20000 \n\

lo        Link encap:Local Loopback  \n\
          inet addr:127.0.0.1  Mask:255.0.0.0
          inet6 addr: ::1/128 Scope:Host
          UP LOOPBACK RUNNING  MTU:16436  Metric:1
          RX packets:27449267 errors:0 dropped:0 overruns:0 frame:0
          TX packets:27449267 errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:0 \n\
          RX bytes:192643017823 (179.4 GiB)  TX bytes:192643017823 (179.4 GiB)

wlan0     Link encap:Ethernet  HWaddr 90:f6:52:27:15:0a  \n\
          inet addr:192.168.0.2  Bcast:192.168.0.255  Mask:255.255.255.0
          inet6 addr: fe80::92f6:52ff:fe27:150a/64 Scope:Link
          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1
          RX packets:12352750 errors:0 dropped:0 overruns:0 frame:0
          TX packets:4501451 errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:1000 \n\
          RX bytes:3916475942 (3.6 GiB)  TX bytes:458353654 (437.1 MiB)
"""

# This is actually from a VirtualBox VM running XP.
MOCK_ROUTE_OUTPUT = b"""\
===========================================================================
Interface List
0x1 ........................... MS TCP Loopback interface
0x2 ...08 00 27 c3 80 ad ...... AMD PCNET Family PCI Ethernet Adapter - Packet Scheduler Miniport
===========================================================================
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0         10.0.2.2       10.0.2.15       20
         10.0.2.0    255.255.255.0        10.0.2.15       10.0.2.15       20
        10.0.2.15  255.255.255.255        127.0.0.1       127.0.0.1       20
   10.255.255.255  255.255.255.255        10.0.2.15       10.0.2.15       20
        127.0.0.0        255.0.0.0        127.0.0.1       127.0.0.1       1
        224.0.0.0        240.0.0.0        10.0.2.15       10.0.2.15       20
  255.255.255.255  255.255.255.255        10.0.2.15       10.0.2.15       1
Default Gateway:          10.0.2.2
===========================================================================
Persistent Routes:
  None
"""

UNIX_TEST_ADDRESSES = set(["127.0.0.1", "192.168.0.6", "192.168.0.2", "192.168.0.10"])
WINDOWS_TEST_ADDRESSES = set(["127.0.0.1", "10.0.2.15", "192.168.0.10"])
CYGWIN_TEST_ADDRESSES = set(["127.0.0.1", "192.168.0.10"])


class FakeProcess(object):
    def __init__(self, output, err):
        self.output = output
        self.err = err
    def communicate(self):
        return (self.output, self.err)


class ListAddresses(testutil.SignalMixin, unittest.TestCase):
    def test_get_local_ip_for(self):
        addr = iputil.get_local_ip_for('127.0.0.1')
        self.failUnless(DOTTED_QUAD_RE.match(addr))
        # Bytes can be taken as input:
        bytes_addr = iputil.get_local_ip_for(b'127.0.0.1')
        self.assertEqual(addr, bytes_addr)
        # The output is a native string:
        self.assertIsInstance(addr, native_str)

    def test_list_async(self):
        d = iputil.get_local_addresses_async()
        def _check(addresses):
            self.failUnlessIn("127.0.0.1", addresses)
            self.failIfIn("0.0.0.0", addresses)
        d.addCallbacks(_check)
        return d
    # David A.'s OpenSolaris box timed out on this test one time when it was at 2s.
    test_list_async.timeout=4

    def _test_list_async_mock(self, command, output, expected):
        ns = Namespace()
        ns.first = True

        def call_Popen(args, bufsize=0, executable=None, stdin=None, stdout=None, stderr=None,
                       preexec_fn=None, close_fds=False, shell=False, cwd=None, env=None,
                       universal_newlines=False, startupinfo=None, creationflags=0):
            if ns.first:
                ns.first = False
                e = OSError("EINTR")
                e.errno = errno.EINTR
                raise e
            elif os.path.basename(args[0]) == command:
                return FakeProcess(output, "")
            else:
                e = OSError("[Errno 2] No such file or directory")
                e.errno = errno.ENOENT
                raise e
        self.patch(subprocess, 'Popen', call_Popen)
        self.patch(os.path, 'isfile', lambda x: True)

        def call_get_local_ip_for(target):
            if target in ("localhost", "127.0.0.1"):
                return "127.0.0.1"
            else:
                return "192.168.0.10"
        self.patch(iputil, 'get_local_ip_for', call_get_local_ip_for)

        def call_which(name):
            return [name]
        self.patch(iputil, 'which', call_which)

        d = iputil.get_local_addresses_async()
        def _check(addresses):
            self.failUnlessEquals(set(addresses), set(expected))
        d.addCallbacks(_check)
        return d

    def test_list_async_mock_ip_addr(self):
        self.patch(iputil, 'platform', "linux2")
        return self._test_list_async_mock("ip", MOCK_IPADDR_OUTPUT, UNIX_TEST_ADDRESSES)

    def test_list_async_mock_ifconfig(self):
        self.patch(iputil, 'platform', "linux2")
        return self._test_list_async_mock("ifconfig", MOCK_IFCONFIG_OUTPUT, UNIX_TEST_ADDRESSES)

    def test_list_async_mock_route(self):
        self.patch(iputil, 'platform', "win32")
        return self._test_list_async_mock("route.exe", MOCK_ROUTE_OUTPUT, WINDOWS_TEST_ADDRESSES)

    def test_list_async_mock_cygwin(self):
        self.patch(iputil, 'platform', "cygwin")
        return self._test_list_async_mock(None, None, CYGWIN_TEST_ADDRESSES)


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
