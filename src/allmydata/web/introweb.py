
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

    def data_known_storage_servers(self, ctx, data):
        i = IClient(ctx).getServiceNamed("introducer")
        storage = [1
                   for (furl, service_name, ri_name, nickname, ver, oldest)
                   in i.get_announcements()
                   if service_name == "storage"]
        return len(storage)

    def data_num_clients(self, ctx, data):
        i = IClient(ctx).getServiceNamed("introducer")
        num_clients = 0
        subscribers = i.get_subscribers()
        for service_name,who in subscribers.items():
            num_clients += len(who)
        return num_clients

    def data_services(self, ctx, data):
        i = IClient(ctx).getServiceNamed("introducer")
        ann = list(i.get_announcements())
        ann.sort(lambda a,b: cmp( (a[1], a), (b[1], b) ) )
        return ann

    def render_service_row(self, ctx, announcement):
        (furl, service_name, ri_name, nickname, ver, oldest) = announcement
        sr = SturdyRef(furl)
        nodeid = sr.tubID
        advertised = [loc.split(":")[0] for loc in sr.locationHints]
        ctx.fillSlots("peerid", "%s %s" % (idlib.nodeid_b2a(nodeid), nickname))
        ctx.fillSlots("advertised", " ".join(advertised))
        ctx.fillSlots("connected", "?")
        ctx.fillSlots("since", "?")
        ctx.fillSlots("announced", "?")
        ctx.fillSlots("version", ver)
        ctx.fillSlots("service_name", service_name)
        return ctx.tag

    def data_subscribers(self, ctx, data):
        i = IClient(ctx).getServiceNamed("introducer")
        s = []
        for service_name, subscribers in i.get_subscribers().items():
            for rref in subscribers:
                s.append( (service_name, rref) )
        s.sort()
        return s

    def render_subscriber_row(self, ctx, s):
        (service_name, rref) = s
        sr = rref.getSturdyRef()
        nodeid = sr.tubID
        # if the subscriber didn't do Tub.setLocation, nodeid will be None
        nodeid_s = "?"
        if nodeid:
            nodeid_s = idlib.nodeid_b2a(nodeid)
        ctx.fillSlots("peerid", nodeid_s)
        advertised = [loc.split(":")[0] for loc in sr.locationHints]
        ctx.fillSlots("advertised", " ".join(advertised))
        remote_host = rref.tracker.broker.transport.getPeer()
        if isinstance(remote_host, address.IPv4Address):
            remote_host_s = "%s:%d" % (remote_host.host, remote_host.port)
        else:
            # loopback is a non-IPv4Address
            remote_host_s = str(remote_host)
        ctx.fillSlots("connected", remote_host_s)
        ctx.fillSlots("since", "?")
        ctx.fillSlots("service_name", service_name)
        return ctx.tag


