"""
Ported to Python 3.
"""

from __future__ import annotations

from past.builtins import long
from six import ensure_text

import time, os.path, textwrap
from typing import Any, Union

from zope.interface import implementer
from twisted.application import service
from twisted.internet import defer
from twisted.internet.address import IPv4Address
from twisted.python.failure import Failure
from foolscap.api import Referenceable
import allmydata
from allmydata import node
from allmydata.util import log, dictutil
from allmydata.util.i2p_provider import create as create_i2p_provider
from allmydata.util.tor_provider import create as create_tor_provider
from allmydata.introducer.interfaces import \
     RIIntroducerPublisherAndSubscriberService_v2
from allmydata.introducer.common import unsign_from_foolscap, \
     SubscriberDescriptor, AnnouncementDescriptor
from allmydata.node import read_config
from allmydata.node import create_node_dir
from allmydata.node import create_connection_handlers
from allmydata.node import create_tub_options
from allmydata.node import create_main_tub


# this is put into README in new node-directories
INTRODUCER_README = """
This directory contains files which contain private data for the Tahoe node,
such as private keys.  On Unix-like systems, the permissions on this directory
are set to disallow users other than its owner from reading the contents of
the files.   See the 'configuration.rst' documentation file for details.
"""

_valid_config = node._common_valid_config

class FurlFileConflictError(Exception):
    pass

def create_introducer(basedir=u"."):
    """
    :returns: a Deferred that yields a new _IntroducerNode instance
    """
    try:
        # see https://tahoe-lafs.org/trac/tahoe-lafs/ticket/2946
        from twisted.internet import reactor

        if not os.path.exists(basedir):
            create_node_dir(basedir, INTRODUCER_README)

        config = read_config(
            basedir, u"client.port",
            generated_files=["introducer.furl"],
            _valid_config=_valid_config(),
        )

        i2p_provider = create_i2p_provider(reactor, config)
        tor_provider = create_tor_provider(reactor, config)

        default_connection_handlers, foolscap_connection_handlers = create_connection_handlers(config, i2p_provider, tor_provider)
        tub_options = create_tub_options(config)

        main_tub = create_main_tub(
            config, tub_options, default_connection_handlers,
            foolscap_connection_handlers, i2p_provider, tor_provider,
        )

        node = _IntroducerNode(
            config,
            main_tub,
            i2p_provider,
            tor_provider,
        )
        i2p_provider.setServiceParent(node)
        tor_provider.setServiceParent(node)
        return defer.succeed(node)
    except Exception:
        return Failure()


class _IntroducerNode(node.Node):
    NODETYPE = "introducer"

    def __init__(self, config, main_tub, i2p_provider, tor_provider):
        node.Node.__init__(self, config, main_tub, i2p_provider, tor_provider)
        self.init_introducer()
        webport = self.get_config("node", "web.port", None)
        if webport:
            self.init_web(webport) # strports string

    def init_introducer(self):
        if not self._is_tub_listening():
            raise ValueError("config error: we are Introducer, but tub "
                             "is not listening ('tub.port=' is empty)")
        introducerservice = IntroducerService()
        introducerservice.setServiceParent(self)

        old_public_fn = self.config.get_config_path(u"introducer.furl")
        private_fn = self.config.get_private_path(u"introducer.furl")

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
        furl = self.tub.registerReference(introducerservice,
                                          furlFile=private_fn)
        self.log(" introducer can be found in {!r}".format(private_fn), umid="qF2L9A")
        self.introducer_url = furl # for tests

    def init_web(self, webport):
        self.log("init_web(webport=%s)", args=(webport,), umid="2bUygA")

        from allmydata.webish import IntroducerWebishServer
        nodeurl_path = self.config.get_config_path(u"node.url")
        config_staticdir = self.get_config("node", "web.static", "public_html")
        staticdir = self.config.get_config_path(config_staticdir)
        ws = IntroducerWebishServer(self, webport, nodeurl_path, staticdir)
        ws.setServiceParent(self)


def stringify_remote_address(rref):
    remote = rref.getPeer()
    if isinstance(remote, IPv4Address):
        return "%s:%d" % (remote.host, remote.port)
    # loopback is a non-IPv4Address
    return str(remote)


# MyPy doesn't work well with remote interfaces...
@implementer(RIIntroducerPublisherAndSubscriberService_v2)
class IntroducerService(service.MultiService, Referenceable):  # type: ignore[misc]
    # The type in Twisted for services is wrong in 22.10...
    # https://github.com/twisted/twisted/issues/10135
    name = "introducer"  # type: ignore[assignment]
    # v1 is the original protocol, added in 1.0 (but only advertised starting
    # in 1.3), removed in 1.12. v2 is the new signed protocol, added in 1.10
    # TODO: reconcile bytes/str for keys
    VERSION : dict[Union[bytes, str], Any]= {
                #"http://allmydata.org/tahoe/protocols/introducer/v1": { },
                b"http://allmydata.org/tahoe/protocols/introducer/v2": { },
                b"application-version": allmydata.__full_version__.encode("utf-8"),
                }

    def __init__(self):
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
        # 'subscriber_info' is a dict, provided directly by v2 clients. The
        # expected keys are: version, nickname, app-versions, my-version,
        # oldest-supported
        self._subscribers = dictutil.UnicodeKeyDict({})

        self._debug_counts = {"inbound_message": 0,
                              "inbound_duplicate": 0,
                              "inbound_no_seqnum": 0,
                              "inbound_old_replay": 0,
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
            kwargs["facility"] = "tahoe.introducer.server"
        return log.msg(*args, **kwargs)

    def get_announcements(self):
        """Return a list of AnnouncementDescriptor for all announcements"""
        announcements = []
        for (index, (_, canary, ann, when)) in list(self._announcements.items()):
            ad = AnnouncementDescriptor(when, index, canary, ann)
            announcements.append(ad)
        return announcements

    def get_subscribers(self):
        """Return a list of SubscriberDescriptor objects for all subscribers"""
        s = []
        for service_name, subscriptions in list(self._subscribers.items()):
            for rref,(subscriber_info,when) in list(subscriptions.items()):
                # note that if the subscriber didn't do Tub.setLocation,
                # tubid will be None. Also, subscribers do not tell us which
                # pubkey they use; only publishers do that.
                tubid = rref.getRemoteTubID() or "?"
                remote_address = stringify_remote_address(rref)
                # these three assume subscriber_info["version"]==0, but
                # should tolerate other versions
                nickname = subscriber_info.get("nickname", u"?")
                version = subscriber_info.get("my-version", u"?")
                app_versions = subscriber_info.get("app-versions", {})
                # 'when' is the time they subscribed
                sd = SubscriberDescriptor(service_name, when,
                                          nickname, version, app_versions,
                                          remote_address, tubid)
                s.append(sd)
        return s

    def remote_get_version(self):
        return self.VERSION

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
        ann, key = unsign_from_foolscap(ann_t) # might raise BadSignature
        service_name = str(ann["service-name"])

        index = (service_name, key)
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

    def remote_subscribe_v2(self, subscriber, service_name, subscriber_info):
        self.log("introducer: subscription[%r] request at %r"
                 % (service_name, subscriber), umid="U3uzLg")
        service_name = ensure_text(service_name)
        subscriber_info = dictutil.UnicodeKeyDict({
            ensure_text(k): v for (k, v) in subscriber_info.items()
        })
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

        assert subscriber_info

        subscribers[subscriber] = (subscriber_info, time.time())
        def _remove():
            self.log("introducer: unsubscribing[%s] %s" % (service_name,
                                                           subscriber),
                     umid="vYGcJg")
            subscribers.pop(subscriber, None)
        subscriber.notifyOnDisconnect(_remove)

        # Make sure types are correct:
        for k in self._announcements:
            assert isinstance(k[0], type(service_name))

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
