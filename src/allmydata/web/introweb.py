"""
Ported to Python 3.
"""
import time
from twisted.web.template import Element, XMLFile, renderElement, renderer
from twisted.python.filepath import FilePath
import allmydata
from allmydata.util import idlib, jsonbytes as json
from allmydata.web.common import (
    render_time,
    MultiFormatResource,
    SlotsSequenceElement,
    add_static_children,
)


class IntroducerRoot(MultiFormatResource):
    """
    A ``Resource`` intended as the root resource for introducers.

    :param _IntroducerNode introducer_node: The introducer node to template
        information about.
    """

    def __init__(self, introducer_node):
        super(IntroducerRoot, self).__init__()
        self.introducer_node = introducer_node
        self.introducer_service = introducer_node.getServiceNamed("introducer")
        # necessary as a root Resource
        self.putChild(b"", self)
        add_static_children(self)

    def _create_element(self):
        """
        Create a ``IntroducerRootElement`` which can be flattened into an HTML
        response.
        """
        return IntroducerRootElement(
            self.introducer_node, self.introducer_service)

    def render_HTML(self, req):
        """
        Render an HTML template describing this introducer node.
        """
        return renderElement(req, self._create_element())

    def render_JSON(self, req):
        """
        Render JSON describing this introducer node.
        """
        res = {}

        counts = {}
        for s in self.introducer_service.get_subscribers():
            if s.service_name not in counts:
                counts[s.service_name] = 0
            counts[s.service_name] += 1
        res[u"subscription_summary"] = counts

        announcement_summary = {}
        for ad in self.introducer_service.get_announcements():
            service_name = ad.service_name
            if service_name not in announcement_summary:
                announcement_summary[service_name] = 0
            announcement_summary[service_name] += 1
        res[u"announcement_summary"] = announcement_summary

        return (json.dumps(res, indent=1) + "\n").encode("utf-8")


class IntroducerRootElement(Element):
    """
    An ``Element`` HTML template which can be flattened to describe this
    introducer node.

    :param _IntroducerNode introducer_node: The introducer node to describe.
    :param IntroducerService introducer_service: The introducer service created
        by the node.
    """

    loader = XMLFile(FilePath(__file__).sibling("introducer.xhtml"))

    def __init__(self, introducer_node, introducer_service):
        super(IntroducerRootElement, self).__init__()
        self.introducer_node = introducer_node
        self.introducer_service = introducer_service
        self.node_data_dict = {
            "my_nodeid": idlib.nodeid_b2a(self.introducer_node.nodeid),
            "version": allmydata.__full_version__,
            "import_path": str(allmydata).replace("/", "/ "),  # XXX kludge for wrapping
            "rendered_at": render_time(time.time()),
        }

    @renderer
    def node_data(self, req, tag):
        return tag.fillSlots(**self.node_data_dict)

    @renderer
    def announcement_summary(self, req, tag):
        services = {}
        for ad in self.introducer_service.get_announcements():
            if ad.service_name not in services:
                services[ad.service_name] = 0
            services[ad.service_name] += 1
        service_names = list(services.keys())
        service_names.sort()
        return u", ".join(u"{}: {}".format(service_name, services[service_name])
                          for service_name in service_names)

    @renderer
    def client_summary(self, req, tag):
        counts = {}
        for s in self.introducer_service.get_subscribers():
            if s.service_name not in counts:
                counts[s.service_name] = 0
            counts[s.service_name] += 1
        return u", ".join(u"{}: {}".format(name, counts[name])
                          for name in sorted(counts.keys()))

    @renderer
    def services(self, req, tag):
        services = self.introducer_service.get_announcements()
        services.sort(key=lambda ad: (ad.service_name, ad.nickname))
        services = [{
            "serverid": ad.serverid,
            "nickname": ad.nickname,
            "connection-hints":
                u"connection hints: " + u" ".join(ad.connection_hints),
            "connected": u"?",
            "announced": render_time(ad.when),
            "version": ad.version,
            "service_name": ad.service_name,
        } for ad in services]
        return SlotsSequenceElement(tag, services)

    @renderer
    def subscribers(self, req, tag):
        subscribers = [{
            "nickname": s.nickname,
            "tubid": s.tubid,
            "connected": s.remote_address,
            "since": render_time(s.when),
            "version": s.version,
            "service_name": s.service_name,
        } for s in self.introducer_service.get_subscribers()]
        return SlotsSequenceElement(tag, subscribers)
