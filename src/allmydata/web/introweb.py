
import time
from nevow import rend, inevow
from foolscap.api import SturdyRef
from twisted.internet import address
import allmydata
import simplejson
from allmydata import get_package_versions_string
from allmydata.util import idlib
from allmydata.web.common import getxmlfile, get_arg

class IntroducerRoot(rend.Page):

    addSlash = True
    docFactory = getxmlfile("introducer.xhtml")

    child_operations = None

    def __init__(self, introducer_node):
        self.introducer_node = introducer_node
        self.introducer_service = introducer_node.getServiceNamed("introducer")
        rend.Page.__init__(self, introducer_node)

    def renderHTTP(self, ctx):
        t = get_arg(inevow.IRequest(ctx), "t")
        if t == "json":
            return self.render_JSON(ctx)
        return rend.Page.renderHTTP(self, ctx)

    def render_JSON(self, ctx):
        res = {}
        clients = self.introducer_service.get_subscribers()
        subscription_summary = dict([ (name, len(clients[name]))
                                      for name in clients ])
        res["subscription_summary"] = subscription_summary

        announcement_summary = {}
        service_hosts = {}
        for (ann,when) in self.introducer_service.get_announcements().values():
            (furl, service_name, ri_name, nickname, ver, oldest) = ann
            if service_name not in announcement_summary:
                announcement_summary[service_name] = 0
            announcement_summary[service_name] += 1
            if service_name not in service_hosts:
                service_hosts[service_name] = set()
            # it's nice to know how many distinct hosts are available for
            # each service. We define a "host" by a set of addresses
            # (hostnames or ipv4 addresses), which we extract from the
            # connection hints. In practice, this is usually close
            # enough: when multiple services are run on a single host,
            # they're usually either configured with the same addresses,
            # or setLocationAutomatically picks up the same interfaces.
            locations = SturdyRef(furl).getTubRef().getLocations()
            # list of tuples, ("ipv4", host, port)
            host = frozenset([hint[1]
                              for hint in locations
                              if hint[0] == "ipv4"])
            service_hosts[service_name].add(host)
        res["announcement_summary"] = announcement_summary
        distinct_hosts = dict([(name, len(hosts))
                               for (name, hosts)
                               in service_hosts.iteritems()])
        res["announcement_distinct_hosts"] = distinct_hosts

        return simplejson.dumps(res, indent=1) + "\n"

    # FIXME: This code is duplicated in root.py and introweb.py.
    def data_version(self, ctx, data):
        return get_package_versions_string()
    def data_import_path(self, ctx, data):
        return str(allmydata).replace("/", "/ ") # XXX kludge for wrapping
    def data_my_nodeid(self, ctx, data):
        return idlib.nodeid_b2a(self.introducer_node.nodeid)

    def render_announcement_summary(self, ctx, data):
        services = {}
        for (ann,when) in self.introducer_service.get_announcements().values():
            (furl, service_name, ri_name, nickname, ver, oldest) = ann
            if service_name not in services:
                services[service_name] = 0
            services[service_name] += 1
        service_names = services.keys()
        service_names.sort()
        return ", ".join(["%s: %d" % (service_name, services[service_name])
                          for service_name in service_names])

    def render_client_summary(self, ctx, data):
        clients = self.introducer_service.get_subscribers()
        service_names = clients.keys()
        service_names.sort()
        return ", ".join(["%s: %d" % (service_name, len(clients[service_name]))
                          for service_name in service_names])

    def data_services(self, ctx, data):
        introsvc = self.introducer_service
        ann = [(since,a)
               for (a,since) in introsvc.get_announcements().values()
               if a[1] != "stub_client"]
        ann.sort(lambda a,b: cmp( (a[1][1], a), (b[1][1], b) ) )
        return ann

    def render_service_row(self, ctx, (since,announcement)):
        (furl, service_name, ri_name, nickname, ver, oldest) = announcement
        sr = SturdyRef(furl)
        nodeid = sr.tubID
        advertised = self.show_location_hints(sr)
        ctx.fillSlots("peerid", "%s %s" % (nodeid, nickname))
        ctx.fillSlots("advertised", " ".join(advertised))
        ctx.fillSlots("connected", "?")
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        ctx.fillSlots("announced",
                      time.strftime(TIME_FORMAT, time.localtime(since)))
        ctx.fillSlots("version", ver)
        ctx.fillSlots("service_name", service_name)
        return ctx.tag

    def data_subscribers(self, ctx, data):
        # use the "stub_client" announcements to get information per nodeid
        clients = {}
        for (ann,when) in self.introducer_service.get_announcements().values():
            if ann[1] != "stub_client":
                continue
            (furl, service_name, ri_name, nickname, ver, oldest) = ann
            sr = SturdyRef(furl)
            nodeid = sr.tubID
            clients[nodeid] = ann

        # then we actually provide information per subscriber
        s = []
        introsvc = self.introducer_service
        for service_name, subscribers in introsvc.get_subscribers().items():
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
        advertised = self.show_location_hints(sr)
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

    def show_location_hints(self, sr, ignore_localhost=True):
        advertised = []
        for hint in sr.locationHints:
            if isinstance(hint, str):
                # Foolscap-0.2.5 and earlier used strings in .locationHints
                if ignore_localhost and hint.startswith("127.0.0.1"):
                    continue
                advertised.append(hint.split(":")[0])
            else:
                # Foolscap-0.2.6 and later use tuples of ("ipv4", host, port)
                if hint[0] == "ipv4":
                    host = hint[1]
                if ignore_localhost and host == "127.0.0.1":
                    continue
                advertised.append(hint[1])
        return advertised


