
import urllib
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path
from allmydata.scripts.common_http import do_http

def get(nodeurl, aliases, from_file, to_file, stdout, stderr):
    if nodeurl[-1] != "/":
        nodeurl += "/"
    rootcap, path = get_alias(aliases, from_file, DEFAULT_ALIAS)
    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if path:
        url += "/" + escape_path(path)

    if to_file:
        outf = open(to_file, "wb")
        close_outf = True
    else:
        outf = stdout
        close_outf = False

    resp = do_http("GET", url)
    if resp.status in (200, 201,):
        while True:
            data = resp.read(4096)
            if not data:
                break
            outf.write(data)
        rc = 0
    else:
        print >>stderr, "Error, got %s %s" % (resp.status, resp.reason)
        print >>stderr, resp.read()
        rc = 1

    if close_outf:
        outf.close()

    return rc
