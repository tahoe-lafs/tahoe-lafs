"""
Ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

import os
from io import BytesIO
from six.moves import urllib, http_client
import six
import allmydata # for __full_version__

from allmydata.util.encodingutil import quote_output
from allmydata.scripts.common import TahoeError
from socket import error as socket_error

# copied from twisted/web/client.py
def parse_url(url, defaultPort=None):
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    scheme = parsed[0]
    path = urllib.parse.urlunparse(('','')+parsed[2:])
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

class BadResponse(object):
    def __init__(self, url, err):
        self.status = -1
        self.reason = "Error trying to connect to %s: %s" % (url, err)
        self.error = err
    def read(self, length=0):
        return ""


def do_http(method, url, body=b""):
    if isinstance(body, bytes):
        body = BytesIO(body)
    elif isinstance(body, six.text_type):
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
        c = http_client.HTTPConnection(host, port)
    elif scheme == "https":
        c = http_client.HTTPSConnection(host, port)
    else:
        raise ValueError("unknown scheme '%s', need http or https" % scheme)
    c.putrequest(method, path)
    c.putheader("Hostname", host)
    c.putheader("User-Agent", allmydata.__full_version__ + " (tahoe-client)")
    c.putheader("Accept", "text/plain, application/octet-stream")
    c.putheader("Connection", "close")

    old = body.tell()
    body.seek(0, os.SEEK_END)
    length = body.tell()
    body.seek(old)
    c.putheader("Content-Length", str(length))

    try:
        c.endheaders()
    except socket_error as err:
        return BadResponse(url, err)

    while True:
        data = body.read(8192)
        if not data:
            break
        c.send(data)

    return c.getresponse()


def format_http_success(resp):
    # ensure_text() shouldn't be necessary when Python 2 is dropped.
    return quote_output(
        "%s %s" % (resp.status, six.ensure_text(resp.reason)),
        quotemarks=False)

def format_http_error(msg, resp):
    # ensure_text() shouldn't be necessary when Python 2 is dropped.
    return quote_output(
        "%s: %s %s\n%s" % (msg, resp.status, six.ensure_text(resp.reason),
                           six.ensure_text(resp.read())),
        quotemarks=False)

def check_http_error(resp, stderr):
    if resp.status < 200 or resp.status >= 300:
        print(format_http_error("Error during HTTP request", resp), file=stderr)
        return 1


class HTTPError(TahoeError):
    def __init__(self, msg, resp):
        TahoeError.__init__(self, format_http_error(msg, resp))
