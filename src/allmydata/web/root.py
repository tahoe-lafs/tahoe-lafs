"""
Ported to Python 3.
"""
import time
from urllib.parse import quote as urlquote

from hyperlink import DecodedURL, URL
from twisted.web import (
    http,
    resource,
)
from twisted.web.util import redirectTo, Redirect
from twisted.python.filepath import FilePath
from twisted.web.template import (
    Element,
    XMLFile,
    renderer,
    renderElement,
    tags,
)

import allmydata # to display import path
from allmydata.util import log, jsonbytes as json
from allmydata.interfaces import IFileNode
from allmydata.web import (
    filenode,
    directory,
    unlinked,
    status,
)
from allmydata.web import storage
from allmydata.web.common import (
    abbreviate_size,
    WebError,
    exception_to_child,
    get_arg,
    MultiFormatResource,
    SlotsSequenceElement,
    get_format,
    get_mutable_type,
    render_exception,
    render_time_delta,
    render_time,
    render_time_attr,
    add_static_children,
)
from allmydata.web.private import (
    create_private_tree,
)
from allmydata import uri

class URIHandler(resource.Resource, object):
    """
    I live at /uri . There are several operations defined on /uri itself,
    mostly involved with creation of unlinked files and directories.
    """

    def __init__(self, client):
        super(URIHandler, self).__init__()
        self.client = client

    @render_exception
    def render_GET(self, req):
        """
        Historically, accessing this via "GET /uri?uri=<capabilitiy>"
        was/is a feature -- which simply redirects to the more-common
        "GET /uri/<capability>" with any other query args
        preserved. New code should use "/uri/<cap>"
        """
        uri_arg = req.args.get(b"uri", [None])[0]
        if uri_arg is None:
            raise WebError("GET /uri requires uri=")

        # shennanigans like putting "%2F" or just "/" itself, or ../
        # etc in the <cap> might be a vector for weirdness so we
        # validate that this is a valid capability before proceeding.
        cap = uri.from_string(uri_arg)
        if isinstance(cap, uri.UnknownURI):
            raise WebError("Invalid capability")

        # so, using URL.from_text(req.uri) isn't going to work because
        # it seems Nevow was creating absolute URLs including
        # host/port whereas req.uri is absolute (but lacks host/port)
        redir_uri = URL.from_text(req.prePathURL().decode('utf8'))
        redir_uri = redir_uri.child(urlquote(uri_arg))
        # add back all the query args that AREN'T "?uri="
        for k, values in req.args.items():
            if k != b"uri":
                for v in values:
                    redir_uri = redir_uri.add(k.decode('utf8'), v.decode('utf8'))
        return redirectTo(redir_uri.to_text().encode('utf8'), req)

    @render_exception
    def render_PUT(self, req):
        """
        either "PUT /uri" to create an unlinked file, or
        "PUT /uri?t=mkdir" to create an unlinked directory
        """
        t = str(get_arg(req, "t", "").strip(), "utf-8")
        if t == "":
            file_format = get_format(req, "CHK")
            mutable_type = get_mutable_type(file_format)
            if mutable_type is not None:
                return unlinked.PUTUnlinkedSSK(req, self.client, mutable_type)
            else:
                return unlinked.PUTUnlinkedCHK(req, self.client)
        if t == "mkdir":
            return unlinked.PUTUnlinkedCreateDirectory(req, self.client)
        errmsg = (
            "/uri accepts only PUT, PUT?t=mkdir, POST?t=upload, "
            "and POST?t=mkdir"
        )
        raise WebError(errmsg, http.BAD_REQUEST)

    @render_exception
    def render_POST(self, req):
        """
        "POST /uri?t=upload&file=newfile" to upload an
        unlinked file or "POST /uri?t=mkdir" to create a
        new directory
        """
        t = str(get_arg(req, "t", "").strip(), "ascii")
        if t in ("", "upload"):
            file_format = get_format(req)
            mutable_type = get_mutable_type(file_format)
            if mutable_type is not None:
                return unlinked.POSTUnlinkedSSK(req, self.client, mutable_type)
            else:
                return unlinked.POSTUnlinkedCHK(req, self.client)
        if t == "mkdir":
            return unlinked.POSTUnlinkedCreateDirectory(req, self.client)
        elif t == "mkdir-with-children":
            return unlinked.POSTUnlinkedCreateDirectoryWithChildren(req,
                                                                    self.client)
        elif t == "mkdir-immutable":
            return unlinked.POSTUnlinkedCreateImmutableDirectory(req,
                                                                 self.client)
        errmsg = ("/uri accepts only PUT, PUT?t=mkdir, POST?t=upload, "
                  "and POST?t=mkdir")
        raise WebError(errmsg, http.BAD_REQUEST)

    @exception_to_child
    def getChild(self, name, req):
        """
        Most requests look like /uri/<cap> so this fetches the capability
        and creates and appropriate handler (depending on the kind of
        capability it was passed).
        """
        # this is in case a URI like "/uri/?uri=<valid capability>" is
        # passed -- we re-direct to the non-trailing-slash version so
        # that there is just one valid URI for "uri" resource.
        if not name:
            u = DecodedURL.from_text(req.uri.decode('utf8'))
            u = u.replace(
                path=(s for s in u.path if s),  # remove empty segments
            )
            return Redirect(u.to_uri().to_text().encode('utf8'))
        try:
            node = self.client.create_node_from_uri(name)
            return directory.make_handler_for(node, self.client)
        except (TypeError, AssertionError) as e:
            log.msg(format="Failed to parse cap, perhaps due to bug: %(e)s",
                    e=e, level=log.WEIRD)
            raise WebError(
                "'{}' is not a valid file- or directory- cap".format(name)
            )


class FileHandler(resource.Resource, object):
    # I handle /file/$FILECAP[/IGNORED] , which provides a URL from which a
    # file can be downloaded correctly by tools like "wget".

    def __init__(self, client):
        super(FileHandler, self).__init__()
        self.client = client

    @exception_to_child
    def getChild(self, name, req):
        if req.method not in (b"GET", b"HEAD"):
            raise WebError("/file can only be used with GET or HEAD")
        # 'name' must be a file URI
        try:
            node = self.client.create_node_from_uri(name)
        except (TypeError, AssertionError):
            # I think this can no longer be reached
            raise WebError("%r is not a valid file- or directory- cap"
                           % name)
        if not IFileNode.providedBy(node):
            raise WebError("%r is not a file-cap" % name)
        return filenode.FileNodeDownloadHandler(self.client, node)

    @render_exception
    def render_GET(self, req):
        raise WebError("/file must be followed by a file-cap and a name",
                       http.NOT_FOUND)

class IncidentReporter(MultiFormatResource):
    """Handler for /report_incident POST request"""

    @render_exception
    def render(self, req):
        if req.method != b"POST":
            raise WebError("/report_incident can only be used with POST")

        log.msg(format="User reports incident through web page: %(details)s",
                details=get_arg(req, "details", ""),
                level=log.WEIRD, umid="LkD9Pw")
        req.setHeader("content-type", "text/plain; charset=UTF-8")
        return b"An incident report has been saved to logs/incidents/ in the node directory."

SPACE = u"\u00A0"*2


class Root(MultiFormatResource):

    addSlash = True

    def __init__(self, client, clock=None, now_fn=None):
        """
        Render root page ("/") of the URI.

        :client allmydata.client._Client: a stats provider.
        :clock: unused here.
        :now_fn: a function that returns current time.

        """
        super(Root, self).__init__()
        self._client = client
        self._now_fn = now_fn

        self.putChild(b"uri", URIHandler(client))
        self.putChild(b"cap", URIHandler(client))

        # Handler for everything beneath "/private", an area of the resource
        # hierarchy which is only accessible with the private per-node API
        # auth token.
        self.putChild(b"private", create_private_tree(client.get_auth_token))

        self.putChild(b"file", FileHandler(client))
        self.putChild(b"named", FileHandler(client))
        self.putChild(b"status", status.Status(client.get_history()))
        self.putChild(b"statistics", status.Statistics(client.stats_provider))
        self.putChild(b"report_incident", IncidentReporter())

        add_static_children(self)

    @exception_to_child
    def getChild(self, path, request):
        if not path:
            # Render "/" path.
            return self
        if path == b"helper_status":
            # the Helper isn't attached until after the Tub starts, so this child
            # needs to created on each request
            return status.HelperStatus(self._client.helper)
        if path == b"storage":
            # Storage isn't initialized until after the web hierarchy is
            # constructed so this child needs to be created later than
            # `__init__`.
            try:
                storage_server = self._client.getServiceNamed("storage")
            except KeyError:
                storage_server = None
            return storage.StorageStatus(storage_server, self._client.nickname)

    @render_exception
    def render_HTML(self, req):
        return renderElement(req, RootElement(self._client, self._now_fn))

    @render_exception
    def render_JSON(self, req):
        req.setHeader("content-type", "application/json; charset=utf-8")
        intro_summaries = [s.summary for s in self._client.introducer_connection_statuses()]
        sb = self._client.get_storage_broker()
        servers = self._describe_known_servers(sb)
        result = {
            "introducers": {
                "statuses": intro_summaries,
            },
            "servers": servers
        }
        return json.dumps(result, indent=1) + "\n"

    def _describe_known_servers(self, broker):
        return list(
            self._describe_server(server)
            for server
            in broker.get_known_servers()
        )

    def _describe_server(self, server):
        status = server.get_connection_status()
        description = {
            u"nodeid": server.get_serverid(),
            u"connection_status": status.summary,
            u"available_space": server.get_available_space(),
            u"nickname": server.get_nickname(),
            u"version": None,
            u"last_received_data": status.last_received_time,
        }
        version = server.get_version()
        if version is not None:
            description[u"version"] = version[b"application-version"]

        return description

class RootElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("welcome.xhtml"))

    def __init__(self, client, now_fn):
        super(RootElement, self).__init__()
        self._client = client
        self._now_fn = now_fn

    _connectedalts = {
        "not-configured": "Not Configured",
        "yes": "Connected",
        "no": "Disconnected",
        }

    @renderer
    def my_nodeid(self, req, tag):
        tubid_s = "TubID: "+self._client.get_long_tubid()
        return tags.td(self._client.get_long_nodeid(), title=tubid_s)

    @renderer
    def my_nickname(self, req, tag):
        return tag(self._client.nickname)

    def _connected_introducers(self):
        return len([1 for cs in self._client.introducer_connection_statuses()
                    if cs.connected])

    @renderer
    def connected_introducers(self, req, tag):
        return tag(str(self._connected_introducers()))

    @renderer
    def connected_to_at_least_one_introducer(self, req, tag):
        if self._connected_introducers():
            return "yes"
        return "no"

    @renderer
    def connected_to_at_least_one_introducer_alt(self, req, tag):
        state = self.connected_to_at_least_one_introducer(req, tag)
        return self._connectedalts.get(state)

    @renderer
    def services(self, req, tag):
        ul = tags.ul()
        try:
            ss = self._client.getServiceNamed("storage")
            stats = ss.get_stats()
            if stats["storage_server.accepting_immutable_shares"]:
                msg = "accepting new shares"
            else:
                msg = "not accepting new shares (read-only)"
            available = stats.get("storage_server.disk_avail")
            if available is not None:
                msg += ", %s available" % abbreviate_size(available)
            ul(tags.li(tags.a("Storage Server", href="storage"), ": ", msg))
        except KeyError:
            ul(tags.li("Not running storage server"))

        if self._client.helper:
            stats = self._client.helper.get_stats()
            active_uploads = stats["chk_upload_helper.active_uploads"]
            ul(tags.li("Helper: %d active uploads" % (active_uploads,)))
        else:
            ul(tags.li("Not running helper"))

        return tag(ul)

    @renderer
    def introducer_description(self, req, tag):
        connected_count = self._connected_introducers()
        if connected_count == 0:
            return tag("No introducers connected")
        elif connected_count == 1:
            return tag("1 introducer connected")
        else:
            return tag("%s introducers connected" % (connected_count,))

    @renderer
    def total_introducers(self, req, tag):
        return tag(str(len(self._get_introducers())))

    # In case we configure multiple introducers
    @renderer
    def introducers(self, req, tag):
        ix = self._get_introducers()
        if not ix:
            return tag("No introducers")
        return tag

    def _get_introducers(self):
        return self._client.introducer_connection_statuses()

    @renderer
    def helper_furl_prefix(self, req, tag):
        try:
            uploader = self._client.getServiceNamed("uploader")
        except KeyError:
            return tag("None")
        furl, connected = uploader.get_helper_info()
        if not furl:
            return tag("None")
        # trim off the secret swissnum
        (prefix, _, swissnum) = furl.rpartition("/")
        return tag("%s/[censored]" % (prefix,))

    def _connected_to_helper(self):
        try:
            uploader = self._client.getServiceNamed("uploader")
        except KeyError:
            return "no" # we don't even have an Uploader
        furl, connected = uploader.get_helper_info()

        if furl is None:
            return "not-configured"
        if connected:
            return "yes"
        return "no"

    @renderer
    def helper_description(self, req, tag):
        if self._connected_to_helper() == "no":
            return tag("Helper not connected")
        return tag("Helper")

    @renderer
    def connected_to_helper(self, req, tag):
        return tag(self._connected_to_helper())

    @renderer
    def connected_to_helper_alt(self, req, tag):
        return tag(self._connectedalts.get(self._connected_to_helper()))

    @renderer
    def known_storage_servers(self, req, tag):
        sb = self._client.get_storage_broker()
        return tag(str(len(sb.get_all_serverids())))

    @renderer
    def connected_storage_servers(self, req, tag):
        sb = self._client.get_storage_broker()
        return tag(str(len(sb.get_connected_servers())))

    @renderer
    def services_table(self, req, tag):
        rows = [ self._describe_server_and_connection(server)
                 for server in self._services() ]
        return SlotsSequenceElement(tag, rows)

    @renderer
    def introducers_table(self, req, tag):
        rows = [ self._describe_connection_status(cs)
                 for cs in self._get_introducers() ]
        return SlotsSequenceElement(tag, rows)

    def _services(self):
        sb = self._client.get_storage_broker()
        return sorted(sb.get_known_servers(), key=lambda s: s.get_serverid())

    @staticmethod
    def _describe_server(server):
        """Return a dict containing server stats."""
        peerid = server.get_longname()
        nickname =  server.get_nickname()
        version = server.get_announcement().get("my-version", "")

        space = server.get_available_space()
        if space is not None:
            available_space = abbreviate_size(space)
        else:
            available_space = "N/A"

        return {
            "peerid": peerid,
            "nickname": nickname,
            "version": version,
            "available_space": available_space,
        }

    def _describe_server_and_connection(self, server):
        """Return a dict containing both server and connection stats."""
        srvstat = self._describe_server(server)
        cs = server.get_connection_status()
        constat = self._describe_connection_status(cs)
        return dict(list(srvstat.items()) + list(constat.items()))

    def _describe_connection_status(self, cs):
        """Return a dict containing some connection stats."""
        others = cs.non_connected_statuses

        if cs.connected:
            summary = cs.summary
            if others:
                hints = "\n".join(["* %s: %s\n" % (which, others[which])
                                for which in sorted(others)])
                details = "Other hints:\n" + hints
            else:
                details = "(no other hints)"
        else:
            details = tags.ul()
            for which in sorted(others):
                details(tags.li("%s: %s" % (which, others[which])))
            summary = [cs.summary, details]

        connected = "yes" if cs.connected else "no"
        connected_alt = self._connectedalts[connected]

        since = cs.last_connection_time

        if since is not None:
            service_connection_status_rel_time = render_time_delta(since, self._now_fn())
            service_connection_status_abs_time = render_time_attr(since)
        else:
            service_connection_status_rel_time = "N/A"
            service_connection_status_abs_time = "N/A"

        last_received_data_time = cs.last_received_time

        if last_received_data_time is not None:
            last_received_data_abs_time = render_time_attr(last_received_data_time)
            last_received_data_rel_time = render_time_delta(last_received_data_time, self._now_fn())
        else:
            last_received_data_abs_time = "N/A"
            last_received_data_rel_time = "N/A"

        return {
            "summary": summary,
            "details": details,
            "service_connection_status": connected,
            "service_connection_status_alt": connected_alt,
            "service_connection_status_abs_time": service_connection_status_abs_time,
            "service_connection_status_rel_time": service_connection_status_rel_time,
            "last_received_data_abs_time": last_received_data_abs_time,
            "last_received_data_rel_time": last_received_data_rel_time,
        }

    @renderer
    def incident_button(self, req, tag):
        # this button triggers a foolscap-logging "incident"
        form = tags.form(
            tags.fieldset(
                tags.input(type="hidden", name="t", value="report-incident"),
                "What went wrong?"+SPACE,
                tags.input(type="text", name="details"), SPACE,
                tags.input(type="submit", value=u"Save \u00BB"),
            ),
            action="report_incident",
            method="post",
            enctype="multipart/form-data"
        )
        return tags.div(form)

    @renderer
    def rendered_at(self, req, tag):
        return tag(render_time(time.time()))

    @renderer
    def version(self, req, tag):
        return tag(allmydata.__full_version__)

    @renderer
    def import_path(self, req, tag):
        return tag(str(allmydata))
