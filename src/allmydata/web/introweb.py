
import time, os
from nevow import rend
from nevow.static import File as nevow_File
from nevow.util import resource_filename
from twisted.web.template import Element, renderer, renderElement, XMLFile
from twisted.python.filepath import FilePath
from twisted.web import resource
import allmydata
import json
from allmydata.version_checks import get_package_versions_string
from allmydata.util import idlib
from allmydata.web.common import (
    getxmlfile,
    render_time,
    MultiFormatPage,
    SlotsSequenceElement,
)


class IntroducerRoot(resource.Resource):
    def __init__(self, introducer_node):
        resource.Resource.__init__(self)
        # probably should fix this.. Resource isn't new-style in py2
        #super(IntroducerRoot, self).__init__()
        self.introducer_node = introducer_node
        self.introducer_service = introducer_node.getServiceNamed("introducer")
        # necessary as a root Resource
        self.putChild('', self)
        static_dir = resource_filename("allmydata.web", "static")
        for filen in os.listdir(static_dir):
            self.putChild(filen, nevow_File(os.path.join(static_dir, filen)))

    def render(self, request):
        return renderElement(request, IntroducerRootElement(
            self.introducer_node, self.introducer_service))

    def render_JSON(self, req):
        res = {}

        counts = {}
        for s in self.introducer_service.get_subscribers():
            if s.service_name not in counts:
                counts[s.service_name] = 0
            counts[s.service_name] += 1
        res["subscription_summary"] = counts

        announcement_summary = {}
        for ad in self.introducer_service.get_announcements():
            service_name = ad.service_name
            if service_name not in announcement_summary:
                announcement_summary[service_name] = 0
            announcement_summary[service_name] += 1
        res["announcement_summary"] = announcement_summary

        return json.dumps(res, indent=1) + "\n"


class IntroducerRootElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("introducer.xhtml"))

    def __init__(self, introducer_node, introducer_service):
        super(IntroducerRootElement, self).__init__()
        self.introducer_node = introducer_node
        self.introducer_service = introducer_service
        self.node_data_dict = {
            'my_nodeid': idlib.nodeid_b2a(self.introducer_node.nodeid),
            'version': get_package_versions_string(),
            'import_path': str(allmydata).replace("/", "/ "),  # XXX kludge for wrapping
            'rendered_at': render_time(time.time()),
        }

    @renderer
    def node_data(self, req, tag):
        return tag.fillSlots(**self.node_data_dict)

    # FIXME: This code is duplicated in root.py and introweb.py.
    def data_rendered_at(self, ctx, data):
        return render_time(time.time())
    def data_version(self, ctx, data):
        return get_package_versions_string()
    def data_import_path(self, ctx, data):
        return str(allmydata).replace("/", "/ ")
    def data_my_nodeid(self, ctx, data):
        return idlib.nodeid_b2a(self.introducer_node.nodeid)

    @renderer
    def announcement_summary(self, req, data):
        services = {}
        for ad in self.introducer_service.get_announcements():
            if ad.service_name not in services:
                services[ad.service_name] = 0
            services[ad.service_name] += 1
        service_names = services.keys()
        service_names.sort()
        return ", ".join(["%s: %d" % (service_name, services[service_name])
                          for service_name in service_names])

    @renderer
    def client_summary(self, req, data):
        counts = {}
        for s in self.introducer_service.get_subscribers():
            if s.service_name not in counts:
                counts[s.service_name] = 0
            counts[s.service_name] += 1
        return ", ".join([ "%s: %d" % (name, counts[name])
                           for name in sorted(counts.keys()) ] )

    @renderer
    def services(self, req, tag):
        services = self.introducer_service.get_announcements()
        services.sort(key=lambda ad: (ad.service_name, ad.nickname))
        services = [{
            "serverid": ad.serverid,
            "nickname": ad.nickname,
            "connection-hints":
                "connection hints: " + " ".join(ad.connection_hints),
            "connected": "?",
            "announced": render_time(ad.when),
            "version": ad.version,
            "service_name": ad.service_name,
        } for ad in services]
        return SlotsSequenceElement(tag, services)

    @renderer
    def subscribers(self, ctx, tag):
        subscribers = [{
            "nickname": s.nickname,
            "tubid": s.tubid,
            "connected": s.remote_address,
            "since": render_time(s.when),
            "version": s.version,
            "service_name": s.service_name,
        } for s in self.introducer_service.get_subscribers()]
        return SlotsSequenceElement(tag, subscribers)
