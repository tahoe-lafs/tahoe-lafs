
import time
from twisted.application import service, strports, internet
from twisted.web import http
from twisted.internet import defer
from nevow import appserver, inevow
from allmydata.util import log

from allmydata.web import introweb, root
from allmydata.web.common import IClient, IOpHandleTable, MyExceptionHandler

# we must override twisted.web.http.Request.requestReceived with a version
# that doesn't use cgi.parse_multipart() . Since we actually use Nevow, we
# override the nevow-specific subclass, nevow.appserver.NevowRequest . This
# is an exact copy of twisted.web.http.Request (from SVN HEAD on 10-Aug-2007)
# that modifies the way form arguments are parsed. Note that this sort of
# surgery may induce a dependency upon a particular version of twisted.web

parse_qs = http.parse_qs
class MyRequest(appserver.NevowRequest):
    fields = None
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

        log.msg(format="web: %(clientip)s %(method)s %(uri)s %(code)s %(length)s",
                clientip=self.getClientIP(),
                method=self.method,
                uri=uri,
                code=self.code,
                length=(self.sentLength or "-"),
                facility="tahoe.webish",
                level=log.OPERATIONAL,
                )


class WebishServer(service.MultiService):
    name = "webish"
    root_class = root.Root

    def __init__(self, webport, nodeurl_path=None):
        service.MultiService.__init__(self)
        self.webport = webport
        self.root = self.root_class()
        self.site = site = appserver.NevowSite(self.root)
        self.site.requestFactory = MyRequest
        if self.root.child_operations:
            self.site.remember(self.root.child_operations, IOpHandleTable)
            self.root.child_operations.setServiceParent(self)
        s = strports.service(webport, site)
        s.setServiceParent(self)
        self.listener = s # stash it so the tests can query for the portnum
        self._started = defer.Deferred()
        if nodeurl_path:
            self._started.addCallback(self._write_nodeurl_file, nodeurl_path)

    def startService(self):
        service.MultiService.startService(self)
        # to make various services available to render_* methods, we stash a
        # reference to the client on the NevowSite. This will be available by
        # adapting the 'context' argument to a special marker interface named
        # IClient.
        self.site.remember(self.parent, IClient)
        # I thought you could do the same with an existing interface, but
        # apparently 'ISite' does not exist
        #self.site._client = self.parent
        self.site.remember(MyExceptionHandler(), inevow.ICanHandleException)
        self._started.callback(None)

    def _write_nodeurl_file(self, junk, nodeurl_path):
        # what is our webport?
        s = self.listener
        if isinstance(s, internet.TCPServer):
            base_url = "http://127.0.0.1:%d/" % s._port.getHost().port
        elif isinstance(s, internet.SSLServer):
            base_url = "https://127.0.0.1:%d/" % s._port.getHost().port
        else:
            base_url = None
        if base_url:
            f = open(nodeurl_path, 'wb')
            # this file is world-readable
            f.write(base_url + "\n")
            f.close()

class IntroducerWebishServer(WebishServer):
    root_class = introweb.IntroducerRoot
