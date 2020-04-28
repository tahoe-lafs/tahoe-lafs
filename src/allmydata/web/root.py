import os
import time
import json
import urllib

from twisted.web import (
    http,
    resource,
)
from twisted.web.util import redirectTo

from hyperlink import DecodedURL, URL

from nevow import rend, tags as T
from nevow.inevow import IRequest
from twisted.web import static
from nevow.util import resource_filename

from twisted.python.filepath import FilePath
from twisted.web.template import (
    Element,
    XMLFile,
    renderer,
    renderElement,
    tags
)

import allmydata # to display import path
from allmydata.version_checks import get_package_versions_string
from allmydata.util import log
from allmydata.interfaces import IFileNode
from allmydata.web import filenode, directory, unlinked, status
from allmydata.web import storage
from allmydata.web.common import (
    abbreviate_size,
    getxmlfile,
    WebError,
    get_arg,
    MultiFormatPage,
    MultiFormatResource,
    get_format,
    get_mutable_type,
    render_time_delta,
    render_time,
    render_time_attr,
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
        redir_uri = redir_uri.child(urllib.quote(uri_arg).decode('utf8'))
        # add back all the query args that AREN'T "?uri="
        for k, values in req.args.items():
            if k != b"uri":
                for v in values:
                    redir_uri = redir_uri.add(k.decode('utf8'), v.decode('utf8'))
        return redirectTo(redir_uri.to_text().encode('utf8'), req)

    def render_PUT(self, req):
        """
        either "PUT /uri" to create an unlinked file, or
        "PUT /uri?t=mkdir" to create an unlinked directory
        """
        t = get_arg(req, "t", "").strip()
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

    def render_POST(self, req):
        """
        "POST /uri?t=upload&file=newfile" to upload an
        unlinked file or "POST /uri?t=mkdir" to create a
        new directory
        """
        t = get_arg(req, "t", "").strip()
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

    def getChild(self, name, req):
        """
        Most requests look like /uri/<cap> so this fetches the capability
        and creates and appropriate handler (depending on the kind of
        capability it was passed).
        """
        # this is in case a URI like "/uri/?cap=<valid capability>" is
        # passed -- we re-direct to the non-trailing-slash version so
        # that there is just one valid URI for "uri" resource.
        if not name:
            u = DecodedURL.from_text(req.uri.decode('utf8'))
            u = u.replace(
                path=(s for s in u.path if s),  # remove empty segments
            )
            return redirectTo(u.to_uri().to_text().encode('utf8'), req)
        try:
            node = self.client.create_node_from_uri(name)
            return directory.make_handler_for(node, self.client)
        except (TypeError, AssertionError):
            raise WebError(
                "'{}' is not a valid file- or directory- cap".format(name)
            )


class FileHandler(resource.Resource, object):
    # I handle /file/$FILECAP[/IGNORED] , which provides a URL from which a
    # file can be downloaded correctly by tools like "wget".

    def __init__(self, client):
        super(FileHandler, self).__init__()
        self.client = client

    def getChild(self, name, req):
        if req.method not in ("GET", "HEAD"):
            raise WebError("/file can only be used with GET or HEAD")
        # 'name' must be a file URI
        try:
            node = self.client.create_node_from_uri(name)
        except (TypeError, AssertionError):
            # I think this can no longer be reached
            raise WebError("'%s' is not a valid file- or directory- cap"
                           % name)
        if not IFileNode.providedBy(node):
            raise WebError("'%s' is not a file-cap" % name)
        return filenode.FileNodeDownloadHandler(self.client, node)

    def render_GET(self, ctx):
        raise WebError("/file must be followed by a file-cap and a name",
                       http.NOT_FOUND)

class IncidentReporter(MultiFormatResource):
    """Handler for /report_incident POST request"""

    def render(self, req):
        if req.method != "POST":
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
        super(Root, self).__init__()
        self.client = client
        self.now_fn = now_fn

        self.putChild("uri", URIHandler(client))
        self.putChild("cap", URIHandler(client))

        # Handler for everything beneath "/private", an area of the resource
        # hierarchy which is only accessible with the private per-node API
        # auth token.
        self.putChild("private", create_private_tree(client.get_auth_token))

        self.putChild("file", FileHandler(client))
        self.putChild("named", FileHandler(client))
        self.putChild("status", status.Status(client.get_history()))
        self.putChild("statistics", status.Statistics(client.stats_provider))
        static_dir = resource_filename("allmydata.web", "static")
        for filen in os.listdir(static_dir):
            self.putChild(filen, static.File(os.path.join(static_dir, filen)))

        self.putChild("report_incident", IncidentReporter())

    # until we get rid of nevow.Page in favour of twisted.web.resource
    # we can't use getChild() -- but we CAN use childFactory or
    # override locatechild
    def childFactory(self, ctx, name):
        request = IRequest(ctx)
        return self.getChild(name, request)


    def getChild(self, path, request):
        if path == "helper_status":
            # the Helper isn't attached until after the Tub starts, so this child
            # needs to created on each request
            return status.HelperStatus(self.client.helper)
        if path == "storage":
            # Storage isn't initialized until after the web hierarchy is
            # constructed so this child needs to be created later than
            # `__init__`.
            try:
                storage_server = self.client.getServiceNamed("storage")
            except KeyError:
                storage_server = None
            return storage.StorageStatus(storage_server, self.client.nickname)
        if not path:
            # Render "/" path.
            return self

    # FIXME: This code is duplicated in root.py and introweb.py.
    def data_rendered_at(self, ctx, data):
        return render_time(time.time())

    def data_version(self, ctx, data):
        return get_package_versions_string()

    def data_import_path(self, ctx, data):
        return str(allmydata)

    def render_HTML(self, req):
        return renderElement(req, RootElement(self.client))

    def render_JSON(self, req):
        req.setHeader("content-type", "application/json; charset=utf-8")
        intro_summaries = [s.summary for s in self.client.introducer_connection_statuses()]
        sb = self.client.get_storage_broker()
        servers = self._describe_known_servers(sb)
        result = {
            "introducers": {
                "statuses": intro_summaries,
            },
            "servers": servers
        }
        return json.dumps(result, indent=1) + "\n"


    def _describe_known_servers(self, broker):
        return sorted(list(
            self._describe_server(server)
            for server
            in broker.get_known_servers()
        ))


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
            description[u"version"] = version["application-version"]

        return description

class RootElement(Element):

    loader = XMLFile(FilePath(__file__).sibling("welcome.xhtml"))

    def __init__(self, client):
        super(RootElement, self).__init__()
        self._client = client

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
            ul(tags.li(tags.a("Storage Server", ": ", msg, href="storage")))
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

    def _render_connection_status(self, tag, cs):
        connected = "yes" if cs.connected else "no"
        tag.fillSlots("service_connection_status", connected)
        tag.fillSlots("service_connection_status_alt",
                      self._connectedalts[connected])

        since = cs.last_connection_time
        tag.fillSlots("service_connection_status_rel_time",
                      render_time_delta(since, self.now_fn())
                      if since is not None
                      else "N/A")
        tag.fillSlots("service_connection_status_abs_time",
                      render_time_attr(since)
                      if since is not None
                      else "N/A")

        last_received_data_time = cs.last_received_time
        tag.fillSlots("last_received_data_abs_time",
                      render_time_attr(last_received_data_time)
                      if last_received_data_time is not None
                      else "N/A")
        tag.fillSlots("last_received_data_rel_time",
                      render_time_delta(last_received_data_time,
                                        self.now_fn())
                      if last_received_data_time is not None
                      else "N/A")

        others = cs.non_connected_statuses
        if cs.connected:
            tag.fillSlots("summary", cs.summary)
            if others:
                details = "\n".join(["* %s: %s\n" % (which, others[which])
                                     for which in sorted(others)])
                tag.fillSlots("details", "Other hints:\n" + details)
            else:
                tag.fillSlots("details", "(no other hints)")
        else:
            details = tags.ul()
            for which in sorted(others):
                details[tags.li("%s: %s" % (which, others[which]))]
            tag.fillSlots("summary", [cs.summary, details])
            tag.fillSlots("details", "")

    @renderer
    def introducers_row(self, req, tag):
        for cs in self._get_introducers():
            self._render_connection_status(tag, cs)
        return tag

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

    def _services(self):
        sb = self._client.get_storage_broker()
        return sorted(sb.get_known_servers(), key=lambda s: s.get_serverid())

    @renderer
    def service_row(self, req, tag):
        servers = self._services()

        # FIXME: handle empty list of servers in a better manner.
        if not servers:
            tag.fillSlots(peerid="",
                          nickname="",
                          service_connection_status="",
                          service_connection_status_alt="",
                          details="",
                          summary="",
                          service_connection_status_abs_time="",
                          service_connection_status_rel_time="",
                          last_received_data_abs_time="",
                          last_received_data_rel_time="",
                          version="",
                          available_space="")

        for server in servers:
            cs = server.get_connection_status()
            self._render_connection_status(tag, cs)

            tag.fillSlots("peerid", server.get_longname())
            tag.fillSlots("nickname", server.get_nickname())

            announcement = server.get_announcement()
            version = announcement.get("my-version", "")
            available_space = server.get_available_space()
            if available_space is None:
                available_space = "N/A"
            else:
                available_space = abbreviate_size(available_space)
                tag.fillSlots("version", version)
                tag.fillSlots("available_space", available_space)

        return tag

    def render_download_form(self, ctx, data):
        # this is a form where users can download files by URI
        form = T.form(action="uri", method="get",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Download a file"],
            T.div["Tahoe-URI to download:"+SPACE,
                  T.input(type="text", name="uri")],
            T.div["Filename to download as:"+SPACE,
                  T.input(type="text", name="filename")],
            T.input(type="submit", value="Download!"),
            ]]
        return T.div[form]

    def render_view_form(self, ctx, data):
        # this is a form where users can download files by URI, or jump to a
        # named directory
        form = T.form(action="uri", method="get",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["View a file or directory"],
            "Tahoe-URI to view:"+SPACE,
            T.input(type="text", name="uri"), SPACE*2,
            T.input(type="submit", value="View!"),
            ]]
        return T.div[form]

    def render_upload_form(self, ctx, data):
        # This is a form where users can upload unlinked files.
        # Users can choose immutable, SDMF, or MDMF from a radio button.

        upload_chk  = T.input(type='radio', name='format',
                              value='chk', id='upload-chk',
                              checked='checked')
        upload_sdmf = T.input(type='radio', name='format',
                              value='sdmf', id='upload-sdmf')
        upload_mdmf = T.input(type='radio', name='format',
                              value='mdmf', id='upload-mdmf')

        form = T.form(action="uri", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Upload a file"],
            T.div["Choose a file:"+SPACE,
                  T.input(type="file", name="file", class_="freeform-input-file")],
            T.input(type="hidden", name="t", value="upload"),
            T.div[upload_chk,  T.label(for_="upload-chk") [" Immutable"],           SPACE,
                  upload_sdmf, T.label(for_="upload-sdmf")[" SDMF"],                SPACE,
                  upload_mdmf, T.label(for_="upload-mdmf")[" MDMF (experimental)"], SPACE*2,
                  T.input(type="submit", value="Upload!")],
            ]]
        return T.div[form]

    def render_mkdir_form(self, ctx, data):
        # This is a form where users can create new directories.
        # Users can choose SDMF or MDMF from a radio button.

        mkdir_sdmf = T.input(type='radio', name='format',
                             value='sdmf', id='mkdir-sdmf',
                             checked='checked')
        mkdir_mdmf = T.input(type='radio', name='format',
                             value='mdmf', id='mkdir-mdmf')

        form = T.form(action="uri", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Create a directory"],
            mkdir_sdmf, T.label(for_='mkdir-sdmf')[" SDMF"],                SPACE,
            mkdir_mdmf, T.label(for_='mkdir-mdmf')[" MDMF (experimental)"], SPACE*2,
            T.input(type="hidden", name="t", value="mkdir"),
            T.input(type="hidden", name="redirect_to_result", value="true"),
            T.input(type="submit", value="Create a directory"),
            ]]
        return T.div[form]

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
