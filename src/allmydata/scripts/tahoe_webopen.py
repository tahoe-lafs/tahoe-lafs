
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError
import urllib

def webopen(options, opener=None):
    nodeurl = options['node-url']
    stderr = options.stderr
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    where = options.where
    if where:
        try:
            rootcap, path = get_alias(options.aliases, where, DEFAULT_ALIAS)
        except UnknownAliasError, e:
            print >>stderr, "error: %s" % e.args[0]
            return 1
        if path == '/':
            path = ''
        url = nodeurl + "uri/%s" % urllib.quote(rootcap)
        if path:
            url += "/" + escape_path(path)
    else:
        url = nodeurl
    if not opener:
        import webbrowser
        opener = webbrowser.open
    opener(url)
    return 0

