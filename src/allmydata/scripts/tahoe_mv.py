
import re
import urllib
import simplejson
from allmydata.scripts.common_http import do_http

def mv(nodeurl, dir_uri, frompath, topath, stdout, stderr):
    frompath = urllib.quote(frompath)
    topath = urllib.quote(topath)
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(dir_uri)
    data = urllib.urlopen(url + frompath + "?t=json").read()

    nodetype, attrs = simplejson.loads(data)
    uri = attrs.get("rw_uri") or attrs["ro_uri"]
    # simplejson always returns unicode, but we know that it's really just a
    # bytestring.
    uri = str(uri)

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



