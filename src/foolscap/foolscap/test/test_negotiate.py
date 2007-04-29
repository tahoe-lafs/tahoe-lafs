
from twisted.trial import unittest

from twisted.internet import protocol, defer, reactor
from twisted.application import internet
from foolscap import pb, negotiate, tokens
from foolscap import Referenceable, Tub, UnauthenticatedTub, BananaError
from foolscap.eventual import flushEventualQueue
crypto_available = False
try:
    from foolscap import crypto
    crypto_available = crypto.available
except ImportError:
    pass

# we use authenticated tubs if possible. If crypto is not available, fall
# back to unauthenticated ones
GoodEnoughTub = UnauthenticatedTub
if crypto_available:
    GoodEnoughTub = Tub

# this is tubID 3hemthez7rvgvyhjx2n5kdj7mcyar3yt
certData_low = \
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wNjExMjYxODUxMTBaFw0wNzExMjYxODUxMTBaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEA1DuK9NoF
fiSreA8rVqYPAjNiUqFelAAYPgnJR92Jry1J/dPA3ieNcCazbjVeKUFjd6+C30XR
APhajsAJFiJdnmgrtVILNrpZDC/vISKQoAmoT9hP/cMqFm8vmUG/+AXO76q63vfH
UmabBVDNTlM8FJpbm9M26cFMrH45G840gA0CAwEAATANBgkqhkiG9w0BAQQFAAOB
gQBCtjgBbF/s4w/16Y15lkTAO0xt8ZbtrvcsFPGTXeporonejnNaJ/aDbJt8Y6nY
ypJ4+LTT3UQwwvqX5xEuJmFhmXGsghRGypbU7Zxw6QZRppBRqz8xMS+y82mMZRQp
ezP+BiTvnoWXzDEP1233oYuELVgOVnHsj+rC017Ykfd7fw==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDUO4r02gV+JKt4DytWpg8CM2JSoV6UABg+CclH3YmvLUn908De
J41wJrNuNV4pQWN3r4LfRdEA+FqOwAkWIl2eaCu1Ugs2ulkML+8hIpCgCahP2E/9
wyoWby+ZQb/4Bc7vqrre98dSZpsFUM1OUzwUmlub0zbpwUysfjkbzjSADQIDAQAB
AoGBAIvxTykw8dpBt8cMyZjzGoZq93Rg74pLnbCap1x52iXmiRmUHWLfVcYT3tDW
4+X0NfBfjL5IvQ4UtTHXsqYjtvJfXWazYYa4INv5wKDBCd5a7s1YQ8R7mnhlBbRd
nqZ6RpGuQbd3gTGZCkUdbHPSqdCPAjryH9mtWoQZIepcIcoJAkEA77gjO+MPID6v
K6lf8SuFXHDOpaNOAiMlxVnmyQYQoF0PRVSpKOQf83An7R0S/jN3C7eZ6fPbZcyK
SFVktHhYwwJBAOKlgndbSkVzkQCMcuErGZT1AxHNNHSaDo8X3C47UbP3nf60SkxI
boqmpuPvEPUB9iPQdiNZGDU04+FUhe5Vtu8CQHDQHXS/hIzOMy2/BfG/Y4F/bSCy
W7HRzKK1jlCoVAbEBL3B++HMieTMsV17Q0bx/WI8Q2jAZE3iFmm4Fi6APHUCQCMi
5Yb7cBg0QlaDb4vY0q51DXTFC0zIVVl5qXjBWXk8+hFygdIxqHF2RIkxlr9k/nOu
7aGtPkOBX5KfN+QrBaECQQCltPE9YjFoqPezfyvGZoWAKb8bWzo958U3uVBnCw2f
Fs8AQDgI/9gOUXxXno51xQSdCnJLQJ8lThRUa6M7/F1B
-----END RSA PRIVATE KEY-----
"""

# this is tubID 6cxxohyb5ysw6ftpwprbzffxrghbfopm
certData_high = \
"""-----BEGIN CERTIFICATE-----
MIIBnjCCAQcCAgCEMA0GCSqGSIb3DQEBBAUAMBcxFTATBgNVBAMUDG5ld3BiX3Ro
aW5neTAeFw0wNjExMjYxODUxNDFaFw0wNzExMjYxODUxNDFaMBcxFTATBgNVBAMU
DG5ld3BiX3RoaW5neTCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEArfrebvt3
8FE3kKoscY2J/8A4J6CUUUiM7/gl00UvGvvjfdaWbsj4w0o8W2tE0X8Zce3dScSl
D6qVXy6AEc4Flqs0q02w9uNzcdDY6LF3NiK0Lq+JP4OjJeImUBe8wUU0RQxqf/oA
GhgHEZhTp6aAdxBXZFOVDloiW6iqrKH/thcCAwEAATANBgkqhkiG9w0BAQQFAAOB
gQBXi+edp3iz07wxcRztvXtTAjY/9gUwlfa6qSTg/cGqbF0OPa+sISBOFRnnC8qM
ENexlkpiiD4Oyj+UtO5g2CMz0E62cTJTqz6PfexnmKIGwYjq5wZ2tzOrB9AmAzLv
TQQ9CdcKBXLd2GCToh8hBvjyyFwj+yTSbq+VKLMFkBY8Rg==
-----END CERTIFICATE-----
-----BEGIN RSA PRIVATE KEY-----
MIICXgIBAAKBgQCt+t5u+3fwUTeQqixxjYn/wDgnoJRRSIzv+CXTRS8a++N91pZu
yPjDSjxba0TRfxlx7d1JxKUPqpVfLoARzgWWqzSrTbD243Nx0NjosXc2IrQur4k/
g6Ml4iZQF7zBRTRFDGp/+gAaGAcRmFOnpoB3EFdkU5UOWiJbqKqsof+2FwIDAQAB
AoGBAKrU3Vp+Y2u+Y+ARqKgrQai1tq36eAhEQ9dRgtqrYTCOyvcCIR5RCirAFvnx
H1bSBUsgNBw+EZGLfzZBs5FICaUjBOQYBYzfxux6+jlGvdl7idfHs7zogyEYBqye
0VkwzZ0mVXM2ujOD/z/ANkdEn2fGj/VwAYDlfvlyNZMckHp5AkEA5sc1VG3snWmG
lz4967MMzJ7XNpZcTvLEspjpH7hFbnXUHIQ4wPYOP7dhnVvKX1FiOQ8+zXVYDDGB
SK1ABzpc+wJBAMD+imwAhHNBbOb3cPYzOz6XRZaetvep3GfE2wKr1HXP8wchNXWj
Ijq6fJinwPlDugHaeNnfb+Dydd+YEiDTSJUCQDGCk2Jlotmyhfl0lPw4EYrkmO9R
GsSlOKXIQFtZwSuNg9AKXdKn9y6cPQjxZF1GrHfpWWPixNz40e+xm4bxcnkCQQCs
+zkspqYQ/CJVPpHkSnUem83GvAl5IKmp5Nr8oPD0i+fjixN0ljyW8RG+bhXcFaVC
BgTuG4QW1ptqRs5w14+lAkEAuAisTPUDsoUczywyoBbcFo3SVpFPNeumEXrj4MD/
uP+TxgBi/hNYaR18mTbKD4mzVSjqyEeRC/emV3xUpUrdqg==
-----END RSA PRIVATE KEY-----
"""

class Target(Referenceable):
    def __init__(self):
        self.calls = 0
    def remote_call(self):
        self.calls += 1


def getPage(url):
    """This is a variant of the standard twisted.web.client.getPage, which is
    smart enough to shut off its connection when its done (even if it fails).
    """
    from twisted.web import client
    scheme, host, port, path = client._parse(url)
    factory = client.HTTPClientFactory(url)
    c = reactor.connectTCP(host, port, factory)
    def shutdown(res, c):
        c.disconnect()
        return res
    factory.deferred.addBoth(shutdown, c)
    return factory.deferred

class OneTimeDeferred(defer.Deferred):
    def callback(self, res):
        if self.called:
            return
        return defer.Deferred.callback(self, res)

class BaseMixin:

    def requireCrypto(self):
        if not crypto_available:
            raise unittest.SkipTest("crypto not available")

    def setUp(self):
        self.connections = []
        self.servers = []
        self.services = []

    def tearDown(self):
        for c in self.connections:
            if c.transport:
                c.transport.loseConnection()
        dl = []
        for s in self.servers:
            dl.append(defer.maybeDeferred(s.stopListening))
        for s in self.services:
            dl.append(defer.maybeDeferred(s.stopService))
        d = defer.DeferredList(dl)
        d.addCallback(self._checkListeners)
        d.addCallback(flushEventualQueue)
        return d
    def _checkListeners(self, res):
        self.failIf(pb.Listeners)

    def stall(self, res, timeout):
        d = defer.Deferred()
        reactor.callLater(timeout, d.callback, res)
        return d

    def makeServer(self, authenticated, options={}, listenerOptions={}):
        if authenticated:
            self.tub = tub = Tub(options=options)
        else:
            self.tub = tub = UnauthenticatedTub(options=options)
        tub.startService()
        self.services.append(tub)
        l = tub.listenOn("tcp:0", listenerOptions)
        tub.setLocation("127.0.0.1:%d" % l.getPortnum())
        self.target = Target()
        return tub.registerReference(self.target), l.getPortnum()

    def makeSpecificServer(self, certData,
                           negotiationClass=negotiate.Negotiation):
        self.tub = tub = Tub(certData=certData)
        tub.negotiationClass = negotiationClass
        tub.startService()
        self.services.append(tub)
        l = tub.listenOn("tcp:0")
        tub.setLocation("127.0.0.1:%d" % l.getPortnum())
        self.target = Target()
        return tub.registerReference(self.target), l.getPortnum()

    def makeNullServer(self):
        f = protocol.Factory()
        f.protocol = protocol.Protocol # discards everything
        s = internet.TCPServer(0, f)
        s.startService()
        self.services.append(s)
        portnum = s._port.getHost().port
        return portnum

    def makeHTTPServer(self):
        try:
            from twisted.web import server, resource, static
        except ImportError:
            raise unittest.SkipTest('this test needs twisted.web')
        root = resource.Resource()
        root.putChild("", static.Data("hello\n", "text/plain"))
        s = internet.TCPServer(0, server.Site(root))
        s.startService()
        self.services.append(s)
        portnum = s._port.getHost().port
        return portnum

    def connectClient(self, portnum):
        tub = UnauthenticatedTub()
        tub.startService()
        self.services.append(tub)
        d = tub.getReference("pb://127.0.0.1:%d/hello" % portnum)
        return d

    def connectHTTPClient(self, portnum):
        return getPage("http://127.0.0.1:%d/foo" % portnum)

class Basic(BaseMixin, unittest.TestCase):

    def testOptions(self):
        url, portnum = self.makeServer(False, {'opt': 12})
        self.failUnlessEqual(self.tub.options['opt'], 12)

    def testAuthenticated(self):
        if not crypto_available:
            raise unittest.SkipTest("crypto not available")
        url, portnum = self.makeServer(True)
        client = Tub()
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        return d
    testAuthenticated.timeout = 10

    def testUnauthenticated(self):
        url, portnum = self.makeServer(False)
        client = UnauthenticatedTub()
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        return d
    testUnauthenticated.timeout = 10

    def testHalfAuthenticated1(self):
        if not crypto_available:
            raise unittest.SkipTest("crypto not available")
        url, portnum = self.makeServer(True)
        client = UnauthenticatedTub()
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        return d
    testHalfAuthenticated1.timeout = 10

    def testHalfAuthenticated2(self):
        if not crypto_available:
            raise unittest.SkipTest("crypto not available")
        url, portnum = self.makeServer(False)
        client = Tub()
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        return d
    testHalfAuthenticated2.timeout = 10

class Versus(BaseMixin, unittest.TestCase):

    def testVersusHTTPServerAuthenticated(self):
        if not crypto_available:
            raise unittest.SkipTest("crypto not available")
        portnum = self.makeHTTPServer()
        client = Tub()
        client.startService()
        self.services.append(client)
        url = "pb://1234@127.0.0.1:%d/target" % portnum
        d = client.getReference(url)
        d.addCallbacks(lambda res: self.fail("this is supposed to fail"),
                       lambda f: f.trap(BananaError))
        # the HTTP server needs a moment to notice that the connection has
        # gone away. Without this, trial flunks the test because of the
        # leftover HTTP server socket.
        d.addCallback(self.stall, 1)
        return d
    testVersusHTTPServerAuthenticated.timeout = 10

    def testVersusHTTPServerUnauthenticated(self):
        portnum = self.makeHTTPServer()
        client = UnauthenticatedTub()
        client.startService()
        self.services.append(client)
        url = "pbu://127.0.0.1:%d/target" % portnum
        d = client.getReference(url)
        d.addCallbacks(lambda res: self.fail("this is supposed to fail"),
                       lambda f: f.trap(BananaError))
        d.addCallback(self.stall, 1) # same reason as above
        return d
    testVersusHTTPServerUnauthenticated.timeout = 10

    def testVersusHTTPClientUnauthenticated(self):
        try:
            from twisted.web import error
        except ImportError:
            raise unittest.SkipTest('this test needs twisted.web')
        url, portnum = self.makeServer(False)
        d = self.connectHTTPClient(portnum)
        d.addCallbacks(lambda res: self.fail("this is supposed to fail"),
                       lambda f: f.trap(error.Error))
        return d
    testVersusHTTPClientUnauthenticated.timeout = 10

    def testVersusHTTPClientAuthenticated(self):
        if not crypto_available:
            raise unittest.SkipTest("crypto not available")
        try:
            from twisted.web import error
        except ImportError:
            raise unittest.SkipTest('this test needs twisted.web')
        url, portnum = self.makeServer(True)
        d = self.connectHTTPClient(portnum)
        d.addCallbacks(lambda res: self.fail("this is supposed to fail"),
                       lambda f: f.trap(error.Error))
        return d
    testVersusHTTPClientAuthenticated.timeout = 10

    def testNoConnection(self):
        url, portnum = self.makeServer(False)
        d = self.tub.stopService()
        d.addCallback(self._testNoConnection_1, url)
        return d
    testNoConnection.timeout = 10
    def _testNoConnection_1(self, res, url):
        self.services.remove(self.tub)
        client = UnauthenticatedTub()
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        d.addCallbacks(lambda res: self.fail("this is supposed to fail"),
                       self._testNoConnection_fail)
        return d
    def _testNoConnection_fail(self, why):
        from twisted.internet import error
        self.failUnless(why.check(error.ConnectionRefusedError))

    def testClientTimeout(self):
        portnum = self.makeNullServer()
        # lower the connection timeout to 2 seconds
        client = UnauthenticatedTub(options={'connect_timeout': 1})
        client.startService()
        self.services.append(client)
        url = "pbu://127.0.0.1:%d/target" % portnum
        d = client.getReference(url)
        d.addCallbacks(lambda res: self.fail("hey! this is supposed to fail"),
                       lambda f: f.trap(tokens.NegotiationError))
        return d
    testClientTimeout.timeout = 10

    def testServerTimeout(self):
        # lower the connection timeout to 1 seconds

        # the debug callback gets fired each time Negotiate.negotiationFailed
        # is fired, which happens twice (once for the timeout, once for the
        # resulting connectionLost), so we have to make sure the Deferred is
        # only fired once.
        d = OneTimeDeferred()
        options = {'server_timeout': 1,
                   'debug_negotiationFailed_cb': d.callback
                   }
        url, portnum = self.makeServer(False, listenerOptions=options)
        f = protocol.ClientFactory()
        f.protocol = protocol.Protocol # discards everything
        s = internet.TCPClient("127.0.0.1", portnum, f)
        s.startService()
        self.services.append(s)
        d.addCallbacks(lambda res: self.fail("hey! this is supposed to fail"),
                       lambda f: self._testServerTimeout_1)
        return d
    testServerTimeout.timeout = 10
    def _testServerTimeout_1(self, f):
        self.failUnless(f.check(tokens.NegotiationError))
        self.failUnlessEqual(f.value.args[0], "negotiation timeout")


class Parallel(BaseMixin, unittest.TestCase):
    # testParallel*: listen on two separate ports, set up a URL with both
    # ports in the locationHints field, the connect. PB is supposed to
    # connect to both ports at the same time, using whichever one completes
    # negotiation first. The other connection is supposed to be dropped
    # silently.

    # the cases we need to cover are enumerated by the possible states that
    # connection[1] can be in when connection[0] (the winning connection)
    # completes negotiation. Those states are:
    #  1: connectTCP initiated and failed
    #  2: connectTCP initiated, but not yet established
    #  3: connection established, but still in the PLAINTEXT phase
    #     (sent GET, waiting for the 101 Switching Protocols)
    #  4: still in ENCRYPTED phase: sent Hello, waiting for their Hello
    #  5: in DECIDING phase (non-master), waiting for their decision
    #   

    def makeServers(self, tubopts={}, lo1={}, lo2={}):
        self.requireCrypto()
        self.tub = tub = Tub(options=tubopts)
        tub.startService()
        self.services.append(tub)
        l1 = tub.listenOn("tcp:0", lo1)
        l2 = tub.listenOn("tcp:0", lo2)
        self.p1, self.p2 = l1.getPortnum(), l2.getPortnum()
        tub.setLocation("127.0.0.1:%d" % l1.getPortnum(),
                        "127.0.0.1:%d" % l2.getPortnum())
        self.target = Target()
        return tub.registerReference(self.target)

    def connect(self, url, authenticated=True):
        self.clientPhases = []
        opts = {"debug_stall_second_connection": True,
                "debug_gatherPhases": self.clientPhases}
        if authenticated:
            self.client = client = Tub(options=opts)
        else:
            self.client = client = UnauthenticatedTub(options=opts)
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        return d

    def checkConnectedToFirstListener(self, rr, targetPhases):
        # verify that we connected to the first listener, and not the second
        self.failUnlessEqual(rr.tracker.broker.transport.getPeer().port,
                             self.p1)
        # then pause a moment for the other connection to finish giving up
        d = self.stall(rr, 0.5)
        # and verify that we finished during the phase that we meant to test
        d.addCallback(lambda res:
                      self.failUnlessEqual(self.clientPhases, targetPhases,
                                           "negotiation was abandoned in "
                                           "the wrong phase"))
        return d

    def test1(self):
        # in this test, we stop listening on the second port, so the second
        # connection will terminate with an ECONNREFUSED before the first one
        # completes. We also slow down the first port so we're sure to
        # recognize the failed second connection before starting negotiation
        # on the first.
        url = self.makeServers(lo1={'debug_slow_connectionMade': True})
        d = self.tub.stopListeningOn(self.tub.getListeners()[1])
        d.addCallback(self._test1_1, url)
        return d
    def _test1_1(self, res, url):
        d = self.connect(url)
        d.addCallback(self.checkConnectedToFirstListener, [])
        #d.addCallback(self.stall, 1)
        return d
    test1.timeout = 10

    def test2(self):
        # slow down the second listener so that the first one is used. The
        # second listener will be connected but it will not respond to
        # negotiation for a moment, allowing the first connection to
        # complete.
        url = self.makeServers(lo2={'debug_slow_connectionMade': True})
        d = self.connect(url)
        d.addCallback(self.checkConnectedToFirstListener,
                      [negotiate.PLAINTEXT])
        #d.addCallback(self.stall, 1)
        return d
    test2.timeout = 10

    def test3(self):
        # have the second listener stall just before it does
        # sendPlaintextServer(). This insures the second connection will be
        # waiting in the PLAINTEXT phase when the first connection completes.
        url = self.makeServers(lo2={'debug_slow_sendPlaintextServer': True})
        d = self.connect(url)
        d.addCallback(self.checkConnectedToFirstListener,
                      [negotiate.PLAINTEXT])
        return d
    test3.timeout = 10

    def test4(self):
        # stall the second listener just before it sends the Hello.
        # This insures the second connection will be waiting in the ENCRYPTED
        # phase when the first connection completes.
        url = self.makeServers(lo2={'debug_slow_sendHello': True})
        d = self.connect(url)
        d.addCallback(self.checkConnectedToFirstListener,
                      [negotiate.ENCRYPTED])
        #d.addCallback(self.stall, 1)
        return d
    test4.timeout = 10

    def test5(self):
        # stall the second listener just before it sends the decision. This
        # insures the second connection will be waiting in the DECIDING phase
        # when the first connection completes.

        # note: this requires that the listener winds up as the master. We
        # force this by connecting from an unauthenticated Tub.
        url = self.makeServers(lo2={'debug_slow_sendDecision': True})
        d = self.connect(url, authenticated=False)
        d.addCallback(self.checkConnectedToFirstListener,
                      [negotiate.DECIDING])
        return d
    test5.timeout = 10


class CrossfireMixin(BaseMixin):
    # testSimultaneous*: similar to Parallel, but connection[0] is initiated
    # in the opposite direction. This is the case when two Tubs initiate
    # connections to each other at the same time.
    tub1IsMaster = False

    def makeServers(self, t1opts={}, t2opts={}, lo1={}, lo2={},
                    tubAauthenticated=True, tubBauthenticated=True):
        if tubAauthenticated or tubBauthenticated:
            self.requireCrypto()
        # first we create two Tubs
        if tubAauthenticated:
            a = Tub(options=t1opts)
        else:
            a = UnauthenticatedTub(options=t1opts)
        if tubBauthenticated:
            b = Tub(options=t1opts)
        else:
            b = UnauthenticatedTub(options=t1opts)

        # then we figure out which one will be the master, and call it tub1
        if a.tubID > b.tubID:
            # a is the master
            tub1,tub2 = a,b
        else:
            tub1,tub2 = b,a
        if not self.tub1IsMaster:
            tub1,tub2 = tub2,tub1
        self.tub1 = tub1
        self.tub2 = tub2

        # now fix up the options and everything else
        self.tub1phases = []
        t1opts['debug_gatherPhases'] = self.tub1phases
        tub1.options = t1opts
        self.tub2phases = []
        t2opts['debug_gatherPhases'] = self.tub2phases
        tub2.options = t2opts

        # connection[0], the winning connection, will be from tub1 to tub2

        tub1.startService()
        self.services.append(tub1)
        l1 = tub1.listenOn("tcp:0", lo1)
        tub1.setLocation("127.0.0.1:%d" % l1.getPortnum())
        self.target1 = Target()
        self.url1 = tub1.registerReference(self.target1)

        # connection[1], the abandoned connection, will be from tub2 to tub1
        tub2.startService()
        self.services.append(tub2)
        l2 = tub2.listenOn("tcp:0", lo2)
        tub2.setLocation("127.0.0.1:%d" % l2.getPortnum())
        self.target2 = Target()
        self.url2 = tub2.registerReference(self.target2)

    def connect(self):
        # initiate connection[1] from tub2 to tub1, which will stall (but the
        # actual getReference will eventually succeed once the
        # reverse-direction connection is established)
        d1 = self.tub2.getReference(self.url1)
        # give it a moment to get to the point where it stalls
        d = self.stall(None, 0.1)
        d.addCallback(self._connect, d1)
        return d, d1
    def _connect(self, res, d1):
        # now initiate connection[0], from tub1 to tub2
        d2 = self.tub1.getReference(self.url2)
        return d2

    def checkConnectedViaReverse(self, rref, targetPhases):
        # assert that connection[0] (from tub1 to tub2) is actually in use.
        # This connection uses a per-client allocated port number for the
        # tub1 side, and the tub2 Listener's port for the tub2 side.
        # Therefore tub1's Broker (as used by its RemoteReference) will have
        # a far-end port number that should match tub2's Listener.
        self.failUnlessEqual(rref.tracker.broker.transport.getPeer().port,
                             self.tub2.getListeners()[0].getPortnum())
        # in addition, connection[1] should have been abandoned during a
        # specific phase.
        self.failUnlessEqual(self.tub2phases, targetPhases)


class CrossfireReverse(CrossfireMixin, unittest.TestCase):
    # just like the following Crossfire except that tub2 is the master, just
    # in case it makes a difference somewhere
    tub1IsMaster = False

    def test1(self):
        # in this test, tub2 isn't listening at all. So not only will
        # connection[1] fail, the tub2.getReference that uses it will fail
        # too (whereas in all other tests, connection[1] is abandoned but
        # tub2.getReference succeeds)
        self.makeServers(lo1={'debug_slow_connectionMade': True})
        d = self.tub2.stopListeningOn(self.tub2.getListeners()[0])
        d.addCallback(self._test1_1)
        return d

    def _test1_1(self, res):
        d,d1 = self.connect()
        d.addCallbacks(lambda res: self.fail("hey! this is supposed to fail"),
                       self._test1_2, errbackArgs=(d1,))
        return d
    def _test1_2(self, why, d1):
        from twisted.internet import error
        self.failUnless(why.check(error.ConnectionRefusedError))
        # but now the other getReference should succeed
        return d1
    test1.timeout = 10

    def test2(self):
        self.makeServers(lo1={'debug_slow_connectionMade': True})
        d,d1 = self.connect()
        d.addCallback(self.checkConnectedViaReverse, [negotiate.PLAINTEXT])
        d.addCallback(lambda res: d1) # other getReference should work too
        return d
    test2.timeout = 10

    def test3(self):
        self.makeServers(lo1={'debug_slow_sendPlaintextServer': True})
        d,d1 = self.connect()
        d.addCallback(self.checkConnectedViaReverse, [negotiate.PLAINTEXT])
        d.addCallback(lambda res: d1) # other getReference should work too
        return d
    test3.timeout = 10

    def test4(self):
        self.makeServers(lo1={'debug_slow_sendHello': True})
        d,d1 = self.connect()
        d.addCallback(self.checkConnectedViaReverse, [negotiate.ENCRYPTED])
        d.addCallback(lambda res: d1) # other getReference should work too
        return d
    test4.timeout = 10

class Crossfire(CrossfireReverse):
    tub1IsMaster = True

    def test5(self):
        # this is the only test where we rely upon the fact that
        # makeServers() always puts the higher-numbered Tub (which will be
        # the master) in self.tub1

        # connection[1] (the abandoned connection) is started from tub2 to
        # tub1. It connects, begins negotiation (tub1 is the master), but
        # then is stalled because we've added the debug_slow_sendDecision
        # flag to tub1's Listener. That allows connection[0] to begin from
        # tub1 to tub2, which is *not* stalled (because we added the slowdown
        # flag to the Listener's options, not tub1.options), so it completes
        # normally. When connection[1] is unpaused and hits switchToBanana,
        # it discovers that it already has a Broker in place, and the
        # connection is abandoned.

        self.makeServers(lo1={'debug_slow_sendDecision': True})
        d,d1 = self.connect()
        d.addCallback(self.checkConnectedViaReverse, [negotiate.DECIDING])
        d.addCallback(lambda res: d1) # other getReference should work too
        return d
    test5.timeout = 10

# TODO: some of these tests cause the TLS connection to be abandoned, and it
# looks like TLS sockets don't shut down very cleanly. I connectionLost
# getting called with the following error (instead of a normal ConnectionDone
# exception):
#  2005/10/10 19:56 PDT [Negotiation,0,127.0.0.1]
#  Negotiation.negotiationFailed: [Failure instance: Traceback:
#   exceptions.AttributeError: TLSConnection instance has no attribute 'socket'
#          twisted/internet/tcp.py:402:connectionLost
#          twisted/pb/negotiate.py:366:connectionLost
#          twisted/pb/negotiate.py:205:debug_forceTimer
#          twisted/pb/negotiate.py:223:debug_fireTimer
#          --- <exception caught here> ---
#          twisted/pb/negotiate.py:324:dataReceived
#          twisted/pb/negotiate.py:432:handlePLAINTEXTServer
#          twisted/pb/negotiate.py:457:sendPlaintextServerAndStartENCRYPTED
#          twisted/pb/negotiate.py:494:startENCRYPTED
#          twisted/pb/negotiate.py:768:startTLS
#          twisted/internet/tcp.py:693:startTLS
#          twisted/internet/tcp.py:314:startTLS
#          ]
#
# specifically, I saw this happen for CrossfireReverse.test2, Parallel.test2

# other tests don't do quite what I want: closing a connection (say, due to a
# duplicate broker) should send a sensible error message to the other side,
# rather than triggering a low-level protocol error.


class Existing(CrossfireMixin, unittest.TestCase):

    def checkNumBrokers(self, res, expected, dummy):
        if type(expected) not in (tuple,list):
            expected = [expected]
        self.failUnless(len(self.tub1.brokers) +
                        len(self.tub1.unauthenticatedBrokers) in expected)
        self.failUnless(len(self.tub2.brokers) +
                        len(self.tub2.unauthenticatedBrokers) in expected)

    def testAuthenticated(self):
        # When two authenticated Tubs connect, that connection should be used
        # in the reverse connection too
        self.makeServers()
        d = self.tub1.getReference(self.url2)
        d.addCallback(self._testAuthenticated_1)
        return d
    def _testAuthenticated_1(self, r12):
        # this should use the existing connection
        d = self.tub2.getReference(self.url1)
        d.addCallback(self.checkNumBrokers, 1, (r12,))
        return d

    def testUnauthenticated(self):
        # But when two non-authenticated Tubs connect, they don't get to
        # share connections.
        self.makeServers(tubAauthenticated=False, tubBauthenticated=False)
        # the non-authenticated Tub gets a tubID of None, so it becomes tub2.
        # We want to verify that connections are not shared regardless of
        # which direction is authenticated. In this test, the first
        # connection
        d = self.tub1.getReference(self.url2)
        d.addCallback(self._testUnauthenticated_1)
        return d
    def _testUnauthenticated_1(self, r12):
        # this should *not* use the existing connection
        d = self.tub2.getReference(self.url1)
        d.addCallback(self.checkNumBrokers, 2, (r12,))
        return d

    def testHalfAuthenticated1(self):
        # When an authenticated Tub connects to a non-authenticated Tub, the
        # reverse connection *is* allowed to share the connection (although,
        # due to what I think are limitations in SSL, it probably won't)
        self.makeServers(tubAauthenticated=True, tubBauthenticated=False)
        # The non-authenticated Tub gets a tubID of None, so it becomes tub2.
        # Therefore this is the authenticated-to-non-authenticated
        # connection.
        d = self.tub1.getReference(self.url2)
        d.addCallback(self._testHalfAuthenticated1_1)
        return d
    def _testHalfAuthenticated1_1(self, r12):
        d = self.tub2.getReference(self.url1)
        d.addCallback(self.checkNumBrokers, (1,2), (r12,))
        return d

    def testHalfAuthenticated2(self):
        # On the other hand, when a non-authenticated Tub connects to an
        # authenticated Tub, the reverse connection is forbidden (because the
        # non-authenticated Tub's identity is based upon its Listener's
        # location)
        self.makeServers(tubAauthenticated=True, tubBauthenticated=False)
        # The non-authenticated Tub gets a tubID of None, so it becomes tub2.
        # Therefore this is the authenticated-to-non-authenticated
        # connection.
        d = self.tub2.getReference(self.url1)
        d.addCallback(self._testHalfAuthenticated2_1)
        return d
    def _testHalfAuthenticated2_1(self, r21):
        d = self.tub1.getReference(self.url2)
        d.addCallback(self.checkNumBrokers, 2, (r21,))
        return d

# this test will have to change when the regular Negotiation starts using
# different decision blocks. The version numbers must be updated each time
# the negotiation version is changed.
assert negotiate.Negotiation.maxVersion == 3
MAX_HANDLED_VERSION = negotiate.Negotiation.maxVersion
UNHANDLED_VERSION = 4
class NegotiationVbig(negotiate.Negotiation):
    maxVersion = UNHANDLED_VERSION
    def __init__(self):
        negotiate.Negotiation.__init__(self)
        self.negotiationOffer["extra"] = "new value"
    def evaluateNegotiationVersion4(self, offer):
        # just like v1, but different
        return self.evaluateNegotiationVersion1(offer)
    def acceptDecisionVersion4(self, decision):
        return self.acceptDecisionVersion1(decision)

class NegotiationVbigOnly(NegotiationVbig):
    minVersion = UNHANDLED_VERSION

class Future(BaseMixin, unittest.TestCase):
    def testFuture1(self):
        # when a peer that understands version=[1] that connects to a peer
        # that understands version=[1,2], they should pick version=1

        # the listening Tub will have the higher tubID, and thus make the
        # negotiation decision
        self.requireCrypto()
        url, portnum = self.makeSpecificServer(certData_high)
        # the client 
        client = Tub(certData=certData_low)
        client.negotiationClass = NegotiationVbig
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        def _check_version(rref):
            ver = rref.tracker.broker._banana_decision_version
            self.failUnlessEqual(ver, MAX_HANDLED_VERSION)
        d.addCallback(_check_version)
        return d
    testFuture1.timeout = 10

    def testFuture2(self):
        # same as before, but the connecting Tub will have the higher tubID,
        # and thus make the negotiation decision
        self.requireCrypto()
        url, portnum = self.makeSpecificServer(certData_low)
        # the client 
        client = Tub(certData=certData_high)
        client.negotiationClass = NegotiationVbig
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        def _check_version(rref):
            ver = rref.tracker.broker._banana_decision_version
            self.failUnlessEqual(ver, MAX_HANDLED_VERSION)
        d.addCallback(_check_version)
        return d
    testFuture2.timeout = 10

    def testFuture3(self):
        # same as testFuture1, but it is the listening server that
        # understands [1,2]
        self.requireCrypto()
        url, portnum = self.makeSpecificServer(certData_high, NegotiationVbig)
        client = Tub(certData=certData_low)
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        def _check_version(rref):
            ver = rref.tracker.broker._banana_decision_version
            self.failUnlessEqual(ver, MAX_HANDLED_VERSION)
        d.addCallback(_check_version)
        return d
    testFuture3.timeout = 10

    def testFuture4(self):
        # same as testFuture2, but it is the listening server that
        # understands [1,2]
        self.requireCrypto()
        url, portnum = self.makeSpecificServer(certData_low, NegotiationVbig)
        # the client 
        client = Tub(certData=certData_high)
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        def _check_version(rref):
            ver = rref.tracker.broker._banana_decision_version
            self.failUnlessEqual(ver, MAX_HANDLED_VERSION)
        d.addCallback(_check_version)
        return d
    testFuture4.timeout = 10

    def testTooFarInFuture1(self):
        # when a peer that understands version=[1] that connects to a peer
        # that only understands version=[2], they should fail to negotiate

        # the listening Tub will have the higher tubID, and thus make the
        # negotiation decision
        self.requireCrypto()
        url, portnum = self.makeSpecificServer(certData_high)
        # the client 
        client = Tub(certData=certData_low)
        client.negotiationClass = NegotiationVbigOnly
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        def _oops_succeeded(rref):
            self.fail("hey! this is supposed to fail")
        def _check_failure(f):
            f.trap(tokens.NegotiationError)
        d.addCallbacks(_oops_succeeded, _check_failure)
        return d
    testTooFarInFuture1.timeout = 10

    def testTooFarInFuture2(self):
        # same as before, but the connecting Tub will have the higher tubID,
        # and thus make the negotiation decision
        self.requireCrypto()
        url, portnum = self.makeSpecificServer(certData_low)
        client = Tub(certData=certData_high)
        client.negotiationClass = NegotiationVbigOnly
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        def _oops_succeeded(rref):
            self.fail("hey! this is supposed to fail")
        def _check_failure(f):
            f.trap(tokens.NegotiationError)
        d.addCallbacks(_oops_succeeded, _check_failure)
        return d
    testTooFarInFuture1.timeout = 10

    def testTooFarInFuture3(self):
        # same as testTooFarInFuture1, but it is the listening server which
        # only understands [2]
        self.requireCrypto()
        url, portnum = self.makeSpecificServer(certData_high,
                                               NegotiationVbigOnly)
        client = Tub(certData=certData_low)
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        def _oops_succeeded(rref):
            self.fail("hey! this is supposed to fail")
        def _check_failure(f):
            f.trap(tokens.NegotiationError)
        d.addCallbacks(_oops_succeeded, _check_failure)
        return d
    testTooFarInFuture3.timeout = 10

    def testTooFarInFuture4(self):
        # same as testTooFarInFuture2, but it is the listening server which
        # only understands [2]
        self.requireCrypto()
        url, portnum = self.makeSpecificServer(certData_low,
                                               NegotiationVbigOnly)
        client = Tub(certData=certData_high)
        client.startService()
        self.services.append(client)
        d = client.getReference(url)
        def _oops_succeeded(rref):
            self.fail("hey! this is supposed to fail")
        def _check_failure(f):
            f.trap(tokens.NegotiationError)
        d.addCallbacks(_oops_succeeded, _check_failure)
        return d
    testTooFarInFuture4.timeout = 10

# disable all tests unless NEWPB_TEST_NEGOTIATION is set in the environment.
# The negotiation tests are sensitive to system load, and the intermittent
# failures are really annoying. The 'right' solution to this involves
# completely rearchitecting connection establishment, to provide debug/test
# hooks to get control in between the various phases. It also requires
# creating a loopback connection type (as a peer of TCP) which has
# deterministic timing behavior.

#import os
if False: #not os.environ.get("NEWPB_TEST_NEGOTIATION"):
    del Basic
    del Versus
    del Parallel
    del CrossfireReverse
    del Crossfire
    del Existing
