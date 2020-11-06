from six import ensure_str

import re, time

from functools import (
    partial,
)
from cgi import (
    FieldStorage,
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

        if self.method == 'POST':
            # We use FieldStorage here because it performs better than
            # cgi.parse_multipart(self.content, pdict) which is what
            # twisted.web.http.Request uses.
            self.fields = FieldStorage(
                self.content,
                {
                    name.lower(): value[-1]
                    for (name, value)
                    in self.requestHeaders.getAllRawHeaders()
                },
                environ={'REQUEST_METHOD': 'POST'})
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
        # there is a form handler which redirects POST /uri?uri=FOO into
        # GET /uri/FOO so folks can paste in non-HTTP-prefixed uris. Make
        # sure we censor these too.
        if queryargs.startswith(b"uri="):
            queryargs = b"uri=[CENSORED]"
        queryargs = "?" + queryargs
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
        method=request.method,
        uri=uri,
        code=request.code,
        length=(request.sentLength or "-"),
        facility="tahoe.webish",
        level=log.OPERATIONAL,
    )


tahoe_lafs_site = partial(
    Site,
    requestFactory=TahoeLAFSRequest,
    logFormatter=_logFormatter,
)


class WebishServer(service.MultiService):
    name = "webish"

    def __init__(self, client, webport, nodeurl_path=None, staticdir=None,
                 clock=None, now_fn=time.time):
        service.MultiService.__init__(self)
        # the 'data' argument to all render() methods default to the Client
        # the 'clock' argument to root.Root is, if set, a
        # twisted.internet.task.Clock that is provided by the unit tests
        # so that they can test features that involve the passage of
        # time in a deterministic manner.

        self.root = root.Root(client, clock, now_fn)
        self.buildServer(webport, nodeurl_path, staticdir)

        # If set, clock is a twisted.internet.task.Clock that the tests
        # use to test ophandle expiration.
        self._operations = OphandleTable(clock)
        self._operations.setServiceParent(self)
        self.root.putChild("operations", self._operations)

        self.root.putChild(b"storage-plugins", StoragePlugins(client))

    def buildServer(self, webport, nodeurl_path, staticdir):
        self.webport = webport
        self.site = tahoe_lafs_site(self.root)
        self.staticdir = staticdir # so tests can check
        if staticdir:
            self.root.putChild("static", static.File(staticdir))
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
        self.buildServer(webport, nodeurl_path, staticdir)
