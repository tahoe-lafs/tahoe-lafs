import re, time
from twisted.application import service, strports, internet
from twisted.web import http
from twisted.internet import defer
from nevow import appserver, inevow, static
from allmydata.util import log, fileutil

from allmydata.web import introweb, root
from allmydata.web.common import IOpHandleTable, MyExceptionHandler

# we must override twisted.web.http.Request.requestReceived with a version
# that doesn't use cgi.parse_multipart() . Since we actually use Nevow, we
# override the nevow-specific subclass, nevow.appserver.NevowRequest . This
# is an exact copy of twisted.web.http.Request (from SVN HEAD on 10-Aug-2007)
# that modifies the way form arguments are parsed. Note that this sort of
# surgery may induce a dependency upon a particular version of twisted.web

parse_qs = http.parse_qs
class MyRequest(appserver.NevowRequest):
    fields = None
    _tahoe_request_had_error = None

    def requestReceived(self, command, path, version):
        """Called by channel when all data has been received.

        This method is not intended for users.
        """
        self.content.seek(0,0)
        self.args = {}
        self.stack = []

        self.method, self.uri = command, path
        self.clientproto = version
        x = self.uri.split('?', 1)

        if len(x) == 1:
            self.path = self.uri
        else:
            self.path, argstring = x
            self.args = parse_qs(argstring, 1)

        # cache the client and server information, we'll need this later to be
        # serialized and sent with the request so CGIs will work remotely
        self.client = self.channel.transport.getPeer()
        self.host = self.channel.transport.getHost()

        # Argument processing.

##      The original twisted.web.http.Request.requestReceived code parsed the
##      content and added the form fields it found there to self.args . It
##      did this with cgi.parse_multipart, which holds the arguments in RAM
##      and is thus unsuitable for large file uploads. The Nevow subclass
##      (nevow.appserver.NevowRequest) uses cgi.FieldStorage instead (putting
##      the results in self.fields), which is much more memory-efficient.
##      Since we know we're using Nevow, we can anticipate these arguments
##      appearing in self.fields instead of self.args, and thus skip the
##      parse-content-into-self.args step.

##      args = self.args
##      ctype = self.getHeader('content-type')
##      if self.method == "POST" and ctype:
##          mfd = 'multipart/form-data'
##          key, pdict = cgi.parse_header(ctype)
##          if key == 'application/x-www-form-urlencoded':
##              args.update(parse_qs(self.content.read(), 1))
##          elif key == mfd:
##              try:
##                  args.update(cgi.parse_multipart(self.content, pdict))
##              except KeyError, e:
##                  if e.args[0] == 'content-disposition':
##                      # Parse_multipart can't cope with missing
##                      # content-dispostion headers in multipart/form-data
##                      # parts, so we catch the exception and tell the client
##                      # it was a bad request.
##                      self.channel.transport.write(
##                              "HTTP/1.1 400 Bad Request\r\n\r\n")
##                      self.channel.transport.loseConnection()
##                      return
##                  raise
        self.processing_started_timestamp = time.time()
        self.process()

    def _logger(self):
        # we build up a log string that hides most of the cap, to preserve
        # user privacy. We retain the query args so we can identify things
        # like t=json. Then we send it to the flog. We make no attempt to
        # match apache formatting. TODO: when we move to DSA dirnodes and
        # shorter caps, consider exposing a few characters of the cap, or
        # maybe a few characters of its hash.
        x = self.uri.split("?", 1)
        if len(x) == 1:
            # no query args
            path = self.uri
            queryargs = ""
        else:
            path, queryargs = x
            # there is a form handler which redirects POST /uri?uri=FOO into
            # GET /uri/FOO so folks can paste in non-HTTP-prefixed uris. Make
            # sure we censor these too.
            if queryargs.startswith("uri="):
                queryargs = "[uri=CENSORED]"
            queryargs = "?" + queryargs
        if path.startswith("/uri"):
            path = "/uri/[CENSORED].."
        elif path.startswith("/file"):
            path = "/file/[CENSORED].."
        elif path.startswith("/named"):
            path = "/named/[CENSORED].."

        uri = path + queryargs

        error = ""
        if self._tahoe_request_had_error:
            error = " [ERROR]"

        log.msg(format="web: %(clientip)s %(method)s %(uri)s %(code)s %(length)s%(error)s",
                clientip=self.getClientIP(),
                method=self.method,
                uri=uri,
                code=self.code,
                length=(self.sentLength or "-"),
                error=error,
                facility="tahoe.webish",
                level=log.OPERATIONAL,
                )


class WebishServer(service.MultiService):
    name = "webish"

    def __init__(self, client, webport, nodeurl_path=None, staticdir=None,
                 clock=None):
        service.MultiService.__init__(self)
        # the 'data' argument to all render() methods default to the Client
        # the 'clock' argument to root.Root is, if set, a
        # twisted.internet.task.Clock that is provided by the unit tests
        # so that they can test features that involve the passage of
        # time in a deterministic manner.
        self.root = root.Root(client, clock)
        self.buildServer(webport, nodeurl_path, staticdir)
        if self.root.child_operations:
            self.site.remember(self.root.child_operations, IOpHandleTable)
            self.root.child_operations.setServiceParent(self)

    def buildServer(self, webport, nodeurl_path, staticdir):
        self.webport = webport
        self.site = site = appserver.NevowSite(self.root)
        self.site.requestFactory = MyRequest
        self.site.remember(MyExceptionHandler(), inevow.ICanHandleException)
        if staticdir:
            self.root.putChild("static", static.File(staticdir))
        if re.search(r'^\d', webport):
            webport = "tcp:"+webport # twisted warns about bare "0" or "3456"
        s = strports.service(webport, site)
        s.setServiceParent(self)

        self._scheme = None
        self._portnum = None
        self._url = None
        self._listener = s # stash it so we can query for the portnum

        self._started = defer.Deferred()
        if nodeurl_path:
            def _write_nodeurl_file(ign):
                # this file will be created with default permissions
                fileutil.write(nodeurl_path, self.getURL() + "\n")
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


class IntroducerWebishServer(WebishServer):
    def __init__(self, introducer, webport, nodeurl_path=None, staticdir=None):
        service.MultiService.__init__(self)
        self.root = introweb.IntroducerRoot(introducer)
        self.buildServer(webport, nodeurl_path, staticdir)

