
import re
import urllib
import simplejson
from allmydata.scripts.common import get_alias, DEFAULT_ALIAS, escape_path, \
                                     UnknownAliasError
from allmydata.scripts.common_http import do_http, format_http_error
from allmydata.util.stringutils import to_str

# this script is used for both 'mv' and 'ln'

def mv(options, mode="move"):
    nodeurl = options['node-url']
    aliases = options.aliases
    from_file = options.from_file
    to_file = options.to_file
    stdout = options.stdout
    stderr = options.stderr

    if nodeurl[-1] != "/":
        nodeurl += "/"
    try:
        rootcap, from_path = get_alias(aliases, from_file, DEFAULT_ALIAS)
    except UnknownAliasError, e:
        e.display(stderr)
        return 1
    from_url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if from_path:
        from_url += "/" + escape_path(from_path)
    # figure out the source cap
    data = urllib.urlopen(from_url + "?t=json").read()
    nodetype, attrs = simplejson.loads(data)
    cap = to_str(attrs.get("rw_uri") or attrs["ro_uri"])

    # now get the target
    try:
        rootcap, path = get_alias(aliases, to_file, DEFAULT_ALIAS)
    except UnknownAliasError, e:
        e.display(stderr)
        return 1
    to_url = nodeurl + "uri/%s" % urllib.quote(rootcap)
    if path:
        to_url += "/" + escape_path(path)

    if to_url.endswith("/"):
        # "mv foo.txt bar/" == "mv foo.txt bar/foo.txt"
        to_url += escape_path(from_path[from_path.rfind("/")+1:])

    to_url += "?t=uri&replace=only-files"

    resp = do_http("PUT", to_url, cap)
    status = resp.status
    if not re.search(r'^2\d\d$', str(status)):
        if status == 409:
            print >>stderr, "Error: You can't overwrite a directory with a file"
        else:
            print >>stderr, format_http_error("Error", resp)
            if mode == "move":
                print >>stderr, "NOT removing the original"
        return 1

    if mode == "move":
        # now remove the original
        resp = do_http("DELETE", from_url)
        if not re.search(r'^2\d\d$', str(status)):
            print >>stderr, format_http_error("Error deleting original after move", resp)
            return 2

    print >>stdout, "OK"
    return 0
