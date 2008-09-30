
import time, os.path
from zope.interface import implements
from twisted.application import service
from foolscap import Referenceable
from allmydata import node
from allmydata.util import log
from allmydata.introducer.interfaces import \
     RIIntroducerPublisherAndSubscriberService
from allmydata.introducer.common import make_index

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
        ws = IntroducerWebishServer(webport, nodeurl_path)
        self.add_service(ws)

class IntroducerService(service.MultiService, Referenceable):
    implements(RIIntroducerPublisherAndSubscriberService)
    name = "introducer"

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        self.introducer_url = None
        # 'index' is (tubid, service_name)
        self._announcements = {} # dict of index -> (announcement, timestamp)
        self._subscribers = {} # dict of (rref->timestamp) dicts

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.introducer"
        return log.msg(*args, **kwargs)

    def get_announcements(self):
        return self._announcements
    def get_subscribers(self):
        return self._subscribers

    def remote_publish(self, announcement):
        self.log("introducer: announcement published: %s" % (announcement,) )
        index = make_index(announcement)
        if index in self._announcements:
            (old_announcement, timestamp) = self._announcements[index]
            if old_announcement == announcement:
                self.log("but we already knew it, ignoring", level=log.NOISY)
                return
            else:
                self.log("old announcement being updated", level=log.NOISY)
        self._announcements[index] = (announcement, time.time())
        (furl, service_name, ri_name, nickname, ver, oldest) = announcement
        for s in self._subscribers.get(service_name, []):
            s.callRemote("announce", set([announcement]))

    def remote_subscribe(self, subscriber, service_name):
        self.log("introducer: subscription[%s] request at %s" % (service_name,
                                                                 subscriber))
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

        announcements = set( [ ann
                               for idx,(ann,when) in self._announcements.items()
                               if idx[1] == service_name] )
        d = subscriber.callRemote("announce", announcements)
        d.addErrback(log.err, facility="tahoe.introducer", level=log.UNUSUAL)



