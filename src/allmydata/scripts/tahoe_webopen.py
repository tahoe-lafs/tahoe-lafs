"""
Ported to Python 3.
"""

from urllib.parse import quote as url_quote

from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError


def webopen(options, opener=None):
    nodeurl = options['node-url']
    stderr = options.stderr
    if not nodeurl.endswith("/"):
        nodeurl += "/"
    where = options.where
    if where:
        try:
            rootcap, path = get_alias(options.aliases, where, DEFAULT_ALIAS)
        except UnknownAliasError as e:
            e.display(stderr)
            return 1
        path = str(path, "utf-8")
        if path == '/':
            path = ''
        url = nodeurl + "uri/%s" % url_quote(rootcap)
        if path:
            url += "/" + escape_path(path)
    else:
        url = nodeurl
    if options['info']:
        url += "?t=info"
    if not opener:
        import webbrowser
        opener = webbrowser.open
    opener(url)
    return 0

