#! /usr/bin/python

import re
import urllib, httplib
import urlparse
import simplejson

# copied from twisted/web/client.py
def _parse(url, defaultPort=None):
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
    scheme, host, port, path = _parse(url)
    if scheme == "http":
        c = httplib.HTTPConnection(host, port)
    elif scheme == "https":
        c = httplib.HTTPSConnection(host, port)
    else:
        raise ValueError("unknown scheme '%s', need http or https" % scheme)
    c.putrequest(method, path)
    import allmydata
    c.putheader("User-Agent", "tahoe_mv/%s" % allmydata.__version__)
    c.putheader("Content-Length", str(len(body)))
    c.endheaders()
    c.send(body)
    return c.getresponse()

def mv(nodeurl, root_uri, frompath, topath, stdout, stderr):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(root_uri.replace("/","!"))
    data = urllib.urlopen(url + frompath + "?t=json").read()

    nodetype, attrs = simplejson.loads(data)
    uri = attrs.get("rw_uri") or attrs["ro_uri"]

    put_url = url + topath + "?t=uri"
    resp = do_http("PUT", put_url, uri)
    status = resp.status
    if not re.search(r'^2\d\d$', str(status)):
        print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
        print >>stderr, resp.read()

    # now remove the original
    resp = do_http("DELETE", url + frompath)
    if not re.search(r'^2\d\d$', str(status)):
        print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
        print >>stderr, resp.read()

    print >>stdout, "OK"
    return



