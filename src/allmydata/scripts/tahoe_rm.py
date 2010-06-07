
import urllib
from allmydata.scripts.common_http import do_http, format_http_success, format_http_error
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError

def rm(options):
    """
    @return: a Deferred which eventually fires with the exit code
    """
    nodeurl = options['node-url']
    aliases = options.aliases
    where = options.where
    stdout = options.stdout
    stderr = options.stderr

    if nodeurl[-1] != "/":
        nodeurl += "/"
    try:
        rootcap, path = get_alias(aliases, where, DEFAULT_ALIAS)
    except UnknownAliasError, e:
        e.display(stderr)
        return 1
    assert path
    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    url += "/" + escape_path(path)

    resp = do_http("DELETE", url)

    if resp.status in (200,):
        print >>stdout, format_http_success(resp)
        return 0

    print >>stderr, format_http_error("ERROR", resp)
    return 1
