
import time, os
from nevow import rend, inevow
from nevow.static import File as nevow_File
from nevow.util import resource_filename
import allmydata
import simplejson
from allmydata import get_package_versions_string
from allmydata.util import idlib
from allmydata.web.common import getxmlfile, get_arg, TIME_FORMAT


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
        for s in self.introducer_service.get_subscribers():
            if s.service_name not in counts:
                counts[s.service_name] = 0
            counts[s.service_name] += 1
        res["subscription_summary"] = counts

        announcement_summary = {}
        service_hosts = {}
        for ad in self.introducer_service.get_announcements():
            service_name = ad.service_name
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
            host = frozenset(ad.advertised_addresses)
            service_hosts[service_name].add(host)
        res["announcement_summary"] = announcement_summary
        distinct_hosts = dict([(name, len(hosts))
                               for (name, hosts)
                               in service_hosts.iteritems()])
        res["announcement_distinct_hosts"] = distinct_hosts

        return simplejson.dumps(res, indent=1) + "\n"

    # FIXME: This code is duplicated in root.py and introweb.py.
    def data_rendered_at(self, ctx, data):
        return time.strftime(TIME_FORMAT, time.localtime())
    def data_version(self, ctx, data):
        return get_package_versions_string()
    def data_import_path(self, ctx, data):
        return str(allmydata).replace("/", "/ ") # XXX kludge for wrapping
    def data_my_nodeid(self, ctx, data):
        return idlib.nodeid_b2a(self.introducer_node.nodeid)

    def render_announcement_summary(self, ctx, data):
        services = {}
        for ad in self.introducer_service.get_announcements():
            if ad.service_name not in services:
                services[ad.service_name] = 0
            services[ad.service_name] += 1
        service_names = services.keys()
        service_names.sort()
        return ", ".join(["%s: %d" % (service_name, services[service_name])
                          for service_name in service_names])

    def render_client_summary(self, ctx, data):
        counts = {}
        for s in self.introducer_service.get_subscribers():
            if s.service_name not in counts:
                counts[s.service_name] = 0
            counts[s.service_name] += 1
        return ", ".join([ "%s: %d" % (name, counts[name])
                           for name in sorted(counts.keys()) ] )

    def data_services(self, ctx, data):
        services = self.introducer_service.get_announcements(False)
        services.sort(key=lambda ad: (ad.service_name, ad.nickname))
        return services

    def render_service_row(self, ctx, ad):
        ctx.fillSlots("serverid", ad.serverid)
        ctx.fillSlots("nickname", ad.nickname)
        ctx.fillSlots("advertised", " ".join(ad.advertised_addresses))
        ctx.fillSlots("connected", "?")
        when_s = time.strftime("%H:%M:%S %d-%b-%Y", time.localtime(ad.when))
        ctx.fillSlots("announced", when_s)
        ctx.fillSlots("version", ad.version)
        ctx.fillSlots("service_name", ad.service_name)
        return ctx.tag

    def data_subscribers(self, ctx, data):
        return self.introducer_service.get_subscribers()

    def render_subscriber_row(self, ctx, s):
        ctx.fillSlots("nickname", s.nickname)
        ctx.fillSlots("tubid", s.tubid)
        ctx.fillSlots("connected", s.remote_address)
        since_s = time.strftime("%H:%M:%S %d-%b-%Y", time.localtime(s.when))
        ctx.fillSlots("since", since_s)
        ctx.fillSlots("version", s.version)
        ctx.fillSlots("service_name", s.service_name)
        return ctx.tag

