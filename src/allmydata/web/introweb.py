
import time, os
from nevow import rend, inevow
from nevow.static import File as nevow_File
from nevow.util import resource_filename
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
        static_dir = resource_filename("allmydata.web", "static")
        for filen in os.listdir(static_dir):
            self.putChild(filen, nevow_File(os.path.join(static_dir, filen)))

    def renderHTTP(self, ctx):
        t = get_arg(inevow.IRequest(ctx), "t")
        if t == "json":
            return self.render_JSON(ctx)
        return rend.Page.renderHTTP(self, ctx)

    def render_JSON(self, ctx):
        res = {}

        counts = {}
        subscribers = self.introducer_service.get_subscribers()
        for (service_name, ign, ign, ign) in subscribers:
            if service_name not in counts:
                counts[service_name] = 0
            counts[service_name] += 1
        res["subscription_summary"] = counts

        announcement_summary = {}
        service_hosts = {}
        for a in self.introducer_service.get_announcements().values():
            (_, _, ann, when) = a
            service_name = ann["service-name"]
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
            furl = ann["anonymous-storage-FURL"]
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
        for a in self.introducer_service.get_announcements().values():
            (_, _, ann, when) = a
            service_name = ann["service-name"]
            if service_name not in services:
                services[service_name] = 0
            services[service_name] += 1
        service_names = services.keys()
        service_names.sort()
        return ", ".join(["%s: %d" % (service_name, services[service_name])
                          for service_name in service_names])

    def render_client_summary(self, ctx, data):
        counts = {}
        clients = self.introducer_service.get_subscribers()
        for (service_name, ign, ign, ign) in clients:
            if service_name not in counts:
                counts[service_name] = 0
            counts[service_name] += 1
        return ", ".join([ "%s: %d" % (name, counts[name])
                           for name in sorted(counts.keys()) ] )

    def data_services(self, ctx, data):
        introsvc = self.introducer_service
        services = []
        for a in introsvc.get_announcements().values():
            (_, _, ann, when) = a
            if ann["service-name"] == "stub_client":
                continue
            services.append( (when, ann) )
        services.sort(key=lambda x: (x[1]["service-name"], x[1]["nickname"]))
        # this used to be:
        #services.sort(lambda a,b: cmp( (a[1][1], a), (b[1][1], b) ) )
        # service_name was the primary key, then the whole tuple (starting
        # with the furl) was the secondary key
        return services

    def render_service_row(self, ctx, (since,ann)):
        sr = SturdyRef(ann["anonymous-storage-FURL"])
        nodeid = sr.tubID
        advertised = self.show_location_hints(sr)
        ctx.fillSlots("peerid", nodeid)
        ctx.fillSlots("nickname", ann["nickname"])
        ctx.fillSlots("advertised", " ".join(advertised))
        ctx.fillSlots("connected", "?")
        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        ctx.fillSlots("announced",
                      time.strftime(TIME_FORMAT, time.localtime(since)))
        ctx.fillSlots("version", ann["my-version"])
        ctx.fillSlots("service_name", ann["service-name"])
        return ctx.tag

    def data_subscribers(self, ctx, data):
        return self.introducer_service.get_subscribers()

    def render_subscriber_row(self, ctx, s):
        (service_name, since, info, rref) = s
        nickname = info.get("nickname", "?")
        version = info.get("my-version", "?")

        sr = rref.getSturdyRef()
        # if the subscriber didn't do Tub.setLocation, nodeid will be None
        nodeid = sr.tubID or "?"
        ctx.fillSlots("peerid", nodeid)
        ctx.fillSlots("nickname", nickname)
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


