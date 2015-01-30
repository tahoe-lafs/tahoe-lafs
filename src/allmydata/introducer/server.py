
import time, os.path, textwrap
from zope.interface import implements
from twisted.application import service
from foolscap.api import Referenceable
import allmydata
from allmydata import node
from allmydata.util import log, rrefutil
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.introducer.interfaces import \
     RIIntroducerPublisherAndSubscriberService_v2
from allmydata.introducer.common import convert_announcement_v1_to_v2, \
     convert_announcement_v2_to_v1, unsign_from_foolscap, make_index, \
     get_tubid_string_from_ann, SubscriberDescriptor, AnnouncementDescriptor

class FurlFileConflictError(Exception):
    pass

class IntroducerNode(node.Node):
    PORTNUMFILE = "introducer.port"
    NODETYPE = "introducer"
    GENERATED_FILES = ['introducer.furl']

    def __init__(self, basedir=u"."):
        node.Node.__init__(self, basedir)
        self.read_config()
        self.init_introducer()
        webport = self.get_config("node", "web.port", None)
        if webport:
            self.init_web(webport) # strports string

    def init_introducer(self):
        introducerservice = IntroducerService(self.basedir)
        self.add_service(introducerservice)

        old_public_fn = os.path.join(self.basedir, u"introducer.furl")
        private_fn = os.path.join(self.basedir, u"private", u"introducer.furl")

        if os.path.exists(old_public_fn):
            if os.path.exists(private_fn):
                msg = """This directory (%s) contains both an old public
                'introducer.furl' file, and a new-style
                'private/introducer.furl', so I cannot safely remove the old
                one. Please make sure your desired FURL is in
                private/introducer.furl, and remove the public file. If this
                causes your Introducer's FURL to change, you need to inform
                all grid members so they can update their tahoe.cfg.
                """
                raise FurlFileConflictError(textwrap.dedent(msg))
            os.rename(old_public_fn, private_fn)
        d = self.when_tub_ready()
        def _publish(res):
            furl = self.tub.registerReference(introducerservice,
                                              furlFile=private_fn)
            self.log(" introducer is at %s" % furl, umid="qF2L9A")
            self.introducer_url = furl # for tests
        d.addCallback(_publish)
        d.addErrback(log.err, facility="tahoe.init",
                     level=log.BAD, umid="UaNs9A")

    def init_web(self, webport):
        self.log("init_web(webport=%s)", args=(webport,), umid="2bUygA")

        from allmydata.webish import IntroducerWebishServer
        nodeurl_path = os.path.join(self.basedir, u"node.url")
        config_staticdir = self.get_config("node", "web.static", "public_html").decode('utf-8')
        staticdir = abspath_expanduser_unicode(config_staticdir, base=self.basedir)
        ws = IntroducerWebishServer(self, webport, nodeurl_path, staticdir)
        self.add_service(ws)

class WrapV1SubscriberInV2Interface: # for_v1
    """I wrap a RemoteReference that points at an old v1 subscriber, enabling
    it to be treated like a v2 subscriber.
    """

    def __init__(self, original):
        self.original = original # also used for tests
    def __eq__(self, them):
        return self.original == them
    def __ne__(self, them):
        return self.original != them
    def __hash__(self):
        return hash(self.original)
    def getRemoteTubID(self):
        return self.original.getRemoteTubID()
    def getSturdyRef(self):
        return self.original.getSturdyRef()
    def getPeer(self):
        return self.original.getPeer()
    def getLocationHints(self):
        return self.original.getLocationHints()
    def callRemote(self, methname, *args, **kwargs):
        m = getattr(self, "wrap_" + methname)
        return m(*args, **kwargs)
    def wrap_announce_v2(self, announcements):
        anns_v1 = [convert_announcement_v2_to_v1(ann) for ann in announcements]
        return self.original.callRemote("announce", set(anns_v1))
    def wrap_set_encoding_parameters(self, parameters):
        # note: unused
        return self.original.callRemote("set_encoding_parameters", parameters)
    def notifyOnDisconnect(self, *args, **kwargs):
        return self.original.notifyOnDisconnect(*args, **kwargs)

class IntroducerService(service.MultiService, Referenceable):
    implements(RIIntroducerPublisherAndSubscriberService_v2)
    name = "introducer"
    # v1 is the original protocol, supported since 1.0 (but only advertised
    # starting in 1.3). v2 is the new signed protocol, supported after 1.9
    VERSION = { "http://allmydata.org/tahoe/protocols/introducer/v1": { },
                "http://allmydata.org/tahoe/protocols/introducer/v2": { },
                "application-version": str(allmydata.__full_version__),
                }

    def __init__(self, basedir="."):
        service.MultiService.__init__(self)
        self.introducer_url = None
        # 'index' is (service_name, key_s, tubid), where key_s or tubid is
        # None
        self._announcements = {} # dict of index ->
                                 # (ann_t, canary, ann, timestamp)

        # ann (the announcement dictionary) is cleaned up: nickname is always
        # unicode, servicename is always ascii, etc, even though
        # simplejson.loads sometimes returns either

        # self._subscribers is a dict mapping servicename to subscriptions
        # 'subscriptions' is a dict mapping rref to a subscription
        # 'subscription' is a tuple of (subscriber_info, timestamp)
        # 'subscriber_info' is a dict, provided directly for v2 clients, or
        # synthesized for v1 clients. The expected keys are:
        #  version, nickname, app-versions, my-version, oldest-supported
        self._subscribers = {}

        # self._stub_client_announcements contains the information provided
        # by v1 clients. We stash this so we can match it up with their
        # subscriptions.
        self._stub_client_announcements = {} # maps tubid to sinfo # for_v1

        self._debug_counts = {"inbound_message": 0,
                              "inbound_duplicate": 0,
                              "inbound_no_seqnum": 0,
                              "inbound_old_replay": 0,
                              "inbound_update": 0,
                              "outbound_message": 0,
                              "outbound_announcements": 0,
                              "inbound_subscribe": 0}
        self._debug_outstanding = 0 # also covers WrapV1SubscriberInV2Interface

    def _debug_retired(self, res):
        self._debug_outstanding -= 1
        return res

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.introducer.server"
        return log.msg(*args, **kwargs)

    def get_announcements(self, include_stub_clients=True):
        """Return a list of AnnouncementDescriptor for all announcements"""
        announcements = []
        for (index, (_, canary, ann, when)) in self._announcements.items():
            if ann["service-name"] == "stub_client":
                if not include_stub_clients:
                    continue
            ad = AnnouncementDescriptor(when, index, canary, ann)
            announcements.append(ad)
        return announcements

    def get_subscribers(self):
        """Return a list of SubscriberDescriptor objects for all subscribers"""
        s = []
        for service_name, subscriptions in self._subscribers.items():
            for rref,(subscriber_info,when) in subscriptions.items():
                # note that if the subscriber didn't do Tub.setLocation,
                # tubid will be None. Also, subscribers do not tell us which
                # pubkey they use; only publishers do that.
                tubid = rref.getRemoteTubID() or "?"
                advertised_addresses = rrefutil.hosts_for_rref(rref)
                remote_address = rrefutil.stringify_remote_address(rref)
                # these three assume subscriber_info["version"]==0, but
                # should tolerate other versions
                if not subscriber_info:
                     # V1 clients that haven't yet sent their stub_info data
                    subscriber_info = {}
                nickname = subscriber_info.get("nickname", u"?")
                version = subscriber_info.get("my-version", u"?")
                app_versions = subscriber_info.get("app-versions", {})
                # 'when' is the time they subscribed
                sd = SubscriberDescriptor(service_name, when,
                                          nickname, version, app_versions,
                                          advertised_addresses, remote_address,
                                          tubid)
                s.append(sd)
        return s

    def remote_get_version(self):
        return self.VERSION

    def remote_publish(self, ann_t): # for_v1
        lp = self.log("introducer: old (v1) announcement published: %s"
                      % (ann_t,), umid="6zGOIw")
        ann_v2 = convert_announcement_v1_to_v2(ann_t)
        return self.publish(ann_v2, None, lp)

    def remote_publish_v2(self, ann_t, canary):
        lp = self.log("introducer: announcement (v2) published", umid="L2QXkQ")
        return self.publish(ann_t, canary, lp)

    def publish(self, ann_t, canary, lp):
        try:
            self._publish(ann_t, canary, lp)
        except:
            log.err(format="Introducer.remote_publish failed on %(ann)s",
                    ann=ann_t,
                    level=log.UNUSUAL, parent=lp, umid="620rWA")
            raise

    def _publish(self, ann_t, canary, lp):
        self._debug_counts["inbound_message"] += 1
        self.log("introducer: announcement published: %s" % (ann_t,),
                 umid="wKHgCw")
        ann, key = unsign_from_foolscap(ann_t) # might raise BadSignatureError
        index = make_index(ann, key)

        service_name = str(ann["service-name"])
        if service_name == "stub_client": # for_v1
            self._attach_stub_client(ann, lp)
            return

        old = self._announcements.get(index)
        if old:
            (old_ann_t, canary, old_ann, timestamp) = old
            if old_ann == ann:
                self.log("but we already knew it, ignoring", level=log.NOISY,
                         umid="myxzLw")
                self._debug_counts["inbound_duplicate"] += 1
                return
            else:
                if "seqnum" in old_ann:
                    # must beat previous sequence number to replace
                    if ("seqnum" not in ann
                        or not isinstance(ann["seqnum"], (int,long))):
                        self.log("not replacing old ann, no valid seqnum",
                                 level=log.NOISY, umid="ySbaVw")
                        self._debug_counts["inbound_no_seqnum"] += 1
                        return
                    if ann["seqnum"] <= old_ann["seqnum"]:
                        self.log("not replacing old ann, new seqnum is too old"
                                 " (%s <= %s) (replay attack?)"
                                 % (ann["seqnum"], old_ann["seqnum"]),
                                 level=log.UNUSUAL, umid="sX7yqQ")
                        self._debug_counts["inbound_old_replay"] += 1
                        return
                    # ok, seqnum is newer, allow replacement
                self.log("old announcement being updated", level=log.NOISY,
                         umid="304r9g")
                self._debug_counts["inbound_update"] += 1
        self._announcements[index] = (ann_t, canary, ann, time.time())
        #if canary:
        #    canary.notifyOnDisconnect ...
        # use a CanaryWatcher? with cw.is_connected()?
        # actually we just want foolscap to give rref.is_connected(), since
        # this is only for the status display

        for s in self._subscribers.get(service_name, []):
            self._debug_counts["outbound_message"] += 1
            self._debug_counts["outbound_announcements"] += 1
            self._debug_outstanding += 1
            d = s.callRemote("announce_v2", set([ann_t]))
            d.addBoth(self._debug_retired)
            d.addErrback(log.err,
                         format="subscriber errored on announcement %(ann)s",
                         ann=ann_t, facility="tahoe.introducer",
                         level=log.UNUSUAL, umid="jfGMXQ")

    def _attach_stub_client(self, ann, lp):
        # There might be a v1 subscriber for whom this is a stub_client.
        # We might have received the subscription before the stub_client
        # announcement, in which case we now need to fix up the record in
        # self._subscriptions .

        # record it for later, in case the stub_client arrived before the
        # subscription
        subscriber_info = self._get_subscriber_info_from_ann(ann)
        ann_tubid = get_tubid_string_from_ann(ann)
        self._stub_client_announcements[ann_tubid] = subscriber_info

        lp2 = self.log("stub_client announcement, "
                       "looking for matching subscriber",
                       parent=lp, level=log.NOISY, umid="BTywDg")

        for sn in self._subscribers:
            s = self._subscribers[sn]
            for (subscriber, info) in s.items():
                # we correlate these by looking for a subscriber whose tubid
                # matches this announcement
                sub_tubid = subscriber.getRemoteTubID()
                if sub_tubid == ann_tubid:
                    self.log(format="found a match, nodeid=%(nodeid)s",
                             nodeid=sub_tubid,
                             level=log.NOISY, parent=lp2, umid="xsWs1A")
                    # found a match. Does it need info?
                    if not info[0]:
                        self.log(format="replacing info",
                                 level=log.NOISY, parent=lp2, umid="m5kxwA")
                        # yup
                        s[subscriber] = (subscriber_info, info[1])
            # and we don't remember or announce stub_clients beyond what we
            # need to get the subscriber_info set up

    def _get_subscriber_info_from_ann(self, ann): # for_v1
        sinfo = { "version": ann["version"],
                  "nickname": ann["nickname"],
                  "app-versions": ann["app-versions"],
                  "my-version": ann["my-version"],
                  "oldest-supported": ann["oldest-supported"],
                  }
        return sinfo

    def remote_subscribe(self, subscriber, service_name): # for_v1
        self.log("introducer: old (v1) subscription[%s] request at %s"
                 % (service_name, subscriber), umid="hJlGUg")
        return self.add_subscriber(WrapV1SubscriberInV2Interface(subscriber),
                                   service_name, None)

    def remote_subscribe_v2(self, subscriber, service_name, subscriber_info):
        self.log("introducer: subscription[%s] request at %s"
                 % (service_name, subscriber), umid="U3uzLg")
        return self.add_subscriber(subscriber, service_name, subscriber_info)

    def add_subscriber(self, subscriber, service_name, subscriber_info):
        self._debug_counts["inbound_subscribe"] += 1
        if service_name not in self._subscribers:
            self._subscribers[service_name] = {}
        subscribers = self._subscribers[service_name]
        if subscriber in subscribers:
            self.log("but they're already subscribed, ignoring",
                     level=log.UNUSUAL, umid="Sy9EfA")
            return

        if not subscriber_info: # for_v1
            # v1 clients don't provide subscriber_info, but they should
            # publish a 'stub client' record which contains the same
            # information. If we've already received this, it will be in
            # self._stub_client_announcements
            tubid = subscriber.getRemoteTubID()
            if tubid in self._stub_client_announcements:
                subscriber_info = self._stub_client_announcements[tubid]

        subscribers[subscriber] = (subscriber_info, time.time())
        def _remove():
            self.log("introducer: unsubscribing[%s] %s" % (service_name,
                                                           subscriber),
                     umid="vYGcJg")
            subscribers.pop(subscriber, None)
        subscriber.notifyOnDisconnect(_remove)

        # now tell them about any announcements they're interested in
        announcements = set( [ ann_t
                               for idx,(ann_t,canary,ann,when)
                               in self._announcements.items()
                               if idx[0] == service_name] )
        if announcements:
            self._debug_counts["outbound_message"] += 1
            self._debug_counts["outbound_announcements"] += len(announcements)
            self._debug_outstanding += 1
            d = subscriber.callRemote("announce_v2", announcements)
            d.addBoth(self._debug_retired)
            d.addErrback(log.err,
                         format="subscriber errored during subscribe %(anns)s",
                         anns=announcements, facility="tahoe.introducer",
                         level=log.UNUSUAL, umid="mtZepQ")
            return d
