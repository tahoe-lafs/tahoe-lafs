
from base64 import b32decode
from zope.interface import implements
from twisted.application import service
from foolscap.api import Referenceable, SturdyRef, eventually
from allmydata.interfaces import InsufficientVersionError
from allmydata.introducer.interfaces import RIIntroducerSubscriberClient, \
     IIntroducerClient
from allmydata.util import log, idlib
from allmydata.util.rrefutil import add_version_to_remote_reference, trap_deadref


class IntroducerClient(service.Service, Referenceable):
    implements(RIIntroducerSubscriberClient, IIntroducerClient)

    def __init__(self, tub, introducer_furl,
                 nickname, my_version, oldest_supported):
        self._tub = tub
        self.introducer_furl = introducer_furl

        assert type(nickname) is unicode
        self._nickname_utf8 = nickname.encode("utf-8") # we always send UTF-8
        self._my_version = my_version
        self._oldest_supported = oldest_supported

        self._published_announcements = set()

        self._publisher = None

        self._local_subscribers = [] # (servicename,cb,args,kwargs) tuples
        self._subscribed_service_names = set()
        self._subscriptions = set() # requests we've actually sent

        # _current_announcements remembers one announcement per
        # (servicename,serverid) pair. Anything that arrives with the same
        # pair will displace the previous one. This stores unpacked
        # announcement dictionaries, which can be compared for equality to
        # distinguish re-announcement from updates. It also provides memory
        # for clients who subscribe after startup.
        self._current_announcements = {}

        self.encoding_parameters = None

        # hooks for unit tests
        self._debug_counts = {
            "inbound_message": 0,
            "inbound_announcement": 0,
            "wrong_service": 0,
            "duplicate_announcement": 0,
            "update": 0,
            "new_announcement": 0,
            "outbound_message": 0,
            }

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
        d = add_version_to_remote_reference(publisher, default)
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
        self._publisher = publisher
        publisher.notifyOnDisconnect(self._disconnected)
        self._maybe_publish()
        self._maybe_subscribe()

    def _disconnected(self):
        self.log("bummer, we've lost our connection to the introducer")
        self._publisher = None
        self._subscriptions.clear()

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.introducer"
        return log.msg(*args, **kwargs)


    def publish(self, furl, service_name, remoteinterface_name):
        assert type(self._nickname_utf8) is str # we always send UTF-8
        ann = (furl, service_name, remoteinterface_name,
               self._nickname_utf8, self._my_version, self._oldest_supported)
        self._published_announcements.add(ann)
        self._maybe_publish()

    def subscribe_to(self, service_name, cb, *args, **kwargs):
        self._local_subscribers.append( (service_name,cb,args,kwargs) )
        self._subscribed_service_names.add(service_name)
        self._maybe_subscribe()
        for (servicename,nodeid),ann_d in self._current_announcements.items():
            if servicename == service_name:
                eventually(cb, nodeid, ann_d)

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
                d.addErrback(trap_deadref)
                d.addErrback(log.err, format="server errored during subscribe",
                             facility="tahoe.introducer",
                             level=log.WEIRD, umid="2uMScQ")

    def _maybe_publish(self):
        if not self._publisher:
            self.log("want to publish, but no introducer yet", level=log.NOISY)
            return
        # this re-publishes everything. The Introducer ignores duplicates
        for ann in self._published_announcements:
            self._debug_counts["outbound_message"] += 1
            d = self._publisher.callRemote("publish", ann)
            d.addErrback(trap_deadref)
            d.addErrback(log.err,
                         format="server errored during publish %(ann)s",
                         ann=ann, facility="tahoe.introducer",
                         level=log.WEIRD, umid="xs9pVQ")



    def remote_announce(self, announcements):
        self.log("received %d announcements" % len(announcements))
        self._debug_counts["inbound_message"] += 1
        for ann in announcements:
            try:
                self._process_announcement(ann)
            except:
                log.err(format="unable to process announcement %(ann)s",
                        ann=ann)
                # Don't let a corrupt announcement prevent us from processing
                # the remaining ones. Don't return an error to the server,
                # since they'd just ignore it anyways.
                pass

    def _process_announcement(self, ann):
        self._debug_counts["inbound_announcement"] += 1
        (furl, service_name, ri_name, nickname_utf8, ver, oldest) = ann
        if service_name not in self._subscribed_service_names:
            self.log("announcement for a service we don't care about [%s]"
                     % (service_name,), level=log.UNUSUAL, umid="dIpGNA")
            self._debug_counts["wrong_service"] += 1
            return
        self.log("announcement for [%s]: %s" % (service_name, ann),
                 umid="BoKEag")
        assert type(furl) is str
        assert type(service_name) is str
        assert type(ri_name) is str
        assert type(nickname_utf8) is str
        nickname = nickname_utf8.decode("utf-8")
        assert type(nickname) is unicode
        assert type(ver) is str
        assert type(oldest) is str

        nodeid = b32decode(SturdyRef(furl).tubID.upper())
        nodeid_s = idlib.shortnodeid_b2a(nodeid)

        ann_d = { "version": 0,
                  "service-name": service_name,

                  "FURL": furl,
                  "nickname": nickname,
                  "app-versions": {}, # need #466 and v2 introducer
                  "my-version": ver,
                  "oldest-supported": oldest,
                  }

        index = (service_name, nodeid)
        if self._current_announcements.get(index, None) == ann_d:
            self.log("reannouncement for [%(service)s]:%(nodeid)s, ignoring",
                     service=service_name, nodeid=nodeid_s,
                     level=log.UNUSUAL, umid="B1MIdA")
            self._debug_counts["duplicate_announcement"] += 1
            return
        if index in self._current_announcements:
            self._debug_counts["update"] += 1
        else:
            self._debug_counts["new_announcement"] += 1

        self._current_announcements[index] = ann_d
        # note: we never forget an index, but we might update its value

        for (service_name2,cb,args,kwargs) in self._local_subscribers:
            if service_name2 == service_name:
                eventually(cb, nodeid, ann_d, *args, **kwargs)

    def remote_set_encoding_parameters(self, parameters):
        self.encoding_parameters = parameters

    def connected_to_introducer(self):
        return bool(self._publisher)
