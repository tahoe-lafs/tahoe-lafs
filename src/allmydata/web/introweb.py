
import time
from nevow import rend
from foolscap.referenceable import SturdyRef
from twisted.internet import address
import allmydata
from allmydata import get_package_versions_string
from allmydata.util import idlib
from common import getxmlfile, IClient

class IntroducerRoot(rend.Page):

    addSlash = True
    docFactory = getxmlfile("introducer.xhtml")

    def data_version(self, ctx, data):
        return get_package_versions_string()
    def data_import_path(self, ctx, data):
        return str(allmydata)
    def data_my_nodeid(self, ctx, data):
        return idlib.nodeid_b2a(IClient(ctx).nodeid)

    def render_announcement_summary(self, ctx, data):
        i = IClient(ctx).getServiceNamed("introducer")
        services = {}
        for ann in i.get_announcements():
            (furl, service_name, ri_name, nickname, ver, oldest) = ann
            if service_name not in services:
                services[service_name] = 0
            services[service_name] += 1
        service_names = services.keys()
        service_names.sort()
        return ", ".join(["%s: %d" % (service_name, services[service_name])
                          for service_name in service_names])

    def render_client_summary(self, ctx, data):
        i = IClient(ctx).getServiceNamed("introducer")
        clients = i.get_subscribers()
        service_names = clients.keys()
        service_names.sort()
        return ", ".join(["%s: %d" % (service_name, len(clients[service_name]))
                          for service_name in service_names])

    def data_services(self, ctx, data):
        i = IClient(ctx).getServiceNamed("introducer")
        ann = [a
               for a in i.get_announcements()
               if a[1] != "stub_client"]
        ann.sort(lambda a,b: cmp( (a[1], a), (b[1], b) ) )
        return ann

    def render_service_row(self, ctx, announcement):
        (furl, service_name, ri_name, nickname, ver, oldest) = announcement
        sr = SturdyRef(furl)
        nodeid = sr.tubID
        advertised = [loc.split(":")[0] for loc in sr.locationHints
                      if not loc.startswith("127.0.0.1:")]
        ctx.fillSlots("peerid", "%s %s" % (nodeid, nickname))
        ctx.fillSlots("advertised", " ".join(advertised))
        ctx.fillSlots("connected", "?")
        ctx.fillSlots("since", "?")
        ctx.fillSlots("announced", "?")
        ctx.fillSlots("version", ver)
        ctx.fillSlots("service_name", service_name)
        return ctx.tag

    def data_subscribers(self, ctx, data):
        i = IClient(ctx).getServiceNamed("introducer")
        # use the "stub_client" announcements to get information per nodeid
        clients = {}
        for ann in i.get_announcements():
            if ann[1] != "stub_client":
                continue
            (furl, service_name, ri_name, nickname, ver, oldest) = ann
            sr = SturdyRef(furl)
            nodeid = sr.tubID
            clients[nodeid] = ann

        # then we actually provide information per subscriber
        s = []
        for service_name, subscribers in i.get_subscribers().items():
            for (rref, timestamp) in subscribers.items():
                sr = rref.getSturdyRef()
                nodeid = sr.tubID
                ann = clients.get(nodeid)
                s.append( (service_name, rref, timestamp, ann) )
        s.sort()
        return s

    def render_subscriber_row(self, ctx, s):
        (service_name, rref, since, ann) = s
        nickname = "?"
        version = "?"
        if ann:
            (furl, service_name_2, ri_name, nickname, version, oldest) = ann

        sr = rref.getSturdyRef()
        # if the subscriber didn't do Tub.setLocation, nodeid will be None
        nodeid = sr.tubID or "?"
        ctx.fillSlots("peerid", "%s %s" % (nodeid, nickname))
        advertised = [loc.split(":")[0] for loc in sr.locationHints
                      if not loc.startswith("127.0.0.1:")]
        ctx.fillSlots("advertised", " ".join(advertised))
        remote_host = rref.tracker.broker.transport.getPeer()
        if isinstance(remote_host, address.IPv4Address):
            remote_host_s = "%s:%d" % (remote_host.host, remote_host.port)
        else:
            # loopback is a non-IPv4Address
            remote_host_s = str(remote_host)
        ctx.fillSlots("connected", remote_host_s)
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        ctx.fillSlots("since",
                      time.strftime(TIME_FORMAT, time.localtime(since)))
        ctx.fillSlots("version", version)
        ctx.fillSlots("service_name", service_name)
        return ctx.tag


