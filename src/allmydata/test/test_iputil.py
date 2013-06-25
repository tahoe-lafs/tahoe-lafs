
import re, errno, subprocess, os

from twisted.trial import unittest

from allmydata.util import iputil
import allmydata.test.common_util as testutil


class Namespace:
    pass

DOTTED_QUAD_RE=re.compile("^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")

MOCK_IPADDR_OUTPUT = """\
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

MOCK_IFCONFIG_OUTPUT = """\
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


class FakeProcess:
    def __init__(self, output, err):
        self.output = output
        self.err = err
    def communicate(self):
        return (self.output, self.err)


class ListAddresses(testutil.SignalMixin, unittest.TestCase):
    def test_get_local_ip_for(self):
        addr = iputil.get_local_ip_for('127.0.0.1')
        self.failUnless(DOTTED_QUAD_RE.match(addr))

    def test_list_async(self):
        d = iputil.get_local_addresses_async()
        def _check(addresses):
            self.failUnlessIn("127.0.0.1", addresses)
            self.failIfIn("0.0.0.0", addresses)
        d.addCallbacks(_check)
        return d
    # David A.'s OpenSolaris box timed out on this test one time when it was at 2s.
    test_list_async.timeout=4

    def _test_list_async_mock(self, command, output):
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
                e = OSError("not found")
                e.errno = errno.EEXIST
                raise e
        self.patch(subprocess, 'Popen', call_Popen)

        d = iputil.get_local_addresses_async()
        def _check(addresses):
            self.failUnlessEquals(set(addresses), set(["127.0.0.1", "192.168.0.6", "192.168.0.2"]))
        d.addCallbacks(_check)
        return d

    def test_list_async_mock_ip_addr(self):
        return self._test_list_async_mock("ip", MOCK_IPADDR_OUTPUT)

    def test_list_async_mock_ifconfig(self):
        return self._test_list_async_mock("ifconfig", MOCK_IFCONFIG_OUTPUT)
