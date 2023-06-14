"""
Ported to Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
from past.builtins import long

from six import ensure_text, ensure_str

import time
from zope.interface import implementer
from twisted.application import service
from foolscap.api import Referenceable
from allmydata.interfaces import InsufficientVersionError
from allmydata.introducer.interfaces import IIntroducerClient, \
     RIIntroducerSubscriberClient_v2
from allmydata.introducer.common import sign_to_foolscap, unsign_from_foolscap,\
     get_tubid_string_from_ann
from allmydata.util import log, yamlutil, connection_status
from allmydata.util.rrefutil import add_version_to_remote_reference
from allmydata.util.observer import (
    ObserverList,
)
from allmydata.crypto.error import BadSignature
from allmydata.util.assertutil import precondition

class InvalidCacheError(Exception):
    pass

V2 = b"http://allmydata.org/tahoe/protocols/introducer/v2"

@implementer(RIIntroducerSubscriberClient_v2, IIntroducerClient)  # type: ignore[misc]
class IntroducerClient(service.Service, Referenceable):

    def __init__(self, tub, introducer_furl,
                 nickname, my_version, oldest_supported,
                 sequencer, cache_filepath):
        self._tub = tub
        self.introducer_furl = introducer_furl

        assert isinstance(nickname, str)
        self._nickname = nickname
        self._my_version = my_version
        self._oldest_supported = oldest_supported
        self._sequencer = sequencer
        self._cache_filepath = cache_filepath

        self._my_subscriber_info = { b"version": 0,
                                     b"nickname": self._nickname,
                                     b"app-versions": [],
                                     b"my-version": self._my_version,
                                     b"oldest-supported": self._oldest_supported,
                                     }

        self._outbound_announcements = {} # not signed
        self._published_announcements = {} # signed
        self._canary = Referenceable()

        self._publisher = None
        self._since = None

        self._local_subscribers = {} # {servicename: ObserverList}
        self._subscriptions = set() # requests we've actually sent

        # _inbound_announcements remembers one announcement per
        # (servicename,serverid) pair. Anything that arrives with the same
        # pair will displace the previous one. This stores tuples of
        # (unpacked announcement dictionary, verifyingkey, rxtime). The ann
        # dicts can be compared for equality to distinguish re-announcement
        # from updates. It also provides memory for clients who subscribe
        # after startup.
        self._inbound_announcements = {}

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
        rc = self._tub.connectTo(ensure_str(self.introducer_furl), self._got_introducer)
        self._introducer_reconnector = rc
        def connect_failed(failure):
            self.log("Initial Introducer connection failed: perhaps it's down",
                     level=log.WEIRD, failure=failure, umid="c5MqUQ")
            self._load_announcements()
        d = self._tub.getReference(self.introducer_furl)
        d.addErrback(connect_failed)

    def _load_announcements(self):
        try:
            with self._cache_filepath.open() as f:
                servers = yamlutil.safe_load(f)
        except EnvironmentError:
            return # no cache file
        if not isinstance(servers, list):
            log.err(InvalidCacheError("not a list"), level=log.WEIRD)
            return
        self.log("Using server data from cache", level=log.UNUSUAL)
        for server_params in servers:
            if not isinstance(server_params, dict):
                log.err(InvalidCacheError("not a dict: %r" % (server_params,)),
                        level=log.WEIRD)
                continue
            # everything coming from yamlutil.safe_load is unicode
            key_s = server_params['key_s'].encode("ascii")
            self._deliver_announcements(key_s, server_params['ann'])

    def _save_announcements(self):
        announcements = []
        for value in self._inbound_announcements.values():
            ann, key_s, time_stamp = value
            # On Python 2, bytes strings are encoded into YAML Unicode strings.
            # On Python 3, bytes are encoded as YAML bytes. To minimize
            # changes, Python 3 for now ensures the same is true.
            server_params = {
                "ann" : ann,
                "key_s" : ensure_text(key_s),
                }
            announcements.append(server_params)
        announcement_cache_yaml = yamlutil.safe_dump(announcements)
        if isinstance(announcement_cache_yaml, str):
            announcement_cache_yaml = announcement_cache_yaml.encode("utf-8")
        self._cache_filepath.setContent(announcement_cache_yaml)

    def _got_introducer(self, publisher):
        self.log("connected to introducer, getting versions")
        default = { b"http://allmydata.org/tahoe/protocols/introducer/v1":
                    { },
                    b"application-version": b"unknown: no get_version()",
                    }
        d = add_version_to_remote_reference(publisher, default)
        d.addCallback(self._got_versioned_introducer)
        d.addErrback(self._got_error)

    def _got_error(self, f):
        # TODO: for the introducer, perhaps this should halt the application
        self._introducer_error = f # polled by tests

    def _got_versioned_introducer(self, publisher):
        self.log("got introducer version: %s" % (publisher.version,))
        # we require an introducer that speaks at least V2
        assert all(type(V2) == type(v) for v in publisher.version)
        if V2 not in publisher.version:
            raise InsufficientVersionError("V2", publisher.version)
        self._publisher = publisher
        self._since = int(time.time())
        publisher.notifyOnDisconnect(self._disconnected)
        self._maybe_publish()
        self._maybe_subscribe()

    def _disconnected(self):
        self.log("bummer, we've lost our connection to the introducer")
        self._publisher = None
        self._since = int(time.time())
        self._subscriptions.clear()

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.introducer.client"
        return log.msg(*args, **kwargs)

    def subscribe_to(self, service_name, callback, *args, **kwargs):
        obs = self._local_subscribers.setdefault(service_name, ObserverList())
        obs.subscribe(lambda key_s, ann: callback(key_s, ann, *args, **kwargs))
        self._maybe_subscribe()
        for index,(ann,key_s,when) in list(self._inbound_announcements.items()):
            precondition(isinstance(key_s, bytes), key_s)
            servicename = index[0]
            if servicename == service_name:
                obs.notify(key_s, ann)

    def _maybe_subscribe(self):
        if not self._publisher:
            self.log("want to subscribe, but no introducer yet",
                     level=log.NOISY)
            return
        for service_name in self._local_subscribers:
            if service_name in self._subscriptions:
                continue
            self._subscriptions.add(service_name)
            self._debug_outstanding += 1
            d = self._publisher.callRemote("subscribe_v2",
                                           self, service_name.encode("utf-8"),
                                           self._my_subscriber_info)
            d.addBoth(self._debug_retired)
            d.addErrback(log.err, facility="tahoe.introducer.client",
                         level=log.WEIRD, umid="2uMScQ")

    def create_announcement_dict(self, service_name, ann):
        ann_d = { "version": 0,
                  # "seqnum" and "nonce" will be populated with new values in
                  # publish(), each time we make a change
                  "nickname": self._nickname,
                  "app-versions": [],
                  "my-version": self._my_version,
                  "oldest-supported": self._oldest_supported,

                  "service-name": service_name,
                  }
        ann_d.update(ann)
        return ann_d

    def publish(self, service_name, ann, signing_key):
        # we increment the seqnum every time we publish something new
        current_seqnum, current_nonce = self._sequencer()

        ann_d = self.create_announcement_dict(service_name, ann)
        self._outbound_announcements[service_name] = ann_d

        # publish all announcements with the new seqnum and nonce
        for service_name,ann_d in list(self._outbound_announcements.items()):
            ann_d["seqnum"] = current_seqnum
            ann_d["nonce"] = current_nonce
            ann_t = sign_to_foolscap(ann_d, signing_key)
            self._published_announcements[service_name] = ann_t
        self._maybe_publish()

    def _maybe_publish(self):
        if not self._publisher:
            self.log("want to publish, but no introducer yet", level=log.NOISY)
            return
        # this re-publishes everything. The Introducer ignores duplicates
        for ann_t in list(self._published_announcements.values()):
            self._debug_counts["outbound_message"] += 1
            self._debug_outstanding += 1
            d = self._publisher.callRemote("publish_v2", ann_t, self._canary)
            d.addBoth(self._debug_retired)
            d.addErrback(log.err, ann_t=ann_t,
                         facility="tahoe.introducer.client",
                         level=log.WEIRD, umid="xs9pVQ")

    def remote_announce_v2(self, announcements):
        lp = self.log("received %d announcements (v2)" % len(announcements))
        return self.got_announcements(announcements, lp)

    def got_announcements(self, announcements, lp=None):
        self._debug_counts["inbound_message"] += 1
        for ann_t in announcements:
            try:
                # this might raise UnknownKeyError or bad-sig error
                ann, key_s = unsign_from_foolscap(ann_t)
                # key is "v0-base32abc123"
                precondition(isinstance(key_s, bytes), key_s)
            except BadSignature:
                self.log("bad signature on inbound announcement: %s" % (ann_t,),
                         parent=lp, level=log.WEIRD, umid="ZAU15Q")
                # process other announcements that arrived with the bad one
                continue

            self._process_announcement(ann, key_s)

    def _process_announcement(self, ann, key_s):
        precondition(isinstance(key_s, bytes), key_s)
        self._debug_counts["inbound_announcement"] += 1
        service_name = str(ann["service-name"])
        if service_name not in self._local_subscribers:
            self.log("announcement for a service we don't care about [%s]"
                     % (service_name,), level=log.UNUSUAL, umid="dIpGNA")
            self._debug_counts["wrong_service"] += 1
            return
        # for ASCII values, simplejson might give us unicode *or* bytes
        if "nickname" in ann and isinstance(ann["nickname"], bytes):
            ann["nickname"] = str(ann["nickname"])
        nick_s = ann.get("nickname",u"").encode("utf-8")
        lp2 = self.log(format="announcement for nickname '%(nick)s', service=%(svc)s: %(ann)s",
                       nick=nick_s, svc=service_name, ann=ann, umid="BoKEag")

        # how do we describe this node in the logs?
        desc_bits = []
        assert key_s
        desc_bits.append(b"serverid=" + key_s[:20])
        if "anonymous-storage-FURL" in ann:
            tubid_s = get_tubid_string_from_ann(ann)
            desc_bits.append(b"tubid=" + tubid_s[:8])
        description = b"/".join(desc_bits)

        # the index is used to track duplicates
        index = (service_name, key_s)

        # is this announcement a duplicate?
        if (index in self._inbound_announcements
            and self._inbound_announcements[index][0] == ann):
            self.log(format="reannouncement for [%(service)s]:%(description)s, ignoring",
                     service=service_name, description=description,
                     parent=lp2, level=log.UNUSUAL, umid="B1MIdA")
            self._debug_counts["duplicate_announcement"] += 1
            return

        # does it update an existing one?
        if index in self._inbound_announcements:
            old,_,_ = self._inbound_announcements[index]
            if "seqnum" in old:
                # must beat previous sequence number to replace
                if ("seqnum" not in ann
                    or not isinstance(ann["seqnum"], (int,long))):
                    self.log("not replacing old announcement, no valid seqnum: %s"
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

        self._inbound_announcements[index] = (ann, key_s, time.time())
        self._save_announcements()
        # note: we never forget an index, but we might update its value

        self._deliver_announcements(key_s, ann)

    def _deliver_announcements(self, key_s, ann):
        precondition(isinstance(key_s, bytes), key_s)
        service_name = str(ann["service-name"])
        obs = self._local_subscribers.get(service_name)
        if obs is not None:
            obs.notify(key_s, ann)

    def connection_status(self):
        assert self.running # startService builds _introducer_reconnector
        irc = self._introducer_reconnector
        last_received = (self._publisher.getDataLastReceivedAt()
                         if self._publisher
                         else None)
        return connection_status.from_foolscap_reconnector(irc, last_received)

    def connected_to_introducer(self):
        return bool(self._publisher)

    def get_since(self):
        return self._since
