from __future__ import print_function

import urllib
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

    url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    url += "/" + escape_path(path)

    resp = do_http("DELETE", url)

    if resp.status in (200,):
        print(format_http_success(resp), file=stdout)
        return 0

    print(format_http_error("ERROR", resp), file=stderr)
    return 1
