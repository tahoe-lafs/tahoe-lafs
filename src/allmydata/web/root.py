import time, os

from twisted.internet import address
from twisted.web import http
from nevow import rend, url, tags as T
from nevow.inevow import IRequest
from nevow.static import File as nevow_File # TODO: merge with static.File?
from nevow.util import resource_filename

import allmydata # to display import path
from allmydata import get_package_versions_string
from allmydata.util import log
from allmydata.interfaces import IFileNode
from allmydata.web import filenode, directory, unlinked, status, operations
from allmydata.web import storage
from allmydata.web.common import abbreviate_size, getxmlfile, WebError, \
     get_arg, RenderMixin, get_format, get_mutable_type, TIME_FORMAT


class URIHandler(RenderMixin, rend.Page):
    # I live at /uri . There are several operations defined on /uri itself,
    # mostly involved with creation of unlinked files and directories.

    def __init__(self, client):
        rend.Page.__init__(self, client)
        self.client = client

    def render_GET(self, ctx):
        req = IRequest(ctx)
        uri = get_arg(req, "uri", None)
        if uri is None:
            raise WebError("GET /uri requires uri=")
        there = url.URL.fromContext(ctx)
        there = there.clear("uri")
        # I thought about escaping the childcap that we attach to the URL
        # here, but it seems that nevow does that for us.
        there = there.child(uri)
        return there

    def render_PUT(self, ctx):
        req = IRequest(ctx)
        # either "PUT /uri" to create an unlinked file, or
        # "PUT /uri?t=mkdir" to create an unlinked directory
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
        errmsg = ("/uri accepts only PUT, PUT?t=mkdir, POST?t=upload, "
                  "and POST?t=mkdir")
        raise WebError(errmsg, http.BAD_REQUEST)

    def render_POST(self, ctx):
        # "POST /uri?t=upload&file=newfile" to upload an
        # unlinked file or "POST /uri?t=mkdir" to create a
        # new directory
        req = IRequest(ctx)
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

    def childFactory(self, ctx, name):
        # 'name' is expected to be a URI
        try:
            node = self.client.create_node_from_uri(name)
            return directory.make_handler_for(node, self.client)
        except (TypeError, AssertionError):
            raise WebError("'%s' is not a valid file- or directory- cap"
                           % name)

class FileHandler(rend.Page):
    # I handle /file/$FILECAP[/IGNORED] , which provides a URL from which a
    # file can be downloaded correctly by tools like "wget".

    def __init__(self, client):
        rend.Page.__init__(self, client)
        self.client = client

    def childFactory(self, ctx, name):
        req = IRequest(ctx)
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

    def renderHTTP(self, ctx):
        raise WebError("/file must be followed by a file-cap and a name",
                       http.NOT_FOUND)

class IncidentReporter(RenderMixin, rend.Page):
    def render_POST(self, ctx):
        req = IRequest(ctx)
        log.msg(format="User reports incident through web page: %(details)s",
                details=get_arg(req, "details", ""),
                level=log.WEIRD, umid="LkD9Pw")
        req.setHeader("content-type", "text/plain")
        return "An incident report has been saved to logs/incidents/ in the node directory (or the configured 'incidents_dir')."

SPACE = u"\u00A0"*2

class Root(rend.Page):

    addSlash = True
    docFactory = getxmlfile("welcome.xhtml")

    def __init__(self, client, clock=None):
        rend.Page.__init__(self, client)
        self.client = client
        # If set, clock is a twisted.internet.task.Clock that the tests
        # use to test ophandle expiration.
        self.child_operations = operations.OphandleTable(clock)
        try:
            s = client.getServiceNamed("storage")
        except KeyError:
            s = None
        self.child_storage = storage.StorageStatus(s, self.client.nickname)

        self.child_uri = URIHandler(client)
        self.child_cap = URIHandler(client)

        self.child_file = FileHandler(client)
        self.child_named = FileHandler(client)
        self.child_status = status.Status(client.get_history())
        self.child_statistics = status.Statistics(client.stats_provider)
        static_dir = resource_filename("allmydata.web", "static")
        for filen in os.listdir(static_dir):
            self.putChild(filen, nevow_File(os.path.join(static_dir, filen)))

    def child_helper_status(self, ctx):
        # the Helper isn't attached until after the Tub starts, so this child
        # needs to created on each request
        return status.HelperStatus(self.client.helper)

    child_report_incident = IncidentReporter()
    #child_server # let's reserve this for storage-server-over-HTTP

    # FIXME: This code is duplicated in root.py and introweb.py.
    def data_rendered_at(self, ctx, data):
        return time.strftime(TIME_FORMAT, time.localtime())
    def data_version(self, ctx, data):
        return get_package_versions_string()
    def data_import_path(self, ctx, data):
        return str(allmydata)
    def render_my_nodeid(self, ctx, data):
        tubid_s = "TubID: "+self.client.get_long_tubid()
        return T.td(title=tubid_s)[self.client.get_long_nodeid()]
    def data_my_nickname(self, ctx, data):
        return self.client.nickname

    def render_services(self, ctx, data):
        ul = T.ul()
        try:
            ss = self.client.getServiceNamed("storage")
            stats = ss.get_stats()
            if stats["storage_server.accepting_immutable_shares"]:
                msg = "accepting new shares"
            else:
                msg = "not accepting new shares (read-only)"
            available = stats.get("storage_server.disk_avail")
            if available is not None:
                msg += ", %s available" % abbreviate_size(available)
            ul[T.li[T.a(href="storage")["Storage Server"], ": ", msg]]
        except KeyError:
            ul[T.li["Not running storage server"]]

        if self.client.helper:
            stats = self.client.helper.get_stats()
            active_uploads = stats["chk_upload_helper.active_uploads"]
            ul[T.li["Helper: %d active uploads" % (active_uploads,)]]
        else:
            ul[T.li["Not running helper"]]

        return ctx.tag[ul]

    def data_introducer_furl_prefix(self, ctx, data):
        ifurl = self.client.introducer_furl
        # trim off the secret swissnum
        (prefix, _, swissnum) = ifurl.rpartition("/")
        if not ifurl:
            return None
        if swissnum == "introducer":
            return ifurl
        else:
            return "%s/[censored]" % (prefix,)

    def data_introducer_description(self, ctx, data):
        if self.data_connected_to_introducer(ctx, data) == "no":
            return "Introducer not connected"
        return "Introducer"

    def data_connected_to_introducer(self, ctx, data):
        if self.client.connected_to_introducer():
            return "yes"
        return "no"

    def data_helper_furl_prefix(self, ctx, data):
        try:
            uploader = self.client.getServiceNamed("uploader")
        except KeyError:
            return None
        furl, connected = uploader.get_helper_info()
        if not furl:
            return None
        # trim off the secret swissnum
        (prefix, _, swissnum) = furl.rpartition("/")
        return "%s/[censored]" % (prefix,)

    def data_helper_description(self, ctx, data):
        if self.data_connected_to_helper(ctx, data) == "no":
            return "Helper not connected"
        return "Helper"

    def data_connected_to_helper(self, ctx, data):
        try:
            uploader = self.client.getServiceNamed("uploader")
        except KeyError:
            return "no" # we don't even have an Uploader
        furl, connected = uploader.get_helper_info()

        if furl is None:
            return "not-configured"
        if connected:
            return "yes"
        return "no"

    def data_known_storage_servers(self, ctx, data):
        sb = self.client.get_storage_broker()
        return len(sb.get_all_serverids())

    def data_connected_storage_servers(self, ctx, data):
        sb = self.client.get_storage_broker()
        return len(sb.get_connected_servers())

    def data_services(self, ctx, data):
        sb = self.client.get_storage_broker()
        return sorted(sb.get_known_servers(), key=lambda s: s.get_serverid())

    def render_service_row(self, ctx, server):
        nodeid = server.get_serverid()

        ctx.fillSlots("peerid", server.get_longname())
        ctx.fillSlots("nickname", server.get_nickname())
        rhost = server.get_remote_host()
        if rhost:
            if nodeid == self.client.nodeid:
                rhost_s = "(loopback)"
            elif isinstance(rhost, address.IPv4Address):
                rhost_s = "%s:%d" % (rhost.host, rhost.port)
            else:
                rhost_s = str(rhost)
            addr = rhost_s
            connected = "yes"
            since = server.get_last_connect_time()
        else:
            addr = "N/A"
            connected = "no"
            since = server.get_last_loss_time()
        announced = server.get_announcement_time()
        announcement = server.get_announcement()
        version = announcement["my-version"]
        service_name = announcement["service-name"]

        ctx.fillSlots("address", addr)
        ctx.fillSlots("connected", connected)
        ctx.fillSlots("connected-bool", bool(rhost))
        ctx.fillSlots("since", time.strftime(TIME_FORMAT,
                                             time.localtime(since)))
        ctx.fillSlots("announced", time.strftime(TIME_FORMAT,
                                                 time.localtime(announced)))
        ctx.fillSlots("version", version)
        ctx.fillSlots("service_name", service_name)

        return ctx.tag

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

    def render_incident_button(self, ctx, data):
        # this button triggers a foolscap-logging "incident"
        form = T.form(action="report_incident", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="report-incident"),
            "What went wrong?"+SPACE,
            T.input(type="text", name="details"), SPACE,
            T.input(type="submit", value=u"Save \u00BB"),
            ]]
        return T.div[form]
