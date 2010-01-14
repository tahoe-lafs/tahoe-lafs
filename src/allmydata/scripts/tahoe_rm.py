
import urllib
from allmydata.scripts.common_http import do_http
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path

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
    rootcap, path = get_alias(aliases, where, DEFAULT_ALIAS)
    assert path
    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    url += "/" + escape_path(path)

    resp = do_http("DELETE", url)

    if resp.status in (200,):
        print >>stdout, "%s %s" % (resp.status, resp.reason)
        return 0

    print >>stderr, "error, got %s %s" % (resp.status, resp.reason)
    print >>stderr, resp.read()
    return 1
