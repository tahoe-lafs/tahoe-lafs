
import urllib
from allmydata.scripts.common_http import do_http, check_http_error
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, UnknownAliasError
from allmydata.util.encodingutil import quote_output

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
            e.display(stderr)
            return 1

    if not where or not path:
        # create a new unlinked directory
        url = nodeurl + "uri?t=mkdir"
        if options["format"]:
            url += "&format=%s" % urllib.quote(options['format'])
        resp = do_http("POST", url)
        rc = check_http_error(resp, stderr)
        if rc:
            return rc
        new_uri = resp.read().strip()
        # emit its write-cap
        print >>stdout, quote_output(new_uri, quotemarks=False)
        return 0

    # create a new directory at the given location
    if path.endswith("/"):
        path = path[:-1]
    # path must be "/".join([s.encode("utf-8") for s in segments])
    url = nodeurl + "uri/%s/%s?t=mkdir" % (urllib.quote(rootcap),
                                           urllib.quote(path))
    if options['format']:
        url += "&format=%s" % urllib.quote(options['format'])

    resp = do_http("POST", url)
    check_http_error(resp, stderr)
    new_uri = resp.read().strip()
    print >>stdout, quote_output(new_uri, quotemarks=False)
    return 0
