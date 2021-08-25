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
from allmydata.scripts.common_http import do_http, format_http_success, format_http_error
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError

def unlink(options, command="unlink"):
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
    except UnknownAliasError as e:
        e.display(stderr)
        return 1
    if not path:
        print("""
'tahoe %s' can only unlink directory entries, so a path must be given.""" % (command,), file=stderr)
        return 1

    url = nodeurl + "uri/%s" % url_quote(rootcap)
    url += "/" + escape_path(path)

    resp = do_http("DELETE", url)

    if resp.status in (200,):
        print(format_http_success(resp), file=stdout)
        return 0

    print(format_http_error("ERROR", resp), file=stderr)
    return 1
