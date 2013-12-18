# -*- coding: utf-8-with-signature-unix; fill-column: 77 -*-
# -*- indent-tabs-mode: nil -*-

import time
from base64 import b32decode
from zope.interface import implements, Interface
from twisted.application import service
import allmydata
from allmydata.interfaces import InsufficientVersionError
from allmydata.util import log, idlib, rrefutil
from foolscap.api import StringConstraint, TupleOf, SetOf, DictOf, Any, \
    RemoteInterface, Referenceable, eventually, SturdyRef
from allmydata.introducer.common import SubscriberDescriptor, \
     AnnouncementDescriptor
FURL = StringConstraint(1000)

# We keep a copy of the old introducer (both client and server) here to
# support compatibility tests. The old client is supposed to handle the new
# server, and new client is supposed to handle the old server.


# Announcements are (FURL, service_name, remoteinterface_name,
#                    nickname, my_version, oldest_supported)
#  the (FURL, service_name, remoteinterface_name) refer to the service being
#  announced. The (nickname, my_version, oldest_supported) refer to the
#  client as a whole. The my_version/oldest_supported strings can be parsed
#  by an allmydata.util.version.Version instance, and then compared. The
#  first goal is to make sure that nodes are not confused by speaking to an
#  incompatible peer. The second goal is to enable the development of
#  backwards-compatibility code.

Announcement = TupleOf(FURL, str, str,
                       str, str, str)

class RIIntroducerSubscriberClient_v1(RemoteInterface):
    __remote_name__ = "RIIntroducerSubscriberClient.tahoe.allmydata.com"

    def announce(announcements=SetOf(Announcement)):
        """I accept announcements from the publisher."""
        return None

    def set_encoding_parameters(parameters=(int, int, int)):
        """Advise the client of the recommended k-of-n encoding parameters
        for this grid. 'parameters' is a tuple of (k, desired, n), where 'n'
        is the total number of shares that will be created for any given
        file, while 'k' is the number of shares that must be retrieved to
        recover that file, and 'desired' is the minimum number of shares that
        must be placed before the uploader will consider its job a success.
        n/k is the expansion ratio, while k determines the robustness.

        Introducers should specify 'n' according to the expected size of the
        grid (there is no point to producing more shares than there are
        peers), and k according to the desired reliability-vs-overhead goals.

        Note that setting k=1 is equivalent to simple replication.
        """
        return None

# When Foolscap can handle multiple interfaces (Foolscap#17), the
# full-powered introducer will implement both RIIntroducerPublisher and
# RIIntroducerSubscriberService. Until then, we define
# RIIntroducerPublisherAndSubscriberService as a combination of the two, and
# make everybody use that.

class RIIntroducerPublisher_v1(RemoteInterface):
    """To publish a service to the world, connect to me and give me your
    announcement message. I will deliver a copy to all connected subscribers."""
    __remote_name__ = "RIIntroducerPublisher.tahoe.allmydata.com"

    def publish(announcement=Announcement):
        # canary?
        return None

class RIIntroducerSubscriberService_v1(RemoteInterface):
    __remote_name__ = "RIIntroducerSubscriberService.tahoe.allmydata.com"

    def subscribe(subscriber=RIIntroducerSubscriberClient_v1, service_name=str):
        """Give me a subscriber reference, and I will call its new_peers()
        method will any announcements that match the desired service name. I
        will ignore duplicate subscriptions.
        """
        return None

class RIIntroducerPublisherAndSubscriberService_v1(RemoteInterface):
    __remote_name__ = "RIIntroducerPublisherAndSubscriberService.tahoe.allmydata.com"
    def get_version():
        return DictOf(str, Any())
    def publish(announcement=Announcement):
        return None
    def subscribe(subscriber=RIIntroducerSubscriberClient_v1, service_name=str):
        return None

class IIntroducerClient(Interface):
    """I provide service introduction facilities for a node. I help nodes
    publish their services to the rest of the world, and I help them learn
    about services available on other nodes."""

    def publish(furl, service_name, remoteinterface_name):
        """Once you call this, I will tell the world that the Referenceable
        available at FURL is available to provide a service named
        SERVICE_NAME. The precise definition of the service being provided is
        identified by the Foolscap 'remote interface name' in the last
        parameter: this is supposed to be a globally-unique string that
        identifies the RemoteInterface that is implemented."""

    def subscribe_to(service_name, callback, *args, **kwargs):
        """Call this if you will eventually want to use services with the
        given SERVICE_NAME. This will prompt me to subscribe to announcements
        of those services. Your callback will be invoked with at least two
        arguments: a serverid (binary string), and an announcement
        dictionary, followed by any additional callback args/kwargs you give
        me. I will run your callback for both new announcements and for
        announcements that have changed, but you must be prepared to tolerate
        duplicates.

        The announcement dictionary that I give you will have the following
        keys:

         version: 0
         service-name: str('storage')

         FURL: str(furl)
         remoteinterface-name: str(ri_name)
         nickname: unicode
         app-versions: {}
         my-version: str
         oldest-supported: str

        Note that app-version will be an empty dictionary until #466 is done
        and both the introducer and the remote client have been upgraded. For
        current (native) server types, the serverid will always be equal to
        the binary form of the FURL's tubid.
        """

    def connected_to_introducer():
        """Returns a boolean, True if we are currently connected to the
        introducer, False if not."""


class IntroducerClient_v1(service.Service, Referenceable):
    implements(RIIntroducerSubscriberClient_v1, IIntroducerClient)

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
        self._debug_outstanding = 0

    def _debug_retired(self, res):
        self._debug_outstanding -= 1
        return res

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
        d = rrefutil.add_version_to_remote_reference(publisher, default)
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
                self._debug_outstanding += 1
                d = self._publisher.callRemote("subscribe", self, service_name)
                d.addBoth(self._debug_retired)
                d.addErrback(rrefutil.trap_deadref)
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
            self._debug_outstanding += 1
            d = self._publisher.callRemote("publish", ann)
            d.addBoth(self._debug_retired)
            d.addErrback(rrefutil.trap_deadref)
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

class IntroducerService_v1(service.MultiService, Referenceable):
    implements(RIIntroducerPublisherAndSubscriberService_v1)
    name = "introducer"
    VERSION = { "http://allmydata.org/tahoe/protocols/introducer/v1":
                 { },
                "application-version": str(allmydata.__full_version__),
                }

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        self.introducer_url = None
        # 'index' is (service_name, tubid)
        self._announcements = {} # dict of index -> (announcement, timestamp)
        self._subscribers = {} # [service_name]->[rref]->timestamp
        self._debug_counts = {"inbound_message": 0,
                              "inbound_duplicate": 0,
                              "inbound_update": 0,
                              "outbound_message": 0,
                              "outbound_announcements": 0,
                              "inbound_subscribe": 0}
        self._debug_outstanding = 0

    def _debug_retired(self, res):
        self._debug_outstanding -= 1
        return res

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.introducer"
        return log.msg(*args, **kwargs)

    def get_announcements(self, include_stub_clients=True):
        announcements = []
        for index, (ann_t, when) in self._announcements.items():
            (furl, service_name, ri_name, nickname, ver, oldest) = ann_t
            if service_name == "stub_client" and not include_stub_clients:
                continue
            ann_d = {"nickname": nickname.decode("utf-8", "replace"),
                     "my-version": ver,
                     "service-name": service_name,
                     "anonymous-storage-FURL": furl,
                     }
            # the V2 introducer uses (service_name, key_s, tubid_s) as an
            # index, so match that format for AnnouncementDescriptor
            new_index = (index[0], None, idlib.nodeid_b2a(index[1]))
            ad = AnnouncementDescriptor(when, new_index, None, ann_d)
            announcements.append(ad)
        return announcements

    def get_subscribers(self):
        s = []
        for service_name, subscribers in self._subscribers.items():
            for rref, when in subscribers.items():
                tubid = rref.getRemoteTubID() or "?"
                advertised_addresses = rrefutil.hosts_for_rref(rref)
                remote_address = rrefutil.stringify_remote_address(rref)
                nickname, version, app_versions = u"?", u"?", {}
                sd = SubscriberDescriptor(service_name, when,
                                          nickname, version, app_versions,
                                          advertised_addresses, remote_address,
                                          tubid)
                s.append(sd)
        return s

    def remote_get_version(self):
        return self.VERSION

    def remote_publish(self, announcement):
        try:
            self._publish(announcement)
        except:
            log.err(format="Introducer.remote_publish failed on %(ann)s",
                    ann=announcement, level=log.UNUSUAL, umid="620rWA")
            raise

    def _publish(self, announcement):
        self._debug_counts["inbound_message"] += 1
        self.log("introducer: announcement published: %s" % (announcement,) )
        (furl, service_name, ri_name, nickname_utf8, ver, oldest) = announcement
        #print "PUB", service_name, nickname_utf8

        nodeid = b32decode(SturdyRef(furl).tubID.upper())
        index = (service_name, nodeid)

        if index in self._announcements:
            (old_announcement, timestamp) = self._announcements[index]
            if old_announcement == announcement:
                self.log("but we already knew it, ignoring", level=log.NOISY)
                self._debug_counts["inbound_duplicate"] += 1
                return
            else:
                self.log("old announcement being updated", level=log.NOISY)
                self._debug_counts["inbound_update"] += 1
        self._announcements[index] = (announcement, time.time())

        for s in self._subscribers.get(service_name, []):
            self._debug_counts["outbound_message"] += 1
            self._debug_counts["outbound_announcements"] += 1
            self._debug_outstanding += 1
            d = s.callRemote("announce", set([announcement]))
            d.addBoth(self._debug_retired)
            d.addErrback(rrefutil.trap_deadref)
            d.addErrback(log.err,
                         format="subscriber errored on announcement %(ann)s",
                         ann=announcement, facility="tahoe.introducer",
                         level=log.UNUSUAL, umid="jfGMXQ")

    def remote_subscribe(self, subscriber, service_name):
        self.log("introducer: subscription[%s] request at %s" % (service_name,
                                                                 subscriber))
        self._debug_counts["inbound_subscribe"] += 1
        if service_name not in self._subscribers:
            self._subscribers[service_name] = {}
        subscribers = self._subscribers[service_name]
        if subscriber in subscribers:
            self.log("but they're already subscribed, ignoring",
                     level=log.UNUSUAL)
            return
        subscribers[subscriber] = time.time()
        def _remove():
            self.log("introducer: unsubscribing[%s] %s" % (service_name,
                                                           subscriber))
            subscribers.pop(subscriber, None)
        subscriber.notifyOnDisconnect(_remove)

        announcements = set(
            [ ann
              for (sn2,nodeid),(ann,when) in self._announcements.items()
              if sn2 == service_name] )

        self._debug_counts["outbound_message"] += 1
        self._debug_counts["outbound_announcements"] += len(announcements)
        self._debug_outstanding += 1
        d = subscriber.callRemote("announce", announcements)
        d.addBoth(self._debug_retired)
        d.addErrback(rrefutil.trap_deadref)
        d.addErrback(log.err,
                     format="subscriber errored during subscribe %(anns)s",
                     anns=announcements, facility="tahoe.introducer",
                     level=log.UNUSUAL, umid="1XChxA")
