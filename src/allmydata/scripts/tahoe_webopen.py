
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path
import urllib

def webopen(options, opener=None):
    nodeurl = options['node-url']
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    where = options.where
    if where:
        rootcap, path = get_alias(options.aliases, where, DEFAULT_ALIAS)
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

