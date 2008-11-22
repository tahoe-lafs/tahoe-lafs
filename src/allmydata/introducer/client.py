
import re, time, sha
from base64 import b32decode
from zope.interface import implements
from twisted.application import service
from foolscap import Referenceable
from allmydata.interfaces import InsufficientVersionError
from allmydata.introducer.interfaces import RIIntroducerSubscriberClient, \
     IIntroducerClient
from allmydata.util import log, idlib
from allmydata.util.rrefutil import get_versioned_remote_reference
from allmydata.introducer.common import make_index


class RemoteServiceConnector:
    """I hold information about a peer service that we want to connect to. If
    we are connected, I hold the RemoteReference, the peer's address, and the
    peer's version information. I remember information about when we were
    last connected to the peer too, even if we aren't currently connected.

    @ivar announcement_time: when we first heard about this service
    @ivar last_connect_time: when we last established a connection
    @ivar last_loss_time: when we last lost a connection

    @ivar version: the peer's version, from the most recent announcement
    @ivar oldest_supported: the peer's oldest supported version, same
    @ivar nickname: the peer's self-reported nickname, same

    @ivar rref: the RemoteReference, if connected, otherwise None
    @ivar remote_host: the IAddress, if connected, otherwise None
    """

    VERSION_DEFAULTS = {
        "storage": { "http://allmydata.org/tahoe/protocols/storage/v1" :
                     { "maximum-immutable-share-size": 2**32 },
                     "application-version": "unknown: no get_version()",
                     },
        "stub_client": { },
        }

    def __init__(self, announcement, tub, ic):
        self._tub = tub
        self._announcement = announcement
        self._ic = ic
        (furl, service_name, ri_name, nickname, ver, oldest) = announcement

        self._furl = furl
        m = re.match(r'pb://(\w+)@', furl)
        assert m
        self._nodeid = b32decode(m.group(1).upper())
        self._nodeid_s = idlib.shortnodeid_b2a(self._nodeid)

        self.service_name = service_name

        self.log("attempting to connect to %s" % self._nodeid_s)
        self.announcement_time = time.time()
        self.last_loss_time = None
        self.rref = None
        self.remote_host = None
        self.last_connect_time = None
        self.version = ver
        self.oldest_supported = oldest
        self.nickname = nickname

    def log(self, *args, **kwargs):
        return self._ic.log(*args, **kwargs)

    def startConnecting(self):
        self._reconnector = self._tub.connectTo(self._furl, self._got_service)

    def stopConnecting(self):
        self._reconnector.stopConnecting()

    def _got_service(self, rref):
        self.log("got connection to %s, getting versions" % self._nodeid_s)

        default = self.VERSION_DEFAULTS.get(self.service_name, {})
        d = get_versioned_remote_reference(rref, default)
        d.addCallback(self._got_versioned_service)

    def _got_versioned_service(self, rref):
        self.log("connected to %s, version %s" % (self._nodeid_s, rref.version))

        self.last_connect_time = time.time()
        self.remote_host = rref.rref.tracker.broker.transport.getPeer()

        self.rref = rref

        self._ic.add_connection(self._nodeid, self.service_name, rref)

        rref.notifyOnDisconnect(self._lost, rref)

    def _lost(self, rref):
        self.log("lost connection to %s" % self._nodeid_s)
        self.last_loss_time = time.time()
        self.rref = None
        self.remote_host = None
        self._ic.remove_connection(self._nodeid, self.service_name, rref)


    def reset(self):
        self._reconnector.reset()


class IntroducerClient(service.Service, Referenceable):
    implements(RIIntroducerSubscriberClient, IIntroducerClient)

    def __init__(self, tub, introducer_furl,
                 nickname, my_version, oldest_supported):
        self._tub = tub
        self.introducer_furl = introducer_furl

        self._nickname = nickname.encode("utf-8")
        self._my_version = my_version
        self._oldest_supported = oldest_supported

        self._published_announcements = set()

        self._publisher = None
        self._connected = False

        self._subscribed_service_names = set()
        self._subscriptions = set() # requests we've actually sent
        self._received_announcements = set()
        # TODO: this set will grow without bound, until the node is restarted

        # we only accept one announcement per (peerid+service_name) pair.
        # This insures that an upgraded host replace their previous
        # announcement. It also means that each peer must have their own Tub
        # (no sharing), which is slightly weird but consistent with the rest
        # of the Tahoe codebase.
        self._connectors = {} # k: (peerid+svcname), v: RemoteServiceConnector
        # self._connections is a set of (peerid, service_name, rref) tuples
        self._connections = set()

        self.counter = 0 # incremented each time we change state, for tests
        self.encoding_parameters = None

    def startService(self):
        service.Service.startService(self)
        self._introducer_error = None
        rc = self._tub.connectTo(self.introducer_furl, self._got_introducer)
        self._introducer_reconnector = rc
        def connect_failed(failure):
            self.log("Initial Introducer connection failed: perhaps it's down",
                     level=log.WEIRD, failure=failure, umid="c5MqUQ")
        d = self._tub.getReference(self.introducer_furl)
        d.addErrback(connect_failed)

    def _got_introducer(self, publisher):
        self.log("connected to introducer, getting versions")
        default = { "http://allmydata.org/tahoe/protocols/introducer/v1":
                    { },
                    "application-version": "unknown: no get_version()",
                    }
        d = get_versioned_remote_reference(publisher, default)
        d.addCallback(self._got_versioned_introducer)
        d.addErrback(self._got_error)

    def _got_error(self, f):
        # TODO: for the introducer, perhaps this should halt the application
        self._introducer_error = f # polled by tests

    def _got_versioned_introducer(self, publisher):
        self.log("got introducer version: %s" % (publisher.version,))
        # we require a V1 introducer
        needed = "http://allmydata.org/tahoe/protocols/introducer/v1"
        if needed not in publisher.version:
            raise InsufficientVersionError(needed, publisher.version)
        self._connected = True
        self._publisher = publisher
        publisher.notifyOnDisconnect(self._disconnected)
        self._maybe_publish()
        self._maybe_subscribe()

    def _disconnected(self):
        self.log("bummer, we've lost our connection to the introducer")
        self._connected = False
        self._publisher = None
        self._subscriptions.clear()

    def stopService(self):
        service.Service.stopService(self)
        self._introducer_reconnector.stopConnecting()
        for rsc in self._connectors.itervalues():
            rsc.stopConnecting()

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.introducer"
        return log.msg(*args, **kwargs)


    def publish(self, furl, service_name, remoteinterface_name):
        ann = (furl, service_name, remoteinterface_name,
               self._nickname, self._my_version, self._oldest_supported)
        self._published_announcements.add(ann)
        self._maybe_publish()

    def subscribe_to(self, service_name):
        self._subscribed_service_names.add(service_name)
        self._maybe_subscribe()

    def _maybe_subscribe(self):
        if not self._publisher:
            self.log("want to subscribe, but no introducer yet",
                     level=log.NOISY)
            return
        for service_name in self._subscribed_service_names:
            if service_name not in self._subscriptions:
                # there is a race here, but the subscription desk ignores
                # duplicate requests.
                self._subscriptions.add(service_name)
                d = self._publisher.callRemote("subscribe", self, service_name)
                d.addErrback(log.err, facility="tahoe.introducer",
                             level=log.WEIRD, umid="2uMScQ")

    def _maybe_publish(self):
        if not self._publisher:
            self.log("want to publish, but no introducer yet", level=log.NOISY)
            return
        # this re-publishes everything. The Introducer ignores duplicates
        for ann in self._published_announcements:
            d = self._publisher.callRemote("publish", ann)
            d.addErrback(log.err, facility="tahoe.introducer",
                         level=log.WEIRD, umid="xs9pVQ")



    def remote_announce(self, announcements):
        for ann in announcements:
            self.log("received %d announcements" % len(announcements))
            (furl, service_name, ri_name, nickname, ver, oldest) = ann
            if service_name not in self._subscribed_service_names:
                self.log("announcement for a service we don't care about [%s]"
                         % (service_name,), level=log.UNUSUAL, umid="dIpGNA")
                continue
            if ann in self._received_announcements:
                self.log("ignoring old announcement: %s" % (ann,),
                         level=log.NOISY)
                continue
            self.log("new announcement[%s]: %s" % (service_name, ann))
            self._received_announcements.add(ann)
            self._new_announcement(ann)

    def _new_announcement(self, announcement):
        # this will only be called for new announcements
        index = make_index(announcement)
        if index in self._connectors:
            self.log("replacing earlier announcement", level=log.NOISY)
            self._connectors[index].stopConnecting()
        rsc = RemoteServiceConnector(announcement, self._tub, self)
        self._connectors[index] = rsc
        rsc.startConnecting()

    def add_connection(self, nodeid, service_name, rref):
        self._connections.add( (nodeid, service_name, rref) )
        self.counter += 1
        # when one connection is established, reset the timers on all others,
        # to trigger a reconnection attempt in one second. This is intended
        # to accelerate server connections when we've been offline for a
        # while. The goal is to avoid hanging out for a long time with
        # connections to only a subset of the servers, which would increase
        # the chances that we'll put shares in weird places (and not update
        # existing shares of mutable files). See #374 for more details.
        for rsc in self._connectors.values():
            rsc.reset()

    def remove_connection(self, nodeid, service_name, rref):
        self._connections.discard( (nodeid, service_name, rref) )
        self.counter += 1


    def get_all_connections(self):
        return frozenset(self._connections)

    def get_all_connectors(self):
        return self._connectors.copy()

    def get_all_peerids(self):
        return frozenset([peerid
                          for (peerid, service_name, rref)
                          in self._connections])

    def get_nickname_for_peerid(self, peerid):
        for k in self._connectors:
            (peerid0, svcname0) = k
            if peerid0 == peerid:
                rsc = self._connectors[k]
                return rsc.nickname
        return None

    def get_all_connections_for(self, service_name):
        return frozenset([c
                          for c in self._connections
                          if c[1] == service_name])

    def get_permuted_peers(self, service_name, key):
        """Return an ordered list of (peerid, versioned-rref) tuples."""

        results = []
        for (c_peerid, c_service_name, rref) in self._connections:
            assert isinstance(c_peerid, str)
            if c_service_name != service_name:
                continue
            permuted = sha.new(key + c_peerid).digest()
            results.append((permuted, c_peerid, rref))

        results.sort(lambda a,b: cmp(a[0], b[0]))
        return [ (r[1], r[2]) for r in results ]



    def remote_set_encoding_parameters(self, parameters):
        self.encoding_parameters = parameters

    def connected_to_introducer(self):
        return self._connected

    def debug_disconnect_from_peerid(self, victim_nodeid):
        # for unit tests: locate and sever all connections to the given
        # peerid.
        for (nodeid, service_name, rref) in self._connections:
            if nodeid == victim_nodeid:
                rref.tracker.broker.transport.loseConnection()
