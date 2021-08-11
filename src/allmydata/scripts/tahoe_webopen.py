"""
Ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

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

