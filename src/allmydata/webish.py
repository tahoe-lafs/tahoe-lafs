"""
General web server-related utilities.
"""
from __future__ import annotations

from six import ensure_str
from typing import IO, Callable, Optional
import re, time, tempfile
from urllib.parse import parse_qsl, urlencode

from cgi import (
    FieldStorage,
)
from io import (
    BytesIO,
)

from twisted.application import service, strports, internet
from twisted.web import static
from twisted.web.http import (
    parse_qs,
)
from twisted.web.server import (
    Request,
    Site,
)
from twisted.internet import defer
from twisted.internet.address import (
    IPv4Address,
    IPv6Address,
)
from allmydata.util import log, fileutil

from allmydata.web import introweb, root
from allmydata.web.operations import OphandleTable

from .web.storage_plugins import (
    StoragePlugins,
)


class FileUploadFieldStorage(FieldStorage):
    """
    Do terrible things to ensure files are still bytes.

    On Python 2, uploaded files were always bytes.  On Python 3, there's a
    heuristic: if the filename is set on a field, it's assumed to be a file
    upload and therefore bytes.  If no filename is set, it's Unicode.

    Unfortunately, we always want it to be bytes, and Tahoe-LAFS also
    enables setting the filename not via the MIME filename, but via a
    separate field called "name".

    Thus we need to do this ridiculous workaround.  Mypy doesn't like it
    either, thus the ``# type: ignore`` below.

    Source for idea:
    https://mail.python.org/pipermail/python-dev/2017-February/147402.html
    """
    @property  # type: ignore
    def filename(self):
        if self.name == "file" and not self._mime_filename:
            # We use the file field to upload files, see directory.py's
            # _POST_upload. Lack of _mime_filename means we need to trick
            # FieldStorage into thinking there is a filename so it'll
            # return bytes.
            return "unknown-filename"
        return self._mime_filename

    @filename.setter
    def filename(self, value):
        self._mime_filename = value


class TahoeLAFSRequest(Request, object):
    """
    ``TahoeLAFSRequest`` adds several features to a Twisted Web ``Request``
    that are useful for Tahoe-LAFS.

    :ivar NoneType|FieldStorage fields: For POST requests, a structured
        representation of the contents of the request body.  For anything
        else, ``None``.
    """
    fields = None

    def requestReceived(self, command, path, version):
        """
        Called by channel when all data has been received.

        Override the base implementation to apply certain site-wide policies
        and to provide less memory-intensive multipart/form-post handling for
        large file uploads.
        """
        self.content.seek(0)
        self.args = {}
        self.stack = []

        self.method, self.uri = command, path
        self.clientproto = version
        x = self.uri.split(b'?', 1)

        if len(x) == 1:
            self.path = self.uri
        else:
            self.path, argstring = x
            self.args = parse_qs(argstring, 1)

        content_type = (self.requestHeaders.getRawHeaders("content-type") or [""])[0]
        if self.method == b'POST' and content_type.split(";")[0] in ("multipart/form-data", "application/x-www-form-urlencoded"):
            # We use FieldStorage here because it performs better than
            # cgi.parse_multipart(self.content, pdict) which is what
            # twisted.web.http.Request uses.

            headers = {
                ensure_str(name.lower()): ensure_str(value[-1])
                for (name, value)
                in self.requestHeaders.getAllRawHeaders()
            }

            if 'content-length' not in headers:
                # Python 3's cgi module would really, really like us to set Content-Length.
                self.content.seek(0, 2)
                headers['content-length'] = str(self.content.tell())
                self.content.seek(0)

            self.fields = FileUploadFieldStorage(
                self.content, headers, environ={'REQUEST_METHOD': 'POST'})
            self.content.seek(0)

        self._tahoeLAFSSecurityPolicy()

        self.processing_started_timestamp = time.time()
        self.process()

    def _tahoeLAFSSecurityPolicy(self):
        """
        Set response properties related to Tahoe-LAFS-imposed security policy.
        This will ensure that all HTTP requests received by the Tahoe-LAFS
        HTTP server have this policy imposed, regardless of other
        implementation details.
        """
        # See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Frame-Options
        self.responseHeaders.setRawHeaders("X-Frame-Options", ["DENY"])
        # See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Referrer-Policy
        self.setHeader("Referrer-Policy", "no-referrer")


def _get_client_ip(request):
    try:
        get = request.getClientAddress
    except AttributeError:
        return request.getClientIP()
    else:
        client_addr = get()
        if isinstance(client_addr, (IPv4Address, IPv6Address)):
            return client_addr.host
        return None


def _logFormatter(logDateTime, request):
    # we build up a log string that hides most of the cap, to preserve
    # user privacy. We retain the query args so we can identify things
    # like t=json. Then we send it to the flog. We make no attempt to
    # match apache formatting. TODO: when we move to DSA dirnodes and
    # shorter caps, consider exposing a few characters of the cap, or
    # maybe a few characters of its hash.
    x = request.uri.split(b"?", 1)
    if len(x) == 1:
        # no query args
        path = request.uri
        queryargs = b""
    else:
        path, queryargs = x
        queryargs = b"?" + censor(queryargs)
    if path.startswith(b"/uri/"):
        path = b"/uri/[CENSORED]"
    elif path.startswith(b"/file/"):
        path = b"/file/[CENSORED]"
    elif path.startswith(b"/named/"):
        path = b"/named/[CENSORED]"

    uri = path + queryargs

    template = "web: %(clientip)s %(method)s %(uri)s %(code)s %(length)s"
    return template % dict(
        clientip=_get_client_ip(request),
        method=str(request.method, "utf-8"),
        uri=str(uri, "utf-8"),
        code=request.code,
        length=(request.sentLength or "-"),
        facility="tahoe.webish",
        level=log.OPERATIONAL,
    )


def censor(queryargs: bytes) -> bytes:
    """
    Replace potentially sensitive values in query arguments with a
    constant string.
    """
    args = parse_qsl(queryargs.decode("ascii"), keep_blank_values=True, encoding="utf8")
    result = []
    for k, v in args:
        if k == "uri":
            # there is a form handler which redirects POST /uri?uri=FOO into
            # GET /uri/FOO so folks can paste in non-HTTP-prefixed uris. Make
            # sure we censor these.
            v = "[CENSORED]"
        elif k == "private-key":
            # Likewise, sometimes a private key is supplied with mutable
            # creation.
            v = "[CENSORED]"

        result.append((k, v))

    # Customize safe to try to leave our markers intact.
    return urlencode(result, safe="[]").encode("ascii")


def anonymous_tempfile_factory(tempdir: bytes) -> Callable[[], IO[bytes]]:
    """
    Create a no-argument callable for creating a new temporary file in the
    given directory.

    :param tempdir: The directory in which temporary files with be created.

    :return: The callable.
    """
    return lambda: tempfile.TemporaryFile(dir=tempdir)


class TahoeLAFSSite(Site, object):
    """
    The HTTP protocol factory used by Tahoe-LAFS.

    Among the behaviors provided:

    * A configurable temporary file factory for large request bodies to avoid
      keeping them in memory.

    * A log formatter that writes some access logs but omits capability
      strings to help keep them secret.
    """
    requestFactory = TahoeLAFSRequest

    def __init__(self, make_tempfile: Callable[[], IO[bytes]], *args, **kwargs):
        Site.__init__(self, *args, logFormatter=_logFormatter, **kwargs)
        assert callable(make_tempfile)
        with make_tempfile():
            pass
        self._make_tempfile = make_tempfile

    def getContentFile(self, length: Optional[int]) -> IO[bytes]:
        if length is None or length >= 1024 * 1024:
            return self._make_tempfile()
        return BytesIO()

class WebishServer(service.MultiService):
    # The type in Twisted for services is wrong in 22.10...
    # https://github.com/twisted/twisted/issues/10135
    name = "webish"  # type: ignore[assignment]

    def __init__(self, client, webport, make_tempfile, nodeurl_path=None, staticdir=None,
                 clock=None, now_fn=time.time):
        service.MultiService.__init__(self)
        # the 'data' argument to all render() methods default to the Client
        # the 'clock' argument to root.Root is, if set, a
        # twisted.internet.task.Clock that is provided by the unit tests
        # so that they can test features that involve the passage of
        # time in a deterministic manner.

        self.root = root.Root(client, clock, now_fn)
        self.buildServer(webport, make_tempfile, nodeurl_path, staticdir)

        # If set, clock is a twisted.internet.task.Clock that the tests
        # use to test ophandle expiration.
        self._operations = OphandleTable(clock)
        self._operations.setServiceParent(self)
        self.root.putChild(b"operations", self._operations)

        self.root.putChild(b"storage-plugins", StoragePlugins(client))

    def buildServer(self, webport, make_tempfile, nodeurl_path, staticdir):
        self.webport = webport
        self.site = TahoeLAFSSite(make_tempfile, self.root)
        self.staticdir = staticdir # so tests can check
        if staticdir:
            self.root.putChild(b"static", static.File(staticdir))
        if re.search(r'^\d', webport):
            webport = "tcp:"+webport # twisted warns about bare "0" or "3456"
        # strports must be native strings.
        webport = ensure_str(webport)
        s = strports.service(webport, self.site)
        s.setServiceParent(self)

        self._scheme = None
        self._portnum = None
        self._url = None
        self._listener = s # stash it so we can query for the portnum

        self._started = defer.Deferred()
        if nodeurl_path:
            def _write_nodeurl_file(ign):
                # this file will be created with default permissions
                line = self.getURL() + "\n"
                fileutil.write_atomically(nodeurl_path, line, mode="")
            self._started.addCallback(_write_nodeurl_file)

    def getURL(self):
        assert self._url
        return self._url

    def getPortnum(self):
        assert self._portnum
        return self._portnum

    def startService(self):
        def _got_port(lp):
            self._portnum = lp.getHost().port
            # what is our webport?
            assert self._scheme
            self._url = "%s://127.0.0.1:%d/" % (self._scheme, self._portnum)
            self._started.callback(None)
            return lp
        def _fail(f):
            self._started.errback(f)
            return f

        service.MultiService.startService(self)
        s = self._listener
        if hasattr(s, 'endpoint') and hasattr(s, '_waitingForPort'):
            # Twisted 10.2 gives us a StreamServerEndpointService. This is
            # ugly but should do for now.
            classname = s.endpoint.__class__.__name__
            if classname.startswith('SSL'):
                self._scheme = 'https'
            else:
                self._scheme = 'http'
            s._waitingForPort.addCallbacks(_got_port, _fail)
        elif isinstance(s, internet.TCPServer):
            # Twisted <= 10.1
            self._scheme = 'http'
            _got_port(s._port)
        elif isinstance(s, internet.SSLServer):
            # Twisted <= 10.1
            self._scheme = 'https'
            _got_port(s._port)
        else:
            # who knows, probably some weirdo future version of Twisted
            self._started.errback(AssertionError("couldn't find out the scheme or port for the web-API server"))

    def get_operations(self):
        """
        :return: a reference to our "active operations" tracker
        """
        return self._operations


class IntroducerWebishServer(WebishServer):
    def __init__(self, introducer, webport, nodeurl_path=None, staticdir=None):
        service.MultiService.__init__(self)
        self.root = introweb.IntroducerRoot(introducer)
        self.buildServer(webport, tempfile.TemporaryFile, nodeurl_path, staticdir)
