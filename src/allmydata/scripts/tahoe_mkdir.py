
import urllib
from allmydata.scripts.common_http import do_http, check_http_error
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, UnknownAliasError
from allmydata.util.stringutils import unicode_to_url

def mkdir(options):
    nodeurl = options['node-url']
    aliases = options.aliases
    where = options.where
    stdout = options.stdout
    stderr = options.stderr
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    if where:
        try:
            rootcap, path = get_alias(aliases, where, DEFAULT_ALIAS)
        except UnknownAliasError, e:
            print >>stderr, "error: %s" % e.args[0]
            return 1

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
                                           urllib.quote(unicode_to_url(path)))
    resp = do_http("POST", url)
    check_http_error(resp, stderr)
    new_uri = resp.read().strip()
    print >>stdout, new_uri
    return 0
