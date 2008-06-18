
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path

def webopen(nodeurl, aliases, where, stdout, stderr):
    import urllib, webbrowser
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    if where.endswith("/"):
        where = where[:-1]
    rootcap, path = get_alias(aliases, where, DEFAULT_ALIAS)
    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if path:
        # move where.endswith check here?
        url += "/" + escape_path(path)
    webbrowser.open(url)
    return 0

