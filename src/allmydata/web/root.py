
import time

from twisted.internet import address
from twisted.web import http
from nevow import rend, url, tags as T
from nevow.inevow import IRequest
from nevow.static import File as nevow_File # TODO: merge with static.File?
from nevow.util import resource_filename
from formless import webform

import allmydata # to display import path
from allmydata import get_package_versions_string
from allmydata import provisioning
from allmydata.util import idlib, log
from allmydata.interfaces import IFileNode
from allmydata.web import filenode, directory, unlinked, status
from allmydata.web.common import abbreviate_size, IClient, getxmlfile, \
     WebError, get_arg, RenderMixin



class URIHandler(RenderMixin, rend.Page):
    # I live at /uri . There are several operations defined on /uri itself,
    # mostly involved with creation of unlinked files and directories.

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
            mutable = bool(get_arg(req, "mutable", "").strip())
            if mutable:
                return unlinked.PUTUnlinkedSSK(ctx)
            else:
                return unlinked.PUTUnlinkedCHK(ctx)
        if t == "mkdir":
            return unlinked.PUTUnlinkedCreateDirectory(ctx)
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
            mutable = bool(get_arg(req, "mutable", "").strip())
            if mutable:
                return unlinked.POSTUnlinkedSSK(ctx)
            else:
                return unlinked.POSTUnlinkedCHK(ctx)
        if t == "mkdir":
            return unlinked.POSTUnlinkedCreateDirectory(ctx)
        errmsg = ("/uri accepts only PUT, PUT?t=mkdir, POST?t=upload, "
                  "and POST?t=mkdir")
        raise WebError(errmsg, http.BAD_REQUEST)

    def childFactory(self, ctx, name):
        # 'name' is expected to be a URI
        client = IClient(ctx)
        try:
            node = client.create_node_from_uri(name)
            return directory.make_handler_for(node)
        except (TypeError, AssertionError):
            raise WebError("'%s' is not a valid file- or directory- cap"
                           % name)

class FileHandler(rend.Page):
    # I handle /file/$FILECAP[/IGNORED] , which provides a URL from which a
    # file can be downloaded correctly by tools like "wget".

    def childFactory(self, ctx, name):
        req = IRequest(ctx)
        if req.method not in ("GET", "HEAD"):
            raise WebError("/file can only be used with GET or HEAD")
        # 'name' must be a file URI
        client = IClient(ctx)
        try:
            node = client.create_node_from_uri(name)
        except (TypeError, AssertionError):
            raise WebError("'%s' is not a valid file- or directory- cap"
                           % name)
        if not IFileNode.providedBy(node):
            raise WebError("'%s' is not a file-cap" % name)
        return filenode.FileNodeDownloadHandler(node)

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
        return "Thank you for your report!"

class Root(rend.Page):

    addSlash = True
    docFactory = getxmlfile("welcome.xhtml")

    child_uri = URIHandler()
    child_cap = URIHandler()
    child_file = FileHandler()
    child_named = FileHandler()

    child_webform_css = webform.defaultCSS
    child_tahoe_css = nevow_File(resource_filename('allmydata.web', 'tahoe.css'))

    child_provisioning = provisioning.ProvisioningTool()
    child_status = status.Status()
    child_helper_status = status.HelperStatus()
    child_statistics = status.Statistics()

    child_report_incident = IncidentReporter()

    def data_version(self, ctx, data):
        return get_package_versions_string()
    def data_import_path(self, ctx, data):
        return str(allmydata)
    def data_my_nodeid(self, ctx, data):
        return idlib.nodeid_b2a(IClient(ctx).nodeid)
    def data_my_nickname(self, ctx, data):
        return IClient(ctx).nickname

    def render_services(self, ctx, data):
        ul = T.ul()
        client = IClient(ctx)
        try:
            ss = client.getServiceNamed("storage")
            allocated_s = abbreviate_size(ss.allocated_size())
            allocated = "about %s allocated" % allocated_s
            sizelimit = "no size limit"
            if ss.sizelimit is not None:
                sizelimit = "size limit is %s" % abbreviate_size(ss.sizelimit)
            ul[T.li["Storage Server: %s, %s" % (allocated, sizelimit)]]
        except KeyError:
            ul[T.li["Not running storage server"]]

        try:
            h = client.getServiceNamed("helper")
            stats = h.get_stats()
            active_uploads = stats["chk_upload_helper.active_uploads"]
            ul[T.li["Helper: %d active uploads" % (active_uploads,)]]
        except KeyError:
            ul[T.li["Not running helper"]]

        return ctx.tag[ul]

    def data_introducer_furl(self, ctx, data):
        return IClient(ctx).introducer_furl
    def data_connected_to_introducer(self, ctx, data):
        if IClient(ctx).connected_to_introducer():
            return "yes"
        return "no"

    def data_helper_furl(self, ctx, data):
        try:
            uploader = IClient(ctx).getServiceNamed("uploader")
        except KeyError:
            return None
        furl, connected = uploader.get_helper_info()
        return furl
    def data_connected_to_helper(self, ctx, data):
        try:
            uploader = IClient(ctx).getServiceNamed("uploader")
        except KeyError:
            return "no" # we don't even have an Uploader
        furl, connected = uploader.get_helper_info()
        if connected:
            return "yes"
        return "no"

    def data_known_storage_servers(self, ctx, data):
        ic = IClient(ctx).introducer_client
        servers = [c
                   for c in ic.get_all_connectors().values()
                   if c.service_name == "storage"]
        return len(servers)

    def data_connected_storage_servers(self, ctx, data):
        ic = IClient(ctx).introducer_client
        return len(ic.get_all_connections_for("storage"))

    def data_services(self, ctx, data):
        ic = IClient(ctx).introducer_client
        c = [ (service_name, nodeid, rsc)
              for (nodeid, service_name), rsc
              in ic.get_all_connectors().items() ]
        c.sort()
        return c

    def render_service_row(self, ctx, data):
        (service_name, nodeid, rsc) = data
        ctx.fillSlots("peerid", idlib.nodeid_b2a(nodeid))
        ctx.fillSlots("nickname", rsc.nickname)
        if rsc.rref:
            rhost = rsc.remote_host
            if nodeid == IClient(ctx).nodeid:
                rhost_s = "(loopback)"
            elif isinstance(rhost, address.IPv4Address):
                rhost_s = "%s:%d" % (rhost.host, rhost.port)
            else:
                rhost_s = str(rhost)
            connected = "Yes: to " + rhost_s
            since = rsc.last_connect_time
        else:
            connected = "No"
            since = rsc.last_loss_time

        TIME_FORMAT = "%H:%M:%S %d-%b-%Y"
        ctx.fillSlots("connected", connected)
        ctx.fillSlots("since", time.strftime(TIME_FORMAT, time.localtime(since)))
        ctx.fillSlots("announced", time.strftime(TIME_FORMAT,
                                                 time.localtime(rsc.announcement_time)))
        ctx.fillSlots("version", rsc.version)
        ctx.fillSlots("service_name", rsc.service_name)

        return ctx.tag

    def render_download_form(self, ctx, data):
        # this is a form where users can download files by URI
        form = T.form(action="uri", method="get",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Download a file"],
            "URI to download: ",
            T.input(type="text", name="uri"), " ",
            "Filename to download as: ",
            T.input(type="text", name="filename"), " ",
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
            "URI to view: ",
            T.input(type="text", name="uri"), " ",
            T.input(type="submit", value="View!"),
            ]]
        return T.div[form]

    def render_upload_form(self, ctx, data):
        # this is a form where users can upload unlinked files
        form = T.form(action="uri", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Upload a file"],
            "Choose a file: ",
            T.input(type="file", name="file", class_="freeform-input-file"),
            T.input(type="hidden", name="t", value="upload"),
            " Mutable?:", T.input(type="checkbox", name="mutable"),
            T.input(type="submit", value="Upload!"),
            ]]
        return T.div[form]

    def render_mkdir_form(self, ctx, data):
        # this is a form where users can create new directories
        form = T.form(action="uri", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Create a directory"],
            T.input(type="hidden", name="t", value="mkdir"),
            T.input(type="hidden", name="redirect_to_result", value="true"),
            T.input(type="submit", value="Create Directory!"),
            ]]
        return T.div[form]

    def render_incident_button(self, ctx, data):
        # this button triggers a foolscap-logging "incident"
        form = T.form(action="report_incident", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Report an Incident"],
            T.input(type="hidden", name="t", value="report-incident"),
            "What went wrong?: ",
            T.input(type="text", name="details"), " ",
            T.input(type="submit", value="Report!"),
            ]]
        return T.div[form]
