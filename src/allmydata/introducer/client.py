
import time
from zope.interface import implements
from twisted.application import service
from foolscap.api import Referenceable, eventually, RemoteInterface
from allmydata.interfaces import InsufficientVersionError
from allmydata.introducer.interfaces import IIntroducerClient, \
     RIIntroducerSubscriberClient_v1, RIIntroducerSubscriberClient_v2
from allmydata.introducer.common import sign_to_foolscap, unsign_from_foolscap,\
     convert_announcement_v1_to_v2, convert_announcement_v2_to_v1, \
     make_index, get_tubid_string_from_ann, get_tubid_string
from allmydata.util import log
from allmydata.util.rrefutil import add_version_to_remote_reference
from allmydata.util.keyutil import BadSignatureError

class WrapV2ClientInV1Interface(Referenceable): # for_v1
    """I wrap a v2 IntroducerClient to make it look like a v1 client, so it
    can be attached to an old server."""
    implements(RIIntroducerSubscriberClient_v1)

    def __init__(self, original):
        self.original = original

    def remote_announce(self, announcements):
        lp = self.original.log("received %d announcements (v1)" %
                               len(announcements))
        anns_v1 = set([convert_announcement_v1_to_v2(ann_v1)
                       for ann_v1 in announcements])
        return self.original.got_announcements(anns_v1, lp)

    def remote_set_encoding_parameters(self, parameters):
        self.original.remote_set_encoding_parameters(parameters)

class RIStubClient(RemoteInterface): # for_v1
    """Each client publishes a service announcement for a dummy object called
    the StubClient. This object doesn't actually offer any services, but the
    announcement helps the Introducer keep track of which clients are
    subscribed (so the grid admin can keep track of things like the size of
    the grid and the client versions in use. This is the (empty)
    RemoteInterface for the StubClient."""

class StubClient(Referenceable): # for_v1
    implements(RIStubClient)

V1 = "http://allmydata.org/tahoe/protocols/introducer/v1"
V2 = "http://allmydata.org/tahoe/protocols/introducer/v2"

class IntroducerClient(service.Service, Referenceable):
    implements(RIIntroducerSubscriberClient_v2, IIntroducerClient)

    def __init__(self, tub, introducer_furl,
                 nickname, my_version, oldest_supported,
                 app_versions):
        self._tub = tub
        self.introducer_furl = introducer_furl

        assert type(nickname) is unicode
        self._nickname = nickname
        self._my_version = my_version
        self._oldest_supported = oldest_supported
        self._app_versions = app_versions

        self._my_subscriber_info = { "version": 0,
                                     "nickname": self._nickname,
                                     "app-versions": self._app_versions,
                                     "my-version": self._my_version,
                                     "oldest-supported": self._oldest_supported,
                                     }
        self._stub_client = None # for_v1
        self._stub_client_furl = None

        self._published_announcements = {}
        self._canary = Referenceable()

        self._publisher = None

        self._local_subscribers = [] # (servicename,cb,args,kwargs) tuples
        self._subscribed_service_names = set()
        self._subscriptions = set() # requests we've actually sent

        # _current_announcements remembers one announcement per
        # (servicename,serverid) pair. Anything that arrives with the same
        # pair will displace the previous one. This stores tuples of
        # (unpacked announcement dictionary, verifyingkey, rxtime). The ann
        # dicts can be compared for equality to distinguish re-announcement
        # from updates. It also provides memory for clients who subscribe
        # after startup.
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
        d = add_version_to_remote_reference(publisher, default)
        d.addCallback(self._got_versioned_introducer)
        d.addErrback(self._got_error)

    def _got_error(self, f):
        # TODO: for the introducer, perhaps this should halt the application
        self._introducer_error = f # polled by tests

    def _got_versioned_introducer(self, publisher):
        self.log("got introducer version: %s" % (publisher.version,))
        # we require an introducer that speaks at least one of (V1, V2)
        if not (V1 in publisher.version or V2 in publisher.version):
            raise InsufficientVersionError("V1 or V2", publisher.version)
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
            kwargs["facility"] = "tahoe.introducer.client"
        return log.msg(*args, **kwargs)

    def subscribe_to(self, service_name, cb, *args, **kwargs):
        self._local_subscribers.append( (service_name,cb,args,kwargs) )
        self._subscribed_service_names.add(service_name)
        self._maybe_subscribe()
        for index,(ann,key_s,when) in self._current_announcements.items():
            servicename = index[0]
            if servicename == service_name:
                eventually(cb, key_s, ann, *args, **kwargs)

    def _maybe_subscribe(self):
        if not self._publisher:
            self.log("want to subscribe, but no introducer yet",
                     level=log.NOISY)
            return
        for service_name in self._subscribed_service_names:
            if service_name in self._subscriptions:
                continue
            self._subscriptions.add(service_name)
            if V2 in self._publisher.version:
                self._debug_outstanding += 1
                d = self._publisher.callRemote("subscribe_v2",
                                               self, service_name,
                                               self._my_subscriber_info)
                d.addBoth(self._debug_retired)
            else:
                d = self._subscribe_handle_v1(service_name) # for_v1
            d.addErrback(log.err, facility="tahoe.introducer.client",
                         level=log.WEIRD, umid="2uMScQ")

    def _subscribe_handle_v1(self, service_name): # for_v1
        # they don't speak V2: must be a v1 introducer. Fall back to the v1
        # 'subscribe' method, using a client adapter.
        ca = WrapV2ClientInV1Interface(self)
        self._debug_outstanding += 1
        d = self._publisher.callRemote("subscribe", ca, service_name)
        d.addBoth(self._debug_retired)
        # We must also publish an empty 'stub_client' object, so the
        # introducer can count how many clients are connected and see what
        # versions they're running.
        if not self._stub_client_furl:
            self._stub_client = sc = StubClient()
            self._stub_client_furl = self._tub.registerReference(sc)
        def _publish_stub_client(ignored):
            furl = self._stub_client_furl
            self.publish("stub_client",
                         { "anonymous-storage-FURL": furl,
                           "permutation-seed-base32": get_tubid_string(furl),
                           })
        d.addCallback(_publish_stub_client)
        return d

    def create_announcement(self, service_name, ann, signing_key, _mod=None):
        full_ann = { "version": 0,
                     "seqnum": time.time(),
                     "nickname": self._nickname,
                     "app-versions": self._app_versions,
                     "my-version": self._my_version,
                     "oldest-supported": self._oldest_supported,

                     "service-name": service_name,
                     }
        full_ann.update(ann)
        if _mod:
            full_ann = _mod(full_ann) # for unit tests
        return sign_to_foolscap(full_ann, signing_key)

    def publish(self, service_name, ann, signing_key=None):
        ann_t = self.create_announcement(service_name, ann, signing_key)
        self._published_announcements[service_name] = ann_t
        self._maybe_publish()

    def _maybe_publish(self):
        if not self._publisher:
            self.log("want to publish, but no introducer yet", level=log.NOISY)
            return
        # this re-publishes everything. The Introducer ignores duplicates
        for ann_t in self._published_announcements.values():
            self._debug_counts["outbound_message"] += 1
            if V2 in self._publisher.version:
                self._debug_outstanding += 1
                d = self._publisher.callRemote("publish_v2", ann_t,
                                               self._canary)
                d.addBoth(self._debug_retired)
            else:
                d = self._handle_v1_publisher(ann_t) # for_v1
            d.addErrback(log.err, ann_t=ann_t,
                         facility="tahoe.introducer.client",
                         level=log.WEIRD, umid="xs9pVQ")

    def _handle_v1_publisher(self, ann_t): # for_v1
        # they don't speak V2, so fall back to the old 'publish' method
        # (which takes an unsigned tuple of bytestrings)
        self.log("falling back to publish_v1",
                 level=log.UNUSUAL, umid="9RCT1A")
        ann_v1 = convert_announcement_v2_to_v1(ann_t)
        self._debug_outstanding += 1
        d = self._publisher.callRemote("publish", ann_v1)
        d.addBoth(self._debug_retired)
        return d


    def remote_announce_v2(self, announcements):
        lp = self.log("received %d announcements (v2)" % len(announcements))
        return self.got_announcements(announcements, lp)

    def got_announcements(self, announcements, lp=None):
        # this is the common entry point for both v1 and v2 announcements
        self._debug_counts["inbound_message"] += 1
        for ann_t in announcements:
            try:
                # this might raise UnknownKeyError or bad-sig error
                ann, key_s = unsign_from_foolscap(ann_t)
                # key is "v0-base32abc123"
            except BadSignatureError:
                self.log("bad signature on inbound announcement: %s" % (ann_t,),
                         parent=lp, level=log.WEIRD, umid="ZAU15Q")
                # process other announcements that arrived with the bad one
                continue

            self._process_announcement(ann, key_s)

    def _process_announcement(self, ann, key_s):
        self._debug_counts["inbound_announcement"] += 1
        service_name = str(ann["service-name"])
        if service_name not in self._subscribed_service_names:
            self.log("announcement for a service we don't care about [%s]"
                     % (service_name,), level=log.UNUSUAL, umid="dIpGNA")
            self._debug_counts["wrong_service"] += 1
            return
        # for ASCII values, simplejson might give us unicode *or* bytes
        if "nickname" in ann and isinstance(ann["nickname"], str):
            ann["nickname"] = unicode(ann["nickname"])
        nick_s = ann.get("nickname",u"").encode("utf-8")
        lp2 = self.log(format="announcement for nickname '%(nick)s', service=%(svc)s: %(ann)s",
                       nick=nick_s, svc=service_name, ann=ann, umid="BoKEag")

        # how do we describe this node in the logs?
        desc_bits = []
        if key_s:
            desc_bits.append("serverid=" + key_s[:20])
        if "anonymous-storage-FURL" in ann:
            tubid_s = get_tubid_string_from_ann(ann)
            desc_bits.append("tubid=" + tubid_s[:8])
        description = "/".join(desc_bits)

        # the index is used to track duplicates
        index = make_index(ann, key_s)

        # is this announcement a duplicate?
        if (index in self._current_announcements
            and self._current_announcements[index][0] == ann):
            self.log(format="reannouncement for [%(service)s]:%(description)s, ignoring",
                     service=service_name, description=description,
                     parent=lp2, level=log.UNUSUAL, umid="B1MIdA")
            self._debug_counts["duplicate_announcement"] += 1
            return

        # does it update an existing one?
        if index in self._current_announcements:
            old,_,_ = self._current_announcements[index]
            if "seqnum" in old:
                # must beat previous sequence number to replace
                if "seqnum" not in ann:
                    self.log("not replacing old announcement, no seqnum: %s"
                             % (ann,),
                             parent=lp2, level=log.NOISY, umid="zFGH3Q")
                    return
                if ann["seqnum"] <= old["seqnum"]:
                    # note that exact replays are caught earlier, by
                    # comparing the entire signed announcement.
                    self.log("not replacing old announcement, "
                             "new seqnum is too old (%s <= %s) "
                             "(replay attack?): %s"
                             % (ann["seqnum"], old["seqnum"], ann),
                             parent=lp2, level=log.UNUSUAL, umid="JAAAoQ")
                    return
                # ok, seqnum is newer, allow replacement
            self._debug_counts["update"] += 1
            self.log("replacing old announcement: %s" % (ann,),
                     parent=lp2, level=log.NOISY, umid="wxwgIQ")
        else:
            self._debug_counts["new_announcement"] += 1
            self.log("new announcement[%s]" % service_name,
                     parent=lp2, level=log.NOISY)

        self._current_announcements[index] = (ann, key_s, time.time())
        # note: we never forget an index, but we might update its value

        for (service_name2,cb,args,kwargs) in self._local_subscribers:
            if service_name2 == service_name:
                eventually(cb, key_s, ann, *args, **kwargs)

    def remote_set_encoding_parameters(self, parameters):
        self.encoding_parameters = parameters

    def connected_to_introducer(self):
        return bool(self._publisher)
