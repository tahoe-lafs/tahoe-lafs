"""
Ported to Python 3.
"""
from __future__ import unicode_literals
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from future.utils import PY2, PY3
if PY2:
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401

from urllib.parse import quote as url_quote
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError
from allmydata.scripts.common_http import do_http, format_http_error

def get(options):
    nodeurl = options['node-url']
    aliases = options.aliases
    from_file = options.from_file
    to_file = options.to_file
    stdout = options.stdout
    stderr = options.stderr

    if nodeurl[-1] != "/":
        nodeurl += "/"
    try:
        rootcap, path = get_alias(aliases, from_file, DEFAULT_ALIAS)
    except UnknownAliasError as e:
        e.display(stderr)
        return 1
    url = nodeurl + "uri/%s" % url_quote(rootcap)
    if path:
        url += "/" + escape_path(path)

    resp = do_http("GET", url)
    if resp.status in (200, 201,):
        if to_file:
            outf = open(to_file, "wb")
        else:
            outf = stdout
            # Make sure we can write bytes; on Python 3 stdout is Unicode by
            # default.
            if PY3 and getattr(outf, "encoding", None) is not None:
                outf = outf.buffer
        while True:
            data = resp.read(4096)
            if not data:
                break
            outf.write(data)
        if to_file:
            outf.close()
        rc = 0
    else:
        print(format_http_error("Error during GET", resp), file=stderr)
        rc = 1

    return rc
