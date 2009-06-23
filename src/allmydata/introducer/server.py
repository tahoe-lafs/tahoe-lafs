
import time, os.path
from base64 import b32decode
from zope.interface import implements
from twisted.application import service
from foolscap.api import Referenceable, SturdyRef
import allmydata
from allmydata import node
from allmydata.util import log, rrefutil
from allmydata.introducer.interfaces import \
     RIIntroducerPublisherAndSubscriberService

class IntroducerNode(node.Node):
    PORTNUMFILE = "introducer.port"
    NODETYPE = "introducer"

    def __init__(self, basedir="."):
        node.Node.__init__(self, basedir)
        self.read_config()
        self.init_introducer()
        webport = self.get_config("node", "web.port", None)
        if webport:
            self.init_web(webport) # strports string

    def init_introducer(self):
        introducerservice = IntroducerService(self.basedir)
        self.add_service(introducerservice)

        d = self.when_tub_ready()
        def _publish(res):
            self.introducer_url = self.tub.registerReference(introducerservice,
                                                             "introducer")
            self.log(" introducer is at %s" % self.introducer_url)
            self.write_config("introducer.furl", self.introducer_url + "\n")
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="UaNs9A")

    def init_web(self, webport):
        self.log("init_web(webport=%s)", args=(webport,))

        from allmydata.webish import IntroducerWebishServer
        nodeurl_path = os.path.join(self.basedir, "node.url")
        ws = IntroducerWebishServer(self, webport, nodeurl_path)
        self.add_service(ws)

class IntroducerService(service.MultiService, Referenceable):
    implements(RIIntroducerPublisherAndSubscriberService)
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
        self._subscribers = {} # dict of (rref->timestamp) dicts
        self._debug_counts = {"inbound_message": 0,
                              "inbound_duplicate": 0,
                              "inbound_update": 0,
                              "outbound_message": 0,
                              "outbound_announcements": 0,
                              "inbound_subscribe": 0}

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.introducer"
        return log.msg(*args, **kwargs)

    def get_announcements(self):
        return self._announcements
    def get_subscribers(self):
        return self._subscribers

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
            d = s.callRemote("announce", set([announcement]))
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
        d = subscriber.callRemote("announce", announcements)
        d.addErrback(rrefutil.trap_deadref)
        d.addErrback(log.err,
                     format="subscriber errored during subscribe %(anns)s",
                     anns=announcements, facility="tahoe.introducer",
                     level=log.UNUSUAL, umid="mtZepQ")
