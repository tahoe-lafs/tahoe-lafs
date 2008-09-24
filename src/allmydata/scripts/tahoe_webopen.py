
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path
import urllib

def webopen(options, opener=None):
    nodeurl = options['node-url']
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    where = options.where
    if where is None:
        where = 'tahoe:'
    rootcap, path = get_alias(options.aliases, where, DEFAULT_ALIAS)
    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if path:
        url += "/" + escape_path(path)
    if not opener:
        import webbrowser
        opener = webbrowser.open
    opener(url)
    return 0

