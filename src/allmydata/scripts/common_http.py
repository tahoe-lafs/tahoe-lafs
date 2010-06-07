
from cStringIO import StringIO
import urlparse, httplib
import allmydata # for __full_version__

from allmydata.util.stringutils import quote_output
from allmydata.scripts.common import TahoeError


# copied from twisted/web/client.py
def parse_url(url, defaultPort=None):
    url = url.strip()
    parsed = urlparse.urlparse(url)
    scheme = parsed[0]
    path = urlparse.urlunparse(('','')+parsed[2:])
    if defaultPort is None:
        if scheme == 'https':
            defaultPort = 443
        else:
            defaultPort = 80
    host, port = parsed[1], defaultPort
    if ':' in host:
        host, port = host.split(':')
        port = int(port)
    if path == "":
        path = "/"
    return scheme, host, port, path


def do_http(method, url, body=""):
    if isinstance(body, str):
        body = StringIO(body)
    elif isinstance(body, unicode):
        raise TypeError("do_http body must be a bytestring, not unicode")
    else:
        # We must give a Content-Length header to twisted.web, otherwise it
        # seems to get a zero-length file. I suspect that "chunked-encoding"
        # may fix this.
        assert body.tell
        assert body.seek
        assert body.read
    scheme, host, port, path = parse_url(url)
    if scheme == "http":
        c = httplib.HTTPConnection(host, port)
    elif scheme == "https":
        c = httplib.HTTPSConnection(host, port)
    else:
        raise ValueError("unknown scheme '%s', need http or https" % scheme)
    c.putrequest(method, path)
    c.putheader("Hostname", host)
    c.putheader("User-Agent", allmydata.__full_version__ + " (tahoe-client)")
    c.putheader("Accept", "text/plain, application/octet-stream")
    c.putheader("Connection", "close")

    old = body.tell()
    body.seek(0, 2)
    length = body.tell()
    body.seek(old)
    c.putheader("Content-Length", str(length))
    c.endheaders()

    while True:
        data = body.read(8192)
        if not data:
            break
        c.send(data)

    return c.getresponse()


def format_http_success(resp):
    return "%s %s" % (resp.status, quote_output(resp.reason, quotemarks=False))

def format_http_error(msg, resp):
    return "%s: %s %s\n%s" % (msg, resp.status, quote_output(resp.reason, quotemarks=False),
                              quote_output(resp.read(), quotemarks=False))

def check_http_error(resp, stderr):
    if resp.status < 200 or resp.status >= 300:
        print >>stderr, format_http_error("Error during HTTP request", resp)
        return 1


class HTTPError(TahoeError):
    def __init__(self, msg, resp):
        TahoeError.__init__(format_http_error(msg, resp))
