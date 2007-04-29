# -*- test-case-name: foolscap.test.test_pb -*-

import os.path, weakref
from zope.interface import implements
from twisted.internet import defer, protocol
from twisted.application import service, strports

from foolscap import ipb, base32, negotiate, broker, observer
from foolscap.referenceable import SturdyRef
from foolscap.tokens import PBError, BananaError
from foolscap.reconnector import Reconnector

crypto_available = False
try:
    from foolscap import crypto
    crypto_available = crypto.available
except ImportError:
    pass


Listeners = []
class Listener(protocol.ServerFactory):
    """I am responsible for a single listening port, which may connect to
    multiple Tubs. I have a strports-based Service, which I will attach as a
    child of one of my Tubs. If that Tub disconnects, I will reparent the
    Service to a remaining one.

    Unauthenticated Tubs use a TubID of 'None'. There may be at most one such
    Tub attached to any given Listener."""

    # this also serves as the ServerFactory

    def __init__(self, port, options={},
                 negotiationClass=negotiate.Negotiation):
        """
        @type port: string
        @param port: a L{twisted.application.strports} -style description.
        """
        name, args, kw = strports.parse(port, None)
        assert name in ("TCP", "UNIX") # TODO: IPv6
        self.port = port
        self.options = options
        self.negotiationClass = negotiationClass
        self.parentTub = None
        self.tubs = {}
        self.redirects = {}
        self.s = strports.service(port, self)
        Listeners.append(self)

    def getPortnum(self):
        """When this Listener was created with a strport string of '0' or
        'tcp:0' (meaning 'please allocate me something'), and if the Listener
        is active (it is attached to a Tub which is in the 'running' state),
        this method will return the port number that was allocated. This is
        useful for the following pattern::

            t = Tub()
            l = t.listenOn('tcp:0')
            t.setLocation('localhost:%d' % l.getPortnum())
        """

        assert self.s.running
        name, args, kw = strports.parse(self.port, None)
        assert name in ("TCP",)
        return self.s._port.getHost().port

    def __repr__(self):
        if self.tubs:
            return "<Listener at 0x%x on %s with tubs %s>" % (
                abs(id(self)),
                self.port,
                ",".join([str(k) for k in self.tubs.keys()]))
        return "<Listener at 0x%x on %s with no tubs>" % (abs(id(self)),
                                                          self.port)

    def addTub(self, tub):
        if tub.tubID in self.tubs:
            if tub.tubID is None:
                raise RuntimeError("This Listener (on %s) already has an "
                                   "unauthenticated Tub, you cannot add a "
                                   "second one" % self.port)
            raise RuntimeError("This Listener (on %s) is already connected "
                               "to TubID '%s'" % (self.port, tub.tubID))
        self.tubs[tub.tubID] = tub
        if self.parentTub is None:
            self.parentTub = tub
            self.s.setServiceParent(self.parentTub)

    def removeTub(self, tub):
        # this might return a Deferred, since the removal might cause the
        # Listener to shut down. It might also return None.
        del self.tubs[tub.tubID]
        if self.parentTub is tub:
            # we need to switch to a new one
            tubs = self.tubs.values()
            if tubs:
                self.parentTub = tubs[0]
                # TODO: I want to do this without first doing
                # disownServiceParent, so the port remains listening. Can we
                # do this? It looks like setServiceParent does
                # disownServiceParent first, so it may glitch.
                self.s.setServiceParent(self.parentTub)
            else:
                # no more tubs, this Listener will go away now
                d = self.s.disownServiceParent()
                Listeners.remove(self)
                return d
        return None

    def getService(self):
        return self.s

    def addRedirect(self, tubID, location):
        assert tubID is not None # unauthenticated Tubs don't get redirects
        self.redirects[tubID] = location
    def removeRedirect(self, tubID):
        del self.redirects[tubID]

    def buildProtocol(self, addr):
        """Return a Broker attached to me (as the service provider).
        """
        proto = self.negotiationClass()
        proto.initServer(self)
        proto.factory = self
        return proto

    def lookupTubID(self, tubID):
        return self.tubs.get(tubID), self.redirects.get(tubID)


class Tub(service.MultiService):
    """I am a presence in the PB universe, also known as a Tub.

    I am a Service (in the twisted.application.service.Service sense),
    so you either need to call my startService() method before using me,
    or setServiceParent() me to a running service.

    This is the primary entry point for all PB-using applications, both
    clients and servers.

    I am known to the outside world by a base URL, which may include
    authentication information (a yURL). This is my 'TubID'.

    I contain Referenceables, and manage RemoteReferences to Referenceables
    that live in other Tubs.


    @param certData: if provided, use it as a certificate rather than
                     generating a new one. This is a PEM-encoded
                     private/public keypair, as returned by Tub.getCertData()

    @param certFile: if provided, the Tub will store its certificate in
                     this file. If the file does not exist when the Tub is
                     created, the Tub will generate a new certificate and
                     store it here. If the file does exist, the certificate
                     will be loaded from this file.

                     The simplest way to use the Tub is to choose a long-term
                     location for the certificate, use certFile= to tell the
                     Tub about it, and then let the Tub manage its own
                     certificate.

                     You may provide certData, or certFile, (or neither), but
                     not both.

    @param options: a dictionary of options that can influence connection
                    connection negotiation. Currently defined keys are:
                     - debug_slow: if True, wait half a second between
                                   each negotiation response

    @ivar brokers: maps TubIDs to L{Broker} instances

    @ivar listeners: maps strport to TCPServer service

    @ivar referenceToName: maps Referenceable to a name
    @ivar nameToReference: maps name to Referenceable

    """
    implements(ipb.ITub)

    unsafeTracebacks = True # TODO: better way to enable this
    logLocalFailures = False
    logRemoteFailures = False
    debugBanana = False
    NAMEBITS = 160 # length of swissnumber for each reference
    TUBIDBITS = 16 # length of non-crypto tubID
    encrypted = True
    negotiationClass = negotiate.Negotiation
    brokerClass = broker.Broker
    keepaliveTimeout = 4*60 # ping when connection has been idle this long
    disconnectTimeout = None # disconnect after this much idle time

    def __init__(self, certData=None, certFile=None, options={}):
        service.MultiService.__init__(self)
        self.setup(options)
        if certFile:
            self.setupEncryptionFile(certFile)
        else:
            self.setupEncryption(certData)

    def setupEncryptionFile(self, certFile):
        if os.path.exists(certFile):
            certData = open(certFile, "rb").read()
            self.setupEncryption(certData)
        else:
            self.setupEncryption(None)
            f = open(certFile, "wb")
            f.write(self.getCertData())
            f.close()

    def setupEncryption(self, certData):
        if not crypto_available:
            raise RuntimeError("crypto for PB is not available, "
                               "try importing foolscap.crypto and see "
                               "what happens")
        if certData:
            cert = crypto.PrivateCertificate.loadPEM(certData)
        else:
            cert = self.createCertificate()
        self.myCertificate = cert
        self.tubID = crypto.digest32(cert.digest("sha1"))

    def setup(self, options):
        self.options = options
        self.listeners = []
        self.locationHints = []

        # local Referenceables
        self.nameToReference = weakref.WeakValueDictionary()
        self.referenceToName = weakref.WeakKeyDictionary()
        self.strongReferences = []
        # remote stuff. Most of these use a TubRef (or NoAuthTubRef) as a
        # dictionary key
        self.tubConnectors = {} # maps TubRef to a TubConnector
        self.waitingForBrokers = {} # maps TubRef to list of Deferreds
        self.brokers = {} # maps TubRef to a Broker that connects to them
        self.unauthenticatedBrokers = [] # inbound Brokers without TubRefs
        self.reconnectors = []

        self._allBrokersAreDisconnected = observer.OneShotObserverList()
        self._activeConnectors = []
        self._allConnectorsAreFinished = observer.OneShotObserverList()

    def setOption(self, name, value):
        if name == "logLocalFailures":
            # log (with log.err) any exceptions that occur during the
            # execution of a local Referenceable's method, which is invoked
            # on behalf of a remote caller. These exceptions are reported to
            # the remote caller through their callRemote's Deferred as usual:
            # this option enables logging on the callee's side (i.e. our
            # side) as well.
            #
            # TODO: This does not yet include Violations which were raised
            # because the inbound callRemote had arguments that didn't meet
            # our specifications. But it should.
            self.logLocalFailures = value
        elif name == "logRemoteFailures":
            # log (with log.err) any exceptions that occur during the
            # execution of a remote Referenceabe's method, invoked on behalf
            # of a local RemoteReference.callRemote(). These exceptions are
            # reported to our local caller through the usual Deferred.errback
            # mechanism: this enables logging on the caller's side (i.e. our
            # side) as well.
            self.logRemoteFailures = value
        elif name == "keepaliveTimeout":
            self.keepaliveTimeout = value
        elif name == "disconnectTimeout":
            self.disconnectTimeout = value
        else:
            raise KeyError("unknown option name '%s'" % name)

    def createCertificate(self):
        # this is copied from test_sslverify.py
        dn = crypto.DistinguishedName(commonName="newpb_thingy")
        keypair = crypto.KeyPair.generate()
        req = keypair.certificateRequest(dn)
        certData = keypair.signCertificateRequest(dn, req,
                                                  lambda dn: True,
                                                  132)
        cert = keypair.newCertificate(certData)
        #opts = cert.options()
        # 'opts' can be given to reactor.listenSSL, or to transport.startTLS

        return cert

    def getCertData(self):
        # the string returned by this method can be used as the certData=
        # argument to create a new Tub with the same identity. TODO: actually
        # test this, I don't know if dump/keypair.newCertificate is the right
        # pair of methods.
        return self.myCertificate.dumpPEM()

    def setLocation(self, *hints):
        """Tell this service what its location is: a host:port description of
        how to reach it from the outside world. You need to use this because
        the Tub can't do it without help. If you do a
        C{s.listenOn('tcp:1234')}, and the host is known as
        C{foo.example.com}, then it would be appropriate to do::

            s.setLocation('foo.example.com:1234')

        You must set the location before you can register any references.

        Encrypted Tubs can have multiple location hints, just provide
        multiple arguments. Unauthenticated Tubs can only have one location."""

        if not self.encrypted and len(hints) > 1:
            raise PBError("Unauthenticated tubs may only have one "
                          "location hint")
        self.locationHints = hints

    def listenOn(self, what, options={}):
        """Start listening for connections.

        @type  what: string or Listener instance
        @param what: a L{twisted.application.strports} -style description,
                     or a L{Listener} instance returned by a previous call to
                     listenOn.
        @param options: a dictionary of options that can influence connection
                        negotiation before the target Tub has been determined

        @return: The Listener object that was created. This can be used to
        stop listening later on, to have another Tub listen on the same port,
        and to figure out which port was allocated when you used a strports
        specification of'tcp:0'. """

        if type(what) is str:
            l = Listener(what, options, self.negotiationClass)
        else:
            assert not options
            l = what
        assert l not in self.listeners
        l.addTub(self)
        self.listeners.append(l)
        return l

    def stopListeningOn(self, l):
        # this returns a Deferred when the port is shut down
        self.listeners.remove(l)
        d = defer.maybeDeferred(l.removeTub, self)
        return d

    def getListeners(self):
        """Return the set of Listener objects that allow the outside world to
        connect to this Tub."""
        return self.listeners[:]

    def clone(self):
        """Return a new Tub (with a different ID), listening on the same
        ports as this one."""
        if self.encrypted:
            t = Tub()
        else:
            t = UnauthenticatedTub()
        for l in self.listeners:
            t.listenOn(l)
        return t

    def connectorStarted(self, c):
        assert self.running
        self._activeConnectors.append(c)
    def connectorFinished(self, c):
        self._activeConnectors.remove(c)
        if not self.running and not self._activeConnectors:
            self._allConnectorsAreFinished.fire(self)


    def _tubsAreNotRestartable(self):
        raise RuntimeError("Sorry, but Tubs cannot be restarted.")
    def _tubHasBeenShutDown(self):
        raise RuntimeError("Sorry, but this Tub has been shut down.")

    def stopService(self):
        # note that once you stopService a Tub, I cannot be restarted. (at
        # least this code is not designed to make that possible.. it might be
        # doable in the future).
        self.startService = self._tubsAreNotRestartable
        self.getReference = self._tubHasBeenShutDown
        self.connectTo = self._tubHasBeenShutDown
        dl = []
        for rc in self.reconnectors:
            rc.stopConnecting()
        del self.reconnectors
        for l in self.listeners:
            # TODO: rethink this, what I want is for stopService to cause all
            # Listeners to shut down, but I'm not sure this is the right way
            # to do it.
            d = l.removeTub(self)
            if isinstance(d, defer.Deferred):
                dl.append(d)
        dl.append(service.MultiService.stopService(self))

        if self._activeConnectors:
            dl.append(self._allConnectorsAreFinished.whenFired())
        for c in self._activeConnectors:
            c.shutdown()

        if self.brokers or self.unauthenticatedBrokers:
            dl.append(self._allBrokersAreDisconnected.whenFired())
        for b in self.brokers.values():
            b.shutdown()
        for b in self.unauthenticatedBrokers:
            b.shutdown()

        return defer.DeferredList(dl)

    def generateSwissnumber(self, bits):
        bytes = os.urandom(bits/8)
        return base32.encode(bytes)

    def buildURL(self, name):
        if self.encrypted:
            # TODO: IPv6 dotted-quad addresses have colons, but need to have
            # host:port
            hints = ",".join(self.locationHints)
            return "pb://" + self.tubID + "@" + hints + "/" + name
        return "pbu://" + self.locationHints[0] + "/" + name

    def registerReference(self, ref, name=None):
        """Make a Referenceable available to the outside world. A URL is
        returned which can be used to access this object. This registration
        will remain in effect (and the Tub will retain a reference to the
        object to keep it meaningful) until explicitly unregistered, or the
        Tub is shut down.

        @type  name: string (optional)
        @param name: if provided, the object will be registered with this
                     name. If not, a random (unguessable) string will be
                     used.

        @rtype: string
        @return: the URL which points to this object. This URL can be passed
        to Tub.getReference() in any Tub on any host which can reach this
        one.
        """

        if not self.locationHints:
            raise RuntimeError("you must setLocation() before "
                               "you can registerReference()")
        name = self._assignName(ref, name)
        assert name
        if ref not in self.strongReferences:
            self.strongReferences.append(ref)
        return self.buildURL(name)

    # this is called by either registerReference or by
    # getOrCreateURLForReference
    def _assignName(self, ref, preferred_name=None):
        """Make a Referenceable available to the outside world, but do not
        retain a strong reference to it. If we must create a new name, use
        preferred_name. If that is None, use a random unguessable name.
        """
        if not self.locationHints:
            # without a location, there is no point in giving it a name
            return None
        if self.referenceToName.has_key(ref):
            return self.referenceToName[ref]
        name = preferred_name
        if not name:
            name = self.generateSwissnumber(self.NAMEBITS)
        self.referenceToName[ref] = name
        self.nameToReference[name] = ref
        return name

    def getReferenceForName(self, name):
        return self.nameToReference[name]

    def getReferenceForURL(self, url):
        # TODO: who should this be used by?
        sturdy = SturdyRef(url)
        assert sturdy.tubID == self.tubID
        return self.getReferenceForName(sturdy.name)

    def getOrCreateURLForReference(self, ref):
        """Return the global URL for the reference, if there is one, or None
        if there is not."""
        name = self._assignName(ref)
        if name:
            return self.buildURL(name)
        return None

    def revokeReference(self, ref):
        # TODO
        pass

    def unregisterURL(self, url):
        sturdy = SturdyRef(url)
        name = sturdy.name
        ref = self.nameToReference[name]
        del self.nameToReference[name]
        del self.referenceToName[ref]
        self.revokeReference(ref)

    def unregisterReference(self, ref):
        name = self.referenceToName[ref]
        url = self.buildURL(name)
        sturdy = SturdyRef(url)
        name = sturdy.name
        del self.nameToReference[name]
        del self.referenceToName[ref]
        if ref in self.strongReferences:
            self.strongReferences.remove(ref)
        self.revokeReference(ref)

    def getReference(self, sturdyOrURL):
        """Acquire a RemoteReference for the given SturdyRef/URL.

        @return: a Deferred that fires with the RemoteReference
        """
        if isinstance(sturdyOrURL, SturdyRef):
            sturdy = sturdyOrURL
        else:
            sturdy = SturdyRef(sturdyOrURL)
        # pb->pb: ok, requires crypto
        # pbu->pb: ok, requires crypto
        # pbu->pbu: ok
        # pb->pbu: ok, requires crypto
        if sturdy.encrypted and not crypto_available:
            e = BananaError("crypto for PB is not available, "
                            "we cannot handle encrypted PB-URLs like %s"
                            % sturdy.getURL())
            return defer.fail(e)
        name = sturdy.name
        d = self.getBrokerForTubRef(sturdy.getTubRef())
        d.addCallback(lambda b: b.getYourReferenceByName(name))
        return d

    def connectTo(self, sturdyOrURL, cb, *args, **kwargs):
        """Establish (and maintain) a connection to a given PBURL.

        I establish a connection to the PBURL and run a callback to inform
        the caller about the newly-available RemoteReference. If the
        connection is lost, I schedule a reconnection attempt for the near
        future. If that one fails, I keep trying at longer and longer
        intervals (exponential backoff).

        I accept a callback which will be fired each time a connection
        attempt succeeds. This callback is run with the new RemoteReference
        and any additional args/kwargs provided to me. The callback should
        then use rref.notifyOnDisconnect() to get a message when the
        connection goes away. At some point after it goes away, the
        Reconnector will reconnect.

        I return a Reconnector object. When you no longer want to maintain
        this connection, call the stopConnecting() method on the Reconnector.
        I promise to not invoke your callback after you've called
        stopConnecting(), even if there was already a connection attempt in
        progress. If you had an active connection before calling
        stopConnecting(), you will still have access to it, until it breaks
        on its own. (I will not attempt to break existing connections, I will
        merely stop trying to create new ones). All my Reconnector objects
        will be shut down when the Tub is stopped.

        Usage::

         def _got_ref(rref, arg1, arg2):
             rref.callRemote('hello again')
             # etc
         rc = tub.connectTo(_got_ref, 'arg1', 'arg2')
         ...
         rc.stopConnecting() # later
        """

        rc = Reconnector(self, sturdyOrURL, cb, *args, **kwargs)
        self.reconnectors.append(rc)
        return rc

    # _removeReconnector is called by the Reconnector
    def _removeReconnector(self, rc):
        self.reconnectors.remove(rc)

    def getBrokerForTubRef(self, tubref):
        if tubref in self.brokers:
            return defer.succeed(self.brokers[tubref])
        if tubref.getTubID() == self.tubID:
            b = self._createLoopbackBroker(tubref)
            # _createLoopbackBroker will call brokerAttached, which will add
            # it to self.brokers
            return defer.succeed(b)

        d = defer.Deferred()
        if tubref not in self.waitingForBrokers:
            self.waitingForBrokers[tubref] = []
        self.waitingForBrokers[tubref].append(d)

        if tubref not in self.tubConnectors:
            # the TubConnector will call our brokerAttached when it finishes
            # negotiation, which will fire waitingForBrokers[tubref].
            c = negotiate.TubConnector(self, tubref)
            self.tubConnectors[tubref] = c
            c.connect()

        return d

    def _createLoopbackBroker(self, tubref):
        t1,t2 = broker.LoopbackTransport(), broker.LoopbackTransport()
        t1.setPeer(t2); t2.setPeer(t1)
        n = negotiate.Negotiation()
        params = n.loopbackDecision()
        b1,b2 = self.brokerClass(params), self.brokerClass(params)
        # we treat b1 as "our" broker, and b2 as "theirs", and we pretend
        # that b2 has just connected to us. We keep track of b1, and b2 keeps
        # track of us.
        b1.setTub(self)
        b2.setTub(self)
        t1.protocol = b1; t2.protocol = b2
        b1.makeConnection(t1); b2.makeConnection(t2)
        self.brokerAttached(tubref, b1, False)
        return b1

    def connectionFailed(self, tubref, why):
        # we previously initiated an outbound TubConnector to this tubref, but
        # it was unable to establish a connection. 'why' is the most useful
        # Failure that occurred (i.e. it is a NegotiationError if we made it
        # that far, otherwise it's a ConnectionFailed).

        if tubref in self.tubConnectors:
            del self.tubConnectors[tubref]
        if tubref in self.brokers:
            # oh, but fortunately an inbound connection must have succeeded.
            # Nevermind.
            return

        # inform hopeful Broker-waiters that they aren't getting one
        if tubref in self.waitingForBrokers:
            waiting = self.waitingForBrokers[tubref]
            del self.waitingForBrokers[tubref]
            for d in waiting:
                d.errback(why)

    def brokerAttached(self, tubref, broker, isClient):
        if not tubref:
            # this is an inbound connection from an unauthenticated Tub
            assert not isClient
            # we just track it so we can disconnect it later
            self.unauthenticatedBrokers.append(broker)
            return

        if tubref in self.tubConnectors:
            # we initiated an outbound connection to this tubref
            if not isClient:
                # however, the connection we got was from an inbound
                # connection. The completed (inbound) connection wins, so
                # abandon the outbound TubConnector
                self.tubConnectors[tubref].shutdown()

            # we don't need the TubConnector any more
            del self.tubConnectors[tubref]

        if tubref in self.brokers:
            # oops, this shouldn't happen but it isn't fatal. Raise
            # BananaError so the Negotiation will drop the connection
            raise BananaError("unexpected duplicate connection")
        self.brokers[tubref] = broker

        # now inform everyone who's been waiting on it
        if tubref in self.waitingForBrokers:
            waiting = self.waitingForBrokers[tubref]
            del self.waitingForBrokers[tubref]
            for d in waiting:
                d.callback(broker)

    def brokerDetached(self, broker, why):
        # the Broker will have already severed all active references
        for tubref in self.brokers.keys():
            if self.brokers[tubref] is broker:
                del self.brokers[tubref]
        if broker in self.unauthenticatedBrokers:
            self.unauthenticatedBrokers.remove(broker)
        # if the Tub has already shut down, we may need to notify observers
        # who are waiting for all of our connections to finish shutting down
        if (not self.running
            and not self.brokers
            and not self.unauthenticatedBrokers):
            self._allBrokersAreDisconnected.fire(self)

class UnauthenticatedTub(Tub):
    encrypted = False

    """
    @type tubID: string
    @ivar tubID: a global identifier for this Tub, possibly including
                 authentication information, hash of SSL certificate
    """

    def __init__(self, tubID=None, options={}):
        service.MultiService.__init__(self)
        self.setup(options)
        self.myCertificate = None
        assert not tubID # not yet
        self.tubID = tubID


def getRemoteURL_TCP(host, port, pathname, *interfaces):
    url = "pb://%s:%d/%s" % (host, port, pathname)
    if crypto_available:
        s = Tub()
    else:
        s = UnauthenticatedTub()
    d = s.getReference(url, interfaces)
    return d
