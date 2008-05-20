
import urllib
from allmydata.scripts.common_http import do_http, check_http_error
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS

def mkdir(nodeurl, aliases, where, stdout, stderr):
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    if where:
        rootcap, path = get_alias(aliases, where, DEFAULT_ALIAS)

    if not where or not path:
        # create a new unlinked directory
        url = nodeurl + "uri?t=mkdir"
        resp = do_http("POST", url)
        rc = check_http_error(resp, stderr)
        if rc:
            return rc
        new_uri = resp.read().strip()
        # emit its write-cap
        print >>stdout, new_uri
        return 0

    # create a new directory at the given location
    if path.endswith("/"):
        path = path[:-1]
    # path (in argv) must be "/".join([s.encode("utf-8") for s in segments])
    url = nodeurl + "uri/%s/%s?t=mkdir" % (urllib.quote(rootcap),
                                           urllib.quote(path))
    resp = do_http("POST", url)
    check_http_error(resp, stderr)
    new_uri = resp.read().strip()
    print >>stdout, new_uri
    return 0
