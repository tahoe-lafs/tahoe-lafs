
from cStringIO import StringIO
import urllib
from allmydata.scripts.common_http import do_http
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path

def put(nodeurl, aliases, from_file, to_file, mutable,
        verbosity, stdin, stdout, stderr):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    if nodeurl[-1] != "/":
        nodeurl += "/"
    if to_file:
        rootcap, path = get_alias(aliases, to_file, DEFAULT_ALIAS)
        url = nodeurl + "uri/%s/" % urllib.quote(rootcap)
        if path:
            url += escape_path(path)
    else:
        url = nodeurl + "uri"
    if mutable:
        url += "?mutable=true"
    if from_file:
        infileobj = open(from_file, "rb")
    else:
        # do_http() can't use stdin directly: for one thing, we need a
        # Content-Length field. So we currently must copy it.
        if verbosity > 0:
            print >>stderr, "waiting for file data on stdin.."
        data = stdin.read()
        infileobj = StringIO(data)

    resp = do_http("PUT", url, infileobj)

    if resp.status in (200, 201,):
        print >>stderr, "%s %s" % (resp.status, resp.reason)
        print >>stdout, resp.read()
        return 0

    print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
    print >>stderr, resp.read()
    return 1
