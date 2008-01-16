
import urllib
from allmydata.scripts.common_http import do_http

def rm(nodeurl, dir_uri, vdrive_pathname, verbosity, stdout, stderr):
    """
    @param verbosity: 0, 1, or 2, meaning quiet, verbose, or very verbose

    @return: a Deferred which eventually fires with the exit code
    """
    if nodeurl[-1] != "/":
        nodeurl += "/"
    url = nodeurl + "uri/%s/" % urllib.quote(dir_uri)
    if vdrive_pathname:
        url += urllib.quote(vdrive_pathname)

    resp = do_http("DELETE", url)

    if resp.status in (200,):
        print >>stdout, "%s %s" % (resp.status, resp.reason)
        return 0

    print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
    print >>stderr, resp.read()
    return 1
