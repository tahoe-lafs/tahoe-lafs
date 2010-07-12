
import urllib
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError
from allmydata.scripts.common_http import do_http, format_http_error
from allmydata.util.fileutil import open_expanduser

def get(options):
    nodeurl = options['node-url']
    aliases = options.aliases
    from_file = options.from_file
    to_file = options.to_file
    stdout = options.stdout
    stderr = options.stderr

    if nodeurl[-1] != "/":
        nodeurl += "/"
    try:
        rootcap, path = get_alias(aliases, from_file, DEFAULT_ALIAS)
    except UnknownAliasError, e:
        e.display(stderr)
        return 1
    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if path:
        url += "/" + escape_path(path)

    resp = do_http("GET", url)
    if resp.status in (200, 201,):
        if to_file:
            outf = open_expanduser(to_file, "wb")
        else:
            outf = stdout
        while True:
            data = resp.read(4096)
            if not data:
                break
            outf.write(data)
        if to_file:
            outf.close()
        rc = 0
    else:
        print >>stderr, format_http_error("Error during GET", resp)
        rc = 1

    return rc
